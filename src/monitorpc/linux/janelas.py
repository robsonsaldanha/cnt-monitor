"""Janelas dos programas no Linux (ícone da bandeja): listar por processo, trazer para a frente, pedir para fechar
e saber qual está na frente. Pelo padrão EWMH do X11, que GNOME, KDE, Cinnamon, XFCE e MATE seguem. No Wayland
só aparecem as janelas que rodam pelo XWayland (o Wayland não deixa um programa ver as janelas dos outros)."""
import threading

_local = threading.local()


def _dpy():
    d = getattr(_local, "d", None)
    if d is None:
        from Xlib import display
        d = _local.d = display.Display()
    return d


def _prop(jan, nome, tipo=None):
    from Xlib import X
    d = _dpy()
    p = jan.get_full_property(d.intern_atom(nome), tipo if tipo is not None else X.AnyPropertyType)
    return p.value if p else None


def listar_janelas():
    """{pid: [(id da janela, título, minimizada, área)]} das janelas normais (as que aparecem na barra)."""
    from collections import defaultdict
    out = defaultdict(list)
    try:
        d = _dpy()
        raiz = d.screen().root
        utf8 = d.intern_atom("UTF8_STRING")
        oculta = d.intern_atom("_NET_WM_STATE_HIDDEN")
        pula = d.intern_atom("_NET_WM_STATE_SKIP_TASKBAR")
        for wid in _prop(raiz, "_NET_CLIENT_LIST") or []:
            try:
                j = d.create_resource_object("window", wid)
                estado = list(_prop(j, "_NET_WM_STATE") or [])
                if pula in estado:
                    continue
                pid = (_prop(j, "_NET_WM_PID") or [0])[0]
                t = _prop(j, "_NET_WM_NAME", utf8)
                titulo = t.decode("utf-8", "replace") if isinstance(t, bytes) else (j.get_wm_name() or "")
                g = j.get_geometry()
                out[int(pid)].append((wid, titulo, oculta in estado, g.width * g.height))
            except Exception:  # noqa: BLE001  (janela fechou no meio)
                continue
    except Exception:  # noqa: BLE001  (sem servidor X)
        pass
    return out


def _mensagem(wid, tipo, dados):
    from Xlib import X, protocol
    d = _dpy()
    raiz = d.screen().root
    ev = protocol.event.ClientMessage(window=d.create_resource_object("window", wid), client_type=d.intern_atom(tipo),
                                      data=(32, (dados + [0] * 5)[:5]))
    raiz.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()


def trazer_para_frente(wid):
    _mensagem(wid, "_NET_ACTIVE_WINDOW", [2, 0, 0])        # 2 = pedido de um "pager" (o gerenciador obedece)


def fechar(wid):
    _mensagem(wid, "_NET_CLOSE_WINDOW", [0, 2])              # igual a clicar no X


def pid_da_frente():
    try:
        d = _dpy()
        w = (_prop(d.screen().root, "_NET_ACTIVE_WINDOW") or [0])[0]
        if w:
            return int((_prop(d.create_resource_object("window", w), "_NET_WM_PID") or [0])[0]) or None
    except Exception:  # noqa: BLE001
        pass
    return None


def monitores():
    """[(esquerda, topo, direita, base)] de cada monitor, o principal primeiro."""
    try:
        from .tela import X11
        return [(m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"]) for m in X11().monitores()]
    except Exception:  # noqa: BLE001
        return [(0, 0, 1920, 1080)]
