"""Efeitos de imagem das tarefas pós-captura (PIL puro, sem janela): embelezar, borda, marca d'água, miniatura,
várias regiões numa imagem só, tirar metadados, hash e código QR (gerar e ler)."""
import os
import hashlib
import tempfile

# fundo do "embelezar": (cor de cima/esquerda, cor de baixo/direita); None = transparente
FUNDOS = {
    "azul": ((58, 123, 213), (0, 210, 255)),
    "roxo": ((131, 58, 180), (253, 29, 29)),
    "por_do_sol": ((255, 94, 98), (255, 195, 113)),
    "verde": ((17, 153, 142), (56, 239, 125)),
    "escuro": ((32, 36, 44), (16, 20, 26)),
    "claro": ((245, 247, 250), (222, 228, 236)),
    "transparente": None,
}


def _gradiente(w, h, c1, c2):
    """Gradiente diagonal (de cima à esquerda para baixo à direita)."""
    from PIL import Image
    faixa = Image.linear_gradient("L").rotate(45, expand=True).resize((w, h))
    a, b = Image.new("RGBA", (w, h), (*c1, 255)), Image.new("RGBA", (w, h), (*c2, 255))
    return Image.composite(b, a, faixa)


def _mascara_cantos(tam, raio):
    from PIL import Image, ImageDraw
    m = Image.new("L", tam, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, tam[0] - 1, tam[1] - 1], radius=raio, fill=255)
    return m


def embelezar(img, margem=40, fundo="azul", cantos=12, sombra=True):
    """Imagem com margem, fundo em gradiente (ou transparente), cantos arredondados e sombra. Devolve RGBA."""
    from PIL import Image, ImageFilter
    img = img.convert("RGBA")
    w, h = img.size
    margem = max(0, int(margem))
    W, H = w + 2 * margem, h + 2 * margem
    cores = FUNDOS.get(fundo, FUNDOS["azul"])
    out = _gradiente(W, H, *cores) if cores else Image.new("RGBA", (W, H), (0, 0, 0, 0))
    raio = max(0, min(int(cantos), min(w, h) // 2))
    mascara = _mascara_cantos((w, h), raio) if raio else Image.new("L", (w, h), 255)
    if sombra and margem:
        borrao = max(4, margem // 3)
        s = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        s.paste((0, 0, 0, 110), (margem, margem + borrao // 2), mascara)
        out = Image.alpha_composite(out, s.filter(ImageFilter.GaussianBlur(borrao)))
    out.paste(img, (margem, margem), Image.composite(img.getchannel("A"), Image.new("L", (w, h), 0), mascara))
    return out


def borda(img, esp, cor="#000000"):
    from PIL import Image
    esp = max(0, int(esp))
    if not esp:
        return img
    rgb = tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))
    modo = "RGBA" if img.mode == "RGBA" else "RGB"
    out = Image.new(modo, (img.width + 2 * esp, img.height + 2 * esp), rgb + ((255,) if modo == "RGBA" else ()))
    out.paste(img, (esp, esp))
    return out


def posicao_marca(tam_img, tam_txt, canto, margem=12):
    W, H = tam_img
    w, h = tam_txt
    if canto == "centro":
        return (W - w) // 2, (H - h) // 2
    x = margem if canto.endswith("esq") else W - w - margem
    y = margem if canto.startswith("sup") else H - h - margem
    return max(0, x), max(0, y)


def marca_dagua(img, texto, canto="inf_dir", opacidade=60):
    """Texto semitransparente com contorno, tamanho proporcional à imagem."""
    from PIL import Image, ImageDraw
    from .captura import _fonte
    texto = (texto or "").strip()
    if not texto:
        return img
    base = img.convert("RGBA")
    tam = max(12, min(base.width, base.height) // 22)
    fonte = _fonte(tam)
    camada = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    l, t, r, b = d.textbbox((0, 0), texto, font=fonte, stroke_width=2)
    x, y = posicao_marca(base.size, (r - l, b - t), canto)
    a = round(255 * max(10, min(100, int(opacidade))) / 100)
    d.text((x - l, y - t), texto, font=fonte, fill=(255, 255, 255, a), stroke_width=2, stroke_fill=(0, 0, 0, a))
    out = Image.alpha_composite(base, camada)
    return out if img.mode == "RGBA" else out.convert("RGB")


def aplicar_efeitos(img, c):
    """Borda e marca d'água conforme as Configurações."""
    img = borda(img, c.get("captura_borda") or 0, c.get("captura_borda_cor") or "#000000")
    return marca_dagua(img, c.get("captura_marca_texto"), c.get("captura_marca_canto") or "inf_dir",
                       c.get("captura_marca_opacidade") or 60)


def aplicar_embelezar(img, c):
    return embelezar(img, c.get("captura_emb_margem", 40), c.get("captura_emb_fundo") or "azul",
                     c.get("captura_emb_cantos", 12), c.get("captura_emb_sombra", True))


def miniatura(img, largura=300):
    m = img.copy()
    m.thumbnail((int(largura), 10 ** 6))
    return m


def juntar_regioes(img, regioes):
    """Várias regiões numa imagem só: o retângulo que envolve todas, transparente fora delas.
    regioes = [(l, t, r, b)] em coordenadas de `img`. Devolve (imagem RGBA, (l, t))."""
    from PIL import Image
    l = min(r[0] for r in regioes)
    t = min(r[1] for r in regioes)
    r_ = max(r[2] for r in regioes)
    b = max(r[3] for r in regioes)
    out = Image.new("RGBA", (r_ - l, b - t), (0, 0, 0, 0))
    for x0, y0, x1, y1 in regioes:
        out.paste(img.crop((x0, y0, x1, y1)).convert("RGBA"), (x0 - l, y0 - t))
    return out, (l, t)


# ---------------------------------------------------------------- arquivos
EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def sem_metadados(caminho):
    """Cópia temporária da imagem sem EXIF (GPS, câmera) nem textos embutidos; a orientação é aplicada antes.
    Devolve o caminho da cópia, ou o próprio arquivo se não for uma imagem que tenha metadados para tirar."""
    from PIL import Image, ImageOps
    ext = os.path.splitext(caminho)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
        return caminho
    with Image.open(caminho) as im:
        tem = bool(im.getexif()) or bool(getattr(im, "text", None)) or "exif" in im.info
        if not tem:
            return caminho
        limpa = ImageOps.exif_transpose(im)
        pasta = tempfile.mkdtemp(prefix="mpc-envio-")
        destino = os.path.join(pasta, os.path.basename(caminho))
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}.get(ext, "PNG")
        if fmt == "PNG" and ext not in (".png",):
            destino = os.path.splitext(destino)[0] + ".png"
        if fmt == "JPEG":
            limpa = limpa.convert("RGB")
            limpa.save(destino, fmt, quality=95)
        else:
            limpa.save(destino, fmt)
    return destino


def hash_arquivo(caminho):
    """MD5, SHA-1 e SHA-256 do arquivo (lido em blocos)."""
    hs = {"MD5": hashlib.md5(usedforsecurity=False), "SHA-1": hashlib.sha1(usedforsecurity=False),
          "SHA-256": hashlib.sha256()}
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            for h in hs.values():
                h.update(bloco)
    return {k: h.hexdigest() for k, h in hs.items()}


def confere_hash(somas, esperado):
    """Qual algoritmo bate com o valor colado (ignora maiúsculas e espaços); None se nenhum."""
    e = "".join((esperado or "").split()).lower()
    return next((k for k, v in somas.items() if e and v == e), None)


# ---------------------------------------------------------------- código QR
def qr_imagem(texto, caixa=8):
    import qrcode
    q = qrcode.QRCode(box_size=caixa, border=2)
    q.add_data(texto)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


def ler_qr(img):
    """Textos dos códigos QR (e de barras) encontrados na imagem."""
    try:
        import zxingcpp
    except ImportError:
        raise RuntimeError("o leitor de código QR (zxing-cpp) não está instalado") from None
    textos = []
    for r in zxingcpp.read_barcodes(img.convert("RGB")):
        if r.text and r.text not in textos:
            textos.append(r.text)
    return textos
