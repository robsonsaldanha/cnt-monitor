"""Atalhos do Windows para a sessão do técnico.

O navegador não recebe Win, Alt+Tab, Alt+F4, Ctrl+Esc, Print Screen e os atalhos reservados do
Chrome/Edge (Ctrl+W, Ctrl+T, Ctrl+N): o Windows ou o próprio navegador agem antes da página. Com
"Atalhos no outro computador" ligado, a página marca o título da janela da sessão (MARCA no fim) e
este gancho, no ícone da bandeja do técnico, troca essas teclas por F13..F22 só enquanto uma janela
marcada está em primeiro plano. O Windows ignora F13..F22; a página recebe e manda a tecla
verdadeira para o outro computador. Em qualquer outra janela (ou com o toggle desligado) nada muda.
"""
import os
import threading

MARCA = "⁣"            # separador invisível no fim do título = sessão com atalhos ligados
PREFIXO = "CNT Monitor"

VK_TAB, VK_CTRL, VK_ALT, VK_ESC, VK_ESPACO, VK_PRINT = 0x09, 0x11, 0x12, 0x1B, 0x20, 0x2C
VK_LWIN, VK_RWIN, VK_F4 = 0x5B, 0x5C, 0x73
VK_F13 = 0x7C
SC_F13 = 0x64   # código de varredura de F13 (F14..F22 = 0x65..0x6D): o Chrome/Edge tira o e.code dele

# tecla física -> (substituta, quando trocar). "sempre" ou o modificador que precisa estar apertado
TROCAS = {
    VK_LWIN: (VK_F13, "sempre"),
    VK_RWIN: (VK_F13 + 1, "sempre"),
    VK_TAB: (VK_F13 + 2, "alt_ou_ctrl"),      # Alt+Tab, Ctrl+Tab
    VK_ESC: (VK_F13 + 3, "alt_ou_ctrl"),      # Alt+Esc, Ctrl+Esc, Ctrl+Shift+Esc
    VK_F4: (VK_F13 + 4, "alt_ou_ctrl"),       # Alt+F4, Ctrl+F4
    VK_ESPACO: (VK_F13 + 5, "alt"),           # Alt+Espaço (menu da janela)
    VK_PRINT: (VK_F13 + 6, "sempre"),
    0x57: (VK_F13 + 7, "ctrl"),               # Ctrl+W
    0x54: (VK_F13 + 8, "ctrl"),               # Ctrl+T
    0x4E: (VK_F13 + 9, "ctrl"),               # Ctrl+N
}


def titulo_da_sessao(titulo):
    """A janela em primeiro plano é uma janela da sessão do técnico com os atalhos ligados?"""
    return isinstance(titulo, str) and titulo.startswith(PREFIXO) and titulo.endswith(MARCA)


def decidir(vk, baixa, alt, ctrl, trocadas):
    """Tecla real -> tecla substituta (ou None para deixar passar). `trocadas` guarda as que desceram
    trocadas, para a subida também ser trocada mesmo que o modificador já tenha sido solto."""
    if not baixa:
        return trocadas.pop(vk, None)
    if vk in trocadas:                        # repetição de tecla segurada
        return trocadas[vk]
    t = TROCAS.get(vk)
    if not t:
        return None
    sub, quando = t
    if quando == "sempre" or (quando == "alt" and alt) or (quando == "ctrl" and ctrl) \
            or (quando == "alt_ou_ctrl" and (alt or ctrl)):
        trocadas[vk] = sub
        return sub
    return None


class Gancho:
    """WH_KEYBOARD_LL numa thread própria com fila de mensagens (o gancho pertence à thread que o instala)."""

    def __init__(self):
        self.trocadas = {}
        self._thread = None

    def iniciar(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._rodar, name="teclado-remoto", daemon=True)
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
        u.GetForegroundWindow.restype = wt.HWND
        u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        k.GetModuleHandleW.restype = wt.HMODULE
        buf = ctypes.create_unicode_buffer(512)
        INJETADA, SOBE = 0x10, 0x0002

        def sessao_na_frente():
            h = u.GetForegroundWindow()
            if not h:
                return False
            u.GetWindowTextW(h, buf, 512)
            return titulo_da_sessao(buf.value)

        def enviar(vk, baixa):
            i = INPUT(type=1)   # INPUT_KEYBOARD
            i.ki = KEYBDINPUT(wVk=vk, wScan=SC_F13 + (vk - VK_F13), dwFlags=0 if baixa else SOBE,
                              time=0, dwExtraInfo=0)
            u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

        def cb(n, wp, lp):
            try:
                if n == 0:
                    ev = ctypes.cast(lp, ctypes.POINTER(KBD)).contents
                    if not ev.flags & INJETADA:
                        baixa = wp in (0x0100, 0x0104)   # WM_KEYDOWN, WM_SYSKEYDOWN
                        vk = ev.vkCode
                        # a subida de uma tecla trocada é sempre trocada; a descida só com a sessão na frente
                        if (not baixa and vk in self.trocadas) or (baixa and sessao_na_frente()):
                            sub = decidir(vk, baixa, u.GetAsyncKeyState(VK_ALT) < 0,
                                          u.GetAsyncKeyState(VK_CTRL) < 0, self.trocadas)
                            if sub:
                                enviar(sub, baixa)
                                return 1      # a tecla real não chega ao Windows deste computador
            except Exception:   # noqa: BLE001  (exceção no gancho travaria o teclado)
                pass
            return u.CallNextHookEx(None, n, wp, lp)

        self._cb = PROC(cb)     # referência viva: o Windows chama este ponteiro
        u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
        u.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
        gancho = None
        # o gancho só existe enquanto uma janela da sessão (com atalhos ligados) está na frente: fora disso
        # o teclado deste computador não passa por aqui
        u.SetTimer(None, 0, 250, None)
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0113:   # WM_TIMER
                quer = sessao_na_frente() or bool(self.trocadas)
                if quer and not gancho:
                    gancho = u.SetWindowsHookExW(13, self._cb, k.GetModuleHandleW(None), 0)   # WH_KEYBOARD_LL
                elif not quer and gancho:
                    u.UnhookWindowsHookEx(gancho)
                    gancho = None
                continue
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))


if os.name != "nt":     # Linux com X11: XGrabKey só com a janela da sessão na frente
    from .linux.teclado import Gancho  # noqa: E402,F401,F811
