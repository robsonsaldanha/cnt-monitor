"""Testes das partes puras da captura de tela: atalhos, emenda da rolagem, anotações, uploader e configuração.

    python build/teste_captura.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import captura, envio, configuracao  # noqa: E402


def teste_atalhos():
    assert captura.ler_atalho("ctrl+shift+print")[2] == "Ctrl+Shift+Print"
    assert captura.ler_atalho("Print")[:2] == (0, 0x2C)
    assert captura.ler_atalho("win+shift+f13")[2] == "Shift+Win+F13"
    assert captura.ler_atalho("Ctrl+A")[2] == "Ctrl+A"
    for ruim in ("A", "Shift+A", "Space", "Ctrl+Ctrl+A", "Foo+X", "Ctrl+", ""):
        assert captura.ler_atalho(ruim) is None, ruim
    v = captura.validar_atalhos({"regiao": "ctrl+print", "janela": "Ctrl+Print", "ocr": "lixo", "cor": "Ctrl+Alt+C"})
    assert v["regiao"] == "Ctrl+Print"
    assert v["janela"] == ""            # repetido
    assert v["tela"] == ""              # o padrão (Ctrl+Print) também ficou repetido
    assert v["ocr"] == ""               # inválido
    assert v["cor"] == "Ctrl+Alt+C"
    assert v["video"] == "Shift+Print"  # ausente: fica o padrão
    assert set(v) == set(captura.ACOES)


def teste_rolagem():
    a = ["H1", "H2"] + [f"L{i}" for i in range(20)] + ["F"]
    b = ["H1", "H2"] + [f"L{i}" for i in range(5, 25)] + ["F"]
    assert captura._deslocamento(a, b) == (5, 2, 1)   # 5 linhas novas, cabeçalho de 2 e rodapé de 1 fixos
    assert captura._deslocamento(a, a) == (0, 0, 0)   # nada mudou: fim da página
    c = ["H1", "H2"] + [f"X{i}" for i in range(20)] + ["F"]
    assert captura._deslocamento(a, c)[0] is None     # sem emenda possível


def _pagina(altura=900, largura=400):
    """Página sintética: cada linha com um desenho diferente (como texto), com trechos repetidos em branco."""
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(7)
    img = Image.new("RGB", (largura, altura), "white")
    d = ImageDraw.Draw(img)
    for y in range(0, altura, 3):
        if (y // 30) % 4 == 3:
            continue   # faixas em branco: linhas iguais entre si
        for _ in range(4):
            x = rnd.randrange(0, largura - 60)
            d.rectangle((x, y, x + rnd.randrange(5, 60), y + 1), fill=(rnd.randrange(200), rnd.randrange(200), 90))
    return img


def _quadro(pagina, topo, altura, barra_y, hover=None):
    """Recorte visível com a barra de rolagem (que muda de lugar) e, opcional, uma faixa com efeito de hover."""
    from PIL import ImageDraw
    q = pagina.crop((0, topo, pagina.width, topo + altura))
    d = ImageDraw.Draw(q)
    d.rectangle((q.width - 16, 0, q.width - 1, q.height - 1), fill=(230, 230, 230))
    d.rectangle((q.width - 14, barra_y, q.width - 3, barra_y + 40), fill=(120, 120, 120))
    if hover:
        d.rectangle((40, hover, 200, hover + 4), fill=(255, 240, 180))
    return q


def teste_rolagem_tolerante():
    """A emenda precisa funcionar com a barra de rolagem mudando e um efeito de hover (antes parava no 1º passo)."""
    pag = _pagina()
    a = _quadro(pag, 0, 300, 0, hover=150)
    b = _quadro(pag, 90, 300, 30, hover=150)
    s, topo, base = captura._deslocamento(captura._linhas(a), captura._linhas(b))
    assert s == 90, (s, topo, base)
    # quase igual (só o hover mudou): conta como "não rolou"
    assert captura._deslocamento(captura._linhas(a), captura._linhas(_quadro(pag, 0, 300, 0, hover=60)))[0] == 0
    # rolou mais do que a área: não há parte em comum
    assert captura._deslocamento(captura._linhas(a), captura._linhas(_quadro(pag, 400, 300, 90)))[0] is None
    # emenda de vários passos reconstrói a página (fora da coluna da barra)
    primeiro = _quadro(pag, 0, 300, 0)
    emenda, ant = captura._Emenda(primeiro), captura._linhas(primeiro)
    for n, topo_q in enumerate((120, 260, 400, 600), 1):
        q = _quadro(pag, topo_q, 300, n * 20)
        lin = captura._linhas(q)
        s, _t, base = captura._deslocamento(ant, lin)
        assert s == topo_q - (0, 120, 260, 400)[n - 1], (topo_q, s)
        emenda.somar(q, s, base)
        ant = lin
    assert emenda.altura == 900
    final = emenda.imagem()
    assert final.size == (400, 900), final.size
    corte = (0, 0, 400 - captura._MARGEM_ROLAGEM, 900)
    assert final.crop(corte).tobytes() == pag.crop(corte).tobytes()


def teste_rolagem_cabecalho_fixo():
    from PIL import ImageDraw
    pag = _pagina()
    qs = []
    for topo in (0, 100):
        q = _quadro(pag, topo, 300, topo // 5)
        ImageDraw.Draw(q).rectangle((0, 0, q.width - 17, 39), fill=(20, 60, 160))   # cabeçalho que não rola
        qs.append(captura._linhas(q))
    s, topo, base = captura._deslocamento(*qs)
    assert s == 100 and topo >= 40, (s, topo, base)
    # cabeçalho e rodapé fixos aparecem uma vez só: em cima e no fim
    from PIL import Image

    def quadro(topo_q):
        q = _quadro(pag, topo_q, 300, topo_q // 5)
        ImageDraw.Draw(q).rectangle((0, 0, q.width - 17, 39), fill=(20, 60, 160))
        ImageDraw.Draw(q).rectangle((0, 270, q.width - 17, 299), fill=(160, 30, 30))
        return q
    q0 = quadro(0)
    emenda, ant = captura._Emenda(q0), captura._linhas(q0)
    for topo_q in (100, 200):
        q = quadro(topo_q)
        lin = captura._linhas(q)
        s, _t, base = captura._deslocamento(ant, lin)
        assert s == 100 and base >= 30, (s, base)
        emenda.somar(q, s, base)
        ant = lin
    final = emenda.imagem()
    esperado = Image.new("RGB", (400, 500))
    esperado.paste(q0.crop((0, 0, 400, 40)), (0, 0))
    esperado.paste(pag.crop((0, 40, 400, 470)), (0, 40))
    esperado.paste(q0.crop((0, 270, 400, 300)), (0, 470))
    corte = (0, 0, 400 - captura._MARGEM_ROLAGEM, 500)
    assert final.size == (400, 500), final.size
    assert final.crop(corte).tobytes() == esperado.crop(corte).tobytes()


def teste_anotacoes():
    from PIL import Image
    branco = Image.new("RGB", (200, 200), "white")
    formas = [
        {"t": "ret", "x0": 110, "y0": 110, "x1": 150, "y1": 150, "cor": "#ff0000", "esp": 4},
        {"t": "tarja", "x0": 260, "y0": 260, "x1": 240, "y1": 240, "cor": "#000000", "esp": 4},  # invertida
        {"t": "marca", "x0": 120, "y0": 260, "x1": 160, "y1": 280, "cor": "#ffeb3b", "esp": 4},
        {"t": "numero", "x0": 280, "y0": 130, "cor": "#1e88e5", "esp": 4, "n": 3},
        {"t": "texto", "x0": 105, "y0": 170, "cor": "#43a047", "esp": 4, "txt": "Olá"},
        {"t": "texto_fundo", "x0": 210, "y0": 205, "cor": "#8e24aa", "esp": 2, "txt": "a\nb"},
        {"t": "seta", "x0": 100, "y0": 100, "x1": 140, "y1": 100, "cor": "#ff0000", "esp": 2},
        {"t": "caneta", "x0": 0, "y0": 0, "pts": [[200, 280], [220, 290], [240, 285]], "cor": "#000000", "esp": 3},
        {"t": "cursor", "x0": 170, "y0": 110, "esp": 4},
        {"t": "elipse", "x0": 120, "y0": 120, "x1": 120, "y1": 120, "esp": 4},   # degenerada: não quebra
        {"t": "ret"},                                                              # sem coordenadas: ignorada
    ]
    out = captura.desenhar_formas(branco, formas, 100, 100)   # a imagem começa em (100, 100) da tela
    assert out.size == (200, 200) and branco.getpixel((10, 30)) == (255, 255, 255)   # original intacta
    assert out.getpixel((11, 30))[0] > 200 and out.getpixel((11, 30))[1] < 60       # borda do retângulo
    assert out.getpixel((150, 150)) == (0, 0, 0)                                   # tarja
    r, g, b = out.getpixel((40, 170))
    assert r > 200 and g > 200 and b < 150                                          # marca-texto amarelo
    assert out.getpixel((180, 30)) != (255, 255, 255)                               # número
    assert any(out.getpixel((x, y)) != (255, 255, 255) for x in range(5, 40) for y in range(70, 95))  # texto
    # desfocar e pixelar mudam um xadrez; fora da área fica igual
    xadrez = Image.new("RGB", (60, 60))
    xadrez.putdata([(255, 255, 255) if (x // 3 + y // 3) % 2 else (0, 0, 0) for y in range(60) for x in range(60)])
    for t in ("desfocar", "pixelar"):
        o = captura.desenhar_formas(xadrez, [{"t": t, "x0": 10, "y0": 10, "x1": 40, "y1": 40, "esp": 4}])
        assert o.crop((10, 10, 40, 40)).tobytes() != xadrez.crop((10, 10, 40, 40)).tobytes(), t
        assert o.crop((45, 45, 60, 60)).tobytes() == xadrez.crop((45, 45, 60, 60)).tobytes(), t
    # caixa para clicar e mover
    l, t, r, b = captura.caixa_forma({"t": "texto", "x0": 10, "y0": 10, "esp": 4, "txt": "abc"})
    assert l <= 10 and 0 < t < 20 and r > 30 and b > 25   # o topo das letras fica abaixo do ponto de origem
    m = captura.mover_forma({"t": "caneta", "x0": 1, "y0": 2, "x1": 1, "y1": 2, "pts": [[1, 2], [3, 4]]}, 10, -1)
    assert (m["x0"], m["y1"], m["pts"]) == (11, 1, [[11, 1], [13, 3]])


def teste_formatar_link():
    u = "https://files.catbox.moe/abc.png"
    assert envio.formatar_link(u, "url", "C:\\x\\a.png") == u
    assert envio.formatar_link(u, "markdown", "C:\\x\\a [1].png") == f"![a (1).png]({u})"
    assert envio.formatar_link(u, "html", "C:\\x\\a<b>.png") == f'<img src="{u}" alt="a&lt;b&gt;.png">'
    assert envio.formatar_link(u, "bbcode", "v.mp4") == f"[url={u}]v.mp4[/url]"
    assert envio.formatar_link(u, "html", "v.mp4") == f'<a href="{u}">v.mp4</a>'
    assert envio.formatar_link(u, None) == u


def teste_uploader():
    u = envio.validar_uploader({"url": "ftp://x", "cabecalhos": {"Authorization": "Bearer x\r\ny", "a:b": "1"}, "campo": "f<i>le"})
    assert u["url"] == "" and u["cabecalhos"] == {"Authorization": "Bearer xy", "ab": "1"} and u["campo"] == "file"
    assert envio.validar_uploader({"url": " https://x.com/up "})["url"] == "https://x.com/up"
    assert envio.caminho_json({"data": {"files": [{"url": "u"}]}}, "data.files.0.url") == "u"
    assert envio.caminho_json({"a": [1]}, "a.5") is None
    assert envio._link(" https://a.b/c ") == "https://a.b/c"
    for ruim in ("", "erro: arquivo grande", "https://a b", None):
        try:
            envio._link(ruim)
        except RuntimeError:
            continue
        raise AssertionError(ruim)


def teste_configuracao():
    r = configuracao._validar({"captura_nome": 'a<b>:c/%Y', "captura_qualidade": 5, "captura_destino": "xx",
                               "captura_atalhos": {"gif": ""}, "captura_imgur_id": "ab c-1", "captura_formato": "bmp"})
    assert r["captura_nome"] == "abc%Y"
    assert r["captura_qualidade"] == 50
    assert r["captura_destino"] == "catbox"
    assert r["captura_formato"] == "png"
    assert r["captura_atalhos"]["gif"] == "" and r["captura_atalhos"]["regiao"] == "Print"
    assert r["captura_imgur_id"] == "abc1"
    assert configuracao._validar({"captura_nome": "///"})["captura_nome"] == "%Y-%m-%d_%H-%M-%S"
    assert configuracao._validar({})["captura_uploader"]["campo"] == "file"
    v = configuracao._validar
    assert v({"captura_pasta": "Prints"})["captura_pasta"] == ""          # relativo: fica a padrão
    assert v({"captura_pasta": "/tmp/x"})["captura_pasta"] == ""          # sem unidade
    assert v({"captura_pasta": ' "D:\\Prints" '})["captura_pasta"] == "D:\\Prints"
    assert v({"captura_pasta": "\\\\nas\\fotos"})["captura_pasta"] == "\\\\nas\\fotos"
    assert v({"captura_envio_formato": "xml"})["captura_envio_formato"] == "url"
    assert v({"captura_envio_formato": "bbcode"})["captura_envio_formato"] == "bbcode"
    d = v({})
    assert d["captura_anotar"] and d["captura_envio_copiar"] and not d["captura_perguntar"]


def teste_cabecalhos_ocultos():
    """O token do uploader não sai por /api/config nem no backup, e salvar com o marcador mantém o valor."""
    import tempfile
    from monitorpc import config
    config.BASE = tempfile.mkdtemp()
    config.CONFIG = os.path.join(config.BASE, "config.json")
    configuracao.aplicar_inicializacao = lambda c=None: None
    configuracao.salvar({"captura_uploader": {"url": "https://x.com/up", "cabecalhos": {"Authorization": "Bearer seg"}}})
    pub = configuracao.publico()["captura_uploader"]["cabecalhos"]
    assert pub == {"Authorization": configuracao.OCULTO}
    assert configuracao.publico(True)["captura_uploader"]["cabecalhos"]["Authorization"] == "Bearer seg"
    assert configuracao.backup()["config"]["captura_uploader"]["cabecalhos"] == {}
    configuracao.salvar_publico({"captura_uploader": {"url": "https://x.com/up", "cabecalhos": {**pub, "X-Novo": "1"}}})
    assert configuracao.obter("captura_uploader")["cabecalhos"] == {"Authorization": "Bearer seg", "X-Novo": "1"}
    configuracao.restaurar(configuracao.backup())
    assert configuracao.obter("captura_uploader")["cabecalhos"]["Authorization"] == "Bearer seg"


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes da captura passaram")
