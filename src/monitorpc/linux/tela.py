"""Tela de quem é atendido no Linux, para o agente do acesso remoto: monitores, captura e mouse/teclado.

- X11 (Mint, "Ubuntu on Xorg", KDE X11...): monitores pelo XRandR, captura com mss (XShm) e entrada pelo XTest.
- Wayland (Ubuntu e Fedora padrão, KDE Plasma 6): o sistema não deixa programa nenhum ver a tela ou mexer no mouse
  por conta própria. Usamos o portal RemoteDesktop + ScreenCast (xdg-desktop-portal), o mesmo do GNOME Remote
  Desktop e do RustDesk: o próprio sistema pergunta à pessoa o que compartilhar a cada sessão. A imagem chega pelo
  PipeWire (lida por um GStreamer separado) e o mouse/teclado vão pelo portal.

Os eventos chegam no formato do Windows (remoto_agente.traduzir): mouse em 0..65535 da área de trabalho inteira e
teclas por scancode (conjunto 1). Aqui viram coordenadas em pixels e códigos de tecla do Linux (evdev)."""
import os
import time
import shutil
import threading
import subprocess

M_MOVER, M_ABSOLUTO, M_RODA = 0x0001, 0x8000, 0x0800
M_BOTAO = {0x0002: (1, True), 0x0004: (1, False), 0x0020: (2, True), 0x0040: (2, False), 0x0008: (3, True), 0x0010: (3, False)}
T_ESTENDIDA, T_SOBE = 0x0001, 0x0002
# scancode estendido (E0 xx) -> código evdev; os outros são iguais ao scancode, menos estes
E0_EVDEV = {0x1C: 96, 0x1D: 97, 0x35: 98, 0x37: 99, 0x38: 100, 0x47: 102, 0x48: 103, 0x49: 104, 0x4B: 105, 0x4D: 106,
            0x4F: 107, 0x50: 108, 0x51: 109, 0x52: 110, 0x53: 111, 0x5B: 125, 0x5C: 126, 0x5D: 127}
SCAN_EVDEV = {0x73: 89, 0x7E: 121}
BTN_EVDEV = {1: 0x110, 2: 0x112, 3: 0x111}          # BTN_LEFT, BTN_MIDDLE, BTN_RIGHT


def evdev(scan, flags):
    """Scancode (conjunto 1) -> código de tecla do kernel (evdev). None se não houver."""
    if flags & T_ESTENDIDA:
        return E0_EVDEV.get(scan)
    if scan in SCAN_EVDEV:
        return SCAN_EVDEV[scan]
    return scan if 1 <= scan <= 0x58 else None


def area_virtual(monitores):
    x0, y0 = min(m["x"] for m in monitores), min(m["y"] for m in monitores)
    x1, y1 = max(m["x"] + m["w"] for m in monitores), max(m["y"] + m["h"] for m in monitores)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def pixel(monitores, x, y):
    """0..65535 da área inteira -> pixel (o inverso exato de remoto_agente._no_monitor)."""
    vx, vy, vw, vh = area_virtual(monitores)
    return vx + x * vw // 65536, vy + y * vh // 65536


def wayland():
    """Sessão Wayland? GNOME, KDE, Cinnamon e XFCE sempre informam XDG_SESSION_TYPE; sem ele (WSLg, sessões
    montadas à mão), havendo X disponível, usamos o X11."""
    tipo = os.environ.get("XDG_SESSION_TYPE")
    if tipo in ("wayland", "x11"):
        return tipo == "wayland"
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


# ================================================================ X11
class X11:
    """Monitores, captura e entrada numa sessão X11. Cada thread usa a própria conexão com o servidor X."""

    def __init__(self):
        self._local = threading.local()

    def _dpy(self):
        d = getattr(self._local, "dpy", None)
        if d is None:
            from Xlib import display
            d = self._local.dpy = display.Display()
        return d

    def monitores(self):
        from Xlib.ext import randr
        d = self._dpy()
        raiz = d.screen().root
        out = []
        try:
            prim = randr.get_output_primary(raiz).output
            for m in randr.get_monitors(raiz, is_active=True).monitors:
                out.append({"x": m.x, "y": m.y, "w": m.width_in_pixels, "h": m.height_in_pixels,
                            "principal": bool(m.primary) or prim in list(m.crtcs or [])})
        except Exception:  # noqa: BLE001  (servidor X sem RandR 1.5)
            out = []
        if not out:
            s = d.screen()
            out = [{"x": 0, "y": 0, "w": s.width_in_pixels, "h": s.height_in_pixels, "principal": True}]
        if not any(m["principal"] for m in out):
            out[0]["principal"] = True
        out.sort(key=lambda m: (not m["principal"], m["x"], m["y"]))
        return out

    def foto_tudo(self):
        """A área de trabalho inteira (todos os monitores) numa imagem só."""
        from PIL import Image
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss
            sct = self._local.sct = mss.mss()
        g = sct.grab(sct.monitors[0])
        return Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX")

    def enviar(self, ev, monitores):
        from Xlib import X
        from Xlib.ext import xtest
        d = self._dpy()
        if ev[0] == "mouse":
            _t, x, y, dado, flags = ev
            if x is not None and flags & M_ABSOLUTO:
                px, py = pixel(monitores, x, y)
                xtest.fake_input(d, X.MotionNotify, x=px, y=py)
            for bit, (b, baixa) in M_BOTAO.items():
                if flags & bit:
                    xtest.fake_input(d, X.ButtonPress if baixa else X.ButtonRelease, b)
            if flags & M_RODA and dado:
                b = 4 if dado > 0 else 5
                for _ in range(max(1, abs(int(dado)) // 120)):
                    xtest.fake_input(d, X.ButtonPress, b)
                    xtest.fake_input(d, X.ButtonRelease, b)
        else:
            _t, scan, flags = ev
            k = evdev(scan, flags)
            if k is None:
                return
            xtest.fake_input(d, X.KeyRelease if flags & T_SOBE else X.KeyPress, k + 8)   # código X = evdev + 8
        d.sync()


# ================================================================ Wayland (portal)
PORTAL, CAMINHO = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
RD, SC, REQ = "org.freedesktop.portal.RemoteDesktop", "org.freedesktop.portal.ScreenCast", "org.freedesktop.portal.Request"


class Wayland:
    """Sessão do portal RemoteDesktop com ScreenCast: um fluxo do PipeWire por monitor escolhido pela pessoa."""

    def __init__(self, pedir=True):
        self._con = None
        self._sessao = None
        self._fluxos = []          # [{"no", "x", "y", "w", "h"}] em pixels lógicos
        self._quadros = {}          # índice -> (largura, altura, bytes RGB)
        self._gst = []
        self._lock = threading.Lock()
        self._n = 0
        if pedir:
            self._iniciar()

    # --- D-Bus
    def _chamar(self, interface, metodo, assinatura, *args):
        from jeepney import new_method_call, DBusAddress, MessageType
        msg = new_method_call(DBusAddress(CAMINHO, PORTAL, interface), metodo, assinatura, args)
        r = self._con.send_and_get_reply(msg, timeout=30)
        if r.header.message_type == MessageType.error:      # sem portal, sem RemoteDesktop, recusado...
            raise OSError(f"O portal de compartilhamento de tela recusou ({metodo}): {r.body[0] if r.body else ''}")
        return r.body

    def _pedido(self, interface, metodo, assinatura, *args, espera=120):
        """Chamada que o portal responde depois (pelo sinal Response de um objeto Request)."""
        from jeepney import MatchRule
        self._n += 1
        token = f"cnt{os.getpid()}_{self._n}"
        remetente = self._con.unique_name[1:].replace(".", "_")
        caminho = f"/org/freedesktop/portal/desktop/request/{remetente}/{token}"
        regra = MatchRule(type="signal", interface=REQ, member="Response", path=caminho)
        opcoes = dict(args[-1])
        opcoes["handle_token"] = ("s", token)
        with self._con.filter(regra) as fila:
            self._chamar(interface, metodo, assinatura, *args[:-1], opcoes)
            r = self._con.recv_until_filtered(fila, timeout=espera)
        codigo, res = r.body
        if codigo != 0:
            raise OSError("A pessoa não autorizou compartilhar a tela." if codigo == 1 else "O portal recusou o acesso.")
        return res

    def _iniciar(self):
        from jeepney.io.blocking import open_dbus_connection
        self._con = open_dbus_connection(bus="SESSION", enable_fds=True)
        res = self._pedido(RD, "CreateSession", "a{sv}", {"session_handle_token": ("s", f"cnts{os.getpid()}")})
        self._sessao = res["session_handle"][1]
        self._pedido(RD, "SelectDevices", "oa{sv}", self._sessao, {"types": ("u", 3)})          # teclado + mouse
        self._pedido(SC, "SelectSources", "oa{sv}", self._sessao,
                     {"types": ("u", 1), "multiple": ("b", True), "cursor_mode": ("u", 2)})     # monitores, com o cursor
        res = self._pedido(RD, "Start", "osa{sv}", self._sessao, "", {}, espera=300)
        for no, props in res.get("streams", ("", []))[1]:
            x, y = props.get("position", ("(ii)", (0, 0)))[1]
            w, h = props.get("size", ("(ii)", (1280, 720)))[1]
            self._fluxos.append({"no": no, "x": x, "y": y, "w": w, "h": h})
        if not self._fluxos:
            raise OSError("Nenhuma tela foi compartilhada.")
        fd = self._chamar(SC, "OpenPipeWireRemote", "oa{sv}", self._sessao, {})[0]
        self._fd = fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)
        for i, f in enumerate(self._fluxos):
            self._abrir_gst(i, f)

    def _abrir_gst(self, i, f):
        """Um gst-launch por monitor: PipeWire -> RGB no tamanho lógico, um quadro depois do outro na saída."""
        gst = shutil.which("gst-launch-1.0")
        if not gst:
            raise OSError("Falta o GStreamer (pacotes gstreamer1.0-tools e gstreamer1.0-pipewire).")
        cmd = [gst, "-q", "pipewiresrc", f"fd={self._fd}", f"path={f['no']}", "always-copy=true", "!", "videoconvert", "!",
               "videoscale", "!", f"video/x-raw,format=RGB,width={f['w']},height={f['h']}", "!", "fdsink", "fd=1"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=(self._fd,), bufsize=0)
        self._gst.append(p)
        threading.Thread(target=self._ler_quadros, args=(i, f, p), daemon=True, name=f"pipewire-{i}").start()

    def _ler_quadros(self, i, f, p):
        tam = f["w"] * f["h"] * 3
        while p.poll() is None:
            buf = bytearray()
            while len(buf) < tam:
                b = p.stdout.read(tam - len(buf))
                if not b:
                    return
                buf += b
            with self._lock:
                self._quadros[i] = (f["w"], f["h"], bytes(buf))

    def monitores(self):
        return [{"x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"], "principal": i == 0} for i, f in enumerate(self._fluxos)]

    def quadro(self, i):
        from PIL import Image
        for _ in range(50):
            with self._lock:
                q = self._quadros.get(i)
            if q:
                return Image.frombytes("RGB", (q[0], q[1]), q[2])
            time.sleep(0.02)
        return Image.new("RGB", (self._fluxos[i]["w"], self._fluxos[i]["h"]))

    def enviar(self, ev, monitores):
        if ev[0] == "mouse":
            _t, x, y, dado, flags = ev
            if x is not None and flags & M_ABSOLUTO:
                px, py = pixel(monitores, x, y)
                f = next((f for f in self._fluxos if f["x"] <= px < f["x"] + f["w"] and f["y"] <= py < f["y"] + f["h"]),
                         self._fluxos[0])
                self._chamar(RD, "NotifyPointerMotionAbsolute", "oa{sv}udd", self._sessao, {}, f["no"],
                             float(px - f["x"]), float(py - f["y"]))
            for bit, (b, baixa) in M_BOTAO.items():
                if flags & bit:
                    self._chamar(RD, "NotifyPointerButton", "oa{sv}iu", self._sessao, {}, BTN_EVDEV[b], 1 if baixa else 0)
            if flags & M_RODA and dado:
                self._chamar(RD, "NotifyPointerAxisDiscrete", "oa{sv}ui", self._sessao, {}, 0,
                             -max(1, abs(int(dado)) // 120) if dado > 0 else max(1, abs(int(dado)) // 120))
        else:
            _t, scan, flags = ev
            k = evdev(scan, flags)
            if k is not None:
                self._chamar(RD, "NotifyKeyboardKeycode", "oa{sv}iu", self._sessao, {}, k, 0 if flags & T_SOBE else 1)

    def fechar(self):
        for p in self._gst:
            try:
                p.kill()
            except OSError:
                pass
        if self._sessao and self._con:
            try:
                from jeepney import new_method_call, DBusAddress
                self._con.send_and_get_reply(new_method_call(
                    DBusAddress(self._sessao, PORTAL, "org.freedesktop.portal.Session"), "Close"), timeout=5)
            except Exception:  # noqa: BLE001
                pass


# ================================================================ o que o agente usa
_x11 = None
_wl = None
_lock_ini = threading.Lock()


_erro_wl = []


def plataforma():
    """X11 ou Wayland da sessão em que o agente está. No Wayland a pessoa autoriza na tela (uma vez por sessão);
    se ela recusar (ou não houver portal), o erro fica guardado e não pergunta de novo a cada quadro."""
    global _x11, _wl
    with _lock_ini:
        if wayland():
            if _erro_wl:
                raise OSError(_erro_wl[0])
            if _wl is None:
                try:
                    _wl = Wayland()
                except Exception as e:  # noqa: BLE001
                    _erro_wl.append(str(e) or "O portal de compartilhamento de tela não respondeu.")
                    raise OSError(_erro_wl[0]) from None
            return _wl
        if _x11 is None:
            _x11 = X11()
        return _x11


def listar_monitores():
    return plataforma().monitores()


class Telas:
    """Mesma interface do Windows: quadro(i) -> imagem do monitor i."""

    def __init__(self, monitores, foto=None):
        self.monitores = monitores
        self._p = None
        self._lock = threading.Lock()
        self._img, self._ts = None, 0.0

    def quadro(self, i):
        if self._p is None:
            self._p = plataforma()
        if isinstance(self._p, Wayland):
            return self._p.quadro(i)
        with self._lock:
            agora = time.monotonic()
            if self._img is None or agora - self._ts > 0.025:
                self._img, self._ts = self._p.foto_tudo(), agora
            img = self._img
        vx, vy, _, _ = area_virtual(self.monitores)
        m = self.monitores[i]
        return img.crop((m["x"] - vx, m["y"] - vy, m["x"] - vx + m["w"], m["y"] - vy + m["h"]))


def enviar_evento(ev, monitores):
    plataforma().enviar(ev, monitores)


def bloquear():
    for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
        if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0:
            return ""
    return "O Linux deste computador não deixou bloquear a tela."


def fechar():
    if _wl is not None:
        _wl.fechar()


def pastas_do_usuario():
    """Área de Trabalho, Documentos e Downloads de quem é atendido (xdg-user-dir: segue o idioma do sistema)."""
    out = {}
    for nome, chave in (("Área de Trabalho", "DESKTOP"), ("Documentos", "DOCUMENTS"), ("Downloads", "DOWNLOAD")):
        p = ""
        if shutil.which("xdg-user-dir"):
            try:
                p = subprocess.run(["xdg-user-dir", chave], capture_output=True, text=True, timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                p = ""
        if p and os.path.isdir(p) and p != os.path.expanduser("~"):
            out[nome] = p
    out.setdefault("Downloads", os.path.join(os.path.expanduser("~"), "Downloads"))
    out["Pasta pessoal"] = os.path.expanduser("~")
    return out


def unidades():
    """A raiz e os discos montados (pendrives, outras partições) que a pessoa enxerga."""
    import psutil
    out = ["/"]
    try:
        for p in psutil.disk_partitions(all=False):
            if p.mountpoint.startswith(("/media/", "/mnt/", "/run/media/")) and p.mountpoint not in out:
                out.append(p.mountpoint)
    except Exception:  # noqa: BLE001
        pass
    return out
