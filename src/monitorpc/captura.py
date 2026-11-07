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
    "regioes": ("Capturar várias regiões", ""),
    "qr": ("Ler código QR da tela", ""),
    "fixar": ("Fixar uma região na tela", ""),
    "pausar": ("Pausar / continuar a gravação", ""),
    "enviar_arquivo": ("Enviar um arquivo", ""),
    "enviar_area": ("Enviar da área de transferência", ""),
    "barra": ("Barra de ferramentas de ações", ""),
    "atalhos": ("Desligar / ligar os atalhos", ""),
}
ATALHOS_PADRAO = {k: v[1] for k, v in ACOES.items()}
# ações que geram uma imagem e passam pelas tarefas pós-captura (as que um fluxo de trabalho pode usar)
ACOES_IMAGEM = ("regiao", "janela", "monitor", "tela", "ultima", "atraso", "rolagem", "regioes")
# ferramentas sobre um arquivo do histórico (capturador.ferramenta) e tarefas do ícone sem arquivo (bandeja)
FERRAMENTAS_ARQUIVO = ("fixar", "imprimir", "editor_fotos", "embelezar", "qr", "ocr", "hash", "ia", "copiar_arquivo")
TAREFAS_ICONE = ("importar", "ia_chave", "barra", "enviar_arquivo", "enviar_area", "enviar_texto", "fixar_area",
                 "escolher_fixar", "escolher_hash", "escolher_qr", "escolher_ocr", "escolher_embelezar", "escolher_editor",
                 "atalhos", "pausar")
# tarefas pós-captura e pós-envio (liga/desliga) que um fluxo pode sobrepor
TAREFAS = ("captura_copiar", "captura_salvar", "captura_perguntar", "captura_copiar_caminho", "captura_mostrar_pasta",
           "captura_ocr_auto", "captura_notificar", "captura_editor", "captura_enviar", "captura_tarefas_rapidas",
           "captura_janela_pos", "captura_embelezar", "captura_efeitos", "captura_editor_fotos", "captura_fixar",
           "captura_imprimir", "captura_miniatura", "captura_rodar_acoes", "captura_copiar_arquivo",
           "captura_copiar_pasta", "captura_qr", "captura_ia", "captura_pre_envio", "captura_apagar_local",
           "captura_envio_copiar", "captura_envio_abrir", "captura_envio_notificar", "captura_envio_janela",
           "captura_envio_encurtar", "captura_envio_qr", "captura_anotar")

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


def validar_acoes(lista):
    """"Realizar ações": [{nome, programa, args}]. `args` aceita %arquivo (caminho entre aspas) e %pasta."""
    out = []
    for a in lista[:10]:
        if not isinstance(a, dict):
            continue
        prog = str(a.get("programa") or "").strip().strip('"')[:260]
        if not prog or any(ch in prog for ch in '<>|\r\n\t"'):
            continue
        out.append({"nome": ("".join(ch for ch in str(a.get("nome") or "") if ord(ch) >= 32).strip()[:60]
                             or os.path.basename(prog)),
                    "programa": prog,
                    "args": "".join(ch for ch in str(a.get("args") or "%arquivo") if ch not in "\r\n")[:500],
                    "ativo": bool(a.get("ativo", True))})
    return out


def montar_comando(acao, caminho):
    """Lista de argumentos para subprocess (sem shell): o arquivo nunca é interpretado como comando."""
    import shlex
    pasta = os.path.dirname(caminho)
    partes = shlex.split(acao["args"].replace("\\", "\\\\"), posix=True) if acao["args"].strip() else []
    return [acao["programa"]] + [p.replace("%arquivo", caminho).replace("%pasta", pasta) for p in partes]


def validar_fluxos(lista):
    """Fluxos de trabalho: {id, nome, acao (de ACOES_IMAGEM), atalho, tarefas {chave: bool}, destino}."""
    from .envio import DESTINOS
    out, usados, ids = [], set(), set()
    for f in lista[:20]:
        if not isinstance(f, dict) or f.get("acao") not in ACOES_IMAGEM:
            continue
        fid = "".join(ch for ch in str(f.get("id") or "") if ch.isalnum())[:16] or f"f{len(out) + 1}"
        while fid in ids:
            fid += "x"
        ids.add(fid)
        r = ler_atalho(f.get("atalho")) if f.get("atalho") else None
        atalho = r[2] if r and r[2].lower() not in usados else ""
        if atalho:
            usados.add(atalho.lower())
        t = f.get("tarefas") if isinstance(f.get("tarefas"), dict) else {}
        out.append({"id": fid, "nome": "".join(ch for ch in str(f.get("nome") or "") if ord(ch) >= 32).strip()[:60]
                    or ACOES[f["acao"]][0], "acao": f["acao"], "atalho": atalho,
                    "tarefas": {k: bool(v) for k, v in t.items() if k in TAREFAS},
                    "destino": f.get("destino") if f.get("destino") in DESTINOS else ""})
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
    _u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    _u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _u.GetAsyncKeyState.restype = ctypes.c_short
    _u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u.GetWindowLongW.restype = ctypes.c_long
    _u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    _u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
    _u.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
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


# ================================================================ anotações (desenhadas com o PIL na imagem final)
# (id, ícone, nome, tecla)
FERRAMENTAS = [
    ("mover", "✥", "Mover e ajustar a área", "m"),
    ("seta", "➜", "Seta", "a"),
    ("ret", "▢", "Retângulo", "r"),
    ("elipse", "◯", "Elipse", "e"),
    ("linha", "╱", "Linha", "l"),
    ("caneta", "✎", "Caneta", "p"),
    ("texto", "T", "Texto", "t"),
    ("texto_fundo", "Ⓣ", "Texto com fundo", "f"),
    ("numero", "①", "Número (1, 2, 3…)", "n"),
    ("marca", "▌", "Marca-texto", "h"),
    ("desfocar", "◍", "Desfocar", "b"),
    ("pixelar", "▦", "Pixelar", "x"),
    ("tarja", "■", "Tarja (esconder)", "k"),
    ("cursor", "⬉", "Cursor do mouse", "c"),
]
CORES = ["#e53935", "#ffb300", "#43a047", "#1e88e5", "#8e24aa", "#ffffff", "#000000"]
ESPESSURAS = [(2, "•", "fina"), (4, "●", "média"), (8, "⬤", "grossa")]
_DE_CLICAR = ("texto", "texto_fundo", "numero", "cursor")
_SETA_CURSOR = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]
_fontes = {}


def _rgb(c):
    c = str(c or "").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else (229, 57, 53)
    except ValueError:
        return 229, 57, 53


def _contraste(rgb):
    return (0, 0, 0) if (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000 > 140 else (255, 255, 255)


def _fonte(tam):
    from PIL import ImageFont
    if tam not in _fontes:
        f = None
        for n in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                f = ImageFont.truetype(n, tam)
                break
            except OSError:
                pass
        if f is None:
            try:
                f = ImageFont.load_default(tam)
            except TypeError:
                f = ImageFont.load_default()
        _fontes[tam] = f
    return _fontes[tam]


def _tam_texto(esp):
    return max(14, int(esp) * 5)


def _raio_numero(esp):
    return max(13, int(int(esp) * 3.5))


def caixa_forma(f):
    """Retângulo (l, t, r, b) ocupado por uma anotação, com folga para clicar nela."""
    t, esp = f.get("t"), int(f.get("esp") or 4)
    x0, y0 = f["x0"], f["y0"]
    x1, y1 = f.get("x1", x0), f.get("y1", y0)
    if t == "caneta" and f.get("pts"):
        xs, ys = [p[0] for p in f["pts"]], [p[1] for p in f["pts"]]
        l, tp, r, b = min(xs), min(ys), max(xs), max(ys)
    elif t in ("texto", "texto_fundo"):
        from PIL import Image, ImageDraw
        tam = _tam_texto(esp)
        l, tp, r, b = ImageDraw.Draw(Image.new("RGB", (1, 1))).multiline_textbbox(
            (x0, y0), str(f.get("txt") or " "), font=_fonte(tam), spacing=tam // 4)
        if t == "texto_fundo":
            l, tp, r, b = l - tam // 4, tp - tam // 4, r + tam // 4, b + tam // 4
    elif t == "numero":
        rr = _raio_numero(esp)
        l, tp, r, b = x0 - rr, y0 - rr, x0 + rr, y0 + rr
    elif t == "cursor":
        s = max(1.0, esp / 3)
        l, tp, r, b = x0, y0, x0 + 12 * s, y0 + 20 * s
    else:
        (l, r), (tp, b) = sorted((x0, x1)), sorted((y0, y1))
    m = max(4, esp)
    return l - m, tp - m, r + m, b + m


def mover_forma(f, dx, dy):
    g = dict(f)
    for k in ("x0", "x1"):
        if k in g:
            g[k] += dx
    for k in ("y0", "y1"):
        if k in g:
            g[k] += dy
    if g.get("pts"):
        g["pts"] = [[x + dx, y + dy] for x, y in g["pts"]]
    return g


def desenhar_formas(img, formas, ox=0, oy=0):
    """Desenha as anotações numa cópia da imagem. As coordenadas das formas são as da tela congelada;
    (ox, oy) é onde o canto desta imagem estava nela."""
    import math
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    out = img.convert("RGB").copy()
    W, H = out.size
    for f in formas or ():
        try:
            t, cor = f.get("t"), _rgb(f.get("cor"))
            esp = max(1, min(40, int(f.get("esp") or 4)))
            X0, Y0 = f["x0"] - ox, f["y0"] - oy
            X1, Y1 = f.get("x1", f["x0"]) - ox, f.get("y1", f["y0"]) - oy
        except (AttributeError, TypeError, KeyError, ValueError):
            continue
        (l, r), (tp, b) = sorted((X0, X1)), sorted((Y0, Y1))
        d = ImageDraw.Draw(out)
        try:
            if t in ("desfocar", "pixelar", "tarja", "marca"):
                cx = (max(0, int(l)), max(0, int(tp)), min(W, int(r)), min(H, int(b)))
                if cx[2] - cx[0] < 2 or cx[3] - cx[1] < 2:
                    continue
                if t == "tarja":
                    d.rectangle((cx[0], cx[1], cx[2] - 1, cx[3] - 1), fill=cor)
                elif t == "marca":
                    p = out.crop(cx)
                    out.paste(Image.blend(p, ImageChops.multiply(p, Image.new("RGB", p.size, cor)), 0.75), cx[:2])
                elif t == "pixelar":
                    bl, p = max(6, esp * 3), out.crop(cx)
                    w, h = p.size
                    out.paste(p.resize((max(1, w // bl), max(1, h // bl)), Image.BILINEAR).resize((w, h), Image.NEAREST),
                              cx[:2])
                else:
                    raio = max(6, esp * 2)
                    m = raio * 2   # margem: sem ela as bordas do desfoque ficam claras
                    c2 = (max(0, cx[0] - m), max(0, cx[1] - m), min(W, cx[2] + m), min(H, cx[3] + m))
                    bl = out.crop(c2).filter(ImageFilter.GaussianBlur(raio))
                    out.paste(bl.crop((cx[0] - c2[0], cx[1] - c2[1], cx[2] - c2[0], cx[3] - c2[1])), cx[:2])
            elif t == "ret":
                d.rectangle((l, tp, r, b), outline=cor, width=esp)
            elif t == "elipse":
                d.ellipse((l, tp, r, b), outline=cor, width=esp)
            elif t in ("linha", "seta"):
                ang, cab, rr = math.atan2(Y1 - Y0, X1 - X0), max(12, esp * 4), esp / 2
                enc = cab * 0.8 if t == "seta" else 0
                d.line((X0, Y0, X1 - math.cos(ang) * enc, Y1 - math.sin(ang) * enc), fill=cor, width=esp)
                d.ellipse((X0 - rr, Y0 - rr, X0 + rr, Y0 + rr), fill=cor)
                if t == "seta":
                    d.polygon([(X1, Y1), (X1 - cab * math.cos(ang - 0.45), Y1 - cab * math.sin(ang - 0.45)),
                               (X1 - cab * math.cos(ang + 0.45), Y1 - cab * math.sin(ang + 0.45))], fill=cor)
                else:
                    d.ellipse((X1 - rr, Y1 - rr, X1 + rr, Y1 + rr), fill=cor)
            elif t == "caneta":
                pts, rr = [(p[0] - ox, p[1] - oy) for p in f.get("pts") or []], esp / 2
                if len(pts) > 1:
                    d.line(pts, fill=cor, width=esp, joint="curve")
                for px, py in pts[:1] + pts[-1:]:
                    d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=cor)
            elif t in ("texto", "texto_fundo"):
                txt = str(f.get("txt") or "")
                if not txt:
                    continue
                tam = _tam_texto(esp)
                fonte, entre = _fonte(tam), tam // 4
                if t == "texto_fundo":
                    bx, pad = d.multiline_textbbox((X0, Y0), txt, font=fonte, spacing=entre), tam // 4
                    d.rounded_rectangle((bx[0] - pad, bx[1] - pad, bx[2] + pad, bx[3] + pad), radius=pad, fill=cor)
                    d.multiline_text((X0, Y0), txt, font=fonte, fill=_contraste(cor), spacing=entre)
                else:
                    d.multiline_text((X0, Y0), txt, font=fonte, fill=cor, spacing=entre,
                                     stroke_width=max(2, tam // 8), stroke_fill=_contraste(cor))
            elif t == "numero":
                rr = _raio_numero(esp)
                d.ellipse((X0 - rr, Y0 - rr, X0 + rr, Y0 + rr), fill=cor, outline=(255, 255, 255), width=2)
                d.text((X0, Y0), str(int(f.get("n") or 1)), font=_fonte(int(rr * 1.1)), fill=_contraste(cor), anchor="mm")
            elif t == "cursor":
                s = max(1.0, esp / 3)
                pts = [(X0 + x * s, Y0 + y * s) for x, y in _SETA_CURSOR]
                d.polygon(pts, fill=(255, 255, 255))
                d.line(pts + pts[:1], fill=(0, 0, 0), width=max(1, round(s)))
        except (ValueError, TypeError) as e:   # forma degenerada: pula só ela
            print("anotação ignorada:", t, e)
    return out


# ================================================================ seleção na tela congelada
def selecionar(img, ox, oy, modo="regiao", janelas=(), anotar=False, lupa=True, estilo=None, escurecer=True, cruz=True):
    """Mostra a imagem por cima de tudo e deixa o usuário escolher.
    regiao -> (l, t, r, b, formas) relativo à imagem; formas = anotações (vazio com `anotar` desligado)
    regioes -> [[l, t, r, b], ...] (várias áreas; Enter ou clique duplo termina)
    cor -> (r, g, b) | regua -> (dx, dy) | None se cancelou (Esc / botão direito).
    `estilo` (dict) guarda a ferramenta, a cor e a espessura escolhidas para a próxima vez."""
    try:
        return _Selecao(img, ox, oy, modo, janelas, anotar, lupa, estilo if isinstance(estilo, dict) else {},
                        escurecer, cruz).rodar()
    finally:
        coletar_tk()


def coletar_tk():
    """A janela Tk tem referências circulares; se o coletor de lixo a apagar em outra thread, a Tcl derruba o processo
    ("Tcl_AsyncDelete: async handler deleted by the wrong thread"). Coleta aqui, na thread que criou a janela."""
    import gc
    gc.collect()


class _Selecao:
    """Fase 1: escolher a área (ou a cor / medir). Fase 2 (só região, com `anotar`): barra de ferramentas para
    desenhar por cima, ajustar a área e confirmar (Enter, clique duplo ou ✓)."""
    FUNDO, ATIVO, TXT = "#101418", "#2b5fa8", "#e6ebf0"
    CURSOR_ALCA = {"lt": "size_nw_se", "rb": "size_nw_se", "rt": "size_ne_sw", "lb": "size_ne_sw",
                   "l": "size_we", "r": "size_we", "t": "size_ns", "b": "size_ns"}

    def __init__(self, img, ox, oy, modo, janelas, anotar, lupa, estilo, escurecer=True, cruz=True):
        import tkinter as tk
        from PIL import ImageEnhance, ImageTk
        self.tk, self.ImageTk = tk, ImageTk
        self.img, self.ox, self.oy, self.modo, self.janelas = img, ox, oy, modo, janelas
        self.area_modo = modo in ("regiao", "regioes")
        self.anotar = bool(anotar) and modo == "regiao"
        self.com_lupa = bool(lupa) or not self.area_modo
        self.usar_cruz = bool(cruz) and self.area_modo
        self.regs, self.regs_itens = [], []
        ids = [x[0] for x in FERRAMENTAS]
        self.estilo = estilo
        if estilo.get("ferr") not in ids:
            estilo["ferr"] = "mover"
        if estilo.get("cor") not in CORES:
            estilo["cor"] = CORES[0]
        if estilo.get("esp") not in [e[0] for e in ESPESSURAS]:
            estilo["esp"] = 4
        self.W, self.H = W, H = img.size
        self.res, self.fase, self.reg = None, 1, None
        self.formas, self.desf, self.ref, self.num = [], [], [], 1
        self.ini = self.hover = self.arr = self.temp = self.acao = self.sel = self.entrada = None
        self.barra, self.barra_id, self.bts, self.fotos, self._pend, self._cursor = None, None, {}, {}, None, "crosshair"

        self.root = root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"{W}x{H}+{ox}+{oy}")
        self.cv = cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, cursor="crosshair", bg="black")
        cv.pack()
        # master=root sempre: com o mini painel aberto existe outro Tk (em outra thread) e a imagem iria para ele
        escuro = self.area_modo and escurecer
        self.fotos["fundo"] = ImageTk.PhotoImage(ImageEnhance.Brightness(img).enhance(0.55) if escuro else img, master=root)
        cv.create_image(0, 0, anchor="nw", image=self.fotos["fundo"])
        self.cruz_h = cv.create_line(0, 0, 0, 0, fill="#9fd0ff", dash=(3, 4), state="hidden")
        self.cruz_v = cv.create_line(0, 0, 0, 0, fill="#9fd0ff", dash=(3, 4), state="hidden")
        self.centro = [cv.create_line(0, 0, 0, 0, fill="#4ea1ff", width=1, state="hidden") for _ in range(2)]
        self.claro = cv.create_image(0, 0, anchor="nw")
        self.borda = cv.create_rectangle(0, 0, 0, 0, outline="#4ea1ff", width=2, state="hidden")
        self.alcas = [cv.create_rectangle(0, 0, 0, 0, fill="#4ea1ff", outline="#ffffff", state="hidden") for _ in range(8)]
        self.marca_sel = cv.create_rectangle(0, 0, 0, 0, outline="#ffffff", dash=(4, 3), state="hidden")
        self.linha = cv.create_line(0, 0, 0, 0, fill="#ffcc00", width=2, state="hidden")
        self.rot_f = cv.create_rectangle(0, 0, 0, 0, fill=self.FUNDO, outline="", state="hidden")
        self.rot = cv.create_text(0, 0, anchor="nw", fill=self.TXT, font=("Segoe UI", 10, "bold"), state="hidden")
        self.lupa = cv.create_image(0, 0, anchor="nw", state="hidden")
        self.lupa_f = cv.create_rectangle(0, 0, 0, 0, fill=self.FUNDO, outline="#4ea1ff", state="hidden")
        self.lupa_t = cv.create_text(0, 0, anchor="nw", fill=self.TXT, font=("Consolas", 9), state="hidden")
        self.ajuda_f = cv.create_rectangle(0, 0, 0, 0, fill=self.FUNDO, outline="")
        self.ajuda = cv.create_text(W // 2, 22, text="", fill=self.TXT, font=("Segoe UI", 10))
        self.dica()

        cv.bind("<Motion>", self.mover)
        cv.bind("<ButtonPress-1>", self.apertar)
        cv.bind("<B1-Motion>", self.arrastar)
        cv.bind("<ButtonRelease-1>", self.soltar)
        cv.bind("<Double-Button-1>", self.duplo)
        cv.bind("<ButtonPress-3>", self.direito)
        root.bind("<Escape>", lambda _e: self.cancelar())
        for k in ("<Return>", "<KP_Enter>"):
            root.bind(k, lambda _e: None if self.entrada else self.confirmar())
        for k in ("<Delete>", "<BackSpace>"):
            root.bind(k, lambda _e: self.apagar())
        for k in ("<Control-z>", "<Control-Z>"):
            root.bind(k, lambda _e: self.desfazer())
        for k in ("<Control-y>", "<Control-Y>"):
            root.bind(k, lambda _e: self.refazer())
        for k, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            root.bind(f"<{k}>", lambda e, dx=dx, dy=dy: self.setas(e, dx, dy))
        root.bind("<Key>", self.tecla)

    # ------------------------------------------------------------ geral
    def rodar(self):
        self.root.after(30, self.focar)
        self.root.mainloop()
        return self.res

    def focar(self):
        try:
            h = _u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            _u.keybd_event(0x12, 0, 0, 0)   # Alt: o Windows só deixa trazer para frente depois de uma tecla
            _u.keybd_event(0x12, 0, 2, 0)
            _u.SetForegroundWindow(h)
        except Exception:
            pass
        self.root.focus_force()

    def texto_ajuda(self):
        if self.modo == "cor":
            return "Clique na cor  ·  setas movem o mouse 1 px  ·  Esc cancela"
        if self.modo == "regua":
            return "Arraste para medir  ·  setas movem o mouse 1 px  ·  Esc cancela"
        if self.modo == "regioes":
            n = len(self.regs)
            return ((f"{n} área{'s' if n > 1 else ''} marcada{'s' if n > 1 else ''}  ·  " if n else "") +
                    "Arraste para marcar cada área  ·  clique numa janela para pegá-la  ·  Enter ou clique duplo captura  ·  "
                    "botão direito desfaz a última  ·  Esc cancela")
        if self.fase == 1:
            return ("Arraste para escolher a área  ·  clique numa janela para pegá-la inteira  ·  "
                    "setas movem o mouse 1 px (Shift: 10)  ·  Esc cancela")
        return ("Desenhe por cima  ·  Enter, clique duplo ou ✓ captura  ·  com ✥ arraste as bordas para ajustar  ·  "
                "Ctrl+Z desfaz  ·  botão direito escolhe outra área  ·  Esc cancela")

    def dica(self, txt=None):
        cv = self.cv
        cv.itemconfigure(self.ajuda, text=txt or self.texto_ajuda())
        bx = cv.bbox(self.ajuda)
        if bx:
            cv.coords(self.ajuda_f, bx[0] - 12, 8, bx[2] + 12, 36)
        cv.tag_raise(self.ajuda_f)
        cv.tag_raise(self.ajuda)

    def rotulo(self, x, y, txt):
        cv = self.cv
        cv.itemconfigure(self.rot, text=txt, state="normal")
        cv.coords(self.rot, x + 6, y + 3)
        bx = cv.bbox(self.rot)
        cv.coords(self.rot_f, bx[0] - 6, bx[1] - 3, bx[2] + 6, bx[3] + 3)
        cv.itemconfigure(self.rot_f, state="normal")
        cv.tag_raise(self.rot_f)
        cv.tag_raise(self.rot)

    def mostrar_lupa(self, x, y):
        from PIL import Image, ImageDraw
        cv, img, W, H = self.cv, self.img, self.W, self.H
        raio, zoom = 8, 9
        lado = (raio * 2 + 1) * zoom
        z = img.crop((x - raio, y - raio, x + raio + 1, y + raio + 1)).resize((lado, lado), Image.NEAREST)
        ImageDraw.Draw(z).rectangle([raio * zoom, raio * zoom, raio * zoom + zoom - 1, raio * zoom + zoom - 1],
                                    outline=(255, 60, 60))
        self.fotos["lupa"] = self.ImageTk.PhotoImage(z, master=self.root)
        lx = x + 24 if x + 24 + lado < W else x - 24 - lado
        ly = y + 24 if y + 24 + lado + 36 < H else y - 24 - lado - 36
        cr = img.getpixel((min(max(x, 0), W - 1), min(max(y, 0), H - 1)))
        cv.itemconfigure(self.lupa, image=self.fotos["lupa"], state="normal")
        cv.coords(self.lupa, lx, ly)
        cv.coords(self.lupa_f, lx - 1, ly - 1, lx + lado + 1, ly + lado + 34)
        cv.itemconfigure(self.lupa_f, state="normal")
        cv.itemconfigure(self.lupa_t, state="normal",
                         text=f"X {x + self.ox}  Y {y + self.oy}\n#{cr[0]:02X}{cr[1]:02X}{cr[2]:02X}  rgb {cr[0]},{cr[1]},{cr[2]}")
        cv.coords(self.lupa_t, lx + 4, ly + lado + 3)
        for i in (self.lupa_f, self.lupa, self.lupa_t):
            cv.tag_raise(i)

    def esconder_lupa(self):
        for i in (self.lupa_f, self.lupa, self.lupa_t):
            self.cv.itemconfigure(i, state="hidden")

    def janela_em(self, x, y):
        px, py = x + self.ox, y + self.oy
        r = next((r for r in self.janelas if r[0] <= px < r[2] and r[1] <= py < r[3]), None)
        if not r:
            return None
        return [max(0, r[0] - self.ox), max(0, r[1] - self.oy), min(self.W, r[2] - self.ox), min(self.H, r[3] - self.oy)]

    def realce(self, l, t, r, b, formas=False):
        """Área clara (sem o escurecido) com a borda e o tamanho."""
        cv = self.cv
        l, t, r, b = max(0, int(l)), max(0, int(t)), min(self.W, int(r)), min(self.H, int(b))
        if r - l < 1 or b - t < 1:
            return
        if self.usar_cruz:
            mx, my, s = (l + r) // 2, (t + b) // 2, 6
            for item, c in zip(self.centro, ((mx - s, my, mx + s + 1, my), (mx, my - s, mx, my + s + 1))):
                cv.coords(item, *c)
                cv.itemconfigure(item, state="normal" if r - l > 30 and b - t > 30 else "hidden")
                cv.tag_raise(item)
        if self.area_modo:
            recorte = self.img.crop((l, t, r, b))
            if formas and self.formas:
                recorte = desenhar_formas(recorte, self.formas, l, t)
            self.fotos["claro"] = self.ImageTk.PhotoImage(recorte, master=self.root)
            cv.itemconfigure(self.claro, image=self.fotos["claro"], state="normal")
            cv.coords(self.claro, l, t)
        cv.coords(self.borda, l, t, r, b)
        cv.itemconfigure(self.borda, state="normal")
        self.rotulo(l, t - 26 if t >= 30 else t + 4, f"{r - l} × {b - t}")

    def setas(self, e, dx, dy):
        """Move o mouse 1 px (Shift: 10 px) para ajustar a seleção com precisão."""
        if self.entrada:
            return
        n = 10 if e.state & 0x1 else 1
        pt = wt.POINT()
        _u.GetCursorPos(ctypes.byref(pt))
        _u.SetCursorPos(pt.x + dx * n, pt.y + dy * n)

    def cancelar(self):
        if self.entrada:
            return self.fechar_entrada(False)
        self.res = None
        self.root.destroy()

    # ------------------------------------------------------------ mouse
    def guias(self, x, y):
        """Linhas que seguem o mouse, de borda a borda (só escolhendo a área)."""
        if not self.usar_cruz:
            return
        cv = self.cv
        cv.coords(self.cruz_h, 0, y, self.W, y)
        cv.coords(self.cruz_v, x, 0, x, self.H)
        for i in (self.cruz_h, self.cruz_v):
            cv.itemconfigure(i, state="normal" if self.fase == 1 else "hidden")
            cv.tag_lower(i, self.claro)

    def mover(self, e):
        if self.fase == 1:
            self.guias(e.x, e.y)
            if self.com_lupa:
                self.mostrar_lupa(e.x, e.y)
            if self.area_modo and self.ini is None:
                alvo = self.janela_em(e.x, e.y)
                if alvo != self.hover:
                    self.hover = alvo
                    if alvo:
                        self.realce(*alvo)
            return
        ferr = self.estilo["ferr"]
        if ferr == "mover":
            a = self.alca_em(e.x, e.y)
            cur = self.CURSOR_ALCA.get(a) or ("fleur" if self.forma_em(e.x, e.y) is not None or self.dentro(e.x, e.y)
                                               else "crosshair")
        else:
            cur = "xterm" if ferr in ("texto", "texto_fundo") else "crosshair"
        if cur != self._cursor:
            self._cursor = cur
            self.cv.configure(cursor=cur)

    def apertar(self, e):
        if self.entrada:
            self.fechar_entrada(True)
            return
        if self.fase == 1:
            self.ini = (e.x, e.y)
            return
        x, y, ferr = e.x, e.y, self.estilo["ferr"]
        if ferr == "mover":
            alca = self.alca_em(x, y)
            if alca:
                self.acao = ("redim", alca, list(self.reg), x, y, self.foto())
                return
            i = self.forma_em(x, y)
            if i is not None:
                self.sel = i
                self.acao = ("forma", i, dict(self.formas[i]), x, y, self.foto())
                self.pintar()
                return
            if self.sel is not None:
                self.sel = None
                self.pintar()
            self.acao = ("area" if self.dentro(x, y) else "nova", None, list(self.reg), x, y, self.foto())
            return
        if ferr in ("texto", "texto_fundo"):
            self.abrir_entrada(x, y, ferr)
            return
        f = {"t": ferr, "x0": x, "y0": y, "x1": x, "y1": y, "cor": self.estilo["cor"], "esp": self.estilo["esp"]}
        if ferr in _DE_CLICAR:
            self.mudar()
            if ferr == "numero":
                f["n"] = self.num
                self.num += 1
            self.formas.append(f)
            self.pintar()
            return
        cv, cor, esp = self.cv, f["cor"], f["esp"]
        if ferr == "caneta":
            f["pts"] = [[x, y]]
            self.temp = cv.create_line(x, y, x, y, fill=cor, width=esp, capstyle="round", joinstyle="round", smooth=True)
        elif ferr in ("seta", "linha"):
            cab = max(12, esp * 4)
            self.temp = cv.create_line(x, y, x, y, fill=cor, width=esp, capstyle="round",
                                       arrow="last" if ferr == "seta" else "none", arrowshape=(cab, cab, cab * 0.45))
        elif ferr == "elipse":
            self.temp = cv.create_oval(x, y, x, y, outline=cor, width=esp)
        elif ferr == "ret":
            self.temp = cv.create_rectangle(x, y, x, y, outline=cor, width=esp)
        else:   # marca, desfocar, pixelar, tarja: o efeito aparece ao soltar
            self.temp = cv.create_rectangle(x, y, x, y, outline="#ffffff", dash=(4, 3), width=1,
                                            fill=cor if ferr == "tarja" else "")
        self.arr = f

    def arrastar(self, e):
        x, y = e.x, e.y
        if self.fase == 1:
            self.guias(x, y)
            if self.com_lupa:
                self.mostrar_lupa(x, y)
            if self.ini is None:
                return
            x0, y0 = self.ini
            if self.area_modo:
                self.realce(min(x0, x), min(y0, y), max(x0, x) + 1, max(y0, y) + 1)
            elif self.modo == "regua":
                self.cv.coords(self.linha, x0, y0, x, y)
                self.cv.itemconfigure(self.linha, state="normal")
                dx, dy = abs(x - x0) + 1, abs(y - y0) + 1
                self.rotulo(x + 12, y - 30, f"{dx} × {dy} px  ·  distância {((dx - 1) ** 2 + (dy - 1) ** 2) ** 0.5:.0f} px")
            return
        if self.arr:
            a, cv = self.arr, self.cv
            if a["t"] == "caneta":
                a["pts"].append([x, y])
                cv.coords(self.temp, *[c for p in a["pts"] for c in p])
                return
            if e.state & 0x1:   # Shift: linhas a cada 45°, quadrados e círculos
                dx, dy = x - a["x0"], y - a["y0"]
                if a["t"] in ("linha", "seta"):
                    import math
                    ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                    dist = math.hypot(dx, dy)
                    x, y = round(a["x0"] + dist * math.cos(ang)), round(a["y0"] + dist * math.sin(ang))
                else:
                    s = max(abs(dx), abs(dy))
                    x, y = a["x0"] + (s if dx >= 0 else -s), a["y0"] + (s if dy >= 0 else -s)
            a["x1"], a["y1"] = x, y
            cv.coords(self.temp, a["x0"], a["y0"], x, y)
            return
        if not self.acao:
            return
        tipo, x0, y0 = self.acao[0], self.acao[3], self.acao[4]
        dx, dy = x - x0, y - y0
        if tipo == "forma":
            self.formas[self.acao[1]] = mover_forma(self.acao[2], dx, dy)
        elif tipo == "redim":
            l, t, r, b = self.acao[2]
            alca = self.acao[1]
            l, r = (l + dx if "l" in alca else l), (r + dx if "r" in alca else r)
            t, b = (t + dy if "t" in alca else t), (b + dy if "b" in alca else b)
            self.reg = self.normal([l, t, r, b])
        elif tipo == "area":
            l, t, r, b = self.acao[2]
            dx = max(-l, min(self.W - r, dx))
            dy = max(-t, min(self.H - b, dy))
            self.reg = [l + dx, t + dy, r + dx, b + dy]
        elif tipo == "nova":
            self.reg = self.normal([x0, y0, x + 1, y + 1])
        self.pedir_pintura()

    def soltar(self, e):
        if self.fase == 1:
            self.soltar_fase1(e)
            return
        if self.arr:
            a, self.arr = self.arr, None
            self.cv.delete(self.temp)
            self.temp = None
            if a["t"] == "caneta":
                pequeno = len(a["pts"]) < 2
            else:
                pequeno = abs(a["x1"] - a["x0"]) < 3 and abs(a["y1"] - a["y0"]) < 3
            if not pequeno:
                self.mudar()
                self.formas.append(a)
            self.pintar()
            return
        if not self.acao:
            return
        tipo, x0, y0, antes = self.acao[0], self.acao[3], self.acao[4], self.acao[5]
        self.acao = None
        if tipo == "nova" and abs(e.x - x0) <= 3 and abs(e.y - y0) <= 3:
            self.reg = self.janela_em(e.x, e.y) or antes[1]   # só um clique fora da área: pega a janela debaixo do mouse
        if self.reg[2] - self.reg[0] < 2 or self.reg[3] - self.reg[1] < 2:
            self.reg = antes[1]
        if (self.formas, self.reg) != (antes[0], antes[1]):
            self.desf.append(antes)
            self.ref.clear()
        self.pintar()

    def soltar_fase1(self, e):
        if self.ini is None:
            return
        x0, y0 = self.ini
        self.ini = None
        W, H = self.W, self.H
        arrastou = abs(e.x - x0) > 3 or abs(e.y - y0) > 3
        if self.modo == "cor":
            self.res = self.img.getpixel((min(max(e.x, 0), W - 1), min(max(e.y, 0), H - 1)))
        elif self.modo == "regua":
            if not arrastou:
                return
            self.res = (abs(e.x - x0) + 1, abs(e.y - y0) + 1)
        else:
            if arrastou:
                reg = [max(0, min(x0, e.x)), max(0, min(y0, e.y)), min(W, max(x0, e.x) + 1), min(H, max(y0, e.y) + 1)]
            elif self.hover:
                reg = list(self.hover)
            else:
                return
            if self.modo == "regioes":
                self._clique = 0 if arrastou else time.time()
                return self.marcar(reg)
            if self.anotar:
                self.entrar_fase2(reg)
                return
            self.res = (*reg, [])
        self.root.destroy()

    # ------------------------------------------------------------ várias regiões
    def marcar(self, reg):
        cv = self.cv
        n = len(self.regs)
        self.fotos[f"r{n}"] = self.ImageTk.PhotoImage(self.img.crop(reg), master=self.root)
        itens = [cv.create_image(reg[0], reg[1], anchor="nw", image=self.fotos[f"r{n}"]),
                 cv.create_rectangle(*reg, outline="#4ea1ff", width=2),
                 cv.create_text(reg[0] + 6, reg[1] + 4, anchor="nw", text=str(n + 1), fill="#ffffff",
                                font=("Segoe UI", 11, "bold"))]
        self.regs.append(reg)
        self.regs_itens.append(itens)
        for i in (self.claro, self.borda, self.rot, self.rot_f, *self.centro):
            cv.itemconfigure(i, state="hidden")
        self.dica()

    def desmarcar(self):
        if not self.regs:
            return self.cancelar()
        self.regs.pop()
        for i in self.regs_itens.pop():
            self.cv.delete(i)
        self.dica()

    def duplo(self, _e):
        if self.modo == "regioes":
            # o 1º clique do clique duplo marcou a janela debaixo do mouse: desfaz, a não ser que seja a única área
            if len(self.regs) > 1 and time.time() - getattr(self, "_clique", 0) < 0.6:
                self.desmarcar()
            return self.confirmar()
        if self.fase == 2 and not self.entrada and self.estilo["ferr"] not in _DE_CLICAR and self.dentro(_e.x, _e.y):
            self.confirmar()

    def direito(self, _e):
        if self.modo == "regioes":
            return self.desmarcar()
        if self.fase == 1:
            return self.cancelar()
        if self.entrada:
            return self.fechar_entrada(False)
        # volta a escolher a área (as anotações ficam)
        self.fase, self.reg, self.sel, self.acao, self.hover = 1, None, None, None, None
        for i in [self.claro, self.borda, self.marca_sel, self.rot, self.rot_f, *self.alcas]:
            self.cv.itemconfigure(i, state="hidden")
        if self.barra_id:
            self.cv.itemconfigure(self.barra_id, state="hidden")
        self._cursor = "crosshair"
        self.cv.configure(cursor="crosshair")
        self.dica()

    # ------------------------------------------------------------ fase 2: anotação
    def entrar_fase2(self, reg):
        self.fase, self.reg, self.hover = 2, self.normal(reg), None
        self.esconder_lupa()
        self.criar_barra()
        self.pintar()
        self.dica()

    def normal(self, r):
        l, t, rr, b = r
        l, rr = sorted((l, rr))
        t, b = sorted((t, b))
        return [max(0, int(l)), max(0, int(t)), min(self.W, int(rr)), min(self.H, int(b))]

    def dentro(self, x, y):
        return bool(self.reg) and self.reg[0] <= x < self.reg[2] and self.reg[1] <= y < self.reg[3]

    def alca_em(self, x, y):
        if not self.reg:
            return None
        l, t, r, b = self.reg
        tol = 7
        if not (l - tol <= x <= r + tol and t - tol <= y <= b + tol):
            return None
        h = "l" if abs(x - l) <= tol else "r" if abs(x - r) <= tol else ""
        v = "t" if abs(y - t) <= tol else "b" if abs(y - b) <= tol else ""
        return (h + v) or None

    def forma_em(self, x, y):
        for i in range(len(self.formas) - 1, -1, -1):
            l, t, r, b = caixa_forma(self.formas[i])
            if l <= x <= r and t <= y <= b:
                return i
        return None

    def pedir_pintura(self):
        if self._pend is None:
            self._pend = self.root.after(16, self._pintar_pendente)

    def _pintar_pendente(self):
        self._pend = None
        self.pintar()

    def pintar(self):
        if not self.reg:
            return
        cv = self.cv
        l, t, r, b = self.reg
        self.realce(l, t, r, b, formas=True)
        meio_x, meio_y, s = (l + r) / 2, (t + b) / 2, 4
        for item, (px, py) in zip(self.alcas, [(l, t), (meio_x, t), (r, t), (r, meio_y), (r, b), (meio_x, b), (l, b), (l, meio_y)]):
            cv.coords(item, px - s, py - s, px + s, py + s)
            cv.itemconfigure(item, state="normal")
            cv.tag_raise(item)
        if self.sel is not None and self.sel < len(self.formas) and self.estilo["ferr"] == "mover":
            cv.coords(self.marca_sel, *caixa_forma(self.formas[self.sel]))
            cv.itemconfigure(self.marca_sel, state="normal")
            cv.tag_raise(self.marca_sel)
        else:
            cv.itemconfigure(self.marca_sel, state="hidden")
        self.posicionar_barra()

    def foto(self):
        return [dict(f) for f in self.formas], list(self.reg), self.num

    def mudar(self):
        self.desf.append(self.foto())
        del self.desf[:-100]
        self.ref.clear()

    def desfazer(self):
        if self.entrada or self.fase != 2 or not self.desf:
            return
        self.ref.append(self.foto())
        self.formas, self.reg, self.num = self.desf.pop()
        self.sel = None
        self.pintar()

    def refazer(self):
        if self.entrada or self.fase != 2 or not self.ref:
            return
        self.desf.append(self.foto())
        self.formas, self.reg, self.num = self.ref.pop()
        self.sel = None
        self.pintar()

    def apagar(self):
        if self.entrada or self.fase != 2 or self.sel is None or self.sel >= len(self.formas):
            return
        self.mudar()
        del self.formas[self.sel]
        self.sel = None
        self.pintar()

    def tecla(self, e):
        if self.entrada or self.fase != 2 or e.state & 0x4:   # 0x4 = Ctrl (tem atalhos próprios)
            return
        ch = (e.char or "").lower()
        if not ch:
            return
        for fid, _ic, _nome, k in FERRAMENTAS:
            if ch == k:
                return self.escolher(fid)
        if ch.isdigit() and 1 <= int(ch) <= len(CORES):
            self.mudar_cor(CORES[int(ch) - 1])

    def escolher(self, fid):
        self.fechar_entrada(True)
        self.estilo["ferr"] = fid
        if fid != "mover":
            self.sel = None
        self.realcar_barra()
        self.pintar()

    def mudar_cor(self, cor):
        self.estilo["cor"] = cor
        self.realcar_barra()
        self.alterar_selecionada("cor", cor)

    def mudar_esp(self, esp):
        self.estilo["esp"] = esp
        self.realcar_barra()
        self.alterar_selecionada("esp", esp)

    def alterar_selecionada(self, chave, valor):
        """Com ✥, a cor e a espessura escolhidas valem também para a anotação selecionada."""
        if self.sel is None or self.sel >= len(self.formas) or self.estilo["ferr"] != "mover":
            return
        if self.formas[self.sel].get(chave) != valor:
            self.mudar()
            self.formas[self.sel] = {**self.formas[self.sel], chave: valor}
            self.pintar()

    def confirmar(self):
        if self.modo == "regioes":
            if self.regs:
                self.res = [list(r) for r in self.regs]
                self.root.destroy()
            return
        if self.fase != 2:
            return
        self.fechar_entrada(True)
        l, t, r, b = self.normal(self.reg)
        if r - l < 1 or b - t < 1:
            return
        self.res = (l, t, r, b, [dict(f) for f in self.formas])
        self.root.destroy()

    # ------------------------------------------------------------ texto
    def abrir_entrada(self, x, y, tipo):
        tk, cor, esp = self.tk, self.estilo["cor"], self.estilo["esp"]
        fundo = cor if tipo == "texto_fundo" else self.FUNDO
        frente = "#%02x%02x%02x" % _contraste(_rgb(cor)) if tipo == "texto_fundo" else cor
        ent = tk.Entry(self.root, font=("Segoe UI", max(9, int(_tam_texto(esp) * 0.75)), "bold"), fg=frente, bg=fundo,
                       insertbackground=frente, relief="flat", width=26)
        wid = self.cv.create_window(x, y, window=ent, anchor="nw")
        self.entrada = (ent, wid, x, y, tipo, cor, esp)
        ent.bind("<Return>", self._entrada_ok)
        ent.bind("<KP_Enter>", self._entrada_ok)
        ent.bind("<Escape>", self._entrada_cancela)
        self.dica("Digite o texto  ·  Enter confirma  ·  Esc cancela")
        ent.focus_force()

    def _entrada_ok(self, _e):
        self.fechar_entrada(True)
        return "break"

    def _entrada_cancela(self, _e):
        self.fechar_entrada(False)
        return "break"

    def fechar_entrada(self, ok):
        if not self.entrada:
            return
        ent, wid, x, y, tipo, cor, esp = self.entrada
        self.entrada = None
        txt = ent.get().strip()
        self.cv.delete(wid)
        ent.destroy()
        self.root.focus_force()
        self.dica()
        if ok and txt:
            self.mudar()
            self.formas.append({"t": tipo, "x0": x, "y0": y, "x1": x, "y1": y, "cor": cor, "esp": esp, "txt": txt[:500]})
            self.pintar()

    # ------------------------------------------------------------ barra de ferramentas
    def criar_barra(self):
        if self.barra:
            self.cv.itemconfigure(self.barra_id, state="normal")
            return
        tk = self.tk
        f = tk.Frame(self.root, bg=self.FUNDO, padx=4, pady=4)

        def botao(chave, rot, dica, cmd, bg=None, fg=None, largura=2):
            b = tk.Label(f, text=rot, bg=bg or self.FUNDO, fg=fg or self.TXT, font=("Segoe UI Symbol", 12), width=largura,
                         padx=3, pady=1, cursor="hand2", highlightthickness=2, highlightbackground=self.FUNDO)
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda _e: cmd())
            b.bind("<Enter>", lambda _e: self.dica(dica))
            b.bind("<Leave>", lambda _e: self.dica())
            if chave:
                self.bts[chave] = b
            return b

        def separador():
            tk.Frame(f, width=1, bg="#3a4350").pack(side="left", fill="y", padx=5)

        for fid, ic, nome, k in FERRAMENTAS:
            botao(fid, ic, f"{nome}  ({k.upper()})", lambda fid=fid: self.escolher(fid))
        separador()
        for i, c in enumerate(CORES, 1):
            botao("cor" + c, " ", f"Cor  ({i})", lambda c=c: self.mudar_cor(c), bg=c, largura=1)
        separador()
        for esp, ic, nome in ESPESSURAS:
            botao(f"esp{esp}", ic, f"Espessura {nome}", lambda esp=esp: self.mudar_esp(esp))
        separador()
        botao(None, "↶", "Desfazer  (Ctrl+Z)", self.desfazer)
        botao(None, "↷", "Refazer  (Ctrl+Y)", self.refazer)
        separador()
        botao(None, "✓", "Capturar  (Enter ou clique duplo na área)", self.confirmar, bg="#2e7d32", fg="#ffffff")
        botao(None, "✕", "Cancelar  (Esc)", self.cancelar, bg="#c62828", fg="#ffffff")
        self.barra = f
        self.barra_id = self.cv.create_window(0, 0, window=f, anchor="nw")
        self.realcar_barra()

    def realcar_barra(self):
        if not self.barra:
            return
        for fid, *_ in FERRAMENTAS:
            self.bts[fid].configure(bg=self.ATIVO if fid == self.estilo["ferr"] else self.FUNDO)
        for c in CORES:
            self.bts["cor" + c].configure(highlightbackground="#ffffff" if c == self.estilo["cor"] else self.FUNDO)
        for esp, *_ in ESPESSURAS:
            self.bts[f"esp{esp}"].configure(bg=self.ATIVO if esp == self.estilo["esp"] else self.FUNDO)

    def posicionar_barra(self):
        if not self.barra:
            return
        self.barra.update_idletasks()
        fw, fh = self.barra.winfo_reqwidth(), self.barra.winfo_reqheight()
        l, t, r, b = self.reg
        y = b + 8
        if y + fh > self.H:
            y = t - fh - 8
            if y < 0:
                y = b - fh - 8   # área ocupa a tela toda: barra por dentro, embaixo
        x = max(0, min(l, self.W - fw))
        self.cv.coords(self.barra_id, x, max(0, y))


# ================================================================ captura com rolagem
_MARGEM_ROLAGEM = 24   # px ignorados em cada lado: a barra de rolagem muda a cada passo e quebrava a emenda


def _linhas(img):
    """Assinatura de cada linha da imagem, sem as bordas laterais."""
    g = img.convert("L")
    w = g.width
    m = _MARGEM_ROLAGEM if w > _MARGEM_ROLAGEM * 4 else 0
    dados = g.tobytes()
    return [hash(dados[i * w + m:(i + 1) * w - m]) for i in range(g.height)]


def _parecidas(a, b):
    """Fração de linhas iguais na mesma posição."""
    return sum(1 for x, y in zip(a, b) if x == y) / max(1, len(a))


def _deslocamento(a, b, tolerancia=0.9):
    """Quantas linhas novas o quadro b trouxe em relação ao a.
    Devolve (s, topo, base): s = 0 se nada mudou, None se não achou a emenda; topo/base = faixas fixas
    (cabeçalho e rodapé que não rolam). Aceita algumas linhas diferentes (efeito de "hover", cursor piscando)."""
    h = len(a)
    topo = 0
    while topo < h and a[topo] == b[topo]:
        topo += 1
    if topo == h:
        return 0, 0, 0
    base = 0
    while base < h - topo and a[h - 1 - base] == b[h - 1 - base]:
        base += 1
    A, B = a[topo:h - base], b[topo:h - base]
    m = len(A)
    # linhas-âncora: as raras (até 3 vezes no quadro; as em branco ficam de fora) votam no deslocamento
    from collections import Counter, defaultdict
    ca, cb = Counter(A), Counter(B)
    pos_a = defaultdict(list)
    for i, x in enumerate(A):
        if ca[x] <= 3:
            pos_a[x].append(i)
    votos = Counter()
    for j, x in enumerate(B):
        if cb[x] <= 3:
            for i in pos_a.get(x, ()):
                if i > j:
                    votos[i - j] += 1
    minimo = max(8, m // 10)          # parte em comum mínima para confiar na emenda
    melhor = None
    for s, _n in votos.most_common(6):
        sobra = m - s
        if sobra < minimo:
            continue
        taxa = sum(1 for i in range(sobra) if A[s + i] == B[i]) / sobra
        if taxa >= tolerancia and (melhor is None or taxa > melhor[1]):
            melhor = (s, taxa)
    if melhor:
        return melhor[0], topo, base
    if _parecidas(a, b) >= 0.9:
        return 0, topo, base          # não rolou: só um detalhe mudou (hover, cursor piscando)
    return None, topo, base


class _Emenda:
    """Monta a imagem da rolagem. `fim` = até que linha do último quadro a imagem já foi montada.
    O rodapé fixo (base) muda de quadro para quadro quando linhas em branco coincidem: por isso ele é cortado
    do que já foi montado quando aparece e só entra uma vez, no fim, tirado do último quadro."""

    def __init__(self, primeiro):
        self.pedacos, self.ultimo, self.fim = [primeiro], primeiro, primeiro.height

    @property
    def altura(self):
        return sum(p.height for p in self.pedacos) + self.ultimo.height - self.fim

    def _cortar(self, n):
        while n > 0 and self.pedacos:
            p = self.pedacos[-1]
            if p.height > n:
                self.pedacos[-1] = p.crop((0, 0, p.width, p.height - n))
                return
            n -= p.height
            self.pedacos.pop()

    def somar(self, quadro, s, base):
        h = quadro.height
        if self.fim > h - base:
            self._cortar(self.fim - (h - base))
            self.fim = h - base
        ini = max(0, self.fim - s)
        if h - base > ini:
            self.pedacos.append(quadro.crop((0, ini, quadro.width, h - base)))
        self.ultimo, self.fim = quadro, h - base

    def imagem(self):
        from PIL import Image
        partes = list(self.pedacos)
        if self.fim < self.ultimo.height:
            partes.append(self.ultimo.crop((0, self.fim, self.ultimo.width, self.ultimo.height)))
        if len(partes) == 1:
            return partes[0]
        out = Image.new("RGB", (partes[0].width, sum(p.height for p in partes)))
        y = 0
        for p in partes:
            out.paste(p, (0, y))
            y += p.height
        return out


class _PainelRolagem:
    """Janelinha "Parar e salvar" da captura com rolagem. Fica fora das capturas (WDA_EXCLUDEFROMCAPTURE)
    e não rouba o foco da janela que está rolando. Roda na mesma thread do laço (eventos bombeados à mão)."""

    def __init__(self, bbox, parar):
        import tkinter as tk
        self.tk = tk
        self.root = r = tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg="#101418")
        self.txt = tk.Label(r, text="Rolando…", fg="#e6ebf0", bg="#101418", font=("Segoe UI", 10), padx=10, pady=8)
        self.txt.pack(side="left")
        b = tk.Label(r, text="■  Parar e salvar", fg="#ffffff", bg="#c62828", font=("Segoe UI", 10, "bold"),
                     padx=12, pady=6, cursor="hand2")
        b.pack(side="left", padx=(0, 6), pady=6)
        b.bind("<Button-1>", lambda _e: parar.set())
        r.update_idletasks()
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        mi = _MONINFO()
        mi.cbSize = ctypes.sizeof(mi)
        _u.GetMonitorInfoW(_u.MonitorFromPoint(wt.POINT((bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2), 2),
                           ctypes.byref(mi))
        tr = mi.rcWork
        x = max(tr.left, min(bbox[0], tr.right - w))
        if bbox[1] - h - 10 >= tr.top:
            y = bbox[1] - h - 10                 # acima da área
        elif bbox[3] + 10 + h <= tr.bottom:
            y = bbox[3] + 10                     # abaixo
        else:
            x, y = tr.right - w - 16, tr.top + 16  # por cima (não sai na captura)
        r.geometry(f"+{x}+{y}")
        r.update()
        try:
            hw = _u.GetParent(r.winfo_id()) or r.winfo_id()
            _u.SetWindowLongW(hw, -20, _u.GetWindowLongW(hw, -20) | 0x08000000 | 0x80)  # NOACTIVATE | TOOLWINDOW
            _u.SetWindowDisplayAffinity(hw, 0x11)  # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+)
        except (AttributeError, OSError):
            pass

    def bombear(self):
        try:
            self.root.update()
        except self.tk.TclError:
            pass

    def texto(self, t):
        try:
            self.txt.configure(text=t)
        except self.tk.TclError:
            pass
        self.bombear()

    def esperar(self, seg):
        fim = time.time() + seg
        while True:
            self.bombear()
            resta = fim - time.time()
            if resta <= 0:
                return
            time.sleep(min(0.03, resta))

    def fechar(self):
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass
        coletar_tk()


def _mouse_dentro(bbox):
    pt = wt.POINT()
    _u.GetCursorPos(ctypes.byref(pt))
    return bbox[0] <= pt.x < bbox[2] and bbox[1] <= pt.y < bbox[3]


def rolagem(bbox, parar=None, max_altura=40000, max_quadros=800):
    """Rola a janela debaixo da área e emenda os quadros até o fim da página.
    Para quando a tela não muda mais (fim), quando `parar` é ligado (botão "Parar e salvar" ou o atalho de novo),
    com Esc, ou no limite de altura. Tirar o mouse de cima da área pausa a rolagem.
    Devolve (imagem, motivo): motivo = "fim" | "parado" | "limite" | "emenda" (não conseguiu emendar)."""
    import threading as _th
    parar = parar or _th.Event()
    l, t, r, b = bbox
    cx, cy = (l + r) // 2, (t + b) // 2
    alvo = _u.GetAncestor(_u.WindowFromPoint(wt.POINT(cx, cy)), 2)
    if alvo:
        _u.keybd_event(0x12, 0, 0, 0)
        _u.keybd_event(0x12, 0, 2, 0)
        _u.SetForegroundWindow(alvo)
    _u.SetCursorPos(cx, cy)
    try:
        painel = _PainelRolagem(bbox, parar)
        esperar, texto = painel.esperar, painel.texto
    except Exception as e:  # noqa: BLE001 (sem a janelinha a captura continua: Esc e o atalho param)
        print("painel da rolagem:", e)
        painel, esperar, texto = None, time.sleep, (lambda _t: None)

    def quadro_estavel():
        """Espera a rolagem suave terminar: dois quadros seguidos (quase) iguais."""
        esperar(0.12)
        img = capturar(bbox)[0]
        lin = _linhas(img)
        fim = time.time() + 1.5
        while time.time() < fim:
            esperar(0.08)
            novo = capturar(bbox)[0]
            nlin = _linhas(novo)
            if _parecidas(lin, nlin) >= 0.98:
                return novo, nlin
            img, lin = novo, nlin
        return img, lin

    def roda(delta):
        _u.mouse_event(0x0800, 0, 0, delta & 0xFFFFFFFF, 0)  # MOUSEEVENTF_WHEEL (negativo = para baixo)

    def apertou_esc():
        return bool(_u.GetAsyncKeyState(0x1B) & 0x8000)

    motivo = "fim"
    try:
        esperar(0.3)
        quadro, ant = quadro_estavel()
        emenda, partes = _Emenda(quadro), 1
        passo, parados, falhas = 120, 0, 0
        texto("Rolando…  Esc ou o botão param")
        while partes < max_quadros:
            if parar.is_set() or apertou_esc():
                motivo = "parado"
                break
            if not _mouse_dentro(bbox):
                texto("Pausado: volte o mouse para a área")
                esperar(0.1)
                continue
            roda(-passo)
            novo, lin = quadro_estavel()
            s, topo, base = _deslocamento(ant, lin)
            if s == 0:
                if passo < 120:
                    passo = 120   # passo pequeno pode ser ignorado por programas antigos
                    continue
                parados += 1
                if parados >= 2:
                    break         # não rola mais: chegou ao fim
                continue
            if s is None:
                # rolou demais (área baixa) ou a página mudou: volta e tenta com passo menor
                falhas += 1
                roda(passo)
                _volta, vlin = quadro_estavel()
                if falhas >= 3 or passo <= 30 or _deslocamento(ant, vlin)[0] != 0:
                    motivo = "emenda"
                    break
                passo = max(30, passo // 2)
                continue
            parados = falhas = 0
            emenda.somar(novo, s, base)
            partes, ant = partes + 1, lin
            altura = emenda.altura
            m = max(1, novo.height - topo - base)
            if s < m * 0.3:
                passo = min(1200, passo + 120)   # anda pouco por passo: acelera
            elif s > m * 0.75:
                passo = max(30, passo // 2)      # perto de passar da área: freia
            texto(f"Rolando…  {partes} partes · {altura:,} px".replace(",", "."))
            if altura >= max_altura:
                motivo = "limite"
                break
        return emenda.imagem(), motivo
    finally:
        if painel:
            painel.fechar()


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


def dados_drop(caminhos):
    """Conteúdo do formato CF_HDROP: DROPFILES (20 bytes, nomes em UTF-16) + nomes separados por \\0 + \\0 final."""
    import struct
    nomes = "".join(os.path.abspath(p) + "\0" for p in caminhos) + "\0"
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + nomes.encode("utf-16-le")


def copiar_arquivos(caminhos):
    """O arquivo em si na área de transferência (colar no Explorer, WhatsApp, Discord, e-mail)."""
    efeito = (1).to_bytes(4, "little")   # DROPEFFECT_COPY
    _area_transferencia([(15, dados_drop(caminhos)), (_u.RegisterClipboardFormatW("Preferred DropEffect"), efeito)])


def ler_area_transferencia():
    """("imagem", PIL) | ("arquivos", [caminhos]) | ("texto", str) | (None, None)."""
    from PIL import ImageGrab
    try:
        d = ImageGrab.grabclipboard()
    except OSError:
        d = None
    if isinstance(d, list) and d:
        return "arquivos", [p for p in d if os.path.isfile(p)]
    if d is not None and hasattr(d, "size"):
        return "imagem", d
    _u.GetClipboardData.restype = wt.HANDLE
    for _ in range(20):
        if _u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return None, None
    try:
        h = _u.GetClipboardData(13)
        if not h:
            return None, None
        p = _k.GlobalLock(h)
        try:
            return "texto", ctypes.wstring_at(p)
        finally:
            _k.GlobalUnlock(h)
    finally:
        _u.CloseClipboard()


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


def pasta_capturas(c):
    """Pasta escolhida nas opções (caminho completo) ou Imagens\\Monitor do PC."""
    pasta = c.get("captura_pasta") or ""
    return pasta if os.path.isabs(pasta) else os.path.join(pasta_imagens(), "Monitor do PC")


def _dialogo(nome, **kw):
    """Diálogo do Windows (pelo Tk) na frente de tudo. Devolve o caminho escolhido ou None."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        return getattr(filedialog, nome)(parent=root, **kw) or None
    finally:
        root.destroy()
        coletar_tk()


def escolher_pasta(inicial=""):
    p = _dialogo("askdirectory", title="Pasta das capturas de tela",
                 initialdir=inicial if os.path.isdir(inicial or "") else pasta_imagens(), mustexist=False)
    return os.path.normpath(p) if p else None


def perguntar_arquivo(sugestao):
    """"Salvar como…": pasta e nome escolhidos na hora. None = cancelou."""
    pasta, nome = os.path.split(sugestao)
    ext = os.path.splitext(nome)[1] or ".png"
    tipos = [("PNG", "*.png"), ("JPG", "*.jpg;*.jpeg"), ("WebP", "*.webp")]
    tipos.sort(key=lambda t: ext not in t[1])
    p = _dialogo("asksaveasfilename", title="Salvar captura como", initialdir=pasta, initialfile=nome,
                 defaultextension=ext, filetypes=tipos)
    if not p:
        return None
    p = os.path.normpath(p)
    return p if p.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else p + ext


def escolher_arquivos(titulo="Escolha o arquivo", varios=False, tipos=None):
    """Janela "Abrir" do Windows. Devolve uma lista de caminhos (vazia se cancelou)."""
    kw = {"title": titulo, "initialdir": pasta_imagens()}
    if tipos:
        kw["filetypes"] = tipos
    r = _dialogo("askopenfilenames" if varios else "askopenfilename", **kw)
    if not r:
        return []
    return [os.path.normpath(p) for p in (r if isinstance(r, (list, tuple)) else [r])]


def abrir_com(caminho, verbo="open"):
    """Abre com o programa padrão do Windows. verbo: open, edit (editor de imagens), print."""
    try:
        os.startfile(caminho, verbo)
    except OSError:
        if verbo == "open":
            raise
        os.startfile(caminho)   # sem esse verbo para o tipo de arquivo: abre normalmente


def mostrar_no_explorer(caminho):
    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(caminho)])


def nome_livre(pasta, base, ext):
    caminho = os.path.join(pasta, base + ext)
    n = 2
    while os.path.exists(caminho):
        caminho = os.path.join(pasta, f"{base}_{n}{ext}")
        n += 1
    return caminho


def nome_arquivo(c, ext):
    pasta = pasta_capturas(c)
    os.makedirs(pasta, exist_ok=True)
    try:
        base = time.strftime(c.get("captura_nome") or "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        base = time.strftime("%Y-%m-%d_%H-%M-%S")
    base = "".join(ch for ch in base if ch not in '<>:"/\\|?*').strip(" .") or "captura"
    return nome_livre(pasta, base, ext)


def _sem_alfa(img):
    """JPG não tem transparência: o fundo transparente vira branco."""
    if img.mode in ("RGBA", "LA", "P"):
        from PIL import Image
        img = img.convert("RGBA")
        fundo = Image.new("RGB", img.size, (255, 255, 255))
        fundo.paste(img, mask=img.getchannel("A"))
        return fundo
    return img.convert("RGB")


def salvar_imagem(img, caminho, c):
    q = int(c.get("captura_qualidade") or 90)
    low = caminho.lower()
    if low.endswith((".jpg", ".jpeg")):
        _sem_alfa(img).save(caminho, "JPEG", quality=q, optimize=True)
    elif low.endswith(".webp"):
        img.save(caminho, "WEBP", lossless=bool(c.get("captura_webp_sem_perda")), quality=q, method=4)
    else:
        img.save(caminho, "PNG", optimize=False)


def extensao(c):
    return {"jpg": ".jpg", "webp": ".webp"}.get(c.get("captura_formato"), ".png")


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
