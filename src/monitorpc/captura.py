"""Captura de tela: atalhos globais, seleção de área (tela congelada), área de transferência, rolagem e OCR.

Roda no ícone da bandeja, na sessão do usuário: o serviço (SYSTEM, sessão 0) não enxerga a tela.
O serviço só usa daqui as listas e a validação dos atalhos.
"""
import io
import os
import json
import time
import base64
import tempfile
import threading
import subprocess

WIN = os.name == "nt"
SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW

# ação: (título, atalho padrão)
ACOES = {
    "regiao": ("Capturar uma região", "Print"),
    "janela": ("Capturar a janela ativa", "Alt+Print"),
    "monitor": ("Capturar o monitor do mouse", ""),
    "tela": ("Capturar a tela inteira", "Ctrl+Print"),
    "ultima": ("Repetir a última região", ""),
    "atraso": ("Capturar uma região com atraso", ""),
    "rolagem": ("Captura com rolagem", ""),
    "video": ("Gravar vídeo (MP4) / parar", "Shift+Print"),
    "gif": ("Gravar GIF / parar", "Ctrl+Shift+Print"),
    "ocr": ("Copiar texto da tela (OCR)", ""),
    "cor": ("Seletor de cor", ""),
    "regua": ("Régua", ""),
}
ATALHOS_PADRAO = {k: v[1] for k, v in ACOES.items()}

MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
_ESPECIAIS = {"print": "Print", "pause": "Pause", "scrolllock": "ScrollLock", "insert": "Insert", "delete": "Delete",
              "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown", "space": "Space"}
TECLAS = {"print": 0x2C, "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
          "pageup": 0x21, "pagedown": 0x22, "space": 0x20}
TECLAS.update({chr(c).lower(): c for c in range(0x41, 0x5B)})
TECLAS.update({str(d): 0x30 + d for d in range(10)})
TECLAS.update({f"f{n}": 0x6F + n for n in range(1, 25)})


def ler_atalho(txt):
    """'Ctrl+Shift+Print' -> (modificadores, tecla virtual, texto normalizado) ou None se inválido.
    Letras, números e espaço exigem Ctrl, Alt ou Win (senão o atalho roubaria a digitação)."""
    partes = [p.strip().lower() for p in str(txt or "").split("+") if p.strip()]
    if not partes:
        return None
    *mods, tecla = partes
    if tecla not in TECLAS or any(m not in MODS for m in mods) or len(set(mods)) != len(mods):
        return None
    m = 0
    for x in mods:
        m |= MODS[x]
    if (len(tecla) == 1 or tecla == "space") and not m & (MODS["ctrl"] | MODS["alt"] | MODS["win"]):
        return None
    nome = _ESPECIAIS.get(tecla) or tecla.upper()
    return m, TECLAS[tecla], "+".join([x.capitalize() for x in ("ctrl", "alt", "shift", "win") if x in mods] + [nome])


def validar_atalhos(d):
    """Um atalho por ação; inválido ou repetido vira vazio (desligado). Ação ausente fica com o padrão."""
    out, usados = {}, set()
    for k, padrao in ATALHOS_PADRAO.items():
        r = ler_atalho(d.get(k, padrao))
        txt = r[2] if r else ""
        if txt and txt.lower() in usados:
            txt = ""
        if txt:
            usados.add(txt.lower())
        out[k] = txt
    return out


# ================================================================ Windows
if WIN:
    import ctypes
    from ctypes import wintypes as wt

    _u = ctypes.WinDLL("user32", use_last_error=True)
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _dwm = ctypes.WinDLL("dwmapi")

    class _MSG(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                    ("time", wt.DWORD), ("pt", wt.POINT)]

    class _MONINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    _u.GetForegroundWindow.restype = wt.HWND
    _u.GetAncestor.restype = wt.HWND
    _u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    _u.WindowFromPoint.restype = wt.HWND
    _u.WindowFromPoint.argtypes = [wt.POINT]
    _u.MonitorFromPoint.restype = wt.HANDLE
    _u.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
    _u.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(_MONINFO)]
    _u.IsWindowVisible.argtypes = [wt.HWND]
    _u.IsIconic.argtypes = [wt.HWND]
    _u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    _u.SetForegroundWindow.argtypes = [wt.HWND]
    _u.GetParent.restype = wt.HWND
    _u.GetParent.argtypes = [wt.HWND]
    _u.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    _u.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    _u.GetMessageW.argtypes = [ctypes.POINTER(_MSG), wt.HWND, wt.UINT, wt.UINT]
    _u.PeekMessageW.argtypes = [ctypes.POINTER(_MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
    _u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u.OpenClipboard.argtypes = [wt.HWND]
    _u.SetClipboardData.restype = wt.HANDLE
    _u.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    _u.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
    _u.RegisterClipboardFormatW.restype = wt.UINT
    _u.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
    _k.GlobalAlloc.restype = wt.HGLOBAL
    _k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    _k.GlobalLock.restype = ctypes.c_void_p
    _k.GlobalLock.argtypes = [wt.HGLOBAL]
    _k.GlobalUnlock.argtypes = [wt.HGLOBAL]
    _k.GlobalFree.argtypes = [wt.HGLOBAL]
    _dwm.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    _ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def dpi_por_monitor():
    """Coordenadas em pixels reais em todos os monitores (sem isso a captura sai deslocada com escala 125%/150%).
    Precisa ser chamado antes de criar qualquer janela."""
    if not WIN:
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def tela_virtual():
    """(x, y, largura, altura) de todos os monitores juntos."""
    g = ctypes.windll.user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def _ret_janela(h):
    r = wt.RECT()
    if _dwm.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # DWMWA_EXTENDED_FRAME_BOUNDS (sem a sombra)
        _u.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def janela_ativa():
    h = _u.GetForegroundWindow()
    if not h:
        return None
    return _ret_janela(_u.GetAncestor(h, 2) or h)  # GA_ROOT


def monitor_do_mouse():
    pt = wt.POINT()
    _u.GetCursorPos(ctypes.byref(pt))
    mi = _MONINFO()
    mi.cbSize = ctypes.sizeof(mi)
    _u.GetMonitorInfoW(_u.MonitorFromPoint(pt, 2), ctypes.byref(mi))
    m = mi.rcMonitor
    return m.left, m.top, m.right, m.bottom


def janelas_visiveis():
    """Retângulos das janelas visíveis, da que está na frente para a de trás (para o "imã" da seleção)."""
    out = []

    def cb(h, _l):
        try:
            if not _u.IsWindowVisible(h) or _u.IsIconic(h):
                return True
            oculta = wt.DWORD(0)
            if _dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(oculta), 4) == 0 and oculta.value:  # DWMWA_CLOAKED
                return True
            r = _ret_janela(h)
            if r[2] - r[0] >= 20 and r[3] - r[1] >= 20:
                out.append(r)
        except Exception:
            pass
        return True

    _u.EnumWindows(_ENUM(cb), 0)
    return out


def capturar(bbox=None):
    """Imagem RGB da tela. bbox = (esq, topo, dir, baixo) em coordenadas da tela; None = todos os monitores.
    Devolve (imagem, x, y) com a posição do canto da imagem na tela."""
    from PIL import ImageGrab
    x, y, w, h = tela_virtual()
    img = ImageGrab.grab(all_screens=True, include_layered_windows=True).convert("RGB")
    if bbox:
        l, t, r, b = max(bbox[0], x), max(bbox[1], y), min(bbox[2], x + w), min(bbox[3], y + h)
        if r - l < 1 or b - t < 1:
            raise RuntimeError("a área escolhida está fora da tela")
        return img.crop((l - x, t - y, r - x, b - y)), l, t
    return img, x, y


# ================================================================ seleção na tela congelada
def selecionar(img, ox, oy, modo="regiao", janelas=()):
    """Mostra a imagem por cima de tudo e deixa o usuário escolher.
    regiao -> (l, t, r, b) relativo à imagem | cor -> (r, g, b) | regua -> (dx, dy) | None se cancelou (Esc / botão direito)."""
    try:
        return _selecionar(img, ox, oy, modo, janelas)
    finally:
        coletar_tk()


def coletar_tk():
    """A janela Tk tem referências circulares; se o coletor de lixo a apagar em outra thread, a Tcl derruba o processo
    ("Tcl_AsyncDelete: async handler deleted by the wrong thread"). Coleta aqui, na thread que criou a janela."""
    import gc
    gc.collect()


def _selecionar(img, ox, oy, modo, janelas):
    import tkinter as tk
    from PIL import Image, ImageDraw, ImageEnhance, ImageTk

    W, H = img.size
    res = {"v": None}
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{W}x{H}+{ox}+{oy}")
    cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, cursor="crosshair", bg="black")
    cv.pack()
    # master=root sempre: com o mini painel aberto existe outro Tk (em outra thread) e a imagem iria para ele
    fundo = ImageTk.PhotoImage(ImageEnhance.Brightness(img).enhance(0.55) if modo == "regiao" else img, master=root)
    cv.create_image(0, 0, anchor="nw", image=fundo)
    fotos = {}
    claro = cv.create_image(0, 0, anchor="nw")
    borda = cv.create_rectangle(0, 0, 0, 0, outline="#4ea1ff", width=2, state="hidden")
    linha = cv.create_line(0, 0, 0, 0, fill="#ffcc00", width=2, state="hidden")
    rot_f = cv.create_rectangle(0, 0, 0, 0, fill="#101418", outline="", state="hidden")
    rot = cv.create_text(0, 0, anchor="nw", fill="#e6ebf0", font=("Segoe UI", 10, "bold"), state="hidden")
    lupa = cv.create_image(0, 0, anchor="nw", state="hidden")
    lupa_f = cv.create_rectangle(0, 0, 0, 0, fill="#101418", outline="#4ea1ff", state="hidden")
    lupa_t = cv.create_text(0, 0, anchor="nw", fill="#e6ebf0", font=("Consolas", 9), state="hidden")
    ajuda = {"regiao": "Arraste para escolher a área  ·  clique numa janela para pegá-la inteira  ·  Esc cancela",
             "cor": "Clique na cor  ·  Esc cancela", "regua": "Arraste para medir  ·  Esc cancela"}[modo]
    cv.create_rectangle(W // 2 - 330, 8, W // 2 + 330, 36, fill="#101418", outline="")
    cv.create_text(W // 2, 22, text=ajuda, fill="#e6ebf0", font=("Segoe UI", 10))
    est = {"ini": None, "hover": None}

    def rotulo(x, y, txt):
        cv.itemconfigure(rot, text=txt, state="normal")
        cv.coords(rot, x + 6, y + 3)
        bx = cv.bbox(rot)
        cv.coords(rot_f, bx[0] - 6, bx[1] - 3, bx[2] + 6, bx[3] + 3)
        cv.itemconfigure(rot_f, state="normal")
        cv.tag_raise(rot_f)
        cv.tag_raise(rot)

    def realce(l, t, r, b):
        l, t, r, b = max(0, l), max(0, t), min(W, r), min(H, b)
        if r - l < 1 or b - t < 1:
            return
        if modo == "regiao":
            fotos["claro"] = ImageTk.PhotoImage(img.crop((l, t, r, b)), master=root)
            cv.itemconfigure(claro, image=fotos["claro"])
            cv.coords(claro, l, t)
        cv.coords(borda, l, t, r, b)
        cv.itemconfigure(borda, state="normal")
        ry = t - 26 if t >= 30 else t + 4
        rotulo(l, ry, f"{r - l} × {b - t}")

    def mostrar_lupa(x, y):
        raio, zoom = 8, 9
        lado = (raio * 2 + 1) * zoom
        z = img.crop((x - raio, y - raio, x + raio + 1, y + raio + 1)).resize((lado, lado), Image.NEAREST)
        d = ImageDraw.Draw(z)
        d.rectangle([raio * zoom, raio * zoom, raio * zoom + zoom - 1, raio * zoom + zoom - 1], outline=(255, 60, 60))
        fotos["lupa"] = ImageTk.PhotoImage(z, master=root)
        lx = x + 24 if x + 24 + lado < W else x - 24 - lado
        ly = y + 24 if y + 24 + lado + 36 < H else y - 24 - lado - 36
        cr = img.getpixel((min(max(x, 0), W - 1), min(max(y, 0), H - 1)))
        cv.itemconfigure(lupa, image=fotos["lupa"], state="normal")
        cv.coords(lupa, lx, ly)
        cv.coords(lupa_f, lx - 1, ly - 1, lx + lado + 1, ly + lado + 34)
        cv.itemconfigure(lupa_f, state="normal")
        cv.itemconfigure(lupa_t, text=f"X {x + ox}  Y {y + oy}\n#{cr[0]:02X}{cr[1]:02X}{cr[2]:02X}  rgb {cr[0]},{cr[1]},{cr[2]}",
                         state="normal")
        cv.coords(lupa_t, lx + 4, ly + lado + 3)
        for i in (lupa_f, lupa, lupa_t):
            cv.tag_raise(i)

    def mover(e):
        mostrar_lupa(e.x, e.y)
        if modo == "regiao" and est["ini"] is None:
            px, py = e.x + ox, e.y + oy
            alvo = next((r for r in janelas if r[0] <= px < r[2] and r[1] <= py < r[3]), None)
            if alvo != est["hover"]:
                est["hover"] = alvo
                if alvo:
                    realce(alvo[0] - ox, alvo[1] - oy, alvo[2] - ox, alvo[3] - oy)

    def apertar(e):
        est["ini"] = (e.x, e.y)

    def arrastar(e):
        mostrar_lupa(e.x, e.y)
        if est["ini"] is None:
            return
        x0, y0 = est["ini"]
        if modo == "regiao":
            realce(min(x0, e.x), min(y0, e.y), max(x0, e.x) + 1, max(y0, e.y) + 1)
        elif modo == "regua":
            cv.coords(linha, x0, y0, e.x, e.y)
            cv.itemconfigure(linha, state="normal")
            dx, dy = abs(e.x - x0) + 1, abs(e.y - y0) + 1
            rotulo(e.x + 12, e.y - 30, f"{dx} × {dy} px  ·  distância {((dx - 1) ** 2 + (dy - 1) ** 2) ** 0.5:.0f} px")

    def soltar(e):
        if est["ini"] is None:
            return
        x0, y0 = est["ini"]
        est["ini"] = None
        arrastou = abs(e.x - x0) > 3 or abs(e.y - y0) > 3
        if modo == "cor":
            res["v"] = img.getpixel((min(max(e.x, 0), W - 1), min(max(e.y, 0), H - 1)))
        elif modo == "regua":
            if not arrastou:
                return
            res["v"] = (abs(e.x - x0) + 1, abs(e.y - y0) + 1)
        elif arrastou:
            res["v"] = (max(0, min(x0, e.x)), max(0, min(y0, e.y)), min(W, max(x0, e.x) + 1), min(H, max(y0, e.y) + 1))
        elif est["hover"]:
            r = est["hover"]
            res["v"] = (max(0, r[0] - ox), max(0, r[1] - oy), min(W, r[2] - ox), min(H, r[3] - oy))
        else:
            return
        root.destroy()

    def cancelar(_e=None):
        res["v"] = None
        root.destroy()

    cv.bind("<Motion>", mover)
    cv.bind("<ButtonPress-1>", apertar)
    cv.bind("<B1-Motion>", arrastar)
    cv.bind("<ButtonRelease-1>", soltar)
    cv.bind("<ButtonPress-3>", cancelar)
    root.bind("<Escape>", cancelar)

    def focar():
        try:
            h = _u.GetParent(root.winfo_id()) or root.winfo_id()
            _u.keybd_event(0x12, 0, 0, 0)   # Alt: o Windows só deixa trazer para frente depois de uma tecla
            _u.keybd_event(0x12, 0, 2, 0)
            _u.SetForegroundWindow(h)
        except Exception:
            pass
        root.focus_force()

    root.after(30, focar)
    root.mainloop()
    return res["v"]


# ================================================================ captura com rolagem
def _linhas(img):
    g = img.convert("L")
    w = g.width
    dados = g.tobytes()
    return [hash(dados[i * w:(i + 1) * w]) for i in range(g.height)]


def _deslocamento(a, b):
    """Quantas linhas novas o quadro b trouxe em relação ao a (0 = nada mudou; None = não achou a emenda).
    Ignora faixas fixas no topo e no rodapé (cabeçalhos que não rolam)."""
    h = len(a)
    topo = 0
    while topo < h and a[topo] == b[topo]:
        topo += 1
    if topo == h:
        return 0, 0, 0
    base = 0
    while base < h - topo and a[h - 1 - base] == b[h - 1 - base]:
        base += 1
    m = h - topo - base
    minimo = max(8, m // 5)
    for s in range(1, m - minimo + 1):
        if a[topo + s] == b[topo] and a[topo + s:h - base] == b[topo:h - base - s]:
            return s, topo, base
    return None, topo, base


def rolagem(bbox, max_quadros=60, max_altura=30000):
    """Rola a janela debaixo da área e emenda os quadros numa imagem só."""
    from PIL import Image
    l, t, r, b = bbox
    pt = wt.POINT((l + r) // 2, (t + b) // 2)
    alvo = _u.GetAncestor(_u.WindowFromPoint(pt), 2)
    if alvo:
        _u.keybd_event(0x12, 0, 0, 0)
        _u.keybd_event(0x12, 0, 2, 0)
        _u.SetForegroundWindow(alvo)
    _u.SetCursorPos(pt.x, pt.y)
    time.sleep(0.3)
    quadro = capturar(bbox)[0]
    pedacos = [quadro]
    ant = _linhas(quadro)
    base_final = 0
    altura = quadro.height
    for _ in range(max_quadros):
        _u.mouse_event(0x0800, 0, 0, (-120 * 3) & 0xFFFFFFFF, 0)  # MOUSEEVENTF_WHEEL, 3 "cliques" para baixo
        time.sleep(0.35)
        novo = capturar(bbox)[0]
        lin = _linhas(novo)
        s, _topo, base = _deslocamento(ant, lin)
        if not s:
            break  # chegou ao fim (nada mudou) ou não achou a emenda
        h = novo.height
        pedacos.append(novo.crop((0, h - base - s, novo.width, h - base)))
        base_final = base
        altura += s
        ant = lin
        if altura >= max_altura:
            break
    if len(pedacos) == 1:
        return quadro
    primeiro = pedacos[0]
    corte = primeiro.height - base_final       # o rodapé fixo vai só no fim
    total = corte + sum(p.height for p in pedacos[1:]) + base_final
    out = Image.new("RGB", (primeiro.width, total))
    out.paste(primeiro.crop((0, 0, primeiro.width, corte)), (0, 0))
    y = corte
    for p in pedacos[1:]:
        out.paste(p, (0, y))
        y += p.height
    if base_final:
        out.paste(primeiro.crop((0, corte, primeiro.width, primeiro.height)), (0, y))
    return out


# ================================================================ área de transferência
def _area_transferencia(itens):
    """itens = [(formato, bytes)]. Formatos: 8 = imagem (DIB), 13 = texto Unicode, ou um registrado ("PNG")."""
    for _ in range(20):
        if _u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("a área de transferência está ocupada por outro programa")
    try:
        _u.EmptyClipboard()
        for fmt, dados in itens:
            h = _k.GlobalAlloc(0x0002, len(dados))  # GMEM_MOVEABLE
            p = _k.GlobalLock(h)
            ctypes.memmove(p, dados, len(dados))
            _k.GlobalUnlock(h)
            if not _u.SetClipboardData(fmt, h):
                _k.GlobalFree(h)
    finally:
        _u.CloseClipboard()


def copiar_imagem(img):
    bmp = io.BytesIO()
    img.convert("RGB").save(bmp, "BMP")
    png = io.BytesIO()
    img.save(png, "PNG")
    _area_transferencia([(8, bmp.getvalue()[14:]), (_u.RegisterClipboardFormatW("PNG"), png.getvalue())])


def copiar_texto(txt):
    _area_transferencia([(13, (str(txt) + "\0").encode("utf-16-le"))])


# ================================================================ tecla Print Screen do Windows
_TECLADO = r"Control Panel\Keyboard"
_VALOR_PRINT = "PrintScreenKeyForSnippingEnabled"


def liberar_printscreen(pasta):
    """No Windows 11 a tecla Print Screen abre a Ferramenta de Captura e o atalho não registra.
    Desliga essa opção (só deste usuário) e guarda o valor antigo para restaurar depois."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _TECLADO) as k:
        try:
            antes = winreg.QueryValueEx(k, _VALOR_PRINT)[0]
        except FileNotFoundError:
            antes = None
        if antes == 0:
            return False
        arq = os.path.join(pasta, "printscreen.json")
        if not os.path.exists(arq):
            os.makedirs(pasta, exist_ok=True)
            with open(arq, "w", encoding="utf-8") as f:
                json.dump({"antes": antes}, f)
        winreg.SetValueEx(k, _VALOR_PRINT, 0, winreg.REG_DWORD, 0)
    return True


def restaurar_printscreen(pasta):
    import winreg
    arq = os.path.join(pasta, "printscreen.json")
    if not os.path.exists(arq):
        return False
    try:
        with open(arq, encoding="utf-8") as f:
            antes = json.load(f).get("antes")
    except (OSError, ValueError):
        antes = None
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _TECLADO) as k:
        if antes is None:
            try:
                winreg.DeleteValue(k, _VALOR_PRINT)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, _VALOR_PRINT, 0, winreg.REG_DWORD, int(antes))
    os.remove(arq)
    return True


# ================================================================ atalhos globais
class Atalhos:
    """RegisterHotKey numa thread com fila de mensagens própria (os atalhos pertencem à thread que registrou)."""
    WM_HOTKEY, WM_APP = 0x0312, 0x8000

    def __init__(self, ao_apertar):
        self.ao_apertar = ao_apertar
        self.desejado = {}
        self.estado = {}
        self.ids = {}
        self.tid = None
        self.registrado = threading.Event()
        pronto = threading.Event()
        threading.Thread(target=self._loop, args=(pronto,), name="atalhos", daemon=True).start()
        pronto.wait(3)

    def definir(self, mapa):
        self.desejado = dict(mapa)
        self.registrado.clear()
        if self.tid:
            _u.PostThreadMessageW(self.tid, self.WM_APP + 1, 0, 0)

    def _registrar(self):
        for n in list(self.ids):
            _u.UnregisterHotKey(None, n)
        self.ids, est = {}, {}
        for n, (acao, txt) in enumerate(sorted(self.desejado.items()), start=1):
            r = ler_atalho(txt) if txt else None
            if not r:
                continue
            if _u.RegisterHotKey(None, n, r[0] | 0x4000, r[1]):  # MOD_NOREPEAT
                self.ids[n] = acao
                est[acao] = "ok"
            else:
                est[acao] = "em uso por outro programa"
        self.estado = est
        self.registrado.set()

    def _loop(self, pronto):
        msg = _MSG()
        _u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # cria a fila de mensagens da thread
        self.tid = _k.GetCurrentThreadId()
        pronto.set()
        while _u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                acao = self.ids.get(msg.wParam)
                if acao:
                    threading.Thread(target=self.ao_apertar, args=(acao,), daemon=True).start()
            elif msg.message == self.WM_APP + 1:
                self._registrar()


# ================================================================ OCR (o do próprio Windows, sem baixar nada)
_OCR_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Esperar($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $null = $task.Wait(-1); $task.Result }
$motor = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $motor) { [Console]::Error.WriteLine('SEM_IDIOMA'); exit 3 }
$arq = Esperar ([Windows.Storage.StorageFile]::GetFileFromPathAsync($args[0])) ([Windows.Storage.StorageFile])
$fluxo = Esperar ($arq.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec = Esperar ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($fluxo)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Esperar ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$res = Esperar ($motor.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
$fluxo.Dispose()
foreach ($l in $res.Lines) { [Console]::Out.WriteLine($l.Text) }
"""


def ocr(img):
    """Texto da imagem pelo OCR do Windows (Windows.Media.Ocr), nos idiomas do usuário."""
    from PIL import Image
    if img.height < 80:  # texto pequeno: ampliar melhora muito o reconhecimento
        f = max(2, 80 // max(1, img.height))
        img = img.resize((img.width * f, img.height * f), Image.LANCZOS)
    if max(img.size) > 2600:
        img.thumbnail((2600, 2600))
    pasta = tempfile.mkdtemp(prefix="mpc-ocr-")
    try:
        png, ps = os.path.join(pasta, "img.png"), os.path.join(pasta, "ocr.ps1")
        img.save(png)
        with open(ps, "w", encoding="utf-8-sig") as f:
            f.write(_OCR_PS)
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps, png],
                           capture_output=True, timeout=60, creationflags=SEM_JANELA)
        if r.returncode == 3:
            raise RuntimeError("o Windows não tem o pacote de OCR de nenhum idioma instalado (Configurações > Hora e idioma > Idioma)")
        if r.returncode != 0:
            raise RuntimeError("o OCR do Windows falhou: " + r.stderr.decode("utf-8", "replace").strip()[:200])
        return r.stdout.decode("utf-8", "replace").strip()
    finally:
        for n in ("img.png", "ocr.ps1"):
            try:
                os.remove(os.path.join(pasta, n))
            except OSError:
                pass
        try:
            os.rmdir(pasta)
        except OSError:
            pass


# ================================================================ arquivos
def pasta_imagens():
    """Pasta Imagens do usuário (segue o redirecionamento do OneDrive)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            p = os.path.expandvars(winreg.QueryValueEx(k, "My Pictures")[0])
            if os.path.isdir(p):
                return p
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "Pictures")


def nome_livre(pasta, base, ext):
    caminho = os.path.join(pasta, base + ext)
    n = 2
    while os.path.exists(caminho):
        caminho = os.path.join(pasta, f"{base}_{n}{ext}")
        n += 1
    return caminho


def nome_arquivo(c, ext):
    pasta = c.get("captura_pasta") or ""
    if not os.path.isabs(pasta):
        pasta = os.path.join(pasta_imagens(), "Monitor do PC")
    os.makedirs(pasta, exist_ok=True)
    try:
        base = time.strftime(c.get("captura_nome") or "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        base = time.strftime("%Y-%m-%d_%H-%M-%S")
    base = "".join(ch for ch in base if ch not in '<>:"/\\|?*').strip(" .") or "captura"
    return nome_livre(pasta, base, ext)


def salvar_imagem(img, caminho, c):
    if caminho.lower().endswith((".jpg", ".jpeg")):
        img.convert("RGB").save(caminho, "JPEG", quality=int(c.get("captura_qualidade") or 90), optimize=True)
    else:
        img.save(caminho, "PNG", optimize=False)


def para_lixeira(caminho):
    """Manda o arquivo para a Lixeira (dá para desfazer), sem perguntar."""
    class _OP(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND), ("wFunc", wt.UINT), ("pFrom", wt.LPCWSTR), ("pTo", wt.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wt.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wt.LPCWSTR)]
    op = _OP(None, 3, os.path.abspath(caminho) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, 0, None, None)
    # FO_DELETE; FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(caminho)


def miniatura(img):
    """JPEG pequeno em base64 para a grade do histórico."""
    m = img.convert("RGB")
    m.thumbnail((360, 220))
    b = io.BytesIO()
    m.save(b, "JPEG", quality=72)
    return base64.b64encode(b.getvalue()).decode()
