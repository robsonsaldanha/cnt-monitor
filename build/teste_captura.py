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
    def linhas(nomes):
        return [(x,) for x in nomes]   # cada linha é uma tupla de pedaços, como em _linhas

    a = linhas(["H1", "H2"] + [f"L{i}" for i in range(20)] + ["F"])
    b = linhas(["H1", "H2"] + [f"L{i}" for i in range(5, 25)] + ["F"])
    assert captura._deslocamento(a, b) == (5, 2, 1)   # 5 linhas novas, cabeçalho de 2 e rodapé de 1 fixos
    assert captura._deslocamento(a, a) == (0, 0, 0)   # nada mudou: fim da página
    c = linhas(["H1", "H2"] + [f"X{i}" for i in range(20)] + ["F"])
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


def teste_rolagem_lateral_fixa_e_ao_vivo():
    """Tela do próprio Monitor: barra lateral parada e números que mudam a cada atualização (antes subia, descia e
    parava no 1º passo). A barra lateral sai da imagem final em vez de repetir a cada pedaço."""
    import random
    from PIL import Image, ImageDraw
    pag = _pagina(1200, 400)
    ImageDraw.Draw(pag).rectangle((80, 0, 95, 1199), fill="white")      # margem lisa da página
    lateral = Image.new("RGB", (81, 300), (30, 30, 40))
    dl = ImageDraw.Draw(lateral)
    dl.line((80, 0, 80, 299), fill=(90, 90, 100))                        # borda da barra lateral
    for y in range(10, 290, 24):
        dl.rectangle((10, y, 60, y + 8), fill=(200, 200, 220))           # itens do menu
    rnd = random.Random(3)

    def quadro(topo_q):
        q = _quadro(pag, topo_q, 300, topo_q // 5)
        d = ImageDraw.Draw(q)
        for y in range(0, 1200, 60):                                     # valores ao vivo, presos à página
            if topo_q <= y < topo_q + 294:
                d.rectangle((120, y - topo_q, 120 + rnd.randrange(20, 100), y - topo_q + 5),
                            fill=(rnd.randrange(255), 50, 50))
        q.paste(lateral, (0, 0))
        return q

    q0 = quadro(0)
    emenda, ant, faixa, fixas = captura._Emenda(q0), q0.convert("L"), None, None
    for topo_q in range(100, 1000, 100):
        q = quadro(topo_q)
        s, topo, base, cols = captura._comparar(ant, q.convert("L"))
        assert s == 100, (topo_q, s)
        faixa = cols if faixa is None else (min(faixa[0], cols[0]), max(faixa[1], cols[1]))
        fixas = fixas or (topo, base)
        emenda.somar(q, s, base)
        ant = q.convert("L")
    assert captura._comparar(ant, quadro(900).convert("L"))[0] == 0     # fim da página: só os valores mudaram
    assert captura._comparar(q0.convert("L"), quadro(600).convert("L"))[0] is None   # rolou além da área
    x0, x1 = captura._alargar(q0.convert("L"), faixa[0], faixa[1], *fixas)
    assert (x0, x1) == (81, 400), (x0, x1)
    final = emenda.imagem(x0, x1)
    assert final.size == (319, 1200), final.size
    corte = (240, 0, 400 - captura._MARGEM_ROLAGEM, 1200)                # fora dos valores ao vivo
    assert final.crop((corte[0] - 81, 0, corte[2] - 81, 1200)).tobytes() == pag.crop(corte).tobytes()


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


def teste_config_so_local():
    """Pelo celular n\u00e3o d\u00e1 para mudar a\u00e7\u00f5es (programas), destino do envio nem a IA; no pr\u00f3prio PC d\u00e1."""
    configuracao.salvar_publico({"captura_acoes": [{"programa": "C:\\x.exe"}], "captura_destino": "uguu",
                                 "captura_borda": 4}, remoto=True)
    c = configuracao.obter()
    assert c["captura_acoes"] == [] and c["captura_destino"] == "catbox" and c["captura_borda"] == 4
    configuracao.salvar_publico({"captura_acoes": [{"programa": "C:\\x.exe"}], "captura_destino": "uguu"})
    c = configuracao.obter()
    assert c["captura_acoes"][0]["programa"] == "C:\\x.exe" and c["captura_destino"] == "uguu"


def teste_opcoes_novas():
    v = configuracao._validar
    d = v({})
    assert d["captura_aviso_clique"] == "abrir" and d["captura_sem_metadados"] and not d["captura_fixar"]
    r = v({"captura_formato": "webp", "captura_borda_cor": "#ABCDEF", "captura_marca_opacidade": 500,
           "captura_aviso_clique": "x", "captura_envio_compartilhar": "whatsapp", "captura_gravar_audio": 'Mic "1"\n',
           "captura_ia_prompt": "", "captura_emb_fundo": "neon"})
    assert r["captura_formato"] == "webp" and r["captura_borda_cor"] == "#abcdef" and r["captura_marca_opacidade"] == 100
    assert r["captura_aviso_clique"] == "abrir" and r["captura_envio_compartilhar"] == "whatsapp"
    assert r["captura_gravar_audio"] == "Mic 1" and r["captura_ia_prompt"] == d["captura_ia_prompt"]
    assert r["captura_emb_fundo"] == "azul"
    assert v({"captura_borda_cor": "red"})["captura_borda_cor"] == "#000000"


def teste_acoes_e_fluxos():
    a = captura.validar_acoes([{"programa": '"C:\\Prog\\x.exe"', "args": '-i "%arquivo" --out %pasta'},
                               {"programa": "a|b"}, "lixo", {"programa": ""}])
    assert len(a) == 1 and a[0]["programa"] == "C:\\Prog\\x.exe" and a[0]["nome"] == "x.exe"
    cmd = captura.montar_comando(a[0], "C:\\Fotos\\minha captura.png")
    assert cmd == ["C:\\Prog\\x.exe", "-i", "C:\\Fotos\\minha captura.png", "--out", "C:\\Fotos"]
    f = captura.validar_fluxos([
        {"id": "a1", "nome": "Enviar r\u00e1pido", "acao": "regiao", "atalho": "ctrl+f1",
         "tarefas": {"captura_enviar": 1, "captura_salvar": 0, "rm -rf": True}, "destino": "uguu"},
        {"id": "a1", "acao": "tela", "atalho": "Ctrl+F1", "destino": "xx"},
        {"acao": "cor"}])
    assert len(f) == 2
    assert f[0]["atalho"] == "Ctrl+F1" and f[0]["tarefas"] == {"captura_enviar": True, "captura_salvar": False}
    assert f[0]["destino"] == "uguu"
    assert f[1]["id"] != "a1" and f[1]["atalho"] == "" and f[1]["destino"] == "" and f[1]["nome"]


def teste_tarefas_rapidas():
    from monitorpc import capturador
    c = configuracao._validar({"captura_enviar": True, "captura_copiar": True, "captura_notificar": True})
    r = capturador.tarefas_rapidas(c, "fixar")
    assert r["captura_fixar"] and r["captura_copiar"] and not r["captura_enviar"] and not r["captura_salvar"]
    assert r["captura_notificar"]   # o aviso continua
    assert capturador.tipo_arquivo("a.WEBP") == "imagem" and capturador.tipo_arquivo("b.mkv") == "video"
    assert capturador.tipo_arquivo("c.gif") == "gif" and capturador.tipo_arquivo("d.zip") == "arquivo"


def teste_efeitos():
    from PIL import Image
    from monitorpc import efeitos
    img = Image.new("RGB", (200, 100), (10, 200, 30))
    e = efeitos.embelezar(img, margem=40, fundo="roxo", cantos=12, sombra=True)
    assert e.size == (280, 180) and e.mode == "RGBA"
    assert e.getpixel((140, 90))[:3] == (10, 200, 30)          # o meio \u00e9 a captura
    assert e.getpixel((40, 40))[:3] != (10, 200, 30)            # canto arredondado mostra o fundo
    t = efeitos.embelezar(img, margem=16, fundo="transparente", sombra=False)
    assert t.getpixel((0, 0))[3] == 0
    b = efeitos.borda(img, 4, "#ff0000")
    assert b.size == (208, 108) and b.getpixel((0, 0)) == (255, 0, 0) and b.getpixel((4, 4)) == (10, 200, 30)
    assert efeitos.posicao_marca((800, 600), (100, 20), "inf_dir") == (688, 568)
    assert efeitos.posicao_marca((800, 600), (100, 20), "sup_esq") == (12, 12)
    assert efeitos.posicao_marca((800, 600), (100, 20), "centro") == (350, 290)
    m = efeitos.marca_dagua(Image.new("RGB", (400, 300), (0, 0, 0)), "Monitor", "inf_dir", 100)
    assert m.mode == "RGB" and m.getbbox() is not None and m.crop((0, 0, 200, 150)).getbbox() is None
    assert efeitos.miniatura(Image.new("RGB", (1000, 500)), 300).size == (300, 150)
    base = Image.new("RGB", (100, 100), (255, 0, 0))
    j, pos = efeitos.juntar_regioes(base, [[10, 10, 30, 30], [50, 60, 70, 90]])
    assert pos == (10, 10) and j.size == (60, 80)
    assert j.getpixel((5, 5))[3] == 255 and j.getpixel((30, 20))[3] == 0 and j.getpixel((45, 60))[3] == 255


def teste_metadados_e_hash():
    import tempfile
    import shutil
    from PIL import Image
    from monitorpc import efeitos
    pasta = tempfile.mkdtemp()
    try:
        arq = os.path.join(pasta, "foto.jpg")
        exif = Image.Exif()
        exif[0x010F] = "CameraX"       # Make
        Image.new("RGB", (50, 40), (1, 2, 3)).save(arq, "JPEG", exif=exif)
        limpa = efeitos.sem_metadados(arq)
        assert limpa != arq
        with Image.open(limpa) as im:
            assert not im.getexif() and im.size == (50, 40)
        shutil.rmtree(os.path.dirname(limpa))
        png = os.path.join(pasta, "a.png")
        Image.new("RGB", (5, 5)).save(png)
        assert efeitos.sem_metadados(png) == png       # sem metadados: envia o pr\u00f3prio arquivo
        txt = os.path.join(pasta, "t.txt")
        with open(txt, "wb") as f:
            f.write(b"abc")
        s = efeitos.hash_arquivo(txt)
        assert s["SHA-256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        assert s["MD5"] == "900150983cd24fb0d6963f7d28e17f72"
        assert efeitos.confere_hash(s, " BA7816BF 8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad ") == "SHA-256"
        assert efeitos.confere_hash(s, "00") is None and efeitos.confere_hash(s, "") is None
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def teste_qr():
    from monitorpc import efeitos
    img = efeitos.qr_imagem("https://exemplo.com/x?a=1")
    assert efeitos.ler_qr(img) == ["https://exemplo.com/x?a=1"]
    from PIL import Image
    assert efeitos.ler_qr(Image.new("RGB", (80, 80), (255, 255, 255))) == []


def teste_salvar_formatos():
    import tempfile
    import shutil
    from PIL import Image
    pasta = tempfile.mkdtemp()
    try:
        rgba = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
        captura.salvar_imagem(rgba, os.path.join(pasta, "a.jpg"), {})
        with Image.open(os.path.join(pasta, "a.jpg")) as im:
            assert im.getpixel((5, 5))[0] > 240             # transparente vira branco no JPG
        captura.salvar_imagem(Image.new("RGB", (20, 10), (9, 9, 9)), os.path.join(pasta, "b.webp"),
                              {"captura_webp_sem_perda": True})
        with Image.open(os.path.join(pasta, "b.webp")) as im:
            assert im.format == "WEBP" and im.getpixel((1, 1))[:3] == (9, 9, 9)
        assert captura.extensao({"captura_formato": "webp"}) == ".webp" and captura.extensao({}) == ".png"
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def teste_area_transferencia_arquivo():
    import struct
    d = captura.dados_drop(["C:\\a b.png", "D:\\c.txt"])
    assert struct.unpack("<IiiII", d[:20]) == (20, 0, 0, 0, 1)
    assert d[20:].decode("utf-16-le") == "C:\\a b.png\0D:\\c.txt\0\0"


def teste_sxcu():
    u = envio.de_sxcu({"Version": "15.0.0", "Name": "Meu host", "DestinationType": "ImageUploader, FileUploader",
                       "RequestMethod": "POST", "RequestURL": "https://up.ex.com/api", "Parameters": {"k": "v w"},
                       "Headers": {"Authorization": "Bearer t"}, "Body": "MultipartFormData",
                       "Arguments": {"album": "1"}, "FileFormName": "image", "URL": "{json:files[0].url}"})
    assert u["url"] == "https://up.ex.com/api?k=v+w" and u["campo"] == "image" and u["resposta"] == "files.0.url"
    assert u["cabecalhos"] == {"Authorization": "Bearer t"} and u["campos"] == {"album": "1"} and u["nome"] == "Meu host"
    assert envio.de_sxcu({"RequestURL": "https://x.com/u", "URL": "$json:data.link$"})["resposta"] == "data.link"
    m = envio.de_sxcu({"RequestURL": "https://x.com/u", "URL": "https://i.x.com/{json:id}.png"})
    assert m["modelo"] == "https://i.x.com/{json:id}.png" and m["resposta"] == ""
    assert envio.de_sxcu({"RequestURL": "https://x.com/u", "URL": "{response}"})["modelo"] == ""
    for ruim in ({"RequestURL": "https://x.com", "RequestMethod": "GET"}, {"RequestURL": "https://x.com", "Body": "JSON"},
                 {"RequestURL": "ftp://x"}, {"Name": "sem url"}, {"RequestURL": "https://x.com", "URL": "{regex:a}"},
                 {"RequestURL": "https://x.com", "DestinationType": "URLShortener"}):
        try:
            envio.de_sxcu(ruim)
        except ValueError:
            continue
        raise AssertionError(ruim)
    assert envio.montar_link("https://i.x.com/{json:data.id}.png", b'{"data": {"id": "abc"}}') == "https://i.x.com/abc.png"
    assert envio.montar_link("{response}", b" https://a.b/c \n") == "https://a.b/c"


def teste_compartilhar():
    assert envio.link_compartilhar("whatsapp", "https://a.b/c?d=1") == "https://wa.me/?text=https%3A%2F%2Fa.b%2Fc%3Fd%3D1"
    assert envio.link_compartilhar("email", "https://a.b").startswith("mailto:?subject=")
    try:
        envio.link_compartilhar("orkut", "https://a.b")
    except ValueError:
        return
    raise AssertionError("servi\u00e7o inv\u00e1lido aceito")


def teste_gravacao_comando():
    from monitorpc import gravacao
    c = gravacao.comando("ff", (10, 20, 110, 80), "s.mp4", "video", 30, True, "Microfone (Realtek)")
    assert c[c.index("-video_size") + 1] == "100x60" and "audio=Microfone (Realtek)" in c and "aac" in c
    assert c[-1] == "s.mp4"
    g = gravacao.comando("ff", (0, 0, 10, 10), "g.mp4", "gif", 15, False, "Microfone")
    assert "dshow" not in g and "-crf" in g and g[g.index("-draw_mouse") + 1] == "0"
    novo = '[in#0 @ 1] "Microfone (Realtek Audio)" (audio)\n[in#0 @ 1]   Alternative name "@device_cm_x"\n' \
           '[in#0 @ 1] "OBS Virtual Camera" (video)\n[in#0 @ 1] "Mixagem est\u00e9reo (Realtek)" (audio)'
    assert gravacao.dispositivos_audio(novo) == ["Microfone (Realtek Audio)", "Mixagem est\u00e9reo (Realtek)"]
    antigo = '[dshow @ 1] DirectShow video devices\n[dshow @ 1]  "Webcam"\n[dshow @ 1] DirectShow audio devices\n' \
             '[dshow @ 1]  "Microfone"\n[dshow @ 1]     Alternative name "@device_cm_{1}"'
    assert gravacao.dispositivos_audio(antigo) == ["Microfone"]


def teste_aviso_posicao():
    from monitorpc import aviso
    area = (0, 0, 1920, 1040)
    assert aviso.posicao(0, area, "inf_dir", 380, 100) == (1526, 926)
    assert aviso.posicao(1, area, "inf_dir", 380, 100, 112) == (1526, 814)    # empilha para cima
    assert aviso.posicao(0, area, "sup_esq", 380, 100) == (14, 14)
    assert aviso.posicao(1, area, "sup_esq", 380, 100, 112) == (14, 126)      # empilha para baixo


def teste_ia_pedido():
    from monitorpc import ia
    url, cab, corpo = ia.montar_pedido("openai", "", "Descreva", "QUJD", "sk-1")
    assert url.endswith("/chat/completions") and cab == {"Authorization": "Bearer sk-1"}
    assert corpo["model"] == ia.PROVEDORES["openai"][1]
    assert corpo["messages"][0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
    url, cab, corpo = ia.montar_pedido("gemini", "gemini-x", "D", "QUJD", "g-1")
    assert "models/gemini-x:generateContent" in url and cab == {"x-goog-api-key": "g-1"} and "g-1" not in url
    assert ia.ler_resposta("openai", {"choices": [{"message": {"content": " oi "}}]}) == "oi"
    assert ia.ler_resposta("gemini", {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}) == "ab"
    try:
        ia.ler_resposta("openai", {"erro": 1})
    except RuntimeError:
        return
    raise AssertionError("resposta inv\u00e1lida aceita")


def teste_historico_banco():
    import tempfile
    from monitorpc import banco, config
    config.DB = os.path.join(tempfile.mkdtemp(), "h.db")
    banco.iniciar()
    banco.iniciar()   # migra\u00e7\u00e3o das colunas novas n\u00e3o quebra na 2\u00aa vez
    base = {"tipo": "imagem", "modo": "regiao", "caminho": "C:\\a.png", "largura": 1, "altura": 1, "bytes": 10}
    a = banco.captura_registrar(base)
    b = banco.captura_registrar({**base, "tipo": "video", "caminho": "C:\\b.mp4", "bytes": 30})
    banco.captura_atualizar(a, {"favorito": 1, "tags": "trabalho", "url": "https://files.catbox.moe/x.png"})
    banco.captura_atualizar(b, {"caminho": "C:\\novo.mp4"})
    itens = {x["id"]: x for x in banco.capturas_listar()}
    assert itens[a]["favorito"] and itens[a]["tags"] == "trabalho" and itens[b]["caminho"] == "C:\\novo.mp4"
    s = banco.capturas_stats()
    assert s["total"] == 2 and s["bytes"] == 40 and s["enviadas"] == 1 and s["favoritas"] == 1
    assert s["por_tipo"] == {"imagem": 1, "video": 1} and s["sites"] == {"files.catbox.moe": 1}
    assert len(s["por_dia"]) == 1 and s["por_dia"][0][1] == 2


def teste_formas_suaves():
    from PIL import Image
    branco = Image.new("RGB", (200, 200), "white")
    for forma in ({"t": "elipse", "x0": 20, "y0": 20, "x1": 160, "y1": 120},
                  {"t": "seta", "x0": 20, "y0": 30, "x1": 170, "y1": 140},
                  {"t": "numero", "x0": 100, "y0": 100, "n": 3},
                  {"t": "caneta", "x0": 10, "y0": 10, "pts": [[10, 10], [60, 90], [120, 40], [180, 160]]}):
        out = captura.desenhar_formas(branco, [{**forma, "cor": "#e53935", "esp": 4}])
        tons = {p for p in out.getdata() if p != (255, 255, 255)}
        assert len(tons) > 12, (forma["t"], len(tons))   # bordas com tons intermediários = sem serrilhado
        assert (200, 200) == out.size
    # forma parcialmente fora da imagem e com recorte deslocado não quebra
    captura.desenhar_formas(branco, [{"t": "seta", "x0": -50, "y0": -50, "x1": 30, "y1": 30, "cor": "#000000", "esp": 8}], 0, 0)


def teste_afastar_mouse_opcoes():
    from monitorpc import config, configuracao
    assert captura.afastar_mouse("") is None and captura.afastar_mouse("lugar-nenhum") is None
    v = configuracao._validar({"captura_afastar_mouse": "inf_dir", "captura_afastar_voltar": False})
    assert v["captura_afastar_mouse"] == "inf_dir" and v["captura_afastar_voltar"] is False
    assert configuracao._validar({"captura_afastar_mouse": "xx"})["captura_afastar_mouse"] == ""
    assert "" in config.OPCOES["captura_afastar_mouse"]


def teste_areas_da_tela():
    janela = (0, 0, 1000, 800)
    partes = [(-50, 0, 70, 800), (0, 0, 70, 800), (70, 0, 330, 800), (80, 100, 300, 140), (5, 5, 9, 9), janela]
    assert sorted(captura.recortar_partes(janela, partes)) == [(0, 0, 70, 800), (70, 0, 330, 800), (80, 100, 300, 140)]
    a = captura.AreasDaTela([(None, janela), (None, (0, 0, 2000, 1000))], partes=False)
    a.partes[0] = captura.recortar_partes(janela, partes)
    assert a.no_ponto(100, 120) == [(80, 100, 300, 140), (70, 0, 330, 800), janela]   # menor primeiro, janela por último
    assert a.no_ponto(20, 20) == [(0, 0, 70, 800), janela]
    assert a.no_ponto(1500, 50) == [(0, 0, 2000, 1000)]   # janela de trás só onde a da frente não cobre
    assert a.no_ponto(5000, 5000) == []


def _selecao_com_teclas(teclas, anotar=True):
    """Abre a seleção numa imagem falsa, escolhe uma área e aperta as teclas. Devolve (resultado, imagens copiadas)."""
    from PIL import Image
    img = Image.new("RGB", (400, 300), (40, 80, 120))
    areas = captura.AreasDaTela([(None, (0, 0, 400, 300))], partes=False)
    areas.partes[0] = [(10, 10, 110, 60)]
    copiadas, original = [], captura.copiar_imagem
    captura.copiar_imagem = copiadas.append
    s = captura._Selecao(img, 0, 0, "regiao", areas, anotar, False, {}, True, True)
    try:
        s._realcar_alvo(50, 30)                       # mouse sobre a parte (10, 10, 110, 60)
        s.ini = (50, 30)
        s.soltar_fase1(type("E", (), {"x": 50, "y": 30})())   # clique: pega a parte realçada
        if anotar:
            s.formas.append({"t": "ret", "x0": 20, "y0": 20, "x1": 60, "y1": 50, "cor": "#e53935", "esp": 4})
        for i, k in enumerate(teclas):
            s.root.after(50 + 30 * i, lambda k=k: s.root.event_generate(k))
        s.root.after(3000, s.root.destroy)            # segurança: não fica aberto se a tecla não fizer nada
        return s.rodar(), copiadas
    finally:
        captura.copiar_imagem = original
        captura.coletar_tk()


def teste_selecao_teclado():
    res, cop = _selecao_com_teclas(["<Control-c>", "<Escape>"])
    assert res is None and len(cop) == 1 and cop[0].size == (100, 50)   # Esc não salva, mas a cópia ficou
    assert cop[0].getpixel((25, 10)) != (40, 80, 120)                    # com a anotação desenhada
    res, cop = _selecao_com_teclas(["<Return>"])
    assert res[:4] == (10, 10, 110, 60) and len(res[4]) == 1 and not cop  # Enter = ✓
    res, cop = _selecao_com_teclas(["<Control-c>", "<Return>"])
    assert res[:4] == (10, 10, 110, 60) and len(cop) == 1
    res, cop = _selecao_com_teclas(["<Escape>"])
    assert res is None and not cop


def teste_selecao_roda_do_mouse():
    from PIL import Image
    areas = captura.AreasDaTela([(None, (0, 0, 400, 300))], partes=False)
    areas.partes[0] = [(10, 10, 110, 60), (0, 0, 200, 300)]
    s = captura._Selecao(Image.new("RGB", (400, 300)), 0, 0, "regiao", areas, False, False, {}, True, True)
    try:
        e = type("E", (), {"x": 50, "y": 30, "delta": 120})()
        assert s.janela_em(50, 30) == [10, 10, 110, 60]
        s.roda(e)
        assert s.hover == [0, 0, 200, 300]
        s.roda(e)
        s.roda(e)                                     # passa do topo: fica na janela inteira
        assert s.hover == [0, 0, 400, 300]
        e.delta = -120
        s.roda(e)
        assert s.janela_em(60, 40) == [0, 0, 200, 300]   # mexer o mouse dentro da escolhida mantém a escolha
        assert s.janela_em(300, 40) == [0, 0, 400, 300]  # fora dela volta à menor do ponto
    finally:
        s.root.destroy()
        captura.coletar_tk()


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes da captura passaram")
