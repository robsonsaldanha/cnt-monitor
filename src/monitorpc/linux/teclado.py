"""Atalhos para a sessão do técnico quando o técnico usa Linux com X11 (mesma regra do Windows: teclado_remoto).

Enquanto a janela da sessão (título marcado) está na frente, este processo reserva no servidor X só as
combinações que o ambiente gráfico usaria (Super, Alt+Tab, Alt+F4, Ctrl+Esc, Print, Ctrl+W/T/N...) e manda no
lugar delas F13..F22 (XTest) para a janela da sessão, que traduz para a tecla verdadeira do outro computador.
Fora da sessão nada fica reservado. Se o gerenciador de janelas já reservou uma combinação (o GNOME reserva o
Super), ela continua com ele: nesse caso a tela cheia da sessão (que trava o teclado no navegador) resolve.
No Wayland nenhum programa pode reservar teclas: só a tela cheia da sessão."""
import time
import threading

from ..teclado_remoto import titulo_da_sessao, decidir, TROCAS, VK_F13

# tecla virtual do Windows (TROCAS) -> keysym do X
KEYSYM = {0x5B: "Super_L", 0x5C: "Super_R", 0x09: "Tab", 0x1B: "Escape", 0x73: "F4", 0x20: "space", 0x2C: "Print",
          0x57: "w", 0x54: "t", 0x4E: "n"}
F13_X = 191         # código X de F13 (evdev 183 + 8); F14..F22 seguem


class Gancho:
    def __init__(self):
        self.trocadas = {}
        self._thread = None

    def iniciar(self):
        import os
        if self._thread is None and os.environ.get("DISPLAY") and os.environ.get("XDG_SESSION_TYPE") != "wayland":
            self._thread = threading.Thread(target=self._rodar, name="teclado-remoto", daemon=True)
            self._thread.start()

    def _rodar(self):
        from Xlib import X, XK, display, error
        from Xlib.ext import xtest
        d = display.Display()
        raiz = d.screen().root
        ativa = d.intern_atom("_NET_ACTIVE_WINDOW")
        nome = d.intern_atom("_NET_WM_NAME")
        utf8 = d.intern_atom("UTF8_STRING")
        codigos = {d.keysym_to_keycode(XK.string_to_keysym(ks)): vk for vk, ks in KEYSYM.items()}
        codigos.pop(0, None)
        travas = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)          # Caps Lock e Num Lock
        mods = {"sempre": (0, X.ShiftMask, X.ControlMask, X.Mod1Mask, X.ControlMask | X.ShiftMask),
                "alt": (X.Mod1Mask, X.Mod1Mask | X.ShiftMask), "ctrl": (X.ControlMask, X.ControlMask | X.ShiftMask),
                "alt_ou_ctrl": (X.Mod1Mask, X.ControlMask, X.Mod1Mask | X.ShiftMask, X.ControlMask | X.ShiftMask)}
        reservado = False
        pegar = error.CatchError(error.BadAccess)

        def sessao_na_frente():
            try:
                w = raiz.get_full_property(ativa, X.AnyPropertyType)
                if not w or not w.value:
                    return False
                jan = d.create_resource_object("window", w.value[0])
                p = jan.get_full_property(nome, utf8)
                titulo = p.value.decode("utf-8", "replace") if p and isinstance(p.value, bytes) else None
                return titulo_da_sessao(titulo)
            except error.XError:
                return False

        def reservar(ligar):
            for kc, vk in codigos.items():
                for m in mods[TROCAS[vk][1]]:
                    for t in travas:
                        if ligar:
                            raiz.grab_key(kc, m | t, False, X.GrabModeAsync, X.GrabModeAsync, onerror=pegar)
                        else:
                            raiz.ungrab_key(kc, m | t)
            d.sync()

        while True:
            quer = sessao_na_frente() or bool(self.trocadas)
            if quer != reservado:
                reservar(quer)
                reservado = quer
            limite = time.monotonic() + 0.25
            while time.monotonic() < limite:
                if not d.pending_events():
                    time.sleep(0.01)
                    continue
                ev = d.next_event()
                if ev.type not in (X.KeyPress, X.KeyRelease) or ev.detail not in codigos:
                    continue
                vk = codigos[ev.detail]
                sub = decidir(vk, ev.type == X.KeyPress, bool(ev.state & X.Mod1Mask), bool(ev.state & X.ControlMask),
                              self.trocadas)
                if sub:
                    xtest.fake_input(d, ev.type, F13_X + (sub - VK_F13))
                    d.sync()
