"""Lançador rápido (no estilo do PowerToys Run): um atalho abre uma caixa de busca no meio da tela.

Acha aplicativos do Iniciar (inclusive os da Microsoft Store), janelas abertas, telas do CNT Monitor e páginas das
Configurações do Windows. Também faz contas, pesquisa na web (?texto) e roda comandos (>comando). Os itens mais
usados sobem na lista. Roda no ícone da bandeja; no Linux também por `cnt-monitor --lancador` (o Wayland não deixa
um programa registrar atalho global, então o atalho fica nas Configurações do sistema).
"""
import os
import re
import ast
import json
import time
import queue
import shlex
import shutil
import operator
import threading
import subprocess
import unicodedata

WIN = os.name == "nt"
SEM_JANELA = 0x08000000
MAX_ITENS = 8

PAGINAS = [("geral", "Visão geral"), ("programas", "Programas"), ("processos", "Processos"), ("rede", "Rede e IPs"),
           ("hardware", "Temperatura e hardware"), ("ventoinhas", "Ventoinhas"), ("capturas", "Captura de tela"),
           ("jogo", "Modo Jogo"), ("otimizar", "Otimizar"), ("regras", "Regras por programa"),
           ("inicializacao", "Inicialização"), ("energia", "Energia"), ("servicos", "Serviços"),
           ("historico", "Histórico"), ("alertas", "Alertas"), ("relatorio", "Relatório"), ("vps", "Servidores (VPS)"),
           ("ajuda", "Acesso remoto"), ("personalizar", "Personalizar"), ("config", "Configurações do CNT Monitor")]

CONFIG_WINDOWS = [
    ("Bluetooth e dispositivos", "ms-settings:bluetooth"), ("Wi-Fi", "ms-settings:network-wifi"),
    ("Rede e internet", "ms-settings:network-status"), ("VPN", "ms-settings:network-vpn"),
    ("Proxy", "ms-settings:network-proxy"), ("Som", "ms-settings:sound"), ("Tela (vídeo, resolução)", "ms-settings:display"),
    ("Windows Update", "ms-settings:windowsupdate"), ("Aplicativos instalados", "ms-settings:appsfeatures"),
    ("Aplicativos padrão", "ms-settings:defaultapps"), ("Energia e bateria", "ms-settings:powersleep"),
    ("Barra de tarefas", "ms-settings:taskbar"), ("Personalização", "ms-settings:personalization"),
    ("Papel de parede (tela de fundo)", "ms-settings:personalization-background"), ("Cores", "ms-settings:colors"),
    ("Mouse", "ms-settings:mousetouchpad"), ("Teclado e digitação", "ms-settings:typing"),
    ("Data e hora", "ms-settings:dateandtime"), ("Notificações", "ms-settings:notifications"),
    ("Privacidade e segurança", "ms-settings:privacy"), ("Armazenamento", "ms-settings:storagesense"),
    ("Impressoras e scanners", "ms-settings:printers"), ("Contas", "ms-settings:yourinfo"),
    ("Sobre este PC", "ms-settings:about"), ("Recuperação", "ms-settings:recovery"),
    ("Segurança do Windows", "windowsdefender:"), ("Gerenciador de dispositivos", "devmgmt.msc"),
    ("Painel de controle", "control"), ("Gerenciador de tarefas", "taskmgr"),
]


# ---------------------------------------------------------------- busca (funções puras)
def normalizar(t):
    t = unicodedata.normalize("NFKD", str(t or ""))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def pontuar(nome, consulta):
    """0 (não combina) a 100 (igual). Começo do nome > começo de palavra > iniciais > meio > letras em ordem."""
    n, q = normalizar(nome), normalizar(consulta).strip()
    if not q:
        return 0
    if n == q:
        return 100
    if n.startswith(q):
        return 90
    palavras = [p for p in re.split(r"[\s\-_.()/\\›:]+", n) if p]
    if any(p.startswith(q) for p in palavras):
        return 75
    if len(q) >= 2 and "".join(p[0] for p in palavras).startswith(q):
        return 65
    if q in n:
        return 50
    i = 0
    for ch in n:
        if i < len(q) and ch == q[i]:
            i += 1
    return 20 if i == len(q) and len(q) >= 3 else 0


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}


def calcular(expr):
    """Conta segura (só números e + - * / // % ^ e parênteses). None se não for uma conta."""
    t = str(expr or "").strip().lstrip("=").replace(",", ".").replace("^", "**").replace("×", "*").replace("÷", "/")
    if not t or len(t) > 200 or not re.fullmatch(r"[\d\s.+\-*/%()]+", t):
        return None
    try:
        arvore = ast.parse(t, mode="eval")
    except SyntaxError:
        return None

    def av(n):
        if isinstance(n, ast.Expression):
            return av(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = av(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = av(n.left), av(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 1000 or abs(a) > 1e6):
                raise ValueError("grande demais")
            return _OPS[type(n.op)](a, b)
        raise ValueError("não é conta")
    try:
        v = av(arvore)
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
    if isinstance(v, float) and (v != v or abs(v) == float("inf")):
        return None
    return v


def formatar_numero(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)
    return (str(v) if isinstance(v, int) else f"{v:.10g}").replace(".", ",")


def chave(item):
    return f"{item['tipo']}:{item['alvo']}"


def buscar(itens, consulta, usos=None, limite=MAX_ITENS):
    """Itens que combinam com a consulta, os mais usados primeiro em caso de empate. Prefixos: ? web, > comando."""
    usos = usos or {}
    q = (consulta or "").strip()
    if not q:
        return []
    if q.startswith("?"):
        t = q[1:].strip()
        return [{"tipo": "web", "nome": f"Pesquisar na web: {t}", "sub": "navegador", "alvo": t}] if t else []
    if q.startswith(">"):
        t = q[1:].strip()
        return [{"tipo": "cmd", "nome": f"Rodar: {t}", "sub": "comando", "alvo": t}] if t else []
    out = []
    v = calcular(q)
    if v is not None and (q.startswith("=") or re.search(r"\d\s*[-+*/%^×÷]\s*[\d(]", q)):
        txt = formatar_numero(v)
        out.append((1000, {"tipo": "calc", "nome": f"= {txt}", "sub": "Enter copia o resultado", "alvo": txt}))
    for it in itens:
        p = pontuar(it["nome"], q)
        if p:
            out.append((p + min(15, usos.get(chave(it), 0) * 3), it))
    out.sort(key=lambda x: (-x[0], x[1]["nome"].lower()))
    vistos, res = set(), []
    for _p, it in out:
        if chave(it) in vistos:
            continue
        vistos.add(chave(it))
        res.append(it)
        if len(res) >= limite:
            break
    if not res:
        res.append({"tipo": "web", "nome": f"Pesquisar na web: {q}", "sub": "navegador", "alvo": q})
    return res


# ---------------------------------------------------------------- fontes
def apps_windows():
    cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
           "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                           timeout=40, creationflags=SEM_JANELA)
        d = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if isinstance(d, dict):
        d = [d]
    return [{"tipo": "app", "nome": x["Name"], "sub": "aplicativo", "alvo": x["AppID"]}
            for x in d if isinstance(x, dict) and x.get("Name") and x.get("AppID")]


PASTAS_DESKTOP = ("/usr/share/applications", "/usr/local/share/applications", "~/.local/share/applications",
                  "/var/lib/flatpak/exports/share/applications", "~/.local/share/flatpak/exports/share/applications",
                  "/var/lib/snapd/desktop/applications")


def apps_linux():
    from .linux.inicializacao import ler_desktop
    out, ids = [], set()
    for p in PASTAS_DESKTOP:
        p = os.path.expanduser(p)
        try:
            nomes = sorted(os.listdir(p))
        except OSError:
            continue
        for n in nomes:
            if not n.endswith(".desktop") or n in ids:
                continue
            try:
                with open(os.path.join(p, n), encoding="utf-8", errors="replace") as f:
                    d = ler_desktop(f.read())
            except OSError:
                continue
            if d.get("Type", "Application") != "Application" or d.get("NoDisplay", "").lower() == "true" \
                    or d.get("Hidden", "").lower() == "true":
                continue
            nome = d.get("Name[pt_BR]") or d.get("Name[pt]") or d.get("Name")
            if nome and d.get("Exec"):
                ids.add(n)
                out.append({"tipo": "app", "nome": nome, "sub": "aplicativo", "alvo": os.path.join(p, n)})
    return out


def itens_fixos():
    out = [{"tipo": "pagina", "nome": f"CNT Monitor › {t}", "sub": "tela do CNT Monitor", "alvo": a} for a, t in PAGINAS]
    if WIN:
        out += [{"tipo": "config", "nome": f"Configurações › {t}", "sub": "Windows", "alvo": a} for t, a in CONFIG_WINDOWS]
    return out


def _terminal(comando):
    for t, args in (("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]),
                    ("mate-terminal", ["-x"]), ("x-terminal-emulator", ["-e"]), ("xterm", ["-e"])):
        if shutil.which(t):
            return [t, *args, "sh", "-c", comando + '; exec "${SHELL:-sh}"']
    return None


def executar(item, abrir_janela=None, trazer_para_frente=None):
    """Abre o item escolhido. Devolve (ok, mensagem)."""
    import webbrowser
    tipo, alvo = item["tipo"], item["alvo"]
    try:
        if tipo == "app" and WIN:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + alvo], close_fds=True)
        elif tipo == "app":
            if shutil.which("gio"):
                subprocess.Popen(["gio", "launch", alvo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            else:
                from .linux.inicializacao import ler_desktop
                with open(alvo, encoding="utf-8", errors="replace") as f:
                    ex = ler_desktop(f.read()).get("Exec", "")
                args = [a for a in shlex.split(ex) if not re.fullmatch(r"%[a-zA-Z]", a)]
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        elif tipo == "janela" and trazer_para_frente:
            trazer_para_frente(alvo)
        elif tipo == "pagina" and abrir_janela:
            abrir_janela(alvo)
        elif tipo == "config":
            os.startfile(alvo)
        elif tipo == "calc":
            from .captura import copiar_texto
            copiar_texto(alvo)
            return True, f"{alvo} copiado."
        elif tipo == "web":
            from urllib.parse import quote
            webbrowser.open("https://www.google.com/search?q=" + quote(alvo))
        elif tipo == "cmd":
            if WIN:
                subprocess.Popen(["cmd.exe", "/k", alvo], creationflags=0x00000010)   # CREATE_NEW_CONSOLE
            else:
                args = _terminal(alvo)
                if not args:
                    return False, "Não achei um terminal instalado."
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            return False, "Item desconhecido."
    except (OSError, ValueError) as e:
        return False, str(e)
    return True, "ok"


# ---------------------------------------------------------------- janela
class Lancador:
    """Caixa de busca numa thread Tk própria (criada uma vez e só escondida/mostrada depois)."""

    COR_FUNDO, COR_TEXTO, COR_SUB, COR_SEL = "#1f2430", "#f2f4f8", "#9aa3b5", "#2f6fed"

    def __init__(self, pasta_usuario, abrir_janela=None, listar_janelas=None, trazer_para_frente=None, monitores=None,
                 notificar=None):
        self.arq_usos = os.path.join(pasta_usuario, "lancador.json")
        self.abrir_janela, self.listar_janelas = abrir_janela, listar_janelas
        self.trazer_para_frente, self.monitores = trazer_para_frente, monitores
        self.notificar = notificar or (lambda *a: None)
        self.pedidos = queue.Queue()
        self.thread = None
        self.apps, self.apps_ts, self._carregando = [], 0, False
        self.resultados = []
        self.sair_ao_esconder = False
        try:
            with open(self.arq_usos, encoding="utf-8") as f:
                self.usos = json.load(f)
        except (OSError, ValueError):
            self.usos = {}

    def carregar_apps(self, esperar=False):
        if self._carregando or time.time() - self.apps_ts < 600:
            return

        def tarefa():
            self._carregando = True
            try:
                self.apps = apps_windows() if WIN else apps_linux()
                self.apps_ts = time.time()
            finally:
                self._carregando = False
        t = threading.Thread(target=tarefa, daemon=True)
        t.start()
        if esperar:
            t.join(45)

    def mostrar(self):
        self.carregar_apps()
        if self.thread is None:
            self.thread = threading.Thread(target=self._tk, name="lancador", daemon=True)
            self.thread.start()
        self.pedidos.put("mostrar")

    def uma_vez(self):
        """`--lancador`: abre, espera escolher (ou Esc) e termina."""
        self.sair_ao_esconder = True
        self.carregar_apps(esperar=True)
        self.pedidos.put("mostrar")
        self._tk()

    def _itens(self):
        out = list(self.apps) + itens_fixos()
        if self.listar_janelas:
            try:
                for _pid, js in self.listar_janelas().items():
                    for h, titulo, _m, _a in js:
                        if titulo and titulo != "CNT Monitor — Lançador":
                            out.append({"tipo": "janela", "nome": titulo[:120], "sub": "janela aberta", "alvo": h})
            except Exception:  # noqa: BLE001
                pass
        return out

    def _usar(self, item):
        if item["tipo"] in ("web", "cmd", "calc", "janela"):
            return
        self.usos[chave(item)] = self.usos.get(chave(item), 0) + 1
        try:
            with open(self.arq_usos, "w", encoding="utf-8") as f:
                json.dump(dict(sorted(self.usos.items(), key=lambda x: -x[1])[:300]), f)
        except OSError:
            pass

    def _tk(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.title("CNT Monitor — Lançador")
        root.configure(bg=self.COR_FUNDO)
        if WIN:
            root.overrideredirect(True)
        root.attributes("-topmost", True)
        fonte = ("Segoe UI", 15) if WIN else ("Sans", 14)
        texto = tk.StringVar()
        caixa = tk.Entry(root, textvariable=texto, font=fonte, bg="#2a3040", fg=self.COR_TEXTO, insertbackground=self.COR_TEXTO,
                         relief="flat", highlightthickness=2, highlightcolor=self.COR_SEL, highlightbackground="#3a4256")
        caixa.pack(fill="x", padx=12, pady=(12, 6), ipady=6)
        lista = tk.Listbox(root, font=(fonte[0], 11), height=MAX_ITENS, bg=self.COR_FUNDO, fg=self.COR_TEXTO,
                           selectbackground=self.COR_SEL, selectforeground="#ffffff", relief="flat", highlightthickness=0,
                           activestyle="none", borderwidth=0)
        lista.pack(fill="both", expand=True, padx=12)
        dica = tk.Label(root, text="Enter abre · ↑↓ escolhe · Esc fecha · ?pesquisa na web · >comando · 12*3 faz conta",
                        bg=self.COR_FUNDO, fg=self.COR_SUB, font=(fonte[0], 9))
        dica.pack(fill="x", padx=12, pady=(4, 10))
        estado = {"itens": [], "visivel": False}

        def atualizar(*_):
            self.resultados = buscar(estado["itens"], texto.get(), self.usos)
            lista.delete(0, "end")
            for it in self.resultados:
                lista.insert("end", f"  {it['nome']}      ·  {it['sub']}")
            if self.resultados:
                lista.selection_set(0)
                lista.activate(0)

        def esconder(*_):
            estado["visivel"] = False
            root.withdraw()
            if self.sair_ao_esconder:
                root.quit()

        def escolher(*_):
            sel = lista.curselection()
            if not self.resultados:
                return
            it = self.resultados[sel[0] if sel else 0]
            esconder()
            ok, msg = executar(it, self.abrir_janela, self.trazer_para_frente)
            if ok:
                self._usar(it)
                if it["tipo"] == "calc":
                    self.notificar(msg, "Lançador")
            else:
                self.notificar(msg, "Lançador")

        def mover(d):
            n = lista.size()
            if not n:
                return "break"
            sel = lista.curselection()
            i = max(0, min(n - 1, (sel[0] if sel else 0) + d))
            lista.selection_clear(0, "end")
            lista.selection_set(i)
            lista.activate(i)
            lista.see(i)
            return "break"

        def perdeu_foco(*_):
            root.after(200, lambda: estado["visivel"] and root.focus_get() is None and esconder())

        def mostrar():
            estado["itens"] = self._itens()
            texto.set("")
            lista.delete(0, "end")
            larg, alt = 680, 420
            x0, y0, x1, y1 = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
            if self.monitores:
                try:
                    px, py = root.winfo_pointerxy()
                    for m in self.monitores():
                        if m[0] <= px < m[2] and m[1] <= py < m[3]:
                            x0, y0, x1, y1 = m
                            break
                except Exception:  # noqa: BLE001
                    pass
            root.geometry(f"{larg}x{alt}+{x0 + (x1 - x0 - larg) // 2}+{y0 + (y1 - y0) // 4}")
            root.deiconify()
            root.lift()
            estado["visivel"] = True
            if WIN and self.trazer_para_frente:
                try:
                    self.trazer_para_frente(int(root.wm_frame(), 16))
                except Exception:  # noqa: BLE001
                    pass
            root.focus_force()
            caixa.focus_set()

        def ver_pedidos():
            try:
                while True:
                    if self.pedidos.get_nowait() == "mostrar":
                        if estado["visivel"]:
                            esconder()          # o mesmo atalho fecha
                        else:
                            mostrar()
            except queue.Empty:
                pass
            if estado["visivel"] and self.apps and not any(i["tipo"] == "app" for i in estado["itens"]):
                estado["itens"] = self._itens()       # os apps terminaram de carregar com a caixa aberta
                atualizar()
            root.after(80, ver_pedidos)

        texto.trace_add("write", atualizar)
        caixa.bind("<Return>", escolher)
        caixa.bind("<Escape>", esconder)
        caixa.bind("<Down>", lambda e: mover(1))
        caixa.bind("<Up>", lambda e: mover(-1))
        lista.bind("<Double-Button-1>", escolher)
        root.bind("<FocusOut>", perdeu_foco)
        root.after(50, ver_pedidos)
        root.mainloop()
        try:
            root.destroy()
        except tk.TclError:
            pass
