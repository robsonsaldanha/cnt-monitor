"""Ícone do Monitor do PC perto do relógio.

Abre a janela do programa, mostra o mini painel flutuante e executa as ações que precisam
da sessão do usuário (mostrar janela de um programa, abrir pasta, fechar janela, notificações).

  MonitorPC.exe            -> abre a janela (e o ícone, se ainda não estiver aberto)
  MonitorPC.exe --bandeja  -> só o ícone (usado ao ligar o Windows)
  MonitorPC.exe --ferramenta NOME "arquivo"  -> enviar, editor, fixar, ocr, qr, hash, embelezar, imprimir,
                                               copiar_arquivo (menu do botão direito do Explorer)
"""
import os
import sys
import json
import time
import base64
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
            _cfg["v"] = api_get("api/config", com_token=True).get("valores", {})
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
        notificar(a.get("msg", ""), a.get("titulo", "Monitor do PC"))
        return True, "Notificação mostrada."
    if tipo.startswith("captura"):
        return executar_captura(tipo, a)
    if tipo == "wsl_desligar":
        r = subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=60, creationflags=0x08000000)
        return r.returncode == 0, "WSL desligado." if r.returncode == 0 else "Não consegui desligar o WSL."
    return False, "Ação desconhecida."


def loop_fila():
    while True:
        try:
            acoes = api_get("api/fila?espera=20", timeout=30, com_token=True).get("acoes", [])
            for a in acoes:
                dados = None
                try:
                    ok, msg, *resto = executar(a)
                    dados = resto[0] if resto else None
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, str(e)
                try:
                    api_post("api/fila/resultado", {"id": a.get("id"), "ok": ok, "msg": msg, "dados": dados})
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


# ------------------------------------------------------------------ captura de tela
CAP = None
AVISOS = None
EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def notificar_windows(msg, titulo="Monitor do PC"):
    if ICONE is not None:
        try:
            ICONE.notify(str(msg)[:250], str(titulo)[:60])
        except Exception:
            pass


def notificar(msg, titulo="Monitor do PC"):
    """Aviso próprio do Monitor no canto da tela (o do Windows só se o próprio falhar)."""
    if AVISOS is not None:
        AVISOS.mostrar(titulo, msg)
    else:
        notificar_windows(msg, titulo)


def esconder_janela():
    """Minimiza a janela do Monitor antes de capturar a pedido dela; devolve a função que a mostra de novo."""
    h = janela_existente() if WIN else None
    if not h or user32.IsIconic(h):
        return None
    user32.ShowWindow(h, 6)  # SW_MINIMIZE
    return lambda: user32.ShowWindow(h, 9)  # SW_RESTORE


def capturar_em_segundo_plano(modo, origem):
    if CAP is None:
        return False, "A captura de tela ainda está iniciando."
    if origem == "menu":
        def tarefa():
            time.sleep(0.3)  # espera o menu do ícone sumir
            CAP.executar(modo)
        threading.Thread(target=tarefa, daemon=True).start()
    else:
        threading.Thread(target=CAP.executar, args=(modo, origem), daemon=True).start()
    return True, "Capturando…"


def executar_captura(tipo, a):
    from monitorpc import captura
    if CAP is None:
        return False, "A captura de tela ainda está iniciando."
    caminho = a.get("caminho", "")
    if tipo == "captura":
        if a.get("modo") == "ffmpeg":
            threading.Thread(target=CAP.baixar_ffmpeg, daemon=True).start()
            return True, "Baixando o ffmpeg… (aviso perto do relógio quando terminar)"
        return capturar_em_segundo_plano(a.get("modo"), "ui")
    if tipo == "captura_editada":
        ok, d = CAP.editada(a.get("cid"), caminho, a.get("png", ""), a.get("nova"), a.get("copiar"))
        return ok, d["msg"], {"id": d["id"]}
    if tipo == "captura_escolher_pasta":
        return CAP.escolher_pasta()
    if tipo == "captura_abrir_pasta":
        return CAP.abrir_pasta()
    if tipo == "captura_tarefa":
        return tarefa_icone(a.get("nome"))
    if not os.path.isfile(caminho):
        return False, "O arquivo não existe mais (foi movido ou apagado)."
    if tipo == "captura_copiar":
        return CAP.copiar(caminho)
    if tipo == "captura_enviar":
        return CAP.enviar(a.get("cid"), caminho)
    if tipo == "captura_ferramenta":
        return CAP.ferramenta(a.get("nome"), caminho, a.get("cid"))
    if tipo == "captura_renomear":
        return CAP.renomear(a.get("cid"), caminho, a.get("novo", ""))
    if tipo == "captura_ler":
        ext = os.path.splitext(caminho)[1].lower()
        if ext not in EXT_IMAGEM:
            return False, "Só imagens abrem no editor."
        if os.path.getsize(caminho) > 45 * 2**20:
            return False, "Imagem grande demais para o editor (mais de 45 MB)."
        with open(caminho, "rb") as f:
            dados = base64.b64encode(f.read()).decode()
        api_post("api/capturas/conteudo", {"id": a.get("cid"), "ext": ext, "dados": dados}, timeout=60)
        return True, "ok"
    if tipo == "captura_apagar":
        if captura.para_lixeira(caminho):
            return True, "Arquivo enviado para a Lixeira e tirado do histórico."
        return False, "Tirei do histórico, mas não consegui mandar o arquivo para a Lixeira."
    return False, "Ação desconhecida."


def tarefa_icone(nome):
    """Ferramentas sem arquivo (pedidas pela janela ou pelo menu). As que abrem janela rodam numa thread."""
    if CAP is None:
        return False, "A captura de tela ainda está iniciando."
    if nome == "importar":
        return CAP.importar_pasta()
    if nome == "ia_chave":
        return CAP.cadastrar_chave_ia()
    acoes = {"barra": CAP.alternar_barra, "enviar_arquivo": CAP.enviar_arquivo, "enviar_area": CAP.enviar_area,
             "enviar_texto": CAP.enviar_texto, "fixar_area": CAP.fixar_area,
             "atalhos": lambda: CAP.executar("atalhos"), "pausar": lambda: CAP.executar("pausar")}
    if nome in acoes:
        alvo = acoes[nome]
    elif nome and nome.startswith("escolher_"):
        alvo = lambda: CAP.escolher_e(nome[9:])   # noqa: E731
    else:
        return False, "Tarefa desconhecida."
    threading.Thread(target=alvo, daemon=True).start()
    return True, "ok"


def loop_captura():
    n, gravando = 0, None
    while True:
        try:
            CAP.sincronizar(cfg())
            if n % 5 == 0:
                CAP.reportar()  # o serviço pode ter reiniciado e esquecido o estado
            g = CAP.gravador.tipo if CAP.gravador.gravando else None
            if g != gravando and ICONE is not None:
                gravando = g
                ICONE.update_menu()  # "Gravar" vira "Parar gravação"
        except Exception as e:  # noqa: BLE001
            print("captura:", e)
        n += 1
        time.sleep(1 if CAP.gravador.gravando else 3)


def fechar_icone():
    """Fecha o ícone: termina uma gravação em andamento e devolve a tecla Print Screen ao Windows."""
    if CAP is not None:
        try:
            CAP.executar("parar")
            from monitorpc import captura
            captura.restaurar_printscreen(PASTA_USUARIO)
        except Exception as e:  # noqa: BLE001
            print("fechar:", e)
    ICONE.stop()


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


def monitores():
    """Retângulos de trabalho (sem a barra de tarefas) de cada monitor: principal primeiro, depois da esquerda para a direita."""
    if not WIN:
        return [(0, 0, 1920, 1040, 0, 0, 1920, 1080)]
    out = []

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p)

    def cb(hmon, _hdc, _rc, _d):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            w, m = mi.rcWork, mi.rcMonitor
            out.append((bool(mi.dwFlags & 1), w.l, w.t, w.r, w.b))
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    except Exception:
        pass
    out.sort(key=lambda m: (not m[0], m[1], m[2]))
    return [m[1:] for m in out] or [(0, 0, 1920, 1040)]


class MiniPainel:
    """Janelinha sempre por cima. Não recebe cliques (o mouse passa através dela) — tudo se ajusta nas Configurações."""

    def __init__(self):
        self.dados = {"r": {}, "j": {}}
        self.root = None
        self.visivel = False
        self.chave_layout = None
        self.chave_pos = None

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
        self.fundo = "#101418"
        root.configure(bg=self.fundo)
        self.frame = tk.Frame(root, bg=self.fundo)
        self.frame.pack()
        self.celulas = {}
        root.after(300, self._tick)
        root.mainloop()

    def _atravessar(self):
        """Deixa o mouse passar através da janela (não é clicável nem rouba o foco do jogo)."""
        if not WIN:
            return
        try:
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE = -20
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000   # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception as e:  # noqa: BLE001
            print("mini painel (atravessar):", e)

    def _montar(self, c):
        import tkinter as tk
        for w in self.frame.winfo_children():
            w.destroy()
        self.celulas = {}
        esc = max(50, min(300, int(c.get("mini_escala") or 100))) / 100
        f1, f2 = max(6, round(9 * esc)), max(8, round(15 * esc))
        pad = round(8 * esc)
        self.fundo = "#101418" if c.get("mini_fundo", True) else "#010101"
        self.root.configure(bg=self.fundo)
        self.frame.configure(bg=self.fundo, padx=round(10 * esc), pady=round(6 * esc))
        if WIN:
            try:
                self.root.attributes("-transparentcolor", "" if c.get("mini_fundo", True) else "#010101")
            except Exception:
                pass
        horizontal = c.get("mini_layout", "horizontal") == "horizontal"
        for i, k in enumerate([x for x in (c.get("mini_itens") or []) if x in ITENS_MINI]):
            cel = tk.Frame(self.frame, bg=self.fundo, padx=pad if horizontal else 0, pady=0 if horizontal else round(2 * esc))
            if horizontal:
                cel.grid(row=0, column=i)
            else:
                cel.grid(row=i, column=0, sticky="we")
            l1 = tk.Label(cel, text=ITENS_MINI[k][0], fg="#9aa6b2", bg=self.fundo, font=("Segoe UI", f1))
            l2 = tk.Label(cel, text="—", fg="#e6ebf0", bg=self.fundo, font=("Segoe UI Semibold", f2))
            if horizontal:
                l1.pack()
                l2.pack()
            else:
                l1.pack(side="left")
                l2.pack(side="right", padx=(round(16 * esc), 0))
            self.celulas[k] = l2

    def _posicionar(self, c):
        r = self.root
        r.update_idletasks()
        mons = monitores()
        n = max(1, int(c.get("mini_monitor") or 1))
        l, t, rt, b = mons[min(n, len(mons)) - 1]
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        mx, my = int(c.get("mini_margem_x") or 0), int(c.get("mini_margem_y") or 0)
        canto = c.get("mini_canto", "sup_dir")
        if canto.endswith("centro"):
            x = l + (rt - l - w) // 2 + mx
        elif canto.endswith("esq"):
            x = l + mx
        else:
            x = rt - w - mx
        y = t + my if canto.startswith("sup") else b - h - my
        x, y = max(l, min(x, rt - w)), max(t, min(y, b - h))
        r.geometry(f"+{x}+{y}")

    def _tick(self):
        try:
            c = cfg()
            ativo = bool(c.get("mini_ativo"))
            chave = (tuple(c.get("mini_itens") or []), c.get("mini_escala"), c.get("mini_layout"), c.get("mini_fundo"))
            pos = (c.get("mini_canto"), c.get("mini_monitor"), c.get("mini_margem_x"), c.get("mini_margem_y"), chave)
            if ativo and chave != self.chave_layout:
                self._montar(c)
                self.chave_layout = chave
                self.chave_pos = None
            if ativo and not self.visivel:
                self.root.deiconify()
                self.visivel = True
                self.chave_pos = None
                self._atravessar()
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
                # a largura muda com os números: reposiciona a cada 2 s e sempre que a configuração mudar
                self._n = getattr(self, "_n", 0) + 1
                if pos != self.chave_pos or self._n % 4 == 0:
                    self.chave_pos = pos
                    self._posicionar(c)
                self.root.attributes("-topmost", True)
                self._atravessar()
        except Exception as e:  # noqa: BLE001
            print("mini painel:", e)
        self.root.after(500, self._tick)


class FaixaAjuda:
    """Faixa sempre por cima enquanto um técnico está vendo e controlando este computador, com o botão Encerrar.
    Não dá para esconder: só some quando a sessão acaba."""
    COR = "#c62828"

    def __init__(self):
        self.ativa = False
        self.root = None
        self.visivel = False

    def iniciar(self):
        threading.Thread(target=self._buscar, daemon=True).start()
        threading.Thread(target=self._tk, daemon=True).start()

    def _buscar(self):
        while True:
            try:
                d = api_post("api/acao", {"tipo": "ajuda_estado"}).get("dados") or {}
                self.ativa = d.get("estado") == "ativa"
            except Exception:
                pass        # serviço fora do ar por um instante: mantém o que estava
            time.sleep(1)

    def _encerrar(self):
        try:
            api_post("api/acao", {"tipo": "ajuda_encerrar"}, timeout=20)
            self.ativa = False
        except Exception as e:  # noqa: BLE001
            notificar(f"Não consegui encerrar o acesso remoto: {e}")

    def _tk(self):
        import tkinter as tk
        root = tk.Tk()
        self.root = root
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=self.COR)
        tk.Label(root, text="Acesso remoto ativo: um técnico está vendo e controlando este computador", fg="#ffffff",
                 bg=self.COR, font=("Segoe UI Semibold", 10), padx=14, pady=7).pack(side="left")
        b = tk.Label(root, text="Encerrar", fg=self.COR, bg="#ffffff", font=("Segoe UI", 9, "bold"), padx=12, pady=3,
                     cursor="hand2")
        b.pack(side="left", padx=(0, 10))
        b.bind("<Button-1>", lambda _e: threading.Thread(target=self._encerrar, daemon=True).start())
        root.after(500, self._tick)
        root.mainloop()

    def _tick(self):
        try:
            if self.ativa:
                r = self.root
                r.update_idletasks()
                l, t, rt, _b = monitores()[0]
                r.geometry(f"+{l + (rt - l - r.winfo_reqwidth()) // 2}+{t}")
                if not self.visivel:
                    r.deiconify()
                    self.visivel = True
                r.attributes("-topmost", True)
                r.lift()
            elif self.visivel:
                self.root.withdraw()
                self.visivel = False
        except Exception as e:  # noqa: BLE001
            print("faixa do acesso remoto:", e)
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


ESTADO = {"jogo": False, "planos": [], "fps": None, "recentes": [], "fluxos": []}


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
    try:   # "Itens recentes" e "Fluxos de trabalho" do menu
        rec = api_get("api/capturas?limite=10", com_token=True).get("itens", [])
        rec = [{k: x[k] for k in ("id", "caminho", "url", "tipo")} for x in rec]
        fl = [(f["id"], f["nome"], f["atalho"]) for f in cfg().get("captura_fluxos") or []]
        if rec != ESTADO["recentes"] or fl != ESTADO["fluxos"]:
            mudou = True
        ESTADO["recentes"], ESTADO["fluxos"] = rec, fl
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


FERRAMENTAS_CLI = ("enviar", "editor", "fixar", "ocr", "qr", "hash", "embelezar", "imprimir", "copiar_arquivo")


def linha_de_comando():
    """`--ferramenta NOME arquivo` (menu do botão direito do Explorer). Devolve (nome, caminho) ou None."""
    if "--ferramenta" not in sys.argv:
        return None
    i = sys.argv.index("--ferramenta")
    if len(sys.argv) < i + 3 or sys.argv[i + 1] not in FERRAMENTAS_CLI:
        return None
    return sys.argv[i + 1], os.path.abspath(sys.argv[i + 2])


def rodar_ferramenta(capturador, nome, caminho):
    """Roda a ferramenta neste processo e espera as janelas e os avisos que ela abriu."""
    from monitorpc import captura, telas
    captura.dpi_por_monitor()
    cap = capturador.Capturador(api_post, cfg, notificar, abrir_janela, lambda: None, PASTA_USUARIO, AVISOS)
    if nome == "fixar":
        cap.fixar_arquivo(caminho)
    elif nome == "hash":
        from monitorpc import efeitos
        telas.hash_janela(os.path.basename(caminho), efeitos.hash_arquivo(caminho))
    else:
        ok, msg = cap.ferramenta(nome, caminho)
        if not ok:
            notificar(msg, "Monitor do PC")
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name != "atalhos":
            t.join()


def main():
    global ICONE, CAP, AVISOS
    _log()
    from monitorpc import aviso
    AVISOS = aviso.Avisos(cfg, notificar_windows) if WIN else None
    cli = linha_de_comando()
    if cli:
        from monitorpc import capturador
        if cli[0] != "editor":
            return rodar_ferramenta(capturador, *cli)
        rodar_ferramenta(capturador, *cli)   # registra no histórico e abre o editor; o ícone (aberto) lê o arquivo
        sys.argv.append("--bandeja")
    so_icone = "--bandeja" in sys.argv
    if not instancia_unica():
        if not so_icone:
            abrir_janela()
        return
    from monitorpc import captura, capturador
    captura.dpi_por_monitor()  # antes de criar qualquer janela
    if not so_icone:
        threading.Timer(0.5, abrir_janela).start()
    if WIN:
        CAP = capturador.Capturador(api_post, cfg, notificar, abrir_janela, esconder_janela, PASTA_USUARIO, AVISOS)
        threading.Thread(target=loop_captura, daemon=True).start()
    for alvo in (loop_fila, loop_janelas, atualizar_icone, loop_extras):
        threading.Thread(target=alvo, daemon=True).start()
    MiniPainel().iniciar()
    FaixaAjuda().iniciar()
    import pystray

    def alternar_jogo(*_):
        def tarefa():
            try:
                r = api_post("api/acao", {"tipo": "jogo_desligar" if ESTADO["jogo"] else "jogo_ligar"}, timeout=40)
                notificar(r.get("msg", ""), "Modo Jogo")
            except Exception as e:  # noqa: BLE001
                notificar(str(e), "Modo Jogo")
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

    def item_cap(rotulo, modo):
        return pystray.MenuItem(rotulo, lambda *_: capturar_em_segundo_plano(modo, "menu"))

    def item_tarefa_rodar(nome):
        threading.Thread(target=tarefa_icone, args=(nome,), daemon=True).start()

    def item_tarefa(rotulo, nome):
        return pystray.MenuItem(rotulo, lambda *_: item_tarefa_rodar(nome))

    def gravando(tipo=None):
        return bool(CAP and CAP.gravador.gravando and (tipo is None or CAP.gravador.tipo == tipo))

    def salvar_cfg(valores):
        threading.Thread(target=lambda: (api_post("api/config", valores), cfg(True)), daemon=True).start()

    def opcao(rotulo, chave, padrao=False):
        """Liga/desliga uma opção de captura (a mesma da janela do Monitor)."""
        return pystray.MenuItem(rotulo, lambda *_: salvar_cfg({chave: not cfg(True).get(chave, padrao)}),
                                checked=lambda *_: bool(_cfg["v"].get(chave, padrao)))

    def escolha(rotulo, chave, valor, padrao):
        return pystray.MenuItem(rotulo, lambda *_: salvar_cfg({chave: valor}), radio=True,
                                checked=lambda *_: _cfg["v"].get(chave, padrao) == valor)

    def pasta_capturas(*_):
        def tarefa():
            ok, msg = CAP.escolher_pasta()
            if ok:
                notificar(msg, "Captura de tela")
        if CAP:
            threading.Thread(target=tarefa, daemon=True).start()

    def item_recente(x):
        nome = os.path.basename(x["caminho"]) + ("  🔗" if x["url"] else "")

        def abrir(*_):
            if x["url"]:
                from monitorpc import captura
                captura.copiar_texto(x["url"])
                notificar(f"Link copiado: {x['url']}", "Itens recentes")
            elif os.path.isfile(x["caminho"]):
                os.startfile(x["caminho"])
        return pystray.MenuItem(nome[:60], abrir)

    def sobre(*_):
        notificar(f"Monitor do PC {config.VERSAO} — monitor do computador, captura de tela e envio. "
                  "github.com/robsonsaldanha/monitor-pc", "Sobre")

    from monitorpc import envio
    menu_capturar = pystray.Menu(
        item_cap("Região", "regiao"), item_cap("Várias regiões", "regioes"), item_cap("Janela ativa", "janela"),
        item_cap("Monitor do mouse", "monitor"), item_cap("Tela inteira", "tela"),
        item_cap("Repetir a última região", "ultima"), item_cap("Região com atraso", "atraso"),
        item_cap("Com rolagem", "rolagem"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda *_: "Parar gravação de vídeo" if gravando("video") else "Gravar vídeo (MP4)",
                         lambda *_: capturar_em_segundo_plano("video", "menu")),
        pystray.MenuItem(lambda *_: "Parar gravação do GIF" if gravando("gif") else "Gravar GIF",
                         lambda *_: capturar_em_segundo_plano("gif", "menu")),
        pystray.MenuItem(lambda *_: "Continuar a gravação" if CAP and CAP.gravador.pausado else "Pausar a gravação",
                         lambda *_: item_tarefa_rodar("pausar"), visible=lambda *_: gravando()),
    )

    menu_upload = pystray.Menu(
        item_tarefa("Enviar arquivo…", "enviar_arquivo"), item_tarefa("Enviar da área de transferência", "enviar_area"),
        item_tarefa("Enviar texto…", "enviar_texto"),
        pystray.Menu.SEPARATOR,
        item_tarefa("Importar pasta para o histórico…", "importar"),
    )
    menu_fluxos = pystray.Menu(lambda: [
        *[pystray.MenuItem(f"{n}  ({a})" if a else n, lambda *_, i=i: capturar_em_segundo_plano(f"fluxo:{i}", "menu"))
          for i, n, a in ESTADO["fluxos"]],
        *([pystray.Menu.SEPARATOR] if ESTADO["fluxos"] else []),
        pystray.MenuItem("Criar e editar fluxos…", lambda *_: abrir_janela("capturas/opcoes"))])
    menu_ferramentas = pystray.Menu(
        item_cap("Seletor de cor", "cor"), item_cap("Régua", "regua"), item_cap("Copiar texto da tela (OCR)", "ocr"),
        item_cap("Ler código QR da tela", "qr"),
        pystray.Menu.SEPARATOR,
        item_cap("Fixar uma região na tela", "fixar"), item_tarefa("Fixar da área de transferência", "fixar_area"),
        item_tarefa("Fixar imagem de um arquivo…", "escolher_fixar"),
        pystray.Menu.SEPARATOR,
        item_tarefa("Embelezar imagem…", "escolher_embelezar"), item_tarefa("Abrir imagem no editor…", "escolher_editor"),
        item_tarefa("Ler código QR de uma imagem…", "escolher_qr"), item_tarefa("Copiar texto de uma imagem (OCR)…", "escolher_ocr"),
        item_tarefa("Verificador de hash…", "escolher_hash"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Barra de ferramentas de ações", lambda *_: item_tarefa_rodar("barra"),
                         checked=lambda *_: bool(CAP and CAP.barra_fim and not CAP.barra_fim.is_set())),
    )
    menu_pos_captura = pystray.Menu(
        opcao("Mostrar menu de tarefas rápidas", "captura_tarefas_rapidas"),
        opcao("Mostrar janela \"Pós-captura\"", "captura_janela_pos"),
        opcao("Embelezar imagem", "captura_embelezar"), opcao("Efeitos de imagem / marca d'água", "captura_efeitos"),
        opcao("Abrir no editor", "captura_editor"), opcao("Abrir no editor de imagens do Windows", "captura_editor_fotos"),
        opcao("Copiar imagem para a área de transferência", "captura_copiar", True), opcao("Fixar na tela", "captura_fixar"),
        opcao("Imprimir imagem", "captura_imprimir"),
        opcao("Salvar imagem", "captura_salvar", True), opcao("Salvar imagem como…", "captura_perguntar"),
        opcao("Salvar miniatura", "captura_miniatura"), opcao("Realizar ações", "captura_rodar_acoes"),
        opcao("Copiar arquivo para a área de transferência", "captura_copiar_arquivo"),
        opcao("Copiar caminho do arquivo", "captura_copiar_caminho"),
        opcao("Copiar caminho da pasta", "captura_copiar_pasta"),
        opcao("Mostrar arquivo no Explorer", "captura_mostrar_pasta"),
        opcao("Analisar imagem com IA", "captura_ia"), opcao("Ler código QR", "captura_qr"),
        opcao("Reconhecer texto (OCR)", "captura_ocr_auto"), opcao("Mostrar janela \"Pré-envio\"", "captura_pre_envio"),
        opcao("Enviar imagem para hospedagem", "captura_enviar"), opcao("Apagar o arquivo local depois de enviar", "captura_apagar_local"),
        pystray.Menu.SEPARATOR,
        opcao("Mostrar aviso", "captura_notificar", True),
        opcao("Ferramentas de anotação na seleção de região", "captura_anotar", True),
    )
    menu_pos_envio = pystray.Menu(
        opcao("Mostrar janela \"Pós-envio\"", "captura_envio_janela"), opcao("Encurtar URL", "captura_envio_encurtar"),
        pystray.MenuItem("Compartilhar URL", pystray.Menu(
            escolha("Não compartilhar", "captura_envio_compartilhar", "", ""),
            *[escolha(t, "captura_envio_compartilhar", k, "") for k, (t, _u) in envio.COMPARTILHAR.items()])),
        opcao("Copiar URL para a área de transferência", "captura_envio_copiar", True),
        pystray.MenuItem("Formato do link", pystray.Menu(
            *[escolha(t, "captura_envio_formato", k, "url") for k, t in envio.FORMATOS.items()])),
        opcao("Abrir URL", "captura_envio_abrir"), opcao("Mostrar janela de código QR", "captura_envio_qr"),
        opcao("Mostrar aviso", "captura_envio_notificar", True),
    )
    menu_recentes = pystray.Menu(lambda: [item_recente(x) for x in ESTADO["recentes"]] or
                                 [pystray.MenuItem("nenhuma captura ainda", None, enabled=False)])

    menu = pystray.Menu(
        pystray.MenuItem("Capturar", menu_capturar, visible=WIN),
        pystray.MenuItem("Upload", menu_upload, visible=WIN),
        pystray.MenuItem("Fluxos de trabalho", menu_fluxos, visible=WIN),
        pystray.MenuItem("Ferramentas", menu_ferramentas, visible=WIN),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Tarefas após a captura", menu_pos_captura, visible=WIN),
        pystray.MenuItem("Tarefas após o envio", menu_pos_envio, visible=WIN),
        pystray.MenuItem("Destinos", pystray.Menu(
            *[escolha(t, "captura_destino", k, "catbox") for k, t in envio.DESTINOS.items()]), visible=WIN),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Configurações…", lambda *_: abrir_janela("config")),
        pystray.MenuItem("Configuração de tarefas…", lambda *_: abrir_janela("capturas/opcoes")),
        pystray.MenuItem("Teclas de atalho…", lambda *_: abrir_janela("capturas/opcoes")),
        pystray.MenuItem("Desativar atalhos", lambda *_: item_tarefa_rodar("atalhos"),
                         checked=lambda *_: not _cfg["v"].get("captura_ativa", True)),
        pystray.MenuItem("Destinos e uploader personalizado…", lambda *_: abrir_janela("capturas/opcoes")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Pasta das capturas", lambda *_: CAP and threading.Thread(target=CAP.abrir_pasta, daemon=True).start()),
        pystray.MenuItem("Escolher a pasta das capturas…", pasta_capturas),
        pystray.MenuItem("Histórico", lambda *_: abrir_janela("capturas")),
        pystray.MenuItem("Itens recentes", menu_recentes),
        pystray.MenuItem("Estatísticas…", lambda *_: abrir_janela("capturas/estatisticas")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir Monitor do PC", lambda *_: abrir_janela(), default=True),
        pystray.MenuItem("Programas", lambda *_: abrir_janela("programas")),
        pystray.MenuItem(lambda *_: "Desligar Modo Jogo" if ESTADO["jogo"] else "Ligar Modo Jogo", alternar_jogo),
        pystray.MenuItem("Plano de energia", pystray.Menu(lambda: [item_plano(p) for p in ESTADO["planos"]] or
                                                          [pystray.MenuItem("carregando…", None, enabled=False)])),
        pystray.MenuItem("Mini painel", alternar_mini, checked=lambda *_: bool(_cfg["v"].get("mini_ativo"))),
        pystray.MenuItem("Otimizar", lambda *_: abrir_janela("otimizar")),
        pystray.MenuItem("Relatório", lambda *_: abrir_janela("relatorio")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Depuração", pystray.Menu(
            pystray.MenuItem("Abrir o registro do ícone (bandeja.log)",
                             lambda *_: os.path.isfile(os.path.join(PASTA_USUARIO, "bandeja.log")) and
                             os.startfile(os.path.join(PASTA_USUARIO, "bandeja.log"))),
            pystray.MenuItem("Abrir a pasta de dados do ícone", lambda *_: os.startfile(PASTA_USUARIO)))),
        pystray.MenuItem("Sobre…", sobre),
        pystray.MenuItem("Fechar ícone (o monitoramento continua)", lambda *_: fechar_icone()),
    )
    ICONE = pystray.Icon("monitor-pc", desenhar(0, 0), "Monitor do PC", menu)
    ICONE.run()


if __name__ == "__main__":
    main()
