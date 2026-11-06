"""Testes das partes puras da captura de tela: atalhos, emenda da rolagem, uploader e validação da configuração.

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
