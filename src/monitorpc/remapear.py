"""Remapear teclas e atalhos (no estilo do Keyboard Manager do PowerToys). Só no Windows.

Regras: tecla -> tecla (CapsLock -> Ctrl), tecla -> nada (desativar), tecla ou atalho -> atalho
(Ctrl+Alt+T -> Ctrl+Shift+Esc), atalho -> tecla e tecla ou atalho -> abrir programa. Cada regra pode valer só
num programa (ex.: chrome.exe). O gancho de teclado (WH_KEYBOARD_LL) fica no ícone, numa thread própria, e só é
instalado quando há regras. A decisão de cada tecla (`Remapeador.evento`) é uma função pura, testada sem teclado.
"""
import os
import re
import time
import threading

WIN = os.name == "nt"
MODS = ("ctrl", "alt", "shift", "win")
VK_MOD = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
          0x12: "alt", 0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}
VK_ESQ = {"ctrl": 0xA2, "alt": 0xA4, "shift": 0xA0, "win": 0x5B}
VK_NEUTRA = 0xE8          # tecla sem uso: evita que soltar Win abra o Iniciar depois de um atalho trocado

# nome: (tecla virtual, estendida, rótulo na tela)
TECLAS = {
    "Ctrl": (0xA2, False, "Ctrl (esquerdo)"), "CtrlDireito": (0xA3, True, "Ctrl direito"),
    "Shift": (0xA0, False, "Shift (esquerdo)"), "ShiftDireito": (0xA1, False, "Shift direito"),
    "Alt": (0xA4, False, "Alt (esquerdo)"), "AltGr": (0xA5, True, "AltGr / Alt direito"),
    "Win": (0x5B, True, "Win"), "WinDireito": (0x5C, True, "Win direito"),
    "CapsLock": (0x14, False, "Caps Lock"), "Tab": (0x09, False, "Tab"), "Esc": (0x1B, False, "Esc"),
    "Enter": (0x0D, False, "Enter"), "Backspace": (0x08, False, "Backspace"), "Space": (0x20, False, "Espaço"),
    "Insert": (0x2D, True, "Insert"), "Delete": (0x2E, True, "Delete"), "Home": (0x24, True, "Home"),
    "End": (0x23, True, "End"), "PageUp": (0x21, True, "Page Up"), "PageDown": (0x22, True, "Page Down"),
    "Up": (0x26, True, "Seta para cima"), "Down": (0x28, True, "Seta para baixo"),
    "Left": (0x25, True, "Seta para a esquerda"), "Right": (0x27, True, "Seta para a direita"),
    "Print": (0x2C, True, "Print Screen"), "ScrollLock": (0x91, False, "Scroll Lock"), "Pause": (0x13, False, "Pause"),
    "NumLock": (0x90, True, "Num Lock"), "Menu": (0x5D, True, "Menu (tecla de contexto)"),
    "PlayPause": (0xB3, True, "Mídia: tocar/pausar"), "ProximaFaixa": (0xB0, True, "Mídia: próxima"),
    "FaixaAnterior": (0xB1, True, "Mídia: anterior"), "PararMidia": (0xB2, True, "Mídia: parar"),
    "VolumeMais": (0xAF, True, "Volume +"), "VolumeMenos": (0xAE, True, "Volume −"), "Mudo": (0xAD, True, "Mudo"),
}
TECLAS.update({chr(c): (c, False, chr(c)) for c in range(0x41, 0x5B)})
TECLAS.update({str(d): (0x30 + d, False, str(d)) for d in range(10)})
TECLAS.update({f"F{n}": (0x6F + n, False, f"F{n}") for n in range(1, 25)})
_POR_MINUSCULA = {k.lower(): k for k in TECLAS}
_POR_VK = {}
for _n, (_vk, _e, _r) in TECLAS.items():
    _POR_VK.setdefault(_vk, _n)
NOMES_MOD = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
TECLAS_UI = [[n, r] for n, (_v, _e, r) in TECLAS.items()]


def ler(txt):
    """'CapsLock' -> (frozenset(), 'CapsLock'); 'Ctrl+Alt+T' -> ({'ctrl','alt'}, 'T'). None se inválido.
    Num atalho os modificadores valem dos dois lados (Ctrl = qualquer Ctrl) e a tecla final não pode ser um deles."""
    partes = [p.strip() for p in str(txt or "").split("+") if p.strip()]
    if not partes:
        return None
    *mods, tecla = partes
    mods = [m.lower() for m in mods]
    if any(m not in MODS for m in mods) or len(set(mods)) != len(mods) or len(mods) > 4:
        return None
    nome = _POR_MINUSCULA.get(tecla.lower())
    if not nome or (mods and TECLAS[nome][0] in VK_MOD):
        return None
    return frozenset(mods), nome


def texto(p):
    mods, nome = p
    return "+".join([NOMES_MOD[m] for m in MODS if m in mods] + [nome])


def validar(lista):
    from .personalizar import caminho_valido
    out, vistos = [], set()
    for r in (lista or [])[:60]:
        if not isinstance(r, dict):
            continue
        de = ler(r.get("de"))
        if not de:
            continue
        abrir = caminho_valido(r.get("abrir")) or ""
        if r.get("abrir") and not abrir:
            continue        # caminho inválido: não pode virar "desativar a tecla"
        para = ""
        if not abrir and r.get("para"):
            p = ler(r.get("para"))
            if not p:
                continue
            para = texto(p)
        prog = str(r.get("programa") or "").strip().lower()[:60]
        if prog and not re.fullmatch(r"[\w .()-]+\.exe", prog):
            prog = ""
        chave = (texto(de).lower(), prog)
        if chave in vistos or (para and para.lower() == chave[0]):
            continue
        vistos.add(chave)
        out.append({"de": texto(de), "para": para, "abrir": abrir, "programa": prog, "ativo": r.get("ativo", True) is not False})
    return out


def _vk(nome):
    return TECLAS[nome][0]


class Remapeador:
    """Decide o que fazer com cada tecla física. evento() -> (bloquear, [(vk, desce)], programa_para_abrir)."""

    def __init__(self, regras=()):
        self.presas = {}         # tecla física bloqueada -> o que mandar quando ela subir
        self.mods = {}           # tecla virtual (lógica) de cada modificador apertado -> nome genérico
        self.definir(regras)

    def definir(self, regras):
        self.teclas, self.atalhos = {}, {}
        for r in regras:
            if not r.get("ativo", True):
                continue
            de = ler(r["de"])
            para = ler(r["para"]) if r.get("para") else None
            alvo = {"para": para, "abrir": r.get("abrir") or "", "programa": r.get("programa") or ""}
            destino = self.atalhos if de[0] else self.teclas
            destino.setdefault(_vk(de[1]), []).append((de[0], alvo))
        self.ativo = bool(self.teclas or self.atalhos)
        self.usa_programa = any(a["programa"] for d in (self.teclas, self.atalhos) for v in d.values() for _m, a in v)

    @staticmethod
    def _escolher(cands, mods, prog):
        """A regra do programa em primeiro plano vale mais que a geral."""
        boas = [a for m, a in cands if m == mods and (not a["programa"] or a["programa"] == prog)]
        boas.sort(key=lambda a: not a["programa"])
        return boas[0] if boas else None

    def _toque(self, para, soltar=()):
        """Sequência para 'apertar' um atalho ou tecla com os modificadores certos, devolvendo depois os que
        estavam apertados (soltar = teclas virtuais dos modificadores físicos que o atalho de destino não usa)."""
        mods_alvo, nome = para
        segurados = set(self.mods.values())
        seq = []
        if any(self.mods.get(v) == "win" for v in soltar):
            seq += [(VK_NEUTRA, True), (VK_NEUTRA, False)]
        seq += [(v, False) for v in soltar]
        apertar = [VK_ESQ[m] for m in MODS if m in mods_alvo and m not in segurados]
        seq += [(v, True) for v in apertar]
        seq += [(_vk(nome), True), (_vk(nome), False)]
        seq += [(v, False) for v in reversed(apertar)]
        seq += [(v, True) for v in soltar]
        return seq

    def evento(self, vk, desce, prog=""):
        if not desce and vk in self.presas:
            envio = self.presas.pop(vk)
            for v, d in envio:
                if not d:
                    self.mods.pop(v, None)
            return True, envio, None
        if desce:
            alvo = self._escolher(self.teclas.get(vk, ()), frozenset(), prog)
            if alvo:
                if alvo["abrir"]:
                    abrir = None if vk in self.presas else alvo["abrir"]      # repetição da tecla segurada
                    self.presas[vk] = []
                    return True, [], abrir
                if not alvo["para"]:
                    self.presas[vk] = []
                    return True, [], None
                mods_alvo, nome = alvo["para"]
                if not mods_alvo:
                    v = _vk(nome)
                    self.presas[vk] = [(v, False)]
                    if v in VK_MOD:
                        self.mods[v] = VK_MOD[v]
                    return True, [(v, True)], None
                self.presas[vk] = []
                return True, self._toque(alvo["para"]), None
            mods = frozenset(self.mods.values())
            alvo = self._escolher(self.atalhos.get(vk, ()), mods, prog) if mods else None
            if alvo:
                self.presas[vk] = []
                if alvo["abrir"]:
                    return True, [], alvo["abrir"]
                if not alvo["para"]:
                    return True, [], None
                soltar = [v for v, m in self.mods.items() if m not in alvo["para"][0]]
                return True, self._toque(alvo["para"], soltar), None
        if vk in VK_MOD:
            if desce:
                self.mods[vk] = VK_MOD[vk]
            else:
                self.mods.pop(vk, None)
        return False, [], None


class Gancho:
    """WH_KEYBOARD_LL numa thread com fila de mensagens. Instalado só com regras; refeito a cada minuto
    (o Windows desliga em silêncio um gancho que demorou a responder)."""

    def __init__(self, abrir=None):
        self.rem = Remapeador()
        self.abrir = abrir
        self._thread = None
        self.erro = ""

    def definir(self, regras):
        self.rem.definir(regras)
        if WIN and self.rem.ativo and self._thread is None:
            self._thread = threading.Thread(target=self._rodar, name="remapear", daemon=True)
            self._thread.start()

    def _rodar(self):
        import ctypes
        from ctypes import wintypes as wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wt.DWORD), ("u", _U)]

        PROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, PROC, wt.HINSTANCE, wt.DWORD]
        u.SetWindowsHookExW.restype = wt.HHOOK
        u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        u.CallNextHookEx.restype = LRESULT
        u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
        u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        u.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
        u.GetForegroundWindow.restype = wt.HWND
        u.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
        k.GetModuleHandleW.restype = wt.HMODULE
        k.OpenProcess.restype = wt.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        ESTENDIDAS = {v for v, e, _r in TECLAS.values() if e}
        cache = {"h": None, "t": 0, "prog": ""}

        def programa():
            h = u.GetForegroundWindow()
            if h == cache["h"] and time.monotonic() - cache["t"] < 1:
                return cache["prog"]
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            nome = ""
            hp = k.OpenProcess(0x1000, False, pid.value)        # PROCESS_QUERY_LIMITED_INFORMATION
            if hp:
                buf, n = ctypes.create_unicode_buffer(520), wt.DWORD(520)
                if k.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)):
                    nome = os.path.basename(buf.value).lower()
                k.CloseHandle(hp)
            cache.update(h=h, t=time.monotonic(), prog=nome)
            return nome

        def enviar(seq):
            arr = (INPUT * len(seq))()
            for i, (vk, desce) in enumerate(seq):
                fl = (0 if desce else 0x0002) | (0x0001 if vk in ESTENDIDAS else 0)
                arr[i] = INPUT(type=1)
                arr[i].ki = KEYBDINPUT(wVk=vk, wScan=u.MapVirtualKeyW(vk, 0) & 0xFF, dwFlags=fl, time=0, dwExtraInfo=0)
            u.SendInput(len(seq), arr, ctypes.sizeof(INPUT))

        def cb(n, wp, lp):
            try:
                if n == 0 and self.rem.ativo:
                    ev = ctypes.cast(lp, ctypes.POINTER(KBD)).contents
                    if not ev.flags & 0x10:          # LLKHF_INJECTED: as teclas que este gancho mesmo mandou
                        desce = wp in (0x0100, 0x0104)
                        prog = programa() if self.rem.usa_programa else ""
                        bloquear, seq, abrir = self.rem.evento(ev.vkCode, desce, prog)
                        if seq:
                            enviar(seq)
                        if abrir and self.abrir:
                            threading.Thread(target=self.abrir, args=(abrir,), daemon=True).start()
                        if bloquear:
                            return 1
            except Exception:  # noqa: BLE001  (exceção dentro do gancho travaria o teclado)
                pass
            return u.CallNextHookEx(None, n, wp, lp)

        self._cb = PROC(cb)
        gancho, desde = None, 0
        u.SetTimer(None, 0, 1000, None)
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0113:   # WM_TIMER
                if gancho and (not self.rem.ativo or time.monotonic() - desde > 60):
                    u.UnhookWindowsHookEx(gancho)
                    gancho = None
                if self.rem.ativo and not gancho:
                    gancho = u.SetWindowsHookExW(13, self._cb, k.GetModuleHandleW(None), 0)
                    desde = time.monotonic()
                    self.erro = "" if gancho else f"não consegui instalar o gancho de teclado ({ctypes.get_last_error()})"
                continue
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
