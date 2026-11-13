"""Testes do acesso remoto (códigos, prova da senha e regras da sessão).

    python build/teste_remoto.py
"""
import os
import sys
import json
import zlib
import base64
import shutil
import tempfile

DADOS = tempfile.mkdtemp(prefix="mpc-teste-remoto-")
os.environ["MONITORPC_DADOS"] = DADOS   # antes de importar: nada toca na pasta de dados de verdade
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import remoto, encontro  # noqa: E402


def _sdp(byte):
    fp = ":".join([f"{byte:02X}"] * 32)
    return ("v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            f"a=fingerprint:sha-256 {fp}\r\na=fingerprint:sha-384 AA:BB\r\n"
            f"m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\na=fingerprint:sha-256 {fp.lower()}\r\n")


A, T, INTRUSO = _sdp(0xA1), _sdp(0xB2), _sdp(0xC3)


def _erro(f, *a):
    try:
        f(*a)
    except remoto.Erro as e:
        return str(e)
    raise AssertionError(f"{f.__name__}{a!r:.80} deveria ter sido recusado")


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def teste_senha():
    s = remoto.nova_senha()
    assert len(s) == 8 and set(s) <= set(remoto.ALFABETO) and remoto.nova_senha() != s
    f = remoto.formatar_senha(s)
    assert len(f) == 9 and f.count("-") == 1
    assert remoto.limpar_senha(" " + f.lower().replace("-", " - ") + "\n") == s


def teste_codigos():
    import re
    sem384 = lambda s: s.replace("a=fingerprint:sha-384 AA:BB\r\n", "")     # noqa: E731  (só a SHA-256 viaja)
    c = remoto.convite(A)
    assert c.startswith("MPCC") and remoto.ler_convite(c) == sem384(A)
    assert remoto.impressao(remoto.ler_convite(c)) == remoto.impressao(A)
    r = remoto.resposta(T)
    assert r.startswith("MPCR") and remoto.ler_resposta(r) == sem384(T)
    # só letras maiúsculas e números: nada que WhatsApp, Telegram ou e-mail tratem como formatação
    assert re.fullmatch(r"[A-Z2-7]+", c) and re.fullmatch(r"[A-Z2-7]+", r)
    # colado com quebras de linha, espaços, minúsculas e símbolos de formatação enfiados no meio
    assert remoto.ler_convite("  " + c[:40] + "\r\n " + c[40:] + "\n") == sem384(A)
    assert remoto.ler_convite("*" + c[:9].lower() + "_" + c[9:50] + "_ ~" + c[50:] + "~*​") == sem384(A)
    assert "código de resposta" in _erro(remoto.ler_convite, r)        # resposta no lugar do convite e vice-versa
    assert "convite" in _erro(remoto.ler_resposta, c)
    assert "conferência" in _erro(remoto.ler_resposta, "572 065")      # os 6 números não são o código de resposta
    assert "versão antiga" in _erro(remoto.ler_convite, "MPC1-eNrtVltv2zYU_itE")
    assert "Cole" in _erro(remoto.ler_convite, "") and "Cole" in _erro(remoto.ler_convite, None)
    for n in range(1, 30):                                              # cortado em qualquer ponto do final
        assert "cortado" in _erro(remoto.ler_convite, c[:-n]), n
    for i in (10, len(c) // 2, len(c) - 3):                             # uma letra trocada no caminho
        assert "alterado" in _erro(remoto.ler_convite, c[:i] + ("A" if c[i] != "A" else "B") + c[i + 1:]), i
    for ruim in ("MPCC", "MPCC@@@", "MPCC" + "A" * 50000, "qualquer texto",
                 remoto._codigo("MPCC", "v=0\r\nsem identidade\r\n")):
        _erro(remoto.ler_convite, ruim)

    def cru(dados):
        z = zlib.compressobj(9, zdict=remoto._DICIONARIO)
        return "MPCC" + base64.b32encode(z.compress(dados) + z.flush()).decode().rstrip("=")

    assert remoto.ler_convite(cru(json.dumps({"v": 2, "sdp": A}).encode())) == A      # o molde do teste é válido
    _erro(remoto.ler_convite, cru(b'["lista"]'))
    _erro(remoto.ler_convite, cru(b'{"v":1,"sdp":"x"}'))
    _erro(remoto.ler_convite, cru(b'{"v":2,"sdp":5}'))
    _erro(remoto.ler_convite, cru(b" " * 50_000_000))      # 50 MB compactados em poucos KB: para no limite
    _erro(remoto.convite, "x" * 70000)


def teste_impressao():
    assert remoto.impressao(A) == ":".join(["A1"] * 32)      # a mesma, repetida nas duas seções
    _erro(remoto.impressao, "v=0\r\n")
    _erro(remoto.impressao, A + T)                            # duas identidades diferentes no mesmo código
    _erro(remoto.impressao, "a=fingerprint:sha-256 A1:B2\r\n")


def teste_prova():
    k = remoto.chave("ABCD-EFGH-JKLM", A, T)
    assert k == remoto.chave("abcdefghjklm", A, T) and len(k) == 32
    mac = remoto.prova(k, "tecnico")
    assert remoto.confere(k, "tecnico", mac) and remoto.confere(k, "tecnico", mac.upper())
    assert not remoto.confere(k, "atendido", mac)             # a prova de um lado não serve para o outro
    assert not remoto.confere(k, "tecnico", None) and not remoto.confere(k, "tecnico", "")
    # senha errada, ou alguém no meio (qualquer uma das duas identidades trocada): a chave sai outra
    for outra in (remoto.chave("ABCD-EFGH-JKLN", A, T), remoto.chave("ABCD-EFGH-JKLM", INTRUSO, T),
                  remoto.chave("ABCD-EFGH-JKLM", A, INTRUSO), remoto.chave("ABCD-EFGH-JKLM", T, A)):
        assert outra != k and not remoto.confere(outra, "tecnico", mac)
    c = remoto.conferencia(A, T)
    assert len(c) == 7 and c.replace(" ", "").isdigit() and c == remoto.conferencia(A, T) != remoto.conferencia(A, INTRUSO)


def _ate_prova(s, senha=None):
    assert remoto.impressao(s.receber_resposta(remoto.resposta(T))) == remoto.impressao(T)
    return s.conferir_prova(A, T, remoto.prova(remoto.chave(senha or s.senha, A, T), "tecnico"))


def teste_sessao_caminho_feliz():
    r = Relogio()
    s = remoto.Sessao(r)
    senha = s.senha
    assert s.estado == "convite" and not s.ativa
    mac = _ate_prova(s)
    assert remoto.confere(remoto.chave(senha, A, T), "atendido", mac)     # o técnico confere o outro lado
    assert s.estado == "aguardando_aceite" and s.conferencia == remoto.conferencia(A, T)
    assert not s.ativa                                                     # sem aceite, nada de vídeo nem controle
    s.aceitar()
    assert s.ativa
    r.t += remoto.DURACAO_MAXIMA + 1
    assert not s.ativa and s.estado == "encerrada" and "tempo máximo" in s.motivo
    assert s.senha == ""


def teste_sessao_senha_errada_cancela_o_convite():
    s = remoto.Sessao(Relogio())
    assert "senha" in _erro(_ate_prova, s, "AAAA-AAAA-AAAA")
    assert s.estado == "encerrada" and s.senha == ""
    _erro(s.receber_resposta, remoto.resposta(T))                          # nem com a resposta certa
    _erro(s.conferir_prova, A, T, "00")
    _erro(s.aceitar)
    assert not s.ativa


def teste_sessao_alguem_no_meio():
    """O intruso troca a identidade no convite que o técnico recebe: mesmo com a senha certa, a prova falha."""
    s = remoto.Sessao(Relogio())
    s.receber_resposta(remoto.resposta(T))
    mac_do_tecnico = remoto.prova(remoto.chave(s.senha, INTRUSO, T), "tecnico")   # o técnico viu o intruso
    _erro(s.conferir_prova, A, T, mac_do_tecnico)
    assert s.estado == "encerrada"


def teste_sessao_ordem_e_reuso():
    s = remoto.Sessao(Relogio())
    _erro(s.aceitar)                                                        # aceite antes da prova
    _erro(s.conferir_prova, A, T, "00")                                     # prova antes da resposta
    assert s.estado == "convite"
    _erro(s.receber_resposta, "MPCRLIXO")                                  # mal colado: pode tentar de novo
    assert s.estado == "convite"
    _ate_prova(s)
    assert "já foi usado" in _erro(s.receber_resposta, remoto.resposta(T))   # o convite vale uma vez
    _erro(s.conferir_prova, A, T, "00")                                     # e a prova também
    assert s.estado == "aguardando_aceite"                                  # repetir não derruba quem está certo
    s.aceitar()
    _erro(s.aceitar)
    assert s.ativa
    s.encerrar("Encerrado por quem está no computador.")
    assert not s.ativa and s.motivo.startswith("Encerrado")
    s.encerrar("outro")
    assert s.motivo.startswith("Encerrado")


def teste_sessao_prazos():
    for avancar, ate, trecho in ((0, remoto.VALIDADE_CONVITE, "venceu"), (1, remoto.ESPERA_CONEXAO, "não se completou"),
                                 (2, remoto.ESPERA_ACEITE, "a tempo")):
        r = Relogio()
        s = remoto.Sessao(r)
        if avancar >= 1:
            s.receber_resposta(remoto.resposta(T))
        if avancar >= 2:
            s.conferir_prova(A, T, remoto.prova(remoto.chave(s.senha, A, T), "tecnico"))
        r.t += ate - 1
        assert s.conferir_prazo() != "encerrada"
        r.t += 2
        passo = (lambda: s.receber_resposta(remoto.resposta(T)), lambda: s.conferir_prova(A, T, "00"), s.aceitar)[avancar]
        assert trecho in _erro(passo), (avancar, s.motivo)
        assert s.estado == "encerrada" and not s.ativa


# ---------------------------------------------------------------- agente: conexão de verdade, na mesma máquina
_MON1 = {"x": 0, "y": 0, "w": 640, "h": 360, "principal": True}


class AreaFalsa:
    """Área de transferência de mentira: os testes não mexem na de quem está rodando."""

    def __init__(self):
        self.n, self.conteudo, self.escritos = 1, None, []

    def seq(self):
        return self.n

    def ler(self):
        return self.conteudo

    def copiar(self, conteudo):          # quem é atendido copiou alguma coisa
        self.conteudo, self.n = conteudo, self.n + 1

    def _escrever(self, tipo, valor):
        self.escritos.append((tipo, valor))
        self.conteudo, self.n = (tipo, valor), self.n + 1

    def texto(self, t):
        self._escrever("texto", t)

    def imagem(self, png):
        self._escrever("imagem", png)

    def arquivos(self, caminhos):
        self._escrever("arquivos", list(caminhos))


def teste_mouse_em_varios_monitores():
    """Monitor 2 à direita, maior e mais alto que o principal: o clique cai no pixel certo da área de trabalho inteira."""
    import remoto_agente as ra
    mons = [{"x": 0, "y": 0, "w": 1920, "h": 1080}, {"x": 1920, "y": -200, "w": 2560, "h": 1440},
            {"x": -1280, "y": 100, "w": 1280, "h": 1024}]
    vx, vy, vw, vh = ra.area_virtual(mons)
    assert (vx, vy, vw, vh) == (-1280, -200, 5760, 1440)
    for m, fx, fy, px, py in ((0, 0, 0, 0, 0), (0, 1, 1, 1919, 1079), (1, 0, 0, 1920, -200), (1, 1, 1, 4479, 1239),
                              (1, 0.5, 0.5, 1920 + 1280, -200 + 720), (2, 0, 0, -1280, 100), (2, 1, 1, -1, 1123)):
        ev = ra.traduzir({"t": "mouse", "a": "move", "m": m, "x": fx, "y": fy}, mons)
        assert len(ev) == 1 and ev[0][4] == 0x0001 | 0x8000 | 0x4000, ev
        nx, ny = ev[0][1], ev[0][2]
        # o Windows converte de volta com pixel = n * largura // 65536
        assert (vx + nx * vw // 65536, vy + ny * vh // 65536) == (px, py), (m, fx, fy, nx, ny)
    assert ra.traduzir({"t": "mouse", "a": "move", "x": 0, "y": 0}, mons)[0][1:3] == (ra._no_monitor(mons, 0, 0, 0))
    for ruim in (3, -1, True, "1", 1.0):                     # monitor que não existe ou número estranho
        assert ra.traduzir({"t": "mouse", "a": "move", "m": ruim, "x": 0, "y": 0}, mons) == [], ruim
    e = ra.Entrada(lambda ev: None, lambda t: None)
    e.monitores = mons
    saiu = []
    e._enviar = saiu.append
    e.aplicar({"t": "mouse", "a": "baixa", "b": 0, "m": 1, "x": 0, "y": 0})
    assert saiu[0][4] & 0x4000 and saiu[0][4] & 0x0002


def teste_atalhos_so_da_lista():
    import remoto_agente as ra
    for nome, seq in remoto.ATALHOS.items():
        assert seq in ("SAS", "BLOQUEAR") or all(c in ra.SCAN or c in ra.SCAN_E0 for c in seq), nome
    saiu, sas, bloq = [], [], []
    e = ra.Entrada(saiu.append, lambda t: None, sas=lambda: sas.append(1) or "", bloquear=lambda: bloq.append(1) or "")
    assert e.aplicar({"t": "atalho", "n": "win_r"}) == ""
    assert saiu == [("tecla", 0x5B, 8 | 1), ("tecla", 0x13, 8), ("tecla", 0x13, 8 | 2), ("tecla", 0x5B, 8 | 1 | 2)]
    e.aplicar({"t": "atalho", "n": "ctrl_alt_del"})
    e.aplicar({"t": "atalho", "n": "win_l"})
    assert sas == [1] and bloq == [1] and len(saiu) == 4          # nada simulado: cada um chama a função própria
    for ruim in ("rodar", "", None, 5, ["win"], "__class__"):
        e.aplicar({"t": "atalho", "n": ruim})
    assert len(saiu) == 4 and not e._teclas                      # nada fica apertado depois de um atalho
    e2 = ra.Entrada(saiu.append, lambda t: None, sas=lambda: "bloqueado")
    assert e2.aplicar({"t": "atalho", "n": "ctrl_alt_del"}) == "bloqueado"     # o aviso volta para o técnico


def teste_area_de_transferencia_modos_e_teto():
    p = remoto.area_permite
    assert p("ambos", "para_atendido") and p("ambos", "para_tecnico")
    assert p("para_atendido", "para_atendido") and not p("para_atendido", "para_tecnico")
    assert p("para_tecnico", "para_tecnico") and not p("para_tecnico", "para_atendido")
    assert not p("desligada", "para_atendido") and not p("desligada", "para_tecnico")
    # o teto de quem é atendido: o técnico não passa por cima, mas mandar para lá continua valendo
    assert not p("ambos", "para_tecnico", False) and not p("para_tecnico", "para_tecnico", False)
    assert p("ambos", "para_atendido", False)
    assert not p("tudo", "para_atendido") and not p("ambos", "lado")


def teste_nomes_e_caminhos():
    n = remoto.nome_seguro
    assert n("relatório final.pdf") == "relatório final.pdf"
    assert n("..\\..\\Windows\\System32\\a.dll") == "a.dll" and n("../../etc/passwd") == "passwd"
    assert n('a<b>c:d"e|f?g*h.txt') == "abcdefgh.txt" and n("x\x00\x1f.txt") == "x.txt"
    assert n("CON.txt") == "_CON.txt" and n("nul") == "_nul" and n("  ..  ") == "arquivo" and n(None) == "arquivo"
    assert len(n("a" * 400 + ".txt")) == 154
    existe = {os.path.join("P", "a.txt"), os.path.join("P", "a (2).txt")}
    assert remoto.caminho_livre("P", "a.txt", existe.__contains__) == os.path.join("P", "a (3).txt")
    assert remoto.caminho_livre("P", "b.txt", existe.__contains__) == os.path.join("P", "b.txt")
    v = remoto.validar_caminho
    if os.name != "nt":         # Linux: caminho absoluto, fora de /proc, /sys e /dev
        assert v("/home/ana/../ana/Documentos") == "/home/ana/Documentos" and v("//home//x") == "/home/x" and v("/") == "/"
        for ruim in ("\\\\servidor\\c$\\x", "C:\\x", "Documentos/a", "", None, 5, "/a\x00b", "/" + "a" * 1000, "/a\nb",
                     "/proc/self/environ", "/sys/kernel", "/dev/sda", "/home/../proc/1/mem"):
            assert "inválido" in _erro(v, ruim), ruim
        return
    assert v("c:\\Users\\Ana\\..\\Ana\\Documentos") == "C:\\Users\\Ana\\Documentos" and v("D:/fotos/x.jpg") == "D:\\fotos\\x.jpg"
    assert v("C:\\") == "C:\\"
    for ruim in ("\\\\servidor\\c$\\x", "\\\\?\\C:\\x", "\\\\.\\PhysicalDrive0", "C:\\a.txt:fluxo", "Documentos\\a", "C:a.txt",
                 "", None, 5, "C:\\a\x00b", "C:\\" + "a" * 1000, "C:\\a\nb"):
        assert "inválido" in _erro(v, ruim), ruim


def teste_historico_de_arquivos():
    r = Relogio()
    remoto.historico_gravar({"sentido": "recebido", "nome": "..\\a.txt", "tamanho": 10, "sha256": "a" * 64,
                             "tecnico": "123 456 789<script>", "caminho": "C:\\x\\a.txt"}, r)
    r.t += 10
    remoto.historico_gravar({"sentido": "enviado", "nome": "b.txt", "tamanho": "20", "sha256": "não é hash"}, r)
    for ruim in ({"sentido": "apagado", "nome": "x"}, {"sentido": "enviado", "tamanho": "muito"}, None, [1]):
        remoto.historico_gravar(ruim, r)
    h = remoto.historico(agora=r)
    assert [x["nome"] for x in h] == ["b.txt", "a.txt"]                         # a mais nova primeiro
    assert h[1]["tecnico"] == "123 456 789" and h[1]["sha256"] == "a" * 64 and h[0]["sha256"] == "" and h[0]["tamanho"] == 20
    r.t += remoto.HISTORICO_DIAS * 86400 - 5                                     # a primeira passou dos 90 dias
    assert [x["nome"] for x in remoto.historico(agora=r)] == ["b.txt"]
    remoto.historico_gravar({"sentido": "enviado", "nome": "c.txt", "tamanho": 1}, r)
    with open(os.path.join(DADOS, "arquivos_remotos.jsonl"), encoding="utf-8") as f:
        assert len(f.readlines()) == 2                                           # e saiu do arquivo ao gravar a próxima


def teste_entrada_traduz_e_solta():
    import remoto_agente as ra
    assert ra.traduzir({"t": "mouse", "a": "move", "x": 0.5, "y": 1}) == [("mouse", 32768, 65535, 0, 0x8001)]
    assert ra.traduzir({"t": "mouse", "a": "baixa", "b": 2, "x": -3, "y": 9})[0] == ("mouse", 0, 65535, 0, 0x8009)
    assert ra.traduzir({"t": "mouse", "a": "roda", "d": -99999, "x": 0, "y": 0})[0][3:] == (-1200, 0x8801)
    assert ra.traduzir({"t": "tecla", "a": "baixa", "c": "KeyA"}) == [("tecla", 0x1E, 8)]
    assert ra.traduzir({"t": "tecla", "a": "sobe", "c": "Delete"}) == [("tecla", 0x53, 8 | 1 | 2)]
    assert ra.SCAN["KeyZ"] == 0x2C and ra.SCAN["Digit0"] == 0x0B and ra.SCAN["F10"] == 0x44 and ra.SCAN["KeyM"] == 0x32
    for ruim in ({"t": "mouse", "a": "move", "x": "1", "y": 0}, {"t": "mouse", "a": "move", "x": float("nan"), "y": 0},
                 {"t": "mouse", "a": "move", "x": True, "y": 0}, {"t": "mouse", "a": "baixa", "b": 7, "x": 0, "y": 0},
                 {"t": "mouse", "a": "baixa", "b": True, "x": 0, "y": 0}, {"t": "mouse", "a": "explodir", "x": 0, "y": 0},
                 {"t": "mouse", "a": "roda", "d": "9", "x": 0, "y": 0}, {"t": "tecla", "a": "baixa", "c": "Power"},
                 {"t": "tecla", "a": "baixa", "c": 30}, {"t": "tecla", "a": "segura", "c": "KeyA"}, {"t": "rodar", "cmd": "calc"}):
        assert ra.traduzir(ruim) == [], ruim
    saiu, area = [], []
    e = ra.Entrada(saiu.append, area.append)
    for m in ({"t": "tecla", "a": "baixa", "c": "ControlLeft"}, {"t": "tecla", "a": "baixa", "c": "KeyC"},
              {"t": "tecla", "a": "sobe", "c": "KeyC"}, {"t": "mouse", "a": "baixa", "b": 0, "x": 0.1, "y": 0.1},
              {"t": "area", "texto": "olá"}, {"t": "area", "texto": "x" * 200000}, {"t": "area", "texto": 5}):
        e.aplicar(m)
    assert area == ["olá"] and len(saiu) == 4
    e.soltar()                                          # o Ctrl e o botão que ficaram apertados
    assert saiu[4:] == [("tecla", 0x1D, 8 | 2), ("mouse", None, None, 0, 0x0004)]
    e.soltar()
    assert len(saiu) == 6


async def _ate(cond, segundos=20):
    import asyncio
    fim = asyncio.get_running_loop().time() + segundos
    while not cond():
        assert asyncio.get_running_loop().time() < fim, "demorou demais"
        await asyncio.sleep(0.05)


async def _sessao_real(senha_errada=False):
    import asyncio
    from PIL import Image, ImageStat
    from aiortc import RTCPeerConnection, RTCSessionDescription
    import remoto_agente as ra

    estados, entrou, area, recebidas, trilhas = [], [], [], [], []
    agente = ra.Agente(estados.append, stun=(), capturar=lambda i: Image.new("RGB", (640, 360), (255, 255, 255)),
                       entrada=ra.Entrada(entrou.append, area.append), monitores=[_MON1], area=AreaFalsa())
    await agente.iniciar()
    assert estados[-1]["estado"] == "convite" and len(estados[-1]["senha"]) == 9
    senha = "AAAA-AAAA" if senha_errada else estados[-1]["senha"]

    # o lado do técnico (no programa de verdade é o WebRTC do navegador)
    tec = RTCPeerConnection()
    oferta = remoto.ler_convite(estados[-1]["convite"])
    canal = []

    @tec.on("datachannel")
    def _canal(c):
        canal.append(c)
        c.on("message", lambda m: recebidas.append(json.loads(m)))
        c.send(json.dumps({"t": "mouse", "a": "baixa", "b": 0, "x": 0.5, "y": 0.5}))       # antes de provar a senha
        c.send(json.dumps({"t": "prova", "mac": remoto.prova(remoto.chave(senha, oferta, tec.localDescription.sdp), "tecnico")}))
        c.send(json.dumps({"t": "prova", "mac": "00"}))                                    # repetida: é ignorada

    tec.on("track", trilhas.append)
    await tec.setRemoteDescription(RTCSessionDescription(sdp=oferta, type="offer"))
    await tec.setLocalDescription(await tec.createAnswer())
    await agente.comando({"cmd": "resposta", "codigo": remoto.resposta(tec.localDescription.sdp)})
    assert estados[-1]["estado"] == "conectando" and estados[-1]["senha"] == "" and estados[-1]["convite"] == ""

    async def brilho():
        q = await asyncio.wait_for(trilhas[0].recv(), 20)
        return ImageStat.Stat(q.to_image().convert("L")).mean[0]

    try:
        if senha_errada:
            await _ate(lambda: estados[-1]["estado"] == "encerrada")
            assert "senha" in estados[-1]["motivo"] and not entrou
            await _ate(lambda: any(m["t"] == "fim" for m in recebidas))
            assert not any(m["t"] in ("prova", "ativa") for m in recebidas)
            await agente.comando({"cmd": "aceitar"})                 # aceitar depois não ressuscita
            assert estados[-1]["erro"] and estados[-1]["estado"] == "encerrada"
            assert not agente.sessao.ativa
            return

        await _ate(lambda: estados[-1]["estado"] == "aguardando_aceite")
        assert estados[-1]["conferencia"] == remoto.conferencia(oferta, tec.localDescription.sdp)
        await _ate(lambda: any(m["t"] == "prova" for m in recebidas))      # a prova vem pela rede: pode chegar depois
        mac = next(m["mac"] for m in recebidas if m["t"] == "prova")
        assert remoto.confere(remoto.chave(senha, oferta, tec.localDescription.sdp), "atendido", mac)
        # senha certa mas ainda sem aceite: tela preta e nada de controle
        canal[0].send(json.dumps({"t": "tecla", "a": "baixa", "c": "KeyA"}))
        assert await brilho() < 40 and await brilho() < 40
        assert not entrou and not area

        await agente.comando({"cmd": "aceitar"})
        await _ate(lambda: any(m["t"] == "ativa" for m in recebidas))
        claro = False
        for _ in range(60):
            if await brilho() > 200:
                claro = True
                break
        assert claro, "a tela não chegou depois do aceite"
        canal[0].send(json.dumps({"t": "mouse", "a": "baixa", "b": 0, "x": 0.25, "y": 0.75}))
        canal[0].send(json.dumps({"t": "tecla", "a": "baixa", "c": "ShiftLeft"}))
        canal[0].send(json.dumps({"t": "area", "texto": "colado"}))
        canal[0].send("isto não é json")
        canal[0].send(json.dumps(["lista"]))
        await _ate(lambda: len(entrou) == 2 and area == ["colado"])
        # 640x360: x = 0,25 * 639 = pixel 160, y = 0,75 * 359 = pixel 269, na área de trabalho inteira (0x4000)
        assert entrou == [("mouse", 16384, 48970, 0, 0xC003), ("tecla", 0x2A, 8)]

        await agente.comando({"cmd": "encerrar"})
        assert estados[-1]["estado"] == "encerrada" and agente.fim.is_set()
        assert entrou[2:] == [("tecla", 0x2A, 8 | 2), ("mouse", None, None, 0, 0x0004)]     # soltou o que estava apertado
        await _ate(lambda: any(m["t"] == "fim" for m in recebidas))
    finally:
        await agente.encerrar()
        await tec.close()


def teste_agente_sessao_completa():
    import asyncio
    asyncio.run(_sessao_real())


async def _ativa(**k):
    """Uma sessão j\u00e1 aceita, com o canal de dados aberto pelo t\u00e9cnico. Devolve (agente, tec, controle, dados, recebidas)."""
    import asyncio
    from PIL import Image
    from aiortc import RTCPeerConnection, RTCSessionDescription
    import remoto_agente as ra

    estados, recebidas = [], {"controle": [], "dados": []}
    k.setdefault("monitores", [_MON1, {"x": 640, "y": 0, "w": 800, "h": 600, "principal": False}])
    agente = ra.Agente(estados.append, stun=(), capturar=lambda i: Image.new("RGB", (64, 36), (255, 255, 255)),
                       entrada=ra.Entrada(lambda ev: None, k["area"].texto if "area" in k else (lambda t: None)), **k)
    await agente.iniciar()
    senha = estados[-1]["senha"]
    tec = RTCPeerConnection()
    oferta = remoto.ler_convite(estados[-1]["convite"])
    canais = {}

    @tec.on("datachannel")
    def _canal(c):
        canais["controle"] = c
        c.on("message", lambda m: recebidas["controle"].append(json.loads(m)))
        c.send(json.dumps({"t": "prova", "mac": remoto.prova(remoto.chave(senha, oferta, tec.localDescription.sdp), "tecnico")}))

    await tec.setRemoteDescription(RTCSessionDescription(sdp=oferta, type="offer"))
    await tec.setLocalDescription(await tec.createAnswer())
    await agente.comando({"cmd": "resposta", "codigo": remoto.resposta(tec.localDescription.sdp)})
    await _ate(lambda: estados[-1]["estado"] == "aguardando_aceite")
    await agente.comando({"cmd": "aceitar"})
    await _ate(lambda: any(m["t"] == "ativa" for m in recebidas["controle"]))
    d = tec.createDataChannel("dados")
    d.on("message", lambda m: recebidas["dados"].append(m if isinstance(m, bytes) else json.loads(m)))
    await _ate(lambda: d.readyState == "open" and agente.arquivos is not None)
    await asyncio.sleep(0.1)
    return agente, tec, canais["controle"], d, recebidas


def teste_agente_arquivos_e_area_de_transferencia():
    import asyncio
    import hashlib
    import remoto_agente as ra

    baixados = tempfile.mkdtemp(prefix="mpc-downloads-", dir=DADOS)
    origem = os.path.join(DADOS, "origem.bin")
    conteudo = os.urandom(3 * remoto.TAM_PEDACO + 123)
    with open(origem, "wb") as f:
        f.write(conteudo)

    def json_de(lista, t, n=None):
        return [m for m in lista if isinstance(m, dict) and m.get("t") == t and (n is None or m.get("id") == n)]

    async def rodar():
        historico, area = [], AreaFalsa()
        agente, tec, ctl, d, rec = await _ativa(area=area, pastas=lambda: {"Downloads": baixados}, ao_arquivo=historico.append)
        try:
            ativa = next(m for m in rec["controle"] if m["t"] == "ativa")
            assert [m["n"] for m in ativa["monitores"]] == [1, 2] and ativa["ler_area"] is True
            mids = [t.mid for t in tec.getTransceivers()]
            assert [m["mid"] for m in ativa["monitores"]] == mids                # cada trilha diz de qual monitor \u00e9

            # --- o t\u00e9cnico manda um arquivo: chega em Downloads\CNT Monitor, com nome seguro, e entra no hist\u00f3rico
            def mandar(n, nome, dados, sha=None, **extra):
                d.send(json.dumps({"t": "enviar", "id": n, "nome": nome, "tamanho": len(dados), **extra}))
                for i in range(0, len(dados), remoto.TAM_PEDACO):
                    d.send(n.to_bytes(4, "big") + dados[i:i + remoto.TAM_PEDACO])
                d.send(json.dumps({"t": "enviado", "id": n, "sha256": sha or hashlib.sha256(dados).hexdigest()}))

            mandar(1, "..\\..\\relat\u00f3rio.bin", conteudo)
            await _ate(lambda: json_de(rec["dados"], "recebido", 1))
            r = json_de(rec["dados"], "recebido", 1)[0]
            assert json_de(rec["dados"], "aceito", 1) and r["ok"], r
            final = os.path.join(baixados, "CNT Monitor", "relat\u00f3rio.bin")
            assert r["caminho"] == final and open(final, "rb").read() == conteudo
            assert historico[-1]["sentido"] == "recebido" and historico[-1]["sha256"] == hashlib.sha256(conteudo).hexdigest()
            mandar(2, "relat\u00f3rio.bin", b"abc")                              # mesmo nome: n\u00e3o sobrescreve
            await _ate(lambda: json_de(rec["dados"], "recebido", 2))
            assert json_de(rec["dados"], "recebido", 2)[0]["caminho"].endswith("relat\u00f3rio (2).bin")
            mandar(3, "estragado.bin", b"12345", sha="0" * 64)                   # SHA-256 n\u00e3o confere: nada fica
            await _ate(lambda: json_de(rec["dados"], "recebido", 3))
            assert not json_de(rec["dados"], "recebido", 3)[0]["ok"]
            assert sorted(os.listdir(os.path.join(baixados, "CNT Monitor"))) == ["relat\u00f3rio (2).bin", "relat\u00f3rio.bin"]
            d.send(json.dumps({"t": "enviar", "id": 4, "nome": "grande", "tamanho": remoto.MAX_ARQUIVO + 1}))
            await _ate(lambda: json_de(rec["dados"], "recebido", 4))
            assert not json_de(rec["dados"], "recebido", 4)[0]["ok"]

            # --- colar no Explorer: s\u00f3 o que chegou nesta sess\u00e3o vai para a \u00e1rea de transfer\u00eancia
            d.send(json.dumps({"t": "colar_arquivos", "id": 5, "caminhos": [final, "C:\\Windows\\win.ini"]}))
            await _ate(lambda: json_de(rec["dados"], "colado", 5))
            assert area.escritos[-1] == ("arquivos", [final])

            # --- buscar no outro PC: pastas de partida, listar e baixar com SHA-256
            d.send(json.dumps({"t": "listar", "id": 6, "caminho": ""}))
            d.send(json.dumps({"t": "listar", "id": 7, "caminho": DADOS}))
            d.send(json.dumps({"t": "listar", "id": 8, "caminho": "\\\\servidor\\c$"}))
            await _ate(lambda: all(json_de(rec["dados"], "lista", n) for n in (6, 7, 8)))
            assert json_de(rec["dados"], "lista", 6)[0]["itens"][0] == {"nome": "Downloads", "caminho": baixados, "pasta": True}
            assert any(x["nome"] == "origem.bin" and x["tamanho"] == len(conteudo) for x in json_de(rec["dados"], "lista", 7)[0]["itens"])
            assert "inv\u00e1lido" in json_de(rec["dados"], "lista", 8)[0]["erro"]
            d.send(json.dumps({"t": "baixar", "id": 9, "caminho": origem}))
            await _ate(lambda: json_de(rec["dados"], "concluido", 9))
            ini = json_de(rec["dados"], "inicio", 9)[0]
            veio = b"".join(m[4:] for m in rec["dados"] if isinstance(m, bytes) and int.from_bytes(m[:4], "big") == 9)
            assert ini["tamanho"] == len(conteudo) and veio == conteudo
            assert json_de(rec["dados"], "concluido", 9)[0]["sha256"] == hashlib.sha256(conteudo).hexdigest()
            assert historico[-1]["sentido"] == "enviado" and historico[-1]["caminho"] == origem
            d.send(json.dumps({"t": "baixar", "id": 10, "caminho": os.path.join(DADOS, "nao-existe.bin")}))
            await _ate(lambda: json_de(rec["dados"], "erro", 10))

            # --- \u00e1rea de transfer\u00eancia: sem o t\u00e9cnico dizer o modo, nada sai daqui
            area.copiar(("texto", "senha do banco"))
            await asyncio.sleep(2 * ra.VIGIAR_AREA_S + 0.3)
            assert not [m for m in rec["controle"] if m["t"] == "area"]
            ctl.send(json.dumps({"t": "area_modo", "modo": "ambos"}))
            await asyncio.sleep(0.2)
            area.copiar(("texto", "copiado l\u00e1"))
            await _ate(lambda: [m for m in rec["controle"] if m["t"] == "area"])
            assert [m["texto"] for m in rec["controle"] if m["t"] == "area"] == ["copiado l\u00e1"]
            ctl.send(json.dumps({"t": "area", "texto": "do t\u00e9cnico"}))           # escrita daqui n\u00e3o volta como c\u00f3pia nova
            await _ate(lambda: area.escritos and area.escritos[-1] == ("texto", "do t\u00e9cnico"))
            await asyncio.sleep(2 * ra.VIGIAR_AREA_S + 0.3)
            assert len([m for m in rec["controle"] if m["t"] == "area"]) == 1
            # imagem do t\u00e9cnico vai para a \u00e1rea de transfer\u00eancia (n\u00e3o para Downloads)
            mandar(11, "imagem.png", b"\x89PNG-de-mentira", area="imagem")
            await _ate(lambda: json_de(rec["dados"], "recebido", 11))
            assert area.escritos[-1] == ("imagem", b"\x89PNG-de-mentira")
            # o programa que copiou ainda segura a área (Excel): a cópia não se perde, sai quando der para ler
            ler, ocupada = area.ler, [2]

            def ler_ocupada():
                if ocupada[0]:
                    ocupada[0] -= 1
                    raise OSError("OpenClipboard: acesso negado")
                return ler()
            area.ler = ler_ocupada
            area.copiar(("texto", "do Excel"))
            await _ate(lambda: [m for m in rec["controle"] if m.get("texto") == "do Excel"])
            assert ocupada[0] == 0
            area.ler = ler
            # quem \u00e9 atendido desliga a leitura (teto): nem com \"ambos\" sai mais nada
            await agente.comando({"cmd": "area_permitir", "valor": False})
            await _ate(lambda: [m for m in rec["controle"] if m["t"] == "area_teto"])
            area.copiar(("texto", "outra senha"))
            await asyncio.sleep(2 * ra.VIGIAR_AREA_S + 0.3)
            assert len([m for m in rec["controle"] if m["t"] == "area"]) == 2
            # monitor escondido anda devagar; o \"ver\" s\u00f3 aceita monitores que existem
            ctl.send(json.dumps({"t": "ver", "m": [1, 7, True, "0"]}))
            await _ate(lambda: agente.visiveis == {1})
            ctl.send(json.dumps({"t": "ola", "tecnico": "987 654 321;rm"}))
            await _ate(lambda: agente.tecnico == "987 654 321")
        finally:
            await agente.encerrar()
            await tec.close()

    asyncio.run(rodar())


def teste_agente_senha_errada():
    import asyncio
    asyncio.run(_sessao_real(senha_errada=True))


def teste_resposta_leva_os_enderecos_reais_do_tecnico():
    """O navegador só informa nomes ".local", que o outro PC quase nunca resolve: o código de resposta
    tem de levar os endereços de verdade, senão a ligação pela rede local só fecha num sentido."""
    local = ("a=candidate:111 1 udp 2113937151 0f1e2d3c-aaaa-bbbb-cccc-1234567890ab.local 50001 typ host generation 0\r\n"
             "a=candidate:222 1 udp 2113939711 9f8e7d6c-AAAA-bbbb-cccc-1234567890ab.local 50002 typ host generation 0\r\n")
    publico = "a=candidate:333 1 udp 1677729535 200.1.2.3 50001 typ srflx raddr 0.0.0.0 rport 0 generation 0\r\n"
    sdp = T + local + publico + "a=end-of-candidates\r\n"
    novo = remoto.com_enderecos(sdp, ["192.168.0.7", "10.8.0.2", "não é ip", "fe80::1"])
    assert ".local" not in novo and publico in novo and novo.endswith("a=end-of-candidates\r\n")
    for ip in ("192.168.0.7", "10.8.0.2"):
        for porta in ("50001", "50002"):
            assert f" {ip} {porta} typ host generation 0\r\n" in novo
    assert novo.count("a=candidate:") == 5 and "fe80" not in novo and "não é ip" not in novo
    assert remoto.com_enderecos(sdp, []) == sdp                                  # sem endereços, fica como veio
    assert remoto.com_enderecos(sdp, ["192.168.0.7"] * 50).count("a=candidate:") <= 1 + 2 * 6
    r = remoto.resposta_do_tecnico("ABCD-EFGH", A, sdp, ["192.168.0.7"])
    lido = remoto.ler_resposta(r["codigo"])
    assert " 192.168.0.7 50001 typ host" in lido and ".local" not in lido
    assert remoto.impressao(lido) == remoto.impressao(sdp)                       # a prova da senha não muda
    assert r["prova"] == remoto.prova(remoto.chave("ABCD-EFGH", A, sdp), "tecnico")
    for ip in remoto.enderecos_locais():
        assert not ip.startswith(("127.", "169.254.")) and ip.count(".") == 3


def teste_agente_mantem_o_caminho_enquanto_espera_a_resposta():
    """A resposta do técnico demora minutos; até lá o agente tem de continuar falando com o servidor STUN
    pelas mesmas portas do convite, senão o roteador esquece o endereço público que está no convite."""
    import asyncio
    from aioice import stun
    import remoto_agente as ra

    async def rodar():
        pedidos = []

        class Stun(asyncio.DatagramProtocol):
            def connection_made(self, t):
                self.t = t

            def datagram_received(self, dados, de):
                m = stun.parse_message(dados)
                pedidos.append(de)
                r = stun.Message(stun.Method.BINDING, stun.Class.RESPONSE, m.transaction_id)
                r.attributes["XOR-MAPPED-ADDRESS"] = de
                self.t.sendto(bytes(r), de)

        ip = (remoto.enderecos_locais() or ["127.0.0.1"])[0]
        t, _ = await asyncio.get_running_loop().create_datagram_endpoint(Stun, local_addr=(ip, 0))
        estados = []
        agente = ra.Agente(estados.append, stun=(f"stun:{ip}:{t.get_extra_info('sockname')[1]}",))
        try:
            await agente.iniciar()
            assert estados[-1]["estado"] == "convite"
            do_convite = set(pedidos)
            assert do_convite, "o agente não consultou o servidor STUN"
            pedidos.clear()
            await _ate(lambda: len(pedidos) >= 3 * len(do_convite), 10)
            assert set(pedidos) == do_convite, (set(pedidos), do_convite)   # as mesmas portas que estão no convite
            await agente.encerrar()
            await asyncio.sleep(0.3)
            pedidos.clear()
            await asyncio.sleep(0.5)
            assert not pedidos                                   # sessão encerrada: para de mandar
        finally:
            await agente.encerrar()
            t.close()

    antes, ra.MANTER_CAMINHO_S = ra.MANTER_CAMINHO_S, 0.1
    try:
        asyncio.run(rodar())
    finally:
        ra.MANTER_CAMINHO_S = antes


def teste_diagnostico_da_conexao():
    sdp = ("m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
           "a=candidate:1 1 udp 2130706431 192.168.0.5 50000 typ host\r\n"
           "a=candidate:2 1 udp 1694498815 200.1.2.3 50000 typ srflx raddr 192.168.0.5 rport 50000\r\n"
           "a=candidate:3 1 udp 16777215 198.51.100.7 49200 typ relay raddr 0.0.0.0 rport 0\r\n"
           "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
           "a=candidate:1 1 udp 2130706431 192.168.0.5 50000 typ host\r\n"
           "a=candidate:4 1 TCP 1518280447 192.168.0.5 9 typ host tcptype active\r\n")
    assert remoto.resumo_candidatos(sdp) == "host/tcp 1, host/udp 1, relay/udp 1, srflx/udp 1"
    assert remoto.resumo_candidatos("") == "nenhum endereço" and "192.168" not in remoto.resumo_candidatos(sdp)
    assert remoto.rota("srflx", "host") == "direta" and remoto.rota("host", "relay") == "retransmitida"
    c = remoto.conclusao_teste
    assert "servidor" in c({"servidor": False, "turn_udp": True, "turn_tcp": True})
    assert "credencial" in c({"servidor": True, "turn_udp": None, "turn_tcp": None})
    assert c({"servidor": True, "turn_udp": True, "turn_tcp": True}).startswith("Tudo certo")
    assert "bloqueia UDP" in c({"servidor": True, "turn_udp": False, "turn_tcp": True})
    assert "TCP não respondeu" in c({"servidor": True, "turn_udp": True, "turn_tcp": False})
    assert "não alcança" in c({"servidor": True, "turn_udp": False, "turn_tcp": False})

def teste_agente_usa_o_retransmissor_por_tcp():
    """O aiortc só usa o primeiro TURN da lista: o TCP vai na frente, senão rede que bloqueia UDP fica sem saída."""
    import remoto_agente as ra
    from aiortc import RTCIceServer
    from aiortc.rtcicetransport import connection_kwargs
    urls = ["turn:t.exemplo:3478?transport=udp", "turn:t.exemplo:3478?transport=tcp"]
    assert ra.turn_por_tcp(urls) == urls[::-1] and ra.turn_por_tcp(urls[:1]) == urls[:1]
    assert connection_kwargs([RTCIceServer(urls=urls, username="u", credential="c")])["turn_transport"] == "udp"
    assert connection_kwargs([RTCIceServer(urls=ra.turn_por_tcp(urls), username="u", credential="c")])["turn_transport"] == "tcp"

def teste_testar_conexao_sem_retransmissor():
    import asyncio
    import time
    import remoto_agente as ra
    nada = (["turn:127.0.0.1:9?transport=udp", "turn:127.0.0.1:9?transport=tcp"], "1:u", "c")
    t = time.time()
    assert asyncio.run(ra.testar((), nada, prazo=3)) == {"stun": None, "turn_udp": False, "turn_tcp": False}
    assert time.time() - t < 8                       # UDP e TCP em paralelo, cada um com o seu prazo
    assert asyncio.run(ra.testar((), None)) == {"stun": None, "turn_udp": None, "turn_tcp": None}

def teste_log_do_agente_vai_para_o_servico():
    import io
    import contextlib
    g = remoto.Gerente()

    class Proc:
        stdout = ['{"log": "ICE: checking"}\n', '{"estado": "convite", "convite": "MPCCx"}\n']

    p = g._proc = Proc()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        g._ler(p)
    assert "remoto: ICE: checking" in err.getvalue() and "log" not in g.estado()

def teste_gerente_e_acoes_do_motor():
    """O serviço liga o agente de verdade (outro processo), o lado do técnico usa só as ações do motor."""
    import asyncio
    import threading
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from monitorpc import motor

    m = motor.Motor.__new__(motor.Motor)
    m.lock, m.procs, m.estado = threading.Lock(), {}, {}
    m.ajuda = remoto.Gerente()
    m.encontro = encontro.Encontro(m.ajuda, url="http://127.0.0.1:9", arquivo=os.path.join(DADOS, "id-gerente.json"))

    def acao(tipo, **k):
        return m.acao({"tipo": tipo, **k})

    e = acao("ajuda_estado")["dados"]
    assert e["estado"] == "parada" and len(e["meu"]["id"]) == 11 and len(e["meu"]["senha"]) == 9
    assert not acao("ajuda_aceitar")["ok"] and not acao("ajuda_resposta", codigo="x")["ok"]
    assert not acao("ajuda_tec_convite", codigo="lixo")["ok"] and not acao("ajuda_inventada")["ok"]
    assert not m.ajuda._viva()
    try:
        m.ajuda._comando = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "remoto_agente.py")]
        r = m.ajuda.pedir(stun=False)
        assert r == (True, ""), r
        e = acao("ajuda_estado")["dados"]
        assert e["estado"] == "convite" and e["convite"].startswith("MPCC")
        proc = m.ajuda._proc
        assert m.ajuda.pedir(stun=False) == (True, "") and m.ajuda._proc is proc          # não abre um segundo
        r = acao("ajuda_resposta", codigo="MPCRLIXO")
        assert not r["ok"] and "alterado" in r["msg"] and r["dados"]["estado"] == "convite"
        assert not acao("ajuda_aceitar")["ok"]                                              # aceite sem técnico

        oferta = acao("ajuda_tec_convite", codigo=e["convite"])["dados"]["sdp"]
        assert "8 letras" in acao("ajuda_tec_resposta", senha="123", oferta=oferta, resposta=oferta)["msg"]

        async def tecnico():
            tec = RTCPeerConnection()
            msgs = []

            @tec.on("datachannel")
            def _canal(c):
                c.on("message", lambda x: msgs.append(json.loads(x)))
                c.send(json.dumps({"t": "prova", "mac": dados["prova"]}))

            await tec.setRemoteDescription(RTCSessionDescription(sdp=oferta, type="offer"))
            await tec.setLocalDescription(await tec.createAnswer())
            dados = acao("ajuda_tec_resposta", senha=e["senha"].lower(), oferta=oferta, resposta=tec.localDescription.sdp)["dados"]
            loop = asyncio.get_running_loop()
            assert (await loop.run_in_executor(None, lambda: acao("ajuda_resposta", codigo=dados["codigo"])))["ok"]
            await _ate(lambda: acao("ajuda_estado")["dados"]["estado"] == "aguardando_aceite")
            assert acao("ajuda_estado")["dados"]["conferencia"] == dados["conferencia"]
            await _ate(lambda: any(x["t"] == "prova" for x in msgs))
            mac = next(x["mac"] for x in msgs if x["t"] == "prova")
            comum = dict(senha=e["senha"], oferta=oferta, resposta=tec.localDescription.sdp)
            assert acao("ajuda_tec_conferir", mac=mac, **comum)["ok"]
            assert not acao("ajuda_tec_conferir", mac="00", **comum)["ok"]
            assert (await loop.run_in_executor(None, lambda: acao("ajuda_aceitar")))["ok"]
            await _ate(lambda: any(x["t"] == "ativa" for x in msgs))
            assert acao("ajuda_estado")["dados"]["estado"] == "ativa"
            await loop.run_in_executor(None, lambda: acao("ajuda_encerrar"))
            await tec.close()

        asyncio.run(tecnico())
        assert acao("ajuda_estado")["dados"]["estado"] == "encerrada"
        assert proc.wait(10) is not None
    finally:
        m.ajuda.parar()


def teste_agente_aberto_pelo_caminho_do_servico():
    """O serviço abre o agente com CreateProcessAsUser e dois canos. Aqui vai com o token do próprio teste:
    trocar de sessão só a conta do serviço consegue. No Linux: subprocess como o usuário da sessão gráfica."""
    import threading
    if os.name != "nt":
        return _agente_pelo_servico_linux()
    import win32api
    import win32con
    import win32security
    from monitorpc import sessao
    import remoto_agente as ra

    meu = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.MAXIMUM_ALLOWED)
    token = win32security.DuplicateTokenEx(meu, win32security.SecurityImpersonation, win32con.MAXIMUM_ALLOWED,
                                           win32security.TokenPrimary)
    g = remoto.Gerente([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "remoto_agente.py")])
    g._abrir = lambda cmd: sessao._abrir_com_token(token, cmd)
    try:
        assert g.pedir(stun=False) == (True, "")
        proc = g._proc
        assert isinstance(proc, sessao._Processo) and proc.poll() is None
        assert remoto.ler_convite(g.estado()["convite"])
        ok, msg = g.ordem("resposta", codigo="MPCRLIXO")
        assert not ok and "alterado" in msg
        g.parar()
        assert proc.wait(10) is not None and g.estado()["estado"] == "encerrada"
    finally:
        g.parar()

    erros = []          # trocar de área de trabalho não pode quebrar quando não há outra (ou não há permissão)

    def alvo():
        try:
            ra._na_tela_ativa()
            ra._na_tela_ativa()
        except Exception as e:  # noqa: BLE001
            erros.append(e)
    t = threading.Thread(target=alvo)
    t.start()
    t.join()
    assert not erros, erros


def _agente_pelo_servico_linux():
    import pwd
    import subprocess
    from monitorpc.linux import sessao as LS
    eu = {"uid": os.getuid(), "tipo": "x11", "display": os.environ.get("DISPLAY", ":0")}
    g = remoto.Gerente([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "remoto_agente.py")])
    g._abrir = lambda cmd: LS._popen_como(eu, cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          text=True, encoding="utf-8", bufsize=1, start_new_session=True)
    try:
        assert g.pedir(stun=False) == (True, "")
        proc = g._proc
        assert proc.poll() is None and remoto.ler_convite(g.estado()["convite"])
        ok, msg = g.ordem("resposta", codigo="MPCRLIXO")
        assert not ok and "alterado" in msg
        g.parar()
        assert proc.wait(10) is not None and g.estado()["estado"] == "encerrada"
    finally:
        g.parar()
    amb = LS.ambiente(eu)          # variáveis da sessão: as de um processo do próprio usuário, com o padrão do systemd
    assert amb["XDG_RUNTIME_DIR"] and amb["DBUS_SESSION_BUS_ADDRESS"] and pwd.getpwuid(eu["uid"])


def teste_acoes_de_ajuda_so_no_proprio_pc():
    import http.client
    import threading
    import time
    from http.server import ThreadingHTTPServer
    from monitorpc import configuracao, servidor
    configuracao.salvar({"acesso_somente_ver": False})

    class MotorFalso:
        recebidas = []

        def acao(self, a):
            self.recebidas.append(a)
            return {"ok": True}

    s = ThreadingHTTPServer(("127.0.0.1", 0), servidor.fabrica(MotorFalso(), "tok", remoto=True))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    servidor.SESSOES["sessao-ajuda"] = time.time() + 60
    try:
        for tipo in ("ajuda_estado", "ajuda_pedir", "ajuda_resposta", "ajuda_aceitar", "ajuda_encerrar", "ajuda_tec_convite",
                     "ajuda_arquivos"):
            con = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=10)
            con.request("POST", "/api/acao", json.dumps({"tipo": tipo}),
                        {"Content-Type": "application/json", "Cookie": "mpc_sessao=sessao-ajuda", "X-Token": "tok"})
            r = con.getresponse()
            r.read()
            con.close()
            assert r.status == 403, tipo
        con = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=10)
        con.request("POST", "/api/acao", json.dumps({"tipo": "mostrar"}),
                    {"Content-Type": "application/json", "Cookie": "mpc_sessao=sessao-ajuda", "X-Token": "tok"})
        assert con.getresponse().status == 200          # as outras ações passam: o 403 é mesmo das de ajuda
        con.close()
        assert [a["tipo"] for a in MotorFalso.recebidas] == ["mostrar"]
    finally:
        servidor.SESSOES.pop("sessao-ajuda", None)
        s.shutdown()


def teste_chamadas_win32_existem():
    """O caminho do serviço só roda instalado: confere aqui que cada função Win32 usada existe mesmo no pywin32."""
    if os.name != "nt":
        return print("  (pulado: só no Windows)")
    import importlib
    import re
    from monitorpc import sessao
    raiz = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
    for arq in ("monitorpc/sessao.py", "monitorpc/remoto.py", "remoto_agente.py", "servico.py", "bandeja.py"):
        with open(os.path.join(raiz, arq), encoding="utf-8") as f:
            usos = set(re.findall(r"\b(win32\w+|pywintypes)\.(\w+)", f.read()))
        for mod, nome in sorted(usos):
            assert hasattr(importlib.import_module(mod), nome) or importlib.util.find_spec(f"{mod}.{nome}"), \
                f"{arq}: {mod}.{nome} não existe"           # função do módulo ou submódulo (win32com.shell)
    assert sessao.em_servico() is False          # o teste roda na sessão de um usuário, não na do serviço


def teste_atalhos_do_windows_so_na_sessao():
    """Win, Alt+Tab, Alt+F4... viram F13..F22 só com a sessão (título marcado) na frente; a página desfaz a troca."""
    import re
    import ctypes
    import remoto_agente
    from monitorpc import teclado_remoto as K
    assert K.titulo_da_sessao("CNT Monitor — 123 456 789" + K.MARCA)
    assert K.titulo_da_sessao("CNT Monitor — 123 456 789 · monitor 2" + K.MARCA)
    assert not K.titulo_da_sessao("CNT Monitor — 123 456 789")          # toggle desligado
    assert not K.titulo_da_sessao("Sem título - Bloco de Notas")          # outro programa na frente
    assert not K.titulo_da_sessao("Outro site" + K.MARCA) and not K.titulo_da_sessao(None)

    tr = {}
    assert K.decidir(K.VK_LWIN, True, False, False, tr) == K.VK_F13          # Win sozinho
    assert K.decidir(K.VK_LWIN, True, False, False, tr) == K.VK_F13          # repetição
    assert K.decidir(0x52, True, False, False, tr) is None                   # o R de Win+R passa normal
    assert K.decidir(K.VK_LWIN, False, False, False, tr) == K.VK_F13 and tr == {}
    assert K.decidir(K.VK_TAB, True, False, False, tr) is None               # Tab sozinho: digitação normal
    assert K.decidir(K.VK_TAB, True, True, False, tr) == K.VK_F13 + 2        # Alt+Tab
    assert K.decidir(K.VK_TAB, False, False, False, tr) == K.VK_F13 + 2      # Alt solto antes do Tab: sobe trocada
    assert K.decidir(K.VK_F4, True, True, False, tr) == K.VK_F13 + 4 and K.decidir(K.VK_F4, False, True, False, tr)
    assert K.decidir(K.VK_ESC, True, False, True, tr) == K.VK_F13 + 3        # Ctrl+Esc / Ctrl+Shift+Esc
    K.decidir(K.VK_ESC, False, False, True, tr)
    assert K.decidir(K.VK_ESPACO, True, False, True, tr) is None             # Ctrl+Espaço não é do Windows
    assert K.decidir(0x57, True, False, True, tr) == K.VK_F13 + 7            # Ctrl+W não fecha a janela da sessão
    K.decidir(0x57, False, False, True, tr)
    assert K.decidir(0x41, True, False, True, tr) is None and tr == {}       # Ctrl+A passa normal (a página manda)

    # a tabela da página (F13.. -> tecla verdadeira) é a mesma do gancho, e o agente conhece cada tecla
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "ui", "index.html"), encoding="utf-8").read()
    js = dict(re.findall(r"(F\d+):'(\w+)'", re.search(r"const RS_FTECLA=\{([^}]*)\}", html).group(1)))
    vk_js = {"MetaLeft": 0x5B, "MetaRight": 0x5C, "Tab": 0x09, "Escape": 0x1B, "F4": 0x73, "Space": 0x20,
             "PrintScreen": 0x2C, "KeyW": 0x57, "KeyT": 0x54, "KeyN": 0x4E}
    assert {f"F{sub - K.VK_F13 + 13}": vk for vk, (sub, _q) in K.TROCAS.items()} == {f: vk_js[c] for f, c in js.items()}
    assert all(c in remoto_agente.SCAN or c in remoto_agente.SCAN_E0 for c in js.values())
    if os.name != "nt":
        return
    u = ctypes.WinDLL("user32")
    for nome in ("SetWindowsHookExW", "UnhookWindowsHookEx", "CallNextHookEx", "SetTimer", "GetForegroundWindow",
                 "GetWindowTextW", "GetAsyncKeyState", "SendInput", "GetMessageW"):
        assert hasattr(u, nome), nome

if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("teste_"):
                f()
                print("ok", nome)
        print("todos os testes do acesso remoto passaram")
    finally:
        shutil.rmtree(DADOS, ignore_errors=True)
