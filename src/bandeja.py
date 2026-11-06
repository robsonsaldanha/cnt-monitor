"""Ícone do Monitor do PC perto do relógio.

Abre a janela do programa, mostra o mini painel flutuante e executa as ações que precisam
da sessão do usuário (mostrar janela de um programa, abrir pasta, fechar janela, notificações).

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
TITULO_JANELA = "Monitor do PC"


def _log():
    os.makedirs(PASTA_USUARIO, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        f = open(os.path.join(PASTA_USUARIO, "bandeja.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


# ------------------------------------------------------------------ API do serviço
_token = {"v": None}
_cfg = {"v": {}, "t": 0}


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
        d = json.loads(r.read())
    if caminho.startswith("api/v1/") and isinstance(d, dict) and "dados" in d:
        return d["dados"]      # a API v1 vem num envelope {"ok", "dados"}
    return d


def api_post(caminho, dados, timeout=5):
    req = urllib.request.Request(config.URL + caminho, data=json.dumps(dados).encode("utf-8"), method="POST",
                                 headers={"X-Token": token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def cfg(forcar=False):
    if forcar or time.time() - _cfg["t"] > 5:
        try:
            _cfg["v"] = api_get("api/config").get("valores", {})
            _cfg["t"] = time.time()
        except Exception:
            pass
    return _cfg["v"]


# ------------------------------------------------------------------ janelas (Windows)
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


def janela_existente():
    for _pid, js in listar_janelas().items():
        for h, titulo, _m, _a in js:
            if titulo == TITULO_JANELA:
                return h
    return None


def abrir_janela(aba=""):
    """Abre a janela do Monitor. Se só pode haver uma (Configurações), traz a que já está aberta."""
    if WIN and not cfg().get("varias_janelas", False):
        h = janela_existente()
        if h:
            trazer_para_frente(h)
            if aba:
                try:
                    api_post("api/ui/navegar", {"aba": aba})
                except Exception:
                    pass
            return
    url = config.URL + (f"#{aba}" if aba else "")
    nav = navegador()
    if nav:
        perfil = os.path.join(PASTA_USUARIO, "janela")
        subprocess.Popen([nav, f"--app={url}", f"--user-data-dir={perfil}", "--window-size=1480,920",
                          "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                          "--disable-sync"], close_fds=True)
    else:
        webbrowser.open(url)


# ------------------------------------------------------------------ ações vindas do serviço
def executar(a):
    tipo = a.get("tipo")
    if not WIN:
        return False, "Só no Windows"
    if tipo == "mostrar":
        js = listar_janelas()
        cands = [j for p in a.get("pids", []) for j in js.get(p, [])]
        if cands:
            cands.sort(key=lambda j: (j[2], -j[3]))  # prefere não minimizada e maior
            trazer_para_frente(cands[0][0])
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
        if os.path.exists(c) and os.path.isfile(c):
            subprocess.Popen(f'explorer.exe /select,"{c}"')
            return True, "Pasta aberta."
        pasta = c if os.path.isdir(c) else os.path.dirname(c)
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
                ICONE.notify(a.get("msg", "")[:250], a.get("titulo", "Monitor do PC")[:60])
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


# ------------------------------------------------------------------ mini painel flutuante
ITENS_MINI = {
    "cpu": ("CPU", lambda r, j: r.get("cpu"), "%", (60, 85)),
    "mem": ("RAM", lambda r, j: r.get("mem_pct"), "%", (75, 88)),
    "temp_cpu": ("CPU °C", lambda r, j: r.get("temp_cpu"), "°", (75, 90)),
    "gpu": ("GPU", lambda r, j: r.get("uso_gpu"), "%", (85, 97)),
    "temp_gpu": ("GPU °C", lambda r, j: r.get("temp_gpu"), "°", (75, 85)),
    "fps": ("FPS", lambda r, j: (j.get("fps_principal") or {}).get("fps"), "", None),
    "rede": ("Rede ↓", lambda r, j: r.get("rec"), "rede", None),
    "disco": ("Disco", lambda r, j: (r.get("ler") or 0) + (r.get("gravar") or 0), "disco", None),
    "hora": ("Hora", lambda r, j: time.strftime("%H:%M"), "txt", None),
}


def _fmt_taxa(b):
    if b is None:
        return "—"
    if b >= 1048576:
        return f"{b / 1048576:.1f} MB/s"
    return f"{b / 1024:.0f} KB/s"


class MiniPainel:
    def __init__(self):
        self.dados = {"r": {}, "j": {}}
        self.root = None
        self.visivel = False
        self.chave_layout = None
        self.pos_arq = os.path.join(PASTA_USUARIO, "mini_painel.json")

    def iniciar(self):
        threading.Thread(target=self._buscar, daemon=True).start()
        threading.Thread(target=self._tk, daemon=True).start()

    def _buscar(self):
        n = 0
        while True:
            c = cfg()
            if c.get("mini_ativo"):
                try:
                    self.dados["r"] = api_get("api/v1/resumo")
                except Exception:
                    self.dados["r"] = {}
                if "fps" in (c.get("mini_itens") or []) and n % 2 == 0:
                    try:
                        self.dados["j"] = api_get("api/jogo")
                    except Exception:
                        pass
                n += 1
                time.sleep(0.5)
            else:
                time.sleep(1.5)

    def _pos(self):
        try:
            with open(self.pos_arq, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _salvar_pos(self, x, y):
        try:
            with open(self.pos_arq, "w", encoding="utf-8") as f:
                json.dump({"x": x, "y": y}, f)
        except OSError:
            pass

    def _tk(self):
        import tkinter as tk
        try:
            if WIN:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        root = tk.Tk()
        self.root = root
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#101418")
        self.frame = tk.Frame(root, bg="#101418", padx=10, pady=6)
        self.frame.pack()
        self.celulas = {}
        arr = {}

        def ini_arraste(e):
            arr["x"], arr["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

        def arrastar(e):
            root.geometry(f"+{e.x_root - arr['x']}+{e.y_root - arr['y']}")

        def soltar(_e):
            self._salvar_pos(root.winfo_x(), root.winfo_y())

        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Abrir Monitor do PC", command=lambda: abrir_janela())
        menu.add_command(label="Configurar mini painel", command=lambda: abrir_janela("config"))
        menu.add_command(label="Voltar para o canto", command=lambda: (self._apagar_pos(), self._posicionar(cfg())))
        menu.add_separator()
        menu.add_command(label="Esconder mini painel", command=lambda: threading.Thread(
            target=lambda: api_post("api/config", {"mini_ativo": False}), daemon=True).start())
        self._binds = (ini_arraste, arrastar, soltar, lambda e: menu.tk_popup(e.x_root, e.y_root))
        for w in (root, self.frame):
            self._ligar(w)
        root.after(300, self._tick)
        root.mainloop()

    def _apagar_pos(self):
        try:
            os.remove(self.pos_arq)
        except OSError:
            pass

    def _ligar(self, w):
        a, b, c, d = self._binds
        w.bind("<ButtonPress-1>", a)
        w.bind("<B1-Motion>", b)
        w.bind("<ButtonRelease-1>", c)
        w.bind("<Button-3>", d)

    def _montar(self, c):
        import tkinter as tk
        for w in self.frame.winfo_children():
            w.destroy()
        self.celulas = {}
        tam = {"pequeno": (8, 12), "normal": (9, 15), "grande": (10, 20)}.get(c.get("mini_tamanho"), (9, 15))
        horizontal = c.get("mini_layout", "horizontal") == "horizontal"
        for i, k in enumerate([x for x in (c.get("mini_itens") or []) if x in ITENS_MINI]):
            rot = ITENS_MINI[k][0]
            cel = tk.Frame(self.frame, bg="#101418", padx=8 if horizontal else 0, pady=0 if horizontal else 2)
            if horizontal:
                cel.grid(row=0, column=i)
            else:
                cel.grid(row=i, column=0, sticky="we")
            l1 = tk.Label(cel, text=rot, fg="#8b97a4", bg="#101418", font=("Segoe UI", tam[0]))
            l2 = tk.Label(cel, text="—", fg="#e6ebf0", bg="#101418", font=("Segoe UI Semibold", tam[1]))
            if horizontal:
                l1.pack()
                l2.pack()
            else:
                l1.pack(side="left")
                l2.pack(side="right", padx=(16, 0))
            for w in (cel, l1, l2):
                self._ligar(w)
            self.celulas[k] = l2

    def _posicionar(self, c):
        r = self.root
        r.update_idletasks()
        p = self._pos()
        if p:
            r.geometry(f"+{p['x']}+{p['y']}")
            return
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        m = 16
        canto = c.get("mini_canto", "sup_dir")
        x = m if canto.endswith("esq") else sw - w - m
        y = m if canto.startswith("sup") else sh - h - 60
        r.geometry(f"+{x}+{y}")

    def _tick(self):
        try:
            c = cfg()
            ativo = bool(c.get("mini_ativo"))
            chave = (tuple(c.get("mini_itens") or []), c.get("mini_tamanho"), c.get("mini_layout"), c.get("mini_canto"))
            if ativo and chave != self.chave_layout:
                self._montar(c)
                self.chave_layout = chave
                self.root.attributes("-alpha", max(0.3, min(1.0, (c.get("mini_opacidade") or 85) / 100)))
                self._posicionar(c)
            if ativo and not self.visivel:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self.visivel = True
                self._posicionar(c)
            elif not ativo and self.visivel:
                self.root.withdraw()
                self.visivel = False
            if ativo:
                self.root.attributes("-alpha", max(0.3, min(1.0, (c.get("mini_opacidade") or 85) / 100)))
                r, j = self.dados["r"], self.dados["j"]
                for k, lbl in self.celulas.items():
                    _rot, f, un, lim = ITENS_MINI[k]
                    v = f(r, j)
                    if un == "rede" or un == "disco":
                        txt = _fmt_taxa(v)
                    elif un == "txt":
                        txt = v
                    elif v is None:
                        txt = "—"
                    else:
                        txt = f"{v:.0f}{un}"
                    cor = "#e6ebf0"
                    if lim and v is not None:
                        cor = "#ef5f5f" if v >= lim[1] else "#e8a33d" if v >= lim[0] else "#4cc38a"
                    if k == "fps" and v:
                        cor = "#ef5f5f" if v < 30 else "#e8a33d" if v < 55 else "#4cc38a"
                    lbl.configure(text=txt, fg=cor)
                self.root.attributes("-topmost", True)
        except Exception as e:  # noqa: BLE001
            print("mini painel:", e)
        self.root.after(500, self._tick)


# ------------------------------------------------------------------ ícone
def desenhar(mem, cpu, ok=True, jogo=False):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(16, 20, 24, 255))
    if not ok:
        d.line([18, 18, 46, 46], fill=(239, 95, 95, 255), width=7)
        d.line([46, 18, 18, 46], fill=(239, 95, 95, 255), width=7)
        return img

    def cor(v, alto, medio):
        if v >= alto:
            return (239, 95, 95, 255)
        if v >= medio:
            return (232, 163, 61, 255)
        return (76, 195, 138, 255)

    for x0, x1, v, c in ((10, 38, mem, cor(mem, 88, 75)), (44, 54, cpu, cor(cpu, 85, 60))):
        d.rounded_rectangle([x0, 10, x1, 54], radius=4, fill=(41, 49, 59, 255))
        h = max(3, int(44 * min(v, 100) / 100))
        d.rounded_rectangle([x0, 54 - h, x1, 54], radius=4, fill=c)
    if jogo:
        d.ellipse([44, 2, 62, 20], fill=(90, 169, 230, 255))
    return img


ESTADO = {"jogo": False, "planos": [], "fps": None}


def atualizar_icone():
    ultimo = None
    while True:
        try:
            e = api_get("api/v1/resumo")
        except Exception:
            e = None
        try:
            if ICONE is not None:
                if e:
                    chave = (int(e["mem_pct"] // 4), int(e["cpu"] // 10), ESTADO["jogo"])
                    if chave != ultimo:
                        ICONE.icon = desenhar(e["mem_pct"], e["cpu"], jogo=ESTADO["jogo"])
                        ultimo = chave
                    partes = [f"Memória {e['mem_pct']:.0f}%", f"CPU {e['cpu']:.0f}%"]
                    if e.get("temp_cpu"):
                        partes.append(f"{e['temp_cpu']:.0f}°C")
                    if ESTADO["jogo"] and ESTADO["fps"]:
                        partes.append(f"{ESTADO['fps']:.0f} FPS")
                    ICONE.title = ("Monitor do PC — " + ", ".join(partes))[:120]
                else:
                    if ultimo != "erro":
                        ICONE.icon = desenhar(0, 0, ok=False)
                        ultimo = "erro"
                    ICONE.title = "Monitor do PC — serviço não está respondendo"
        except Exception:
            pass
        time.sleep(3)


def atualizar_extras(forcar=False):
    """Estado do Modo Jogo e lista de planos de energia para o menu do ícone."""
    mudou = False
    try:
        j = api_get("api/jogo")
        if bool(j.get("ativo")) != ESTADO["jogo"]:
            mudou = True
        ESTADO["jogo"] = bool(j.get("ativo"))
        ESTADO["fps"] = (j.get("fps_principal") or {}).get("fps")
    except Exception:
        pass
    try:
        pl = api_get("api/energia/planos", timeout=15)
        if [(p["guid"], p["ativo"]) for p in pl] != [(p["guid"], p["ativo"]) for p in ESTADO["planos"]]:
            mudou = True
        ESTADO["planos"] = pl
    except Exception:
        pass
    if (mudou or forcar) and ICONE is not None:
        try:
            ICONE.update_menu()
        except Exception:
            pass


def loop_extras():
    while True:
        atualizar_extras()
        time.sleep(4)


def instancia_unica():
    if not WIN:
        return True
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MonitorPC-Bandeja")
    return ctypes.windll.kernel32.GetLastError() != 183


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
    for alvo in (loop_fila, loop_janelas, atualizar_icone, loop_extras):
        threading.Thread(target=alvo, daemon=True).start()
    MiniPainel().iniciar()
    import pystray

    def alternar_jogo(*_):
        def tarefa():
            try:
                r = api_post("api/acao", {"tipo": "jogo_desligar" if ESTADO["jogo"] else "jogo_ligar"}, timeout=40)
                ICONE.notify(r.get("msg", "")[:250], "Modo Jogo")
            except Exception as e:  # noqa: BLE001
                ICONE.notify(str(e)[:250], "Modo Jogo")
            atualizar_extras(True)
        threading.Thread(target=tarefa, daemon=True).start()

    def item_plano(p):
        def ativar(*_):
            def tarefa():
                try:
                    api_post("api/acao", {"tipo": "energia_plano", "guid": p["guid"]}, timeout=20)
                except Exception:
                    pass
                atualizar_extras(True)
            threading.Thread(target=tarefa, daemon=True).start()
        return pystray.MenuItem(p["nome"], ativar, radio=True,
                                checked=lambda *_: any(x["guid"] == p["guid"] and x["ativo"] for x in ESTADO["planos"]))

    def alternar_mini(*_):
        threading.Thread(target=lambda: (api_post("api/config", {"mini_ativo": not cfg(True).get("mini_ativo")}),
                                         cfg(True)), daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir Monitor do PC", lambda *_: abrir_janela(), default=True),
        pystray.MenuItem("Programas", lambda *_: abrir_janela("programas")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda *_: "Desligar Modo Jogo" if ESTADO["jogo"] else "Ligar Modo Jogo", alternar_jogo),
        pystray.MenuItem("Plano de energia", pystray.Menu(lambda: [item_plano(p) for p in ESTADO["planos"]] or
                                                          [pystray.MenuItem("carregando…", None, enabled=False)])),
        pystray.MenuItem("Mini painel", alternar_mini, checked=lambda *_: bool(_cfg["v"].get("mini_ativo"))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Otimizar", lambda *_: abrir_janela("otimizar")),
        pystray.MenuItem("Relatório", lambda *_: abrir_janela("relatorio")),
        pystray.MenuItem("Configurações", lambda *_: abrir_janela("config")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Fechar ícone (o monitoramento continua)", lambda *_: ICONE.stop()),
    )
    ICONE = pystray.Icon("monitor-pc", desenhar(0, 0), "Monitor do PC", menu)
    ICONE.run()


if __name__ == "__main__":
    main()
