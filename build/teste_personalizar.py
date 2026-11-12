"""Testes do Personalizar: tema por horário, papel de parede, perfis, renomear em massa, lançador e remapear teclas.

    python build/teste_personalizar.py

Nada aqui mexe na tela nem no teclado. As gravações no registro (HKCU) só rodam no CI (variável CI), nunca no PC
de quem roda os testes.
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import personalizar as P, renomear as R, lancador as L, remapear as M, configuracao  # noqa: E402

WIN = os.name == "nt"


def _hora(h, m=0):
    import time
    return time.struct_time((2026, 11, 11, h, m, 0, 2, 315, 0))


def teste_periodo():
    c, e = P.minutos("07:00"), P.minutos("19:00")
    assert P.periodo(6 * 60 + 59, c, e) == "escuro" and P.periodo(7 * 60, c, e) == "claro"
    assert P.periodo(18 * 60 + 59, c, e) == "claro" and P.periodo(19 * 60, c, e) == "escuro"
    c, e = P.minutos("20:00"), P.minutos("06:00")       # claro à noite (quem trabalha de madrugada)
    assert P.periodo(21 * 60, c, e) == "claro" and P.periodo(7 * 60, c, e) == "escuro"
    assert P.minutos("24:00") is None and P.minutos("7:5") is None and P.minutos("07:05") == 425


def teste_caminhos():
    assert P.caminho_valido("") == ""
    assert P.caminho_valido("imagens\\a.png") is None
    absoluto = "C:\\Users\\x\\a.png" if WIN else "/home/x/a.png"
    assert P.caminho_valido(f'"{absoluto}"') == absoluto
    assert P.caminho_valido(absoluto + "\n") is None or P.caminho_valido(absoluto + "\n") == absoluto


class _Falso:
    def __init__(self):
        self.temas, self.papeis, self.posts = [], [], []

    def tema(self, modo):
        self.temas.append(modo)
        return True, "ok"

    def papel(self, c):
        self.papeis.append(c)
        return True, "ok"

    def post(self, caminho, dados, timeout=5):
        self.posts.append((caminho, dados))
        return {"ok": True, "msg": "ok"}


def _com_falsos(f):
    falso = _Falso()
    antes = P.aplicar_tema, P.definir_papel
    P.aplicar_tema, P.definir_papel = falso.tema, falso.papel
    pasta = tempfile.mkdtemp()
    try:
        f(falso, pasta)
    finally:
        P.aplicar_tema, P.definir_papel = antes
        shutil.rmtree(pasta, ignore_errors=True)


def teste_tema_so_na_virada():
    def corpo(falso, pasta):
        pz = P.Personalizador(pasta, falso.post)
        c = {"pers_tema_auto": True, "pers_tema_claro": "07:00", "pers_tema_escuro": "19:00",
             "pers_papel_escuro": "/x/noite.png"}
        pz.tick(c, _hora(10))
        pz.tick(c, _hora(12))      # mesmo período: não reaplica (não desfaz uma troca feita à mão)
        assert falso.temas == ["claro"] and falso.papeis == []
        pz.tick(c, _hora(19, 30))
        assert falso.temas == ["claro", "escuro"] and falso.papeis == ["/x/noite.png"]
        pz2 = P.Personalizador(pasta, falso.post)     # o ícone reiniciou: lembra o período já aplicado
        pz2.tick(c, _hora(20))
        assert falso.temas == ["claro", "escuro"]
        pz2.tick({**c, "pers_tema_auto": False}, _hora(8))
        assert falso.temas == ["claro", "escuro"]
    _com_falsos(corpo)


def teste_rotacao_do_papel():
    def corpo(falso, pasta):
        imgs = os.path.join(pasta, "imgs")
        os.makedirs(imgs)
        for n in ("a.jpg", "b.png", "c.bmp", "texto.txt"):
            open(os.path.join(imgs, n), "w").close()
        assert len(P.imagens_da_pasta(imgs)) == 3
        pz = P.Personalizador(pasta, falso.post)
        c = {"pers_papel_pasta": imgs, "pers_papel_minutos": 5, "pers_papel_aleatorio": False}
        pz.tick(c)
        pz.tick(c)                 # ainda não passaram 5 minutos
        assert len(falso.papeis) == 1
        pz.estado["papel_ts"] -= 301
        pz.tick(c)
        assert len(falso.papeis) == 2 and falso.papeis[0] != falso.papeis[1]
    _com_falsos(corpo)
    lista = ["a", "b", "c"]
    assert P.proximo_papel(lista, "c", False) == "a" and P.proximo_papel(lista, "x", False) == "a"
    assert P.proximo_papel(lista, "a", True, sorteio=lambda l: l[0]) == "b"
    assert P.proximo_papel([], None, True) is None


def teste_perfis():
    l = P.validar_perfis([{"nome": "A", "atalho": "ctrl+alt+1", "tema": "roxo", "jogo": "ligar", "abrir": ["relativo.exe"]},
                          {"nome": "B", "atalho": "Ctrl+Alt+1"}, {"nome": ""}, {"nome": "A", "id": "x"}])
    assert [p["nome"] for p in l] == ["A", "B", "A"]
    assert l[0]["atalho"] == "Ctrl+Alt+1" and l[1]["atalho"] == ""      # atalho repetido fica sem
    assert l[0]["tema"] == "" and l[0]["jogo"] == "ligar" and l[0]["abrir"] == []
    assert len({p["id"] for p in l}) == 3
    assert [p["id"] for p in P.validar_perfis(P.PERFIS_PADRAO)] == ["trabalho", "jogo", "apresentacao"]

    def corpo(falso, pasta):
        pz = P.Personalizador(pasta, falso.post)
        inexistente = os.path.join(pasta, "nao-existe.exe")
        p = P.validar_perfis([{"id": "j", "nome": "Jogo", "tema": "escuro", "jogo": "ligar", "mini": "ligar",
                               "plano": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "abrir": [inexistente]}])[0]
        ok, msg = pz.aplicar_perfil(p)
        assert not ok and "Não deu" in msg and "nao-existe.exe" in msg
        assert falso.temas == ["escuro"]
        assert ("api/acao", {"tipo": "energia_plano", "guid": p["plano"]}) in falso.posts
        assert ("api/acao", {"tipo": "jogo_ligar"}) in falso.posts
        assert ("api/config", {"mini_ativo": True}) in falso.posts
        assert pz.perfil_ativo() == "j"
        assert "tema escuro" in P.descrever_perfil(p)
    _com_falsos(corpo)


def teste_renomear_regras():
    r = R.regras_validas({"buscar": "IMG_", "substituir": "Foto ", "numerar": True, "inicio": 7, "digitos": 3})
    assert R.novo_nome("IMG_1234.JPG", r, 0) == "Foto 1234 007.JPG"
    assert R.novo_nome("IMG_1234.JPG", r, 2) == "Foto 1234 009.JPG"
    r = R.regras_validas({"buscar": r"(\d+)-(\d+)", "substituir": r"\2-\1", "regex": True})
    assert R.novo_nome("10-20.txt", r, 0) == "20-10.txt"
    r = R.regras_validas({"alvo": "extensao", "caixa": "minusculas"})
    assert R.novo_nome("foto.JPG", r, 0) == "foto.jpg"
    r = R.regras_validas({"caixa": "titulo", "prefixo": "2026 - "})
    assert R.novo_nome("relatório de vendas.pdf", r, 0) == "2026 - Relatório De Vendas.pdf"
    r = R.regras_validas({"buscar": "a", "substituir": "o", "todas": False})
    assert R.novo_nome("banana.txt", r, 0) == "bonana.txt"
    r = R.regras_validas({"buscar": "A", "substituir": "x", "diferenciar": True})
    assert R.novo_nome("aA.txt", r, 0) == "ax.txt"
    try:
        R.planejar([("a.txt", False)], {"buscar": "(", "regex": True})
        assert False, "regex inválida deveria dar erro"
    except ValueError as e:
        assert "inválida" in str(e)


def teste_renomear_conflitos():
    itens = [("a.txt", False), ("b.txt", False), ("fica.txt", False), ("pasta", True)]
    plano = R.planejar(itens, {"buscar": "a", "substituir": "b", "filtro": "a.txt;b.txt"})
    assert [x["novo"] for x in plano] == ["b.txt", "b.txt"]
    a = plano[0]
    assert a["erro"] == "dois arquivos ficariam com o mesmo nome" or a["erro"] == "já existe um arquivo com esse nome"
    plano = R.planejar(itens, {"buscar": "a", "substituir": "fic", "filtro": "a.txt"})
    assert plano[0]["novo"] == "fic.txt" and plano[0]["erro"] is None
    plano = R.planejar(itens, {"prefixo": "x", "incluir_pastas": True})
    assert any(x["pasta"] and x["novo"] == "xpasta" for x in plano)
    plano = R.planejar([("a.txt", False)], {"buscar": "a", "substituir": "b/c"})
    assert "caractere" in plano[0]["erro"]
    plano = R.planejar([("a.txt", False), ("b.txt", False)], {"buscar": "a", "substituir": "b"})
    assert plano[0]["erro"] == "já existe um arquivo com esse nome"      # b.txt não muda: fica lá
    if WIN:
        assert R.planejar([("x.txt", False)], {"buscar": "x", "substituir": "con"})[0]["erro"] == "nome reservado do Windows"
    assert R.resumo(plano)["erros"] == 1


def teste_renomear_no_disco():
    pasta = tempfile.mkdtemp()
    desf = os.path.join(pasta, "..", f"desfazer-{os.getpid()}.json")
    try:
        for n in ("um.txt", "dois.txt", "tres.txt"):
            with open(os.path.join(pasta, n), "w") as f:
                f.write(n)
        ok, msg, d = R.aplicar(pasta, {"numerar": True, "num_posicao": "inicio", "num_sep": "_", "inicio": 1}, desf)
        assert ok and d["feitos"] == 3, msg
        assert sorted(os.listdir(pasta)) == ["01_dois.txt", "02_tres.txt", "03_um.txt"]
        with open(os.path.join(pasta, "03_um.txt")) as f:
            assert f.read() == "um.txt"
        assert R.ha_desfazer(desf)["n"] == 3
        ok, msg, _d = R.desfazer(desf)
        assert ok and sorted(os.listdir(pasta)) == ["dois.txt", "tres.txt", "um.txt"], msg
        assert R.ha_desfazer(desf) is None
        feitos, erros = R._mover_em_duas_etapas(pasta, [("um.txt", "dois.txt"), ("dois.txt", "um.txt")])   # troca
        assert len(feitos) == 2 and not erros
        with open(os.path.join(pasta, "dois.txt")) as f:
            assert f.read() == "um.txt"
        ok, msg, _d = R.aplicar(pasta, {"buscar": "nada-casa"}, desf)
        assert not ok and msg == "Nada para renomear."
    finally:
        shutil.rmtree(pasta, ignore_errors=True)
        if os.path.exists(desf):
            os.remove(desf)


def teste_lancador_busca():
    assert L.pontuar("Google Chrome", "google chrome") == 100
    assert L.pontuar("Google Chrome", "goo") > L.pontuar("Google Chrome", "chr") > L.pontuar("Google Chrome", "ogle")
    assert L.pontuar("Visual Studio Code", "vsc") == 65
    assert L.pontuar("Configurações › Notificações", "notificacoes") == 75     # sem acento também acha
    assert L.pontuar("Bloco de Notas", "xyz") == 0
    itens = [{"tipo": "app", "nome": "Calculadora", "sub": "", "alvo": "calc"},
             {"tipo": "app", "nome": "Calendário", "sub": "", "alvo": "cal"},
             {"tipo": "app", "nome": "Paint", "sub": "", "alvo": "paint"}]
    assert [x["alvo"] for x in L.buscar(itens, "cal")] == ["calc", "cal"]
    assert [x["alvo"] for x in L.buscar(itens, "cal", {"app:cal": 3})] == ["cal", "calc"]   # o mais usado sobe
    assert L.buscar(itens, "?gatos")[0] == {"tipo": "web", "nome": "Pesquisar na web: gatos", "sub": "navegador", "alvo": "gatos"}
    assert L.buscar(itens, ">ipconfig")[0]["tipo"] == "cmd"
    assert L.buscar(itens, "12*3")[0] == {"tipo": "calc", "nome": "= 36", "sub": "Enter copia o resultado", "alvo": "36"}
    assert all(x["tipo"] != "calc" for x in L.buscar(itens, "2024"))
    assert L.buscar(itens, "zzzz")[0]["tipo"] == "web"
    assert L.buscar(itens, "") == []
    assert any(x["alvo"] == "personalizar" for x in L.itens_fixos())


def teste_calculadora_segura():
    assert L.calcular("2+3*4") == 14 and L.calcular("2^10") == 1024 and L.calcular("=(1+1)*-3") == -6
    assert L.formatar_numero(L.calcular("10/4")) == "2,5" and L.formatar_numero(L.calcular("1,5*2")) == "3"
    for ruim in ("__import__('os')", "2**99999", "1/0", "abc", "", "9**9**9", "[1]*9"):
        assert L.calcular(ruim) is None, ruim


def teste_remapear_validar():
    l = M.validar([{"de": "capslock", "para": "ctrl"}, {"de": "ctrl+alt+t", "para": "ctrl+shift+esc", "programa": "Chrome.EXE"},
                   {"de": "F1"}, {"de": "ctrl+ctrl"}, {"de": "ctrl+shift"}, {"de": "CapsLock", "para": "Esc"},
                   {"de": "F2", "abrir": "relativo.exe"}, {"de": "F3", "para": "F3"}, {"de": "win+e", "para": "ctrl+c"},
                   {"de": "F4", "programa": "c:/x/chrome.exe"}])
    assert [(r["de"], r["para"], r["programa"]) for r in l] == [
        ("CapsLock", "Ctrl", ""), ("Ctrl+Alt+T", "Ctrl+Shift+Esc", "chrome.exe"), ("F1", "", ""), ("Win+E", "Ctrl+C", ""),
        ("F4", "", "")]
    assert M.ler("Ctrl+Alt+Delete") == (frozenset({"ctrl", "alt"}), "Delete") and M.ler("shift+") is not None
    assert M.ler("ctrl+CtrlDireito") is None and M.ler("") is None and M.ler("Hiper+A") is None


def teste_remapear_decisoes():
    rm = M.Remapeador(M.validar([{"de": "CapsLock", "para": "Ctrl"}, {"de": "Ctrl+Alt+T", "para": "Ctrl+Shift+Esc"},
                                 {"de": "F1"}, {"de": "Win+E", "para": "Ctrl+C"},
                                 {"de": "F12", "abrir": "C:\\calc.exe" if WIN else "/usr/bin/calc"},
                                 {"de": "F9", "para": "PlayPause", "programa": "chrome.exe"}]))
    CAPS, CTRL, ALT, T, SHIFT, ESC, LWIN, E, C = 0x14, 0xA2, 0xA4, 0x54, 0xA0, 0x1B, 0x5B, 0x45, 0x43
    assert rm.evento(CAPS, True) == (True, [(CTRL, True)], None)
    assert rm.mods == {CTRL: "ctrl"}                       # o Ctrl que saiu do CapsLock conta para atalhos
    assert rm.evento(CAPS, True) == (True, [(CTRL, True)], None)        # repetição da tecla segurada
    assert rm.evento(CAPS, False) == (True, [(CTRL, False)], None) and rm.mods == {}
    assert rm.evento(CTRL, True) == (False, [], None)
    assert rm.evento(ALT, True) == (False, [], None)
    bloq, seq, _a = rm.evento(T, True)
    assert bloq and seq == [(ALT, False), (SHIFT, True), (ESC, True), (ESC, False), (SHIFT, False), (ALT, True)]
    assert rm.evento(T, False) == (True, [], None)        # a subida do T também não chega ao Windows
    assert rm.evento(ALT, False) == (False, [], None) and rm.evento(CTRL, False) == (False, [], None)
    assert rm.evento(T, True) == (False, [], None)        # T sem Ctrl+Alt passa normal
    rm.evento(T, False)
    assert rm.evento(0x70, True) == (True, [], None) and rm.evento(0x70, False) == (True, [], None)   # F1 desativada
    rm.evento(LWIN, True)
    bloq, seq, _a = rm.evento(E, True)
    assert bloq and seq[:3] == [(M.VK_NEUTRA, True), (M.VK_NEUTRA, False), (LWIN, False)] and seq[-1] == (LWIN, True)
    assert (CTRL, True) in seq and (C, True) in seq
    rm.evento(E, False)
    rm.evento(LWIN, False)
    b, s, abrir = rm.evento(0x7B, True)
    assert b and abrir and rm.evento(0x7B, True)[2] is None    # segurar F12 não abre 30 calculadoras
    rm.evento(0x7B, False)
    assert rm.evento(0x78, True, "notepad.exe") == (False, [], None)
    rm.evento(0x78, False, "notepad.exe")
    assert rm.evento(0x78, True, "chrome.exe") == (True, [(0xB3, True)], None)
    assert rm.usa_programa and not M.Remapeador([]).ativo


def teste_configuracao():
    c = configuracao._validar({"pers_tema_claro": "6:3", "pers_tema_escuro": "18:45", "pers_papel_minutos": 7,
                               "pers_lancador_atalho": "space", "pers_papel_pasta": "relativa",
                               "pers_remapear": [{"de": "CapsLock", "para": "Esc"}], "pers_perfis": "não é lista"})
    assert c["pers_tema_claro"] == "07:00" and c["pers_tema_escuro"] == "18:45"   # "6:3" é inválido (fica o padrão)
    assert configuracao._validar({"pers_tema_claro": "6:30"})["pers_tema_claro"] == "06:30"
    assert c["pers_papel_minutos"] == 0 and c["pers_lancador_atalho"] == "" and c["pers_papel_pasta"] == ""
    assert c["pers_remapear"][0]["para"] == "Esc" and [p["id"] for p in c["pers_perfis"]][0] == "trabalho"
    assert configuracao._validar({})["pers_lancador_atalho"] == "Alt+Space"
    assert {"pers_perfis", "pers_remapear", "pers_papel_pasta"} <= configuracao.SO_LOCAL


def teste_windows_registro():
    """Só no CI (Windows): grava no HKCU do usuário do CI e confere a leitura; devolve como estava."""
    if not (WIN and os.environ.get("CI")):
        print("  (pulado: só roda no CI do Windows)")
        return
    import winreg
    antes = {x["id"]: x["valor"] for x in P.ler_sistema()}
    for ident, valor in (("relogio_segundos", True), ("explorer_extensoes", True), ("barra_pesquisa", "oculta"),
                         ("menu_classico", True)):
        ok, msg = P.aplicar_sistema(ident, valor)
        assert ok, msg
        assert {x["id"]: x["valor"] for x in P.ler_sistema()}[ident] == valor, ident
        P.aplicar_sistema(ident, antes[ident] if antes[ident] is not None else False)
    assert P.aplicar_sistema("barra_pesquisa", "inventado") == (False, "Valor inválido.")
    tema = P.tema_atual()
    assert P.aplicar_tema("escuro")[0] and P.tema_atual() == "escuro"
    P.aplicar_tema(tema)
    sub = P.NOTIFY + "\\987654321012"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, sub) as k:
        winreg.SetValueEx(k, "ExecutablePath", 0, winreg.REG_SZ, "{F38BF404-1D43-42F2-9305-67DE0B28FC23}\\notepad.exe")
        winreg.SetValueEx(k, "InitialTooltip", 0, winreg.REG_SZ, "Teste do CNT Monitor")
    try:
        assert P.definir_icone("987654321012", True)[0]
        it = next(x for x in P.icones_bandeja() if x["id"] == "987654321012")
        assert it["visivel"] and it["existe"] and it["nome"] == "Teste do CNT Monitor"
        assert P.definir_icone("../x", True)[0] is False
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes do Personalizar passaram")
