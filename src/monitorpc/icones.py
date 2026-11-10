"""Ícone de cada programa, extraído do próprio .exe e guardado em PNG (cache em disco)."""
import io
import os
import re
import hashlib
import threading

from . import config

WIN = os.name == "nt"
PASTA = os.path.join(config.BASE, "icones")
_mem = {}
_lock = threading.Lock()

if WIN:
    import ctypes
    from ctypes import wintypes as wt

    _shell32 = ctypes.WinDLL("shell32")
    _user32 = ctypes.WinDLL("user32")
    _gdi32 = ctypes.WinDLL("gdi32")
    _shell32.ExtractIconExW.argtypes = [wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.HICON), ctypes.POINTER(wt.HICON), wt.UINT]
    _shell32.ExtractIconExW.restype = wt.UINT
    _user32.GetDC.restype = wt.HDC
    _user32.GetDC.argtypes = [wt.HWND]
    _user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    _user32.DestroyIcon.argtypes = [wt.HICON]
    _gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    _gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
    _gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                    ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG), ("bmWidthBytes", wt.LONG),
                    ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    _user32.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]


def _bits(hdc, hbm, w, h):
    bi = BITMAPINFOHEADER(40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    _gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bi), 0)
    return buf.raw


if WIN:
    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wt.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wt.DWORD),
                    ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]


def _icone_shell(caminho):
    """Ícone que o Explorer mostra para o arquivo (inclui o ícone padrão de .exe sem ícone próprio)."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    sfi = SHFILEINFOW()
    r = ctypes.WinDLL("shell32").SHGetFileInfoW(ctypes.c_wchar_p(caminho), 0, ctypes.byref(sfi), ctypes.sizeof(sfi),
                                                 0x100 | 0x0)  # SHGFI_ICON | SHGFI_LARGEICON
    return sfi.hIcon if r and sfi.hIcon else None


def _extrair(caminho, tam=32):
    from PIL import Image
    grande = wt.HICON()
    pequeno = wt.HICON()
    n = _shell32.ExtractIconExW(caminho, 0, ctypes.byref(grande), ctypes.byref(pequeno), 1)
    hicon, outro = None, None
    if n not in (0, 0xFFFFFFFF):
        hicon = grande.value or pequeno.value
        outro = pequeno.value if grande.value else None
    if not hicon:
        hicon = _icone_shell(caminho)
    if not hicon:
        return None
    ii = ICONINFO()
    hdc = _user32.GetDC(None)
    try:
        if not _user32.GetIconInfo(hicon, ctypes.byref(ii)) or not ii.hbmColor:
            return None
        bm = BITMAP()
        _gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, abs(bm.bmHeight)
        if not (0 < w <= 512 and 0 < h <= 512):
            return None
        cor = _bits(hdc, ii.hbmColor, w, h)
        img = Image.frombuffer("RGBA", (w, h), cor, "raw", "BGRA", 0, 1)
        if img.getchannel("A").getextrema()[1] == 0:  # ícone antigo sem transparência: usa a máscara
            masc = Image.frombuffer("RGBA", (w, h), _bits(hdc, ii.hbmMask, w, h), "raw", "BGRA", 0, 1).convert("L")
            alfa = masc.point(lambda v: 0 if v > 128 else 255)
            img = img.convert("RGB").convert("RGBA")
            img.putalpha(alfa)
        if img.size != (tam, tam):
            img = img.resize((tam, tam), Image.LANCZOS)
        saida = io.BytesIO()
        img.save(saida, "PNG", optimize=True)
        return saida.getvalue()
    finally:
        if ii.hbmColor:
            _gdi32.DeleteObject(ii.hbmColor)
        if ii.hbmMask:
            _gdi32.DeleteObject(ii.hbmMask)
        _user32.ReleaseDC(None, hdc)
        _user32.DestroyIcon(hicon)
        if outro:
            _user32.DestroyIcon(outro)


def png(caminho):
    """PNG 32x32 do ícone do executável, ou None."""
    # só arquivo em disco local (C:\...): caminho de rede faria o serviço se autenticar em outro computador
    if not WIN or not caminho or not re.fullmatch(r'[A-Za-z]:\\[^"<>|?*]+', caminho):
        return None
    chave = caminho.lower()
    with _lock:
        if chave in _mem:
            return _mem[chave]
    arq = os.path.join(PASTA, hashlib.sha1(chave.encode("utf-8")).hexdigest() + ".png")
    dados = None
    try:
        if os.path.exists(arq) and os.path.getmtime(arq) >= os.path.getmtime(caminho):
            with open(arq, "rb") as f:
                dados = f.read()
    except OSError:
        pass
    if dados is None and os.path.isfile(caminho):
        try:
            dados = _extrair(caminho)
        except Exception:
            dados = None
        if dados:
            try:
                os.makedirs(PASTA, exist_ok=True)
                with open(arq, "wb") as f:
                    f.write(dados)
            except OSError:
                pass
    with _lock:
        if len(_mem) > 2000:
            _mem.clear()
        _mem[chave] = dados
    return dados
