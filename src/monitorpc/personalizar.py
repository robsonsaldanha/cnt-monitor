"""Personalizar: barra e relógio do Windows, ícones ao lado do relógio, tema e papel de parede automáticos e perfis.

Quem aplica é o ícone da bandeja, com o usuário da sessão (o registro HKCU e o papel de parede são de cada usuário).
O serviço só usa daqui a validação da configuração. As opções nativas do Windows (barra, relógio, tema) são lidas
e gravadas direto no registro, sem cópia na configuração do Monitor: o que vale é sempre o que o Windows tem,
então mudar pelas Configurações do Windows não briga com o Monitor.
"""
import os
import re
import json
import time
import random
import threading

WIN = os.name == "nt"
EXT_IMAGEM = (".jpg", ".jpeg", ".png", ".bmp")

# ---------------------------------------------------------------- validação (usada também pelo serviço)
OPCAO_PERFIL = {"tema": ("", "claro", "escuro"), "jogo": ("", "ligar", "desligar"), "mini": ("", "ligar", "desligar"),
                "barra": ("", "ocultar", "mostrar"), "icones_area": ("", "ocultar", "mostrar")}
PERFIS_PADRAO = [
    {"id": "trabalho", "nome": "Trabalho", "tema": "claro", "jogo": "desligar", "mini": "desligar", "barra": "mostrar",
     "icones_area": "mostrar"},
    {"id": "jogo", "nome": "Jogo", "tema": "escuro", "jogo": "ligar", "mini": "ligar"},
    {"id": "apresentacao", "nome": "Apresentação", "barra": "ocultar", "icones_area": "ocultar", "acordado": True,
     "mini": "desligar"},
]


def minutos(txt):
    """'07:30' -> 450; None se inválido."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(txt or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def caminho_valido(v):
    """'' (vazio) ou caminho completo; None se não for um caminho completo."""
    v = str(v or "").strip().strip('"')[:260]
    if not v:
        return ""
    if any(ord(ch) < 32 for ch in v):
        return None
    if WIN:
        import ntpath
        return v if ntpath.isabs(v) and ntpath.splitdrive(v)[0] else None
    return v if os.path.isabs(v) else None


def periodo(agora_min, claro_min, escuro_min):
    """'claro' ou 'escuro' para a hora do dia (em minutos). Aceita o escuro antes do claro (virada da meia-noite)."""
    if claro_min == escuro_min:
        return "claro"
    if claro_min < escuro_min:
        return "claro" if claro_min <= agora_min < escuro_min else "escuro"
    return "escuro" if escuro_min <= agora_min < claro_min else "claro"


def validar_perfis(lista):
    from .captura import ler_atalho
    out, ids, usados = [], set(), set()
    for p in (lista or [])[:12]:
        if not isinstance(p, dict):
            continue
        nome = "".join(ch for ch in str(p.get("nome") or "") if ord(ch) >= 32).strip()[:40]
        if not nome:
            continue
        pid = "".join(ch for ch in str(p.get("id") or "") if ch.isalnum() or ch == "_")[:20] or f"p{len(out) + 1}"
        while pid in ids:
            pid += "x"
        ids.add(pid)
        r = ler_atalho(p.get("atalho")) if p.get("atalho") else None
        atalho = r[2] if r and r[2].lower() not in usados else ""
        if atalho:
            usados.add(atalho.lower())
        d = {"id": pid, "nome": nome, "atalho": atalho}
        for k, ops in OPCAO_PERFIL.items():
            d[k] = p.get(k) if p.get(k) in ops else ""
        d["papel"] = caminho_valido(p.get("papel")) or ""
        plano = str(p.get("plano") or "")
        d["plano"] = plano if re.fullmatch(r"[0-9a-fA-F-]{36}|[\w.-]{1,40}", plano) else ""
        d["acordado"] = bool(p.get("acordado"))
        d["abrir"] = [c for c in (caminho_valido(x) for x in (p.get("abrir") or [])[:10] if isinstance(x, str)) if c]
        out.append(d)
    return out


def descrever_perfil(p):
    """Resumo em uma linha do que o perfil faz (para a tela e para o aviso)."""
    partes = []
    if p.get("tema"):
        partes.append(f"tema {p['tema']}")
    if p.get("papel"):
        partes.append("papel de parede")
    if p.get("plano"):
        partes.append("plano de energia")
    if p.get("jogo"):
        partes.append("Modo Jogo " + ("ligado" if p["jogo"] == "ligar" else "desligado"))
    if p.get("mini"):
        partes.append("mini painel " + ("ligado" if p["mini"] == "ligar" else "desligado"))
    if p.get("barra"):
        partes.append("barra " + ("oculta" if p["barra"] == "ocultar" else "fixa"))
    if p.get("icones_area"):
        partes.append("ícones da área de trabalho " + ("ocultos" if p["icones_area"] == "ocultar" else "visíveis"))
    if p.get("acordado"):
        partes.append("sem dormir")
    if p.get("abrir"):
        partes.append(f"abre {len(p['abrir'])} programa(s)")
    return ", ".join(partes) or "nada configurado"


def imagens_da_pasta(pasta):
    try:
        return sorted(os.path.join(pasta, n) for n in os.listdir(pasta) if n.lower().endswith(EXT_IMAGEM))
    except OSError:
        return []


def proximo_papel(lista, atual, aleatorio, sorteio=random.choice):
    """Próximo papel da rotação (nunca repete o atual, se houver outro)."""
    if not lista:
        return None
    if aleatorio:
        outros = [x for x in lista if x != atual] or lista
        return sorteio(outros)
    try:
        return lista[(lista.index(atual) + 1) % len(lista)]
    except ValueError:
        return lista[0]


# ---------------------------------------------------------------- Windows: opções nativas
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
PERS = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
BUSCA = r"Software\Microsoft\Windows\CurrentVersion\Search"
DEV = ADV + r"\TaskbarDeveloperSettings"
MENU_CLASSICO = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"
NOTIFY = r"Control Panel\NotifyIconSettings"
LIGA = [(True, "Ligado", 1), (False, "Desligado", 0)]
TEMA = [("claro", "Claro", 1), ("escuro", "Escuro", 0)]

# id: título, grupo, chave, valor, opções [(valor da API, rótulo, DWORD)], padrão do Windows (DWORD), só no Windows 11
SISTEMA = [
    ("barra_alinhamento", "Ícones da barra", "barra", ADV, "TaskbarAl",
     [("esquerda", "À esquerda", 0), ("centro", "No centro", 1)], 1, True),
    ("barra_pesquisa", "Pesquisa na barra", "barra", BUSCA, "SearchboxTaskbarMode",
     [("oculta", "Oculta", 0), ("icone", "Só o ícone", 1), ("caixa", "Caixa de pesquisa", 2), ("rotulo", "Ícone e rótulo", 3)], 1, False),
    ("barra_visao", "Botão Visão de tarefas", "barra", ADV, "ShowTaskViewButton", LIGA, 1, False),
    ("barra_widgets", "Widgets", "barra", ADV, "TaskbarDa", LIGA, 1, True),
    ("barra_fim_tarefa", "“Finalizar tarefa” no clique direito da barra", "barra", DEV, "TaskbarEndTask", LIGA, 0, True),
    ("relogio_segundos", "Segundos no relógio", "barra", ADV, "ShowSecondsInSystemClock", LIGA, 0, False),
    ("tema_apps", "Tema dos aplicativos", "tema", PERS, "AppsUseLightTheme", TEMA, 1, False),
    ("tema_sistema", "Tema do Windows (barra, Iniciar)", "tema", PERS, "SystemUsesLightTheme", TEMA, 1, False),
    ("cor_barra", "Cor de destaque na barra e no Iniciar (só com o tema escuro do Windows)", "tema", PERS,
     "ColorPrevalence", LIGA, 0, False),
    ("explorer_extensoes", "Mostrar a extensão dos arquivos", "explorer", ADV, "HideFileExt",
     [(True, "Ligado", 0), (False, "Desligado", 1)], 1, False),
    ("explorer_ocultos", "Mostrar arquivos ocultos", "explorer", ADV, "Hidden",
     [(True, "Ligado", 1), (False, "Desligado", 2)], 2, False),
]
ESPECIAIS = [   # não são um DWORD simples
    ("barra_ocultar", "Ocultar a barra automaticamente", "barra", LIGA, False),
    ("menu_classico", "Menu de contexto clássico (o do Windows 10, sem “Mostrar mais opções”)", "explorer", LIGA, True),
]
REINICIAR_EXPLORER = {"menu_classico"}

if WIN:
    import ctypes
    import winreg
    from ctypes import wintypes as wt

    _u = ctypes.WinDLL("user32", use_last_error=True)
    _sh = ctypes.WinDLL("shell32")
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _u.FindWindowW.restype = wt.HWND
    _u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    _u.FindWindowExW.restype = wt.HWND
    _u.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
    _u.SendMessageW.restype = ctypes.c_ssize_t
    _u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_void_p, wt.UINT, wt.UINT,
                                       ctypes.POINTER(ctypes.c_size_t)]
    _u.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
    _u.IsWindowVisible.argtypes = [wt.HWND]
    _k.SetThreadExecutionState.argtypes = [wt.DWORD]
    _k.SetThreadExecutionState.restype = wt.DWORD

    class _APPBAR(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT), ("uEdge", wt.UINT),
                    ("rc", wt.RECT), ("lParam", wt.LPARAM)]
    _sh.SHAppBarMessage.restype = ctypes.c_size_t
    _sh.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(_APPBAR)]
    _sh.SHChangeNotify.argtypes = [ctypes.c_long, wt.UINT, ctypes.c_void_p, ctypes.c_void_p]


def windows11():
    import sys
    return WIN and sys.getwindowsversion().build >= 22000


def _ler_dword(chave, valor):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
            v, tipo = winreg.QueryValueEx(k, valor)
            return int(v) if tipo == winreg.REG_DWORD else None
    except OSError:
        return None


def _gravar_dword(chave, valor, n):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, chave, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, valor, 0, winreg.REG_DWORD, int(n))


def avisar_mudanca(area):
    """Avisa as janelas (Explorer, barra) que uma configuração mudou."""
    r = ctypes.c_size_t()
    texto = ctypes.create_unicode_buffer(area)
    _u.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.cast(texto, ctypes.c_void_p), 0x0002, 1000, ctypes.byref(r))


def _barra_oculta():
    abd = _APPBAR(cbSize=ctypes.sizeof(_APPBAR), hWnd=_u.FindWindowW("Shell_TrayWnd", None))
    return bool(_sh.SHAppBarMessage(4, ctypes.byref(abd)) & 1)      # ABM_GETSTATE / ABS_AUTOHIDE


def _ocultar_barra(sim):
    abd = _APPBAR(cbSize=ctypes.sizeof(_APPBAR), hWnd=_u.FindWindowW("Shell_TrayWnd", None))
    abd.lParam = 1 if sim else 2                                     # ABS_AUTOHIDE / ABS_ALWAYSONTOP
    _sh.SHAppBarMessage(10, ctypes.byref(abd))                       # ABM_SETSTATE


def _menu_classico():
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, MENU_CLASSICO + r"\InprocServer32").Close()
        return True
    except OSError:
        return False


def _definir_menu_classico(sim):
    if sim:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, MENU_CLASSICO + r"\InprocServer32", 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "")
        return
    for sub in (MENU_CLASSICO + r"\InprocServer32", MENU_CLASSICO):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except FileNotFoundError:
            pass


def ler_sistema():
    """Opções nativas com o valor atual (o que o Windows tem agora)."""
    w11 = windows11()
    out = []
    for i, titulo, grupo, chave, valor, ops, padrao, so11 in SISTEMA:
        if so11 and not w11:
            continue
        n = _ler_dword(chave, valor)
        n = padrao if n is None else n
        atual = next((v for v, _r, d in ops if d == n), None)
        out.append({"id": i, "titulo": titulo, "grupo": grupo, "valor": atual,
                    "opcoes": [[v, r] for v, r, _d in ops], "reiniciar": False})
    for i, titulo, grupo, ops, so11 in ESPECIAIS:
        if so11 and not w11:
            continue
        try:
            atual = _barra_oculta() if i == "barra_ocultar" else _menu_classico()
        except OSError:
            atual = None
        out.append({"id": i, "titulo": titulo, "grupo": grupo, "valor": atual,
                    "opcoes": [[v, r] for v, r, _d in ops], "reiniciar": i in REINICIAR_EXPLORER})
    return out


def aplicar_sistema(ident, valor):
    """Grava uma opção nativa. Devolve (ok, mensagem)."""
    try:
        if ident == "barra_ocultar":
            _ocultar_barra(bool(valor))
            return True, "Barra " + ("ocultada automaticamente." if valor else "sempre visível.")
        if ident == "menu_classico":
            _definir_menu_classico(bool(valor))
            return True, "Feito. Vale depois de reiniciar o Explorer (botão “Reiniciar o Explorer”)."
        item = next((x for x in SISTEMA if x[0] == ident), None)
        if not item:
            return False, "Opção desconhecida."
        _i, titulo, _g, chave, nome, ops, _p, _s = item
        d = next((d for v, _r, d in ops if v == valor), None)
        if d is None:
            return False, "Valor inválido."
        _gravar_dword(chave, nome, d)
    except PermissionError:
        return False, ("O Windows bloqueia programas de mudar essa opção. Mude em Configurações › Personalização "
                       "› Barra de tarefas.")
    except OSError as e:
        return False, f"Não consegui mudar: {e}"
    if chave == PERS:
        avisar_mudanca("ImmersiveColorSet")
    elif chave in (ADV, DEV):
        _sh.SHChangeNotify(0x08000000, 0, None, None)                # SHCNE_ASSOCCHANGED: o Explorer relê
        avisar_mudanca("TraySettings")
    else:
        avisar_mudanca("TraySettings")
    return True, f"{titulo}: feito."


def reiniciar_explorer():
    import subprocess
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, creationflags=0x08000000)
    time.sleep(1)
    subprocess.Popen(["explorer.exe"], close_fds=True)
    return True, "Explorer reiniciado."


# ícones perto do relógio: a mesma lista de Configurações › Personalização › Barra de tarefas › Outros ícones
PASTAS_CONHECIDAS = {"{6D809377-6AF0-444B-8957-A3773F02200E}": "ProgramW6432",
                     "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": "ProgramFiles(x86)",
                     "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": "SystemRoot",
                     "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": None}


def caminho_icone(p):
    """Troca o {GUID} de pasta conhecida que o Windows grava no começo do caminho pela pasta de verdade."""
    m = re.match(r"(\{[0-9A-Fa-f-]{36}\})(.*)", p or "")
    if not m:
        return p or ""
    g = m.group(1).upper()
    if g not in PASTAS_CONHECIDAS:
        return p
    var = PASTAS_CONHECIDAS[g]
    base = (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32") if var is None
            else os.environ.get(var) or os.environ.get("ProgramFiles", ""))
    return base + m.group(2)


def icones_bandeja():
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as s:
                        def val(n, s=s):
                            try:
                                return winreg.QueryValueEx(s, n)[0]
                            except OSError:
                                return None
                        exe = caminho_icone(val("ExecutablePath"))
                        if not exe:
                            continue
                        nome = (val("InitialTooltip") or "").split("\n")[0].strip() or os.path.basename(exe)
                        out.append({"id": sub, "nome": nome[:80], "exe": exe, "visivel": bool(val("IsPromoted")),
                                    "existe": os.path.exists(exe)})
                except OSError:
                    continue
    except OSError:
        pass
    out.sort(key=lambda x: (not x["existe"], not x["visivel"], x["nome"].lower()))
    return out


def definir_icone(ident, visivel):
    if not re.fullmatch(r"\d{1,24}", str(ident)):
        return False, "Ícone inválido."
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY + "\\" + ident, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1 if visivel else 0)
    except OSError as e:
        return False, f"Não consegui mudar: {e}"
    avisar_mudanca("TraySettings")
    return True, "Ícone " + ("ao lado do relógio." if visivel else "dentro da setinha (^).")


def tema_atual():
    n = _ler_dword(PERS, "AppsUseLightTheme")
    return "escuro" if n == 0 else "claro"


def aplicar_tema(modo):
    d = 0 if modo == "escuro" else 1
    try:
        _gravar_dword(PERS, "AppsUseLightTheme", d)
        _gravar_dword(PERS, "SystemUsesLightTheme", d)
    except OSError as e:
        return False, f"Não consegui mudar o tema: {e}"
    avisar_mudanca("ImmersiveColorSet")
    return True, "Tema " + modo + "."


def definir_papel(caminho):
    if not os.path.isfile(caminho):
        return False, "Imagem não encontrada."
    buf = ctypes.create_unicode_buffer(os.path.abspath(caminho))
    if not _u.SystemParametersInfoW(0x0014, 0, ctypes.cast(buf, ctypes.c_void_p), 0x01 | 0x02):  # SPI_SETDESKWALLPAPER
        return False, "O Windows recusou essa imagem como papel de parede."
    return True, "Papel de parede trocado."


def _defview():
    d = _u.FindWindowExW(_u.FindWindowW("Progman", None), None, "SHELLDLL_DefView", None)
    w = None
    while not d:     # com a apresentação de slides, a área de trabalho fica dentro de uma WorkerW
        w = _u.FindWindowExW(None, w, "WorkerW", None)
        if not w:
            return None
        d = _u.FindWindowExW(w, None, "SHELLDLL_DefView", None)
    return d


def icones_area_visiveis():
    d = _defview()
    lv = _u.FindWindowExW(d, None, "SysListView32", None) if d else None
    return bool(lv and _u.IsWindowVisible(lv))


def mostrar_icones_area(sim):
    d = _defview()
    if not d:
        return False, "Não achei a área de trabalho."
    if icones_area_visiveis() != bool(sim):
        _u.SendMessageW(d, 0x0111, 0x7402, 0)        # o mesmo comando de "Exibir › Mostrar ícones da área de trabalho"
    return True, "Ícones da área de trabalho " + ("visíveis." if sim else "ocultos.")


def ocultar_barra(sim):
    _ocultar_barra(sim)
    return True, "Barra " + ("oculta." if sim else "fixa.")


class Acordado:
    """Não deixa o PC dormir nem desligar a tela (perfil Apresentação). O pedido vale enquanto a thread vive."""

    def __init__(self):
        self.ligado = False
        self._ev = threading.Event()

    def definir(self, sim):
        if sim == self.ligado:
            return
        self.ligado = sim
        if sim:
            self._ev.clear()
            threading.Thread(target=self._segurar, name="acordado", daemon=True).start()
        else:
            self._ev.set()

    def _segurar(self):
        if WIN:
            _k.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)   # CONTINUOUS | SYSTEM | DISPLAY
            self._ev.wait()
            _k.SetThreadExecutionState(0x80000000)
            return
        import subprocess   # Linux: systemd-inhibit enquanto o perfil estiver ativo
        try:
            p = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--who=CNT Monitor",
                                  "--why=Perfil Apresentação", "sleep", "infinity"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return
        self._ev.wait()
        p.terminate()


# ---------------------------------------------------------------- o que roda no ícone
class Personalizador:
    """Tema por horário, rotação do papel de parede e perfis. `api_post` fala com o serviço (plano de energia,
    Modo Jogo, mini painel); o resto é feito aqui, na sessão do usuário."""

    def __init__(self, pasta_usuario, api_post, notificar=None):
        self.arq = os.path.join(pasta_usuario, "personalizar.json")
        self.api_post = api_post
        self.notificar = notificar or (lambda *a: None)
        self.acordado = Acordado()
        self.lock = threading.Lock()
        try:
            with open(self.arq, encoding="utf-8") as f:
                self.estado = json.load(f)
        except (OSError, ValueError):
            self.estado = {}

    def _gravar(self):
        try:
            os.makedirs(os.path.dirname(self.arq), exist_ok=True)
            with open(self.arq, "w", encoding="utf-8") as f:
                json.dump(self.estado, f)
        except OSError:
            pass

    def tick(self, c, agora=None):
        """Chamado a cada ~20 s. Só age na virada do horário (se você trocar o tema à mão, ele não desfaz)."""
        agora = agora or time.localtime()
        with self.lock:
            if c.get("pers_tema_auto"):
                cl, es = minutos(c.get("pers_tema_claro")), minutos(c.get("pers_tema_escuro"))
                if cl is not None and es is not None:
                    p = periodo(agora.tm_hour * 60 + agora.tm_min, cl, es)
                    if p != self.estado.get("periodo"):
                        self.estado["periodo"] = p
                        self._gravar()
                        aplicar_tema(p)
                        papel = c.get("pers_papel_claro" if p == "claro" else "pers_papel_escuro")
                        if papel:
                            definir_papel(papel)
            else:
                self.estado.pop("periodo", None)
            n = c.get("pers_papel_minutos") or 0
            if n and c.get("pers_papel_pasta") and time.time() - self.estado.get("papel_ts", 0) >= n * 60:
                self.proximo_papel(c)

    def proximo_papel(self, c):
        lista = imagens_da_pasta(c.get("pers_papel_pasta") or "")
        if not lista:
            return False, "A pasta da rotação não tem imagens (JPG, PNG ou BMP)."
        p = proximo_papel(lista, self.estado.get("papel"), c.get("pers_papel_aleatorio", True))
        self.estado.update(papel=p, papel_ts=time.time())
        self._gravar()
        return definir_papel(p)

    def perfil_ativo(self):
        return self.estado.get("perfil")

    def aplicar_perfil(self, p):
        """Aplica um perfil e devolve (ok, mensagem). Falha numa parte não impede as outras."""
        feitos, erros = [], []

        def passo(rotulo, f, *a):
            try:
                ok, msg = f(*a)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            (feitos if ok else erros).append(rotulo if ok else f"{rotulo}: {msg}")

        def servico(dados, rotulo):
            try:
                r = self.api_post("api/acao", dados, timeout=40)
                (feitos if r.get("ok") else erros).append(rotulo if r.get("ok") else f"{rotulo}: {r.get('msg')}")
            except Exception as e:  # noqa: BLE001
                erros.append(f"{rotulo}: {e}")

        if p.get("tema"):
            passo("tema", aplicar_tema, p["tema"])
        if p.get("papel"):
            passo("papel de parede", definir_papel, p["papel"])
        if p.get("plano"):
            servico({"tipo": "energia_plano", "guid": p["plano"]}, "plano de energia")
        if p.get("jogo"):
            servico({"tipo": "jogo_ligar" if p["jogo"] == "ligar" else "jogo_desligar"}, "Modo Jogo")
        if p.get("mini"):
            try:
                self.api_post("api/config", {"mini_ativo": p["mini"] == "ligar"})
                feitos.append("mini painel")
            except Exception as e:  # noqa: BLE001
                erros.append(f"mini painel: {e}")
        if WIN and p.get("barra"):
            passo("barra", ocultar_barra, p["barra"] == "ocultar")
        if WIN and p.get("icones_area"):
            passo("ícones da área de trabalho", mostrar_icones_area, p["icones_area"] == "mostrar")
        self.acordado.definir(bool(p.get("acordado")))
        for prog in p.get("abrir") or []:
            passo(os.path.basename(prog), abrir_programa, prog)
        with self.lock:
            self.estado["perfil"] = p["id"]
            self._gravar()
        msg = f"Perfil “{p['nome']}” aplicado" + (f" ({', '.join(feitos)})" if feitos else "") + "."
        if erros:
            msg += " Não deu: " + "; ".join(erros) + "."
        return not erros, msg


def abrir_programa(caminho):
    import subprocess
    if not os.path.exists(caminho):
        return False, "não encontrado"
    if WIN:
        os.startfile(caminho)
    else:
        subprocess.Popen(["xdg-open", caminho] if not os.access(caminho, os.X_OK) or os.path.isdir(caminho)
                         else [caminho], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True, "aberto"


if not WIN:
    from .linux.personalizar import (ler_sistema, aplicar_sistema, icones_bandeja, definir_icone, tema_atual,  # noqa: E402,F401,F811
                                     aplicar_tema, definir_papel, reiniciar_explorer, mostrar_icones_area,
                                     ocultar_barra)
