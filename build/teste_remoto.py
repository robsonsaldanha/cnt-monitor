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

from monitorpc import remoto  # noqa: E402


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
    assert len(s) == 12 and set(s) <= set(remoto.ALFABETO) and remoto.nova_senha() != s
    f = remoto.formatar_senha(s)
    assert len(f) == 14 and f.count("-") == 2
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
    agente = ra.Agente(estados.append, stun=(), capturar=lambda: Image.new("RGB", (640, 360), (255, 255, 255)),
                       entrada=ra.Entrada(entrou.append, area.append))
    await agente.iniciar()
    assert estados[-1]["estado"] == "convite" and len(estados[-1]["senha"]) == 14
    senha = "AAAA-AAAA-AAAA" if senha_errada else estados[-1]["senha"]

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
        assert entrou == [("mouse", 16384, 49151, 0, 0x8003), ("tecla", 0x2A, 8)]

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


def teste_agente_senha_errada():
    import asyncio
    asyncio.run(_sessao_real(senha_errada=True))


def teste_gerente_e_acoes_do_motor():
    """O serviço liga o agente de verdade (outro processo), o lado do técnico usa só as ações do motor."""
    import asyncio
    import threading
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from monitorpc import motor

    m = motor.Motor.__new__(motor.Motor)
    m.lock, m.procs, m.estado = threading.Lock(), {}, {}
    m.ajuda = remoto.Gerente()

    def acao(tipo, **k):
        return m.acao({"tipo": tipo, **k})

    assert acao("ajuda_estado")["dados"] == {"estado": "parada"}
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
        assert "12 letras" in acao("ajuda_tec_resposta", senha="123", oferta=oferta, resposta=oferta)["msg"]

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
    trocar de sessão só a conta do serviço consegue."""
    import threading
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
        for tipo in ("ajuda_estado", "ajuda_pedir", "ajuda_resposta", "ajuda_aceitar", "ajuda_encerrar", "ajuda_tec_convite"):
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
    import importlib
    import re
    from monitorpc import sessao
    raiz = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
    for arq in ("monitorpc/sessao.py", "monitorpc/remoto.py", "remoto_agente.py", "servico.py", "bandeja.py"):
        with open(os.path.join(raiz, arq), encoding="utf-8") as f:
            usos = set(re.findall(r"\b(win32\w+|pywintypes)\.(\w+)", f.read()))
        for mod, nome in sorted(usos):
            assert hasattr(importlib.import_module(mod), nome), f"{arq}: {mod}.{nome} não existe"
    assert sessao.em_servico() is False          # o teste roda na sessão de um usuário, não na do serviço


if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("teste_"):
                f()
                print("ok", nome)
        print("todos os testes do acesso remoto passaram")
    finally:
        shutil.rmtree(DADOS, ignore_errors=True)
