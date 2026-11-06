"""Ícone do Monitor do PC perto do relógio. Abre a janela do programa e executa as ações
que precisam da sessão do usuário (mostrar janela de um programa, abrir pasta, fechar janela).

  MonitorPC.exe            -> abre a janela (e o ícone, se ainda não estiver aberto)
  MonitorPC.exe --bandeja  -> só o ícone (usado ao ligar o Windows)
"""
import os
import sys
import json
import time
import shutil
import threading
import subprocess
import webbrowser
import urllib.error
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitorpc import config  # noqa: E402

WIN = os.name == "nt"
PASTA_USUARIO = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "MonitorPC")
ICONE = None


def _log():
    os.makedirs(PASTA_USUARIO, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        f = open(os.path.join(PASTA_USUARIO, "bandeja.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


# ------------------------------------------------------------------ janela do monitor
def navegador():
    if not WIN:
        return None
    for c in (
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ):
        if c and os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def abrir_janela(aba=""):
    url = config.URL + (f"#{aba}" if aba else "")
    nav = navegador()
    if nav:
        perfil = os.path.join(PASTA_USUARIO, "janela")
        subprocess.Popen([nav, f"--app={url}", f"--user-data-dir={perfil}", "--window-size=1480,920",
                          "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                          "--disable-sync"], close_fds=True)
    else:
        webbrowser.open(url)


# ------------------------------------------------------------------ API do serviço
_token = {"v": None}


def token():
    if _token["v"] is None:
        try:
            with open(config.TOKEN, encoding="utf-8") as f:
                _token["v"] = f.read().strip()
        except OSError:
            _token["v"] = ""
    return _token["v"]


def api_get(caminho, timeout=3, com_token=False):
    req = urllib.request.Request(config.URL + caminho, headers={"X-Token": token()} if com_token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_post(caminho, dados, timeout=5):
    req = urllib.request.Request(config.URL + caminho, data=json.dumps(dados).encode("utf-8"), method="POST",
                                 headers={"X-Token": token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ janelas dos programas (Windows)
if WIN:
    import ctypes
    from ctypes import wintypes as wt
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    _ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.GetWindowLongW.restype = ctypes.c_long


def listar_janelas():
    """{pid: [(hwnd, titulo, minimizada, area)]} das janelas de programa (as que aparecem na barra de tarefas)."""
    out = defaultdict(list)
    if not WIN:
        return out

    def cb(h, _l):
        try:
            if not user32.IsWindowVisible(h) or user32.GetWindow(h, 4):  # GW_OWNER
                return True
            if user32.GetWindowLongW(h, -20) & 0x80:  # WS_EX_TOOLWINDOW
                return True
            n = user32.GetWindowTextLengthW(h)
            if n <= 0:
                return True
            cloak = wt.DWORD(0)
            if dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(cloak), 4) == 0 and cloak.value:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            out[pid.value].append((h, buf.value, bool(user32.IsIconic(h)), (r.right - r.left) * (r.bottom - r.top)))
        except Exception:
            pass
        return True

    user32.EnumWindows(_ENUM(cb), 0)
    return out


def trazer_para_frente(h):
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)  # SW_RESTORE
    else:
        user32.ShowWindow(h, 5)  # SW_SHOW
    user32.keybd_event(0x12, 0, 0, 0)   # Alt (o Windows só deixa trazer para frente depois de uma tecla)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(h)
    user32.BringWindowToTop(h)


def executar(a):
    tipo = a.get("tipo")
    if not WIN:
        return False, "Só no Windows"
    if tipo == "mostrar":
        js = listar_janelas()
        cands = [j for p in a.get("pids", []) for j in js.get(p, [])]
        if cands:
            cands.sort(key=lambda j: (j[2], -j[3]))  # prefere não minimizada e maior
            h = cands[0][0]
            trazer_para_frente(h)
            return True, f'Janela "{cands[0][1][:60]}" aberta.'
        exe = a.get("exe")
        if exe and os.path.exists(exe):
            os.startfile(exe)
            return True, "Esse programa não tinha janela aberta (estava só perto do relógio). Pedi para ele abrir."
        return False, "Esse programa não tem janela para mostrar."
    if tipo == "fechar_janelas":
        js = listar_janelas()
        n = 0
        for p in a.get("pids", []):
            for h, _t, _m, _a in js.get(p, []):
                user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE (igual a clicar no X)
                n += 1
        if n:
            return True, f"Pedi para fechar {n} janela(s). Se o programa perguntar algo (salvar?), responda na janela dele."
        return False, "Nenhuma janela aberta para fechar. Use “Forçar encerramento” se quiser encerrar mesmo assim."
    if tipo == "pasta":
        c = a.get("caminho", "")
        if os.path.exists(c):
            subprocess.Popen(f'explorer.exe /select,"{c}"')
            return True, "Pasta aberta."
        pasta = os.path.dirname(c)
        if os.path.isdir(pasta):
            os.startfile(pasta)
            return True, "Pasta aberta."
        return False, "Essa pasta não existe mais."
    if tipo == "abrir_exe":
        c = a.get("caminho", "")
        if os.path.exists(c):
            if a.get("minimizado"):
                si = subprocess.STARTUPINFO()
                si.dwFlags |= 1          # STARTF_USESHOWWINDOW
                si.wShowWindow = 7       # SW_SHOWMINNOACTIVE
                subprocess.Popen([c], startupinfo=si, cwd=os.path.dirname(c), close_fds=True)
            else:
                os.startfile(c)
            return True, "Programa aberto."
        return False, "Arquivo não encontrado."
    if tipo == "notificar":
        if ICONE is not None:
            try:
                ICONE.notify(a.get("msg", ""), a.get("titulo", "Monitor do PC"))
            except Exception as e:  # noqa: BLE001
                return False, str(e)
        return True, "Notificação mostrada."
    if tipo == "wsl_desligar":
        r = subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=60, creationflags=0x08000000)
        return r.returncode == 0, "WSL desligado." if r.returncode == 0 else "Não consegui desligar o WSL."
    return False, "Ação desconhecida."


def loop_fila():
    while True:
        try:
            acoes = api_get("api/fila?espera=20", timeout=30, com_token=True).get("acoes", [])
            for a in acoes:
                try:
                    ok, msg = executar(a)
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, str(e)
                try:
                    api_post("api/fila/resultado", {"id": a.get("id"), "ok": ok, "msg": msg})
                except Exception:
                    pass
        except urllib.error.HTTPError as e:
            if e.code == 403:
                _token["v"] = None  # serviço reiniciou: lê o token novo
            time.sleep(3)
        except Exception:
            time.sleep(3)


def loop_janelas():
    while True:
        try:
            js = listar_janelas()
            frente = None
            if WIN:
                h = user32.GetForegroundWindow()
                if h:
                    pid = wt.DWORD(0)
                    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    frente = pid.value
            api_post("api/janelas", {"janelas": {str(p): [t[:120] for _h, t, _m, _a in v] for p, v in js.items()},
                                     "frente": frente})
        except urllib.error.HTTPError as e:
            if e.code == 403:
                _token["v"] = None
        except Exception:
            pass
        time.sleep(2)


# ------------------------------------------------------------------ ícone
def desenhar(mem, cpu, ok=True):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(28, 30, 38, 255))
    if not ok:
        d.line([18, 18, 46, 46], fill=(239, 83, 80, 255), width=7)
        d.line([46, 18, 18, 46], fill=(239, 83, 80, 255), width=7)
        return img

    def cor(v, alto, medio):
        if v >= alto:
            return (239, 83, 80, 255)
        if v >= medio:
            return (255, 183, 77, 255)
        return (102, 187, 106, 255)

    for x0, x1, v, c in ((10, 38, mem, cor(mem, 88, 75)), (44, 54, cpu, cor(cpu, 85, 60))):
        d.rounded_rectangle([x0, 10, x1, 54], radius=4, fill=(55, 58, 70, 255))
        h = max(3, int(44 * min(v, 100) / 100))
        d.rounded_rectangle([x0, 54 - h, x1, 54], radius=4, fill=c)
    return img


def atualizar_icone():
    ultimo = None
    while True:
        try:
            e = api_get("api/agora")
        except Exception:
            e = None
        try:
            if ICONE is not None:
                if e:
                    chave = (int(e["mem_pct"] // 4), int(e["cpu"] // 10))
                    if chave != ultimo:
                        ICONE.icon = desenhar(e["mem_pct"], e["cpu"])
                        ultimo = chave
                    partes = [f"Memória {e['mem_pct']:.0f}%", f"CPU {e['cpu']:.0f}%"]
                    if e.get("temp_cpu"):
                        partes.append(f"{e['temp_cpu']:.0f}°C")
                    ICONE.title = "Monitor do PC — " + " · ".join(partes)
                else:
                    if ultimo != "erro":
                        ICONE.icon = desenhar(0, 0, ok=False)
                        ultimo = "erro"
                    ICONE.title = "Monitor do PC — serviço não está respondendo"
        except Exception:
            pass
        time.sleep(3)


def instancia_unica():
    if not WIN:
        return True
    import ctypes as ct
    ct.windll.kernel32.CreateMutexW(None, False, "Local\\MonitorPC-Bandeja")
    return ct.windll.kernel32.GetLastError() != 183


def main():
    global ICONE
    _log()
    so_icone = "--bandeja" in sys.argv
    if not instancia_unica():
        if not so_icone:
            abrir_janela()
        return
    if not so_icone:
        threading.Timer(0.5, abrir_janela).start()
    for alvo in (loop_fila, loop_janelas, atualizar_icone):
        threading.Thread(target=alvo, daemon=True).start()
    import pystray
    estado_jogo = {"ativo": False}
    planos_cache = {"lista": [], "t": 0}

    def atualizar_extras():
        while True:
            try:
                estado_jogo["ativo"] = bool(api_get("api/jogo").get("ativo"))
            except Exception:
                pass
            try:
                planos_cache["lista"] = api_get("api/energia/planos", timeout=15)
            except Exception:
                pass
            time.sleep(10)

    threading.Thread(target=atualizar_extras, daemon=True).start()

    def alternar_jogo(*_):
        try:
            r = api_post("api/acao", {"tipo": "jogo_desligar" if estado_jogo["ativo"] else "jogo_ligar"}, timeout=30)
            estado_jogo["ativo"] = not estado_jogo["ativo"]
            ICONE.notify(r.get("msg", "")[:250], "Modo Jogo")
        except Exception as e:  # noqa: BLE001
            ICONE.notify(str(e), "Modo Jogo")

    def item_plano(p):
        def ativar(*_):
            try:
                api_post("api/acao", {"tipo": "energia_plano", "guid": p["guid"]}, timeout=20)
                for x in planos_cache["lista"]:
                    x["ativo"] = x["guid"] == p["guid"]
            except Exception:
                pass
        return pystray.MenuItem(p["nome"], ativar, checked=lambda *_: any(x["guid"] == p["guid"] and x["ativo"] for x in planos_cache["lista"]), radio=True)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir Monitor do PC", lambda *_: abrir_janela(), default=True),
        pystray.MenuItem("Programas", lambda *_: abrir_janela("programas")),
        pystray.MenuItem(lambda *_: "Desligar Modo Jogo" if estado_jogo["ativo"] else "Ligar Modo Jogo", alternar_jogo),
        pystray.MenuItem("Plano de energia", pystray.Menu(lambda: [item_plano(p) for p in planos_cache["lista"]] or
                                                          [pystray.MenuItem("carregando…", None, enabled=False)])),
        pystray.MenuItem("Relatório", lambda *_: abrir_janela("relatorio")),
        pystray.MenuItem("Configurações", lambda *_: abrir_janela("config")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Fechar ícone (o monitoramento continua)", lambda *_: ICONE.stop()),
    )
    ICONE = pystray.Icon("monitor-pc", desenhar(0, 0), "Monitor do PC", menu)
    ICONE.run()


if __name__ == "__main__":
    main()
