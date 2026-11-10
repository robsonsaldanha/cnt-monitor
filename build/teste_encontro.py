"""Testes do acesso por ID e senha: o ponto de encontro (servidor-vps/encontro.js, precisa do Node) e o lado do serviço.

    python build/teste_encontro.py
"""
import os
import sys
import json
import hmac
import time
import base64
import shutil
import socket
import hashlib
import tempfile
import threading
import subprocess
import urllib.error
import urllib.request

DADOS = tempfile.mkdtemp(prefix="mpc-teste-encontro-")
os.environ["MONITORPC_DADOS"] = DADOS   # antes de importar: nada toca na pasta de dados de verdade
RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(RAIZ, "src"))

from monitorpc import remoto, encontro  # noqa: E402

SEGREDO = "segredo-de-teste"
_ip = [0]


def _servidor(turn=True):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    amb = dict(os.environ, PORTA=str(porta), TURN_HOST="203.0.113.9", TURN_SEGREDO=SEGREDO if turn else "")
    p = subprocess.Popen(["node", os.path.join(RAIZ, "servidor-vps", "encontro.js")], env=amb,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "ouvindo" in p.stdout.readline()
    return p, f"http://127.0.0.1:{porta}/api"


def _req(url, rota, corpo=None, ip=None):
    """Cada chamada vem de um IP diferente (o servidor confia no X-Real-IP posto pelo nginx), salvo quando o teste escolhe."""
    _ip[0] += 1
    cab = {"Content-Type": "application/json", "X-Real-IP": ip or f"10.0.{_ip[0] // 250}.{_ip[0] % 250}"}
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url + rota, data=dados, headers=cab, method="POST" if dados is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


CHAVE_A, CHAVE_B = "a" * 43, "b" * 43


def teste_servidor():
    p, url = _servidor()
    try:
        assert _req(url, "/registrar", {"id": "12345", "chave": CHAVE_A})[0] == 400
        assert _req(url, "/registrar", {"id": "123456789", "chave": "curta"})[0] == 400
        cod, d = _req(url, "/registrar", {"id": "123456789", "chave": CHAVE_A})
        assert cod == 200 and d["ok"]
        t = d["turn"]                                   # credencial temporária no formato do coturn (use-auth-secret)
        assert t["urls"][0].startswith("turn:203.0.113.9:3478") and int(t["username"].split(":")[0]) > time.time() + 3600
        assert t["credential"] == base64.b64encode(hmac.new(SEGREDO.encode(), t["username"].encode(), hashlib.sha1).digest()).decode()
        assert _req(url, "/registrar", {"id": "123456789", "chave": CHAVE_B})[0] == 409       # ID de outro PC
        assert _req(url, "/registrar", {"id": "123456789", "chave": CHAVE_A})[0] == 200       # o dono renova
        assert _req(url, "/conectar", {"id": "987654321"})[0] == 404
        assert _req(url, "/esperar?id=123456789&chave=" + CHAVE_B)[0] == 404                  # chave errada não lê a fila

        cod, d = _req(url, "/conectar", {"id": "123 456 789"})
        assert cod == 200 and len(d["pedido"]) >= 22 and d["turn"]
        pedido = d["pedido"]
        cod, d = _req(url, "/esperar?id=123456789&chave=" + CHAVE_A)
        assert d["msgs"] == [{"tipo": "pedido", "pedido": pedido}]
        assert _req(url, "/enviar", {"id": "123456789", "chave": CHAVE_B, "pedido": pedido, "dados": {}})[0] == 404
        assert _req(url, "/enviar", {"id": "123456789", "chave": CHAVE_A, "pedido": pedido, "dados": {"tipo": "convite", "codigo": "MPCC1"}})[0] == 200
        assert _req(url, "/receber?pedido=" + pedido)[1]["msgs"] == [{"dados": {"tipo": "convite", "codigo": "MPCC1"}}]
        assert _req(url, "/responder", {"pedido": pedido, "dados": {"tipo": "resposta", "codigo": "MPCR1"}})[0] == 200
        assert _req(url, "/esperar?id=123456789&chave=" + CHAVE_A)[1]["msgs"] == [
            {"tipo": "msg", "pedido": pedido, "dados": {"tipo": "resposta", "codigo": "MPCR1"}}]
        assert _req(url, "/responder", {"pedido": "x" * 30, "dados": {}})[0] == 404

        # a espera longa volta assim que chega mensagem
        res = []
        th = threading.Thread(target=lambda: res.append(_req(url, "/esperar?id=123456789&chave=" + CHAVE_A)))
        th.start()
        time.sleep(0.3)
        t0 = time.time()
        _req(url, "/conectar", {"id": "123456789"})
        th.join(10)
        assert res and res[0][1]["msgs"][0]["tipo"] == "pedido" and time.time() - t0 < 3

        # tentativas por ID são limitadas (adivinhar a senha exige uma conexão por tentativa)
        cods = [_req(url, "/conectar", {"id": "123456789"})[0] for _ in range(16)]
        assert cods.count(429) >= 2 and cods[0] == 200, cods
        # corpo grande demais
        assert _req(url, "/responder", {"pedido": pedido, "dados": "x" * 70000})[0] in (400, 413)
        assert _req(url, "/sair", {"id": "123456789", "chave": CHAVE_A})[0] == 200
        assert _req(url, "/registrar", {"id": "123456789", "chave": CHAVE_B})[0] == 200     # ID liberado
    finally:
        p.kill()


class GerenteFalso:
    def __init__(self):
        self.e, self.chamadas = {"estado": "parada"}, []

    def estado(self):
        return dict(self.e)

    def pedir(self, stun=True, turn=None):
        self.chamadas.append(("pedir", turn))
        self.e = {"estado": "convite", "convite": "MPCCFALSO"}
        return True, ""

    def ordem(self, cmd, **k):
        self.chamadas.append((cmd, k))
        if cmd == "resposta":
            self.e = {"estado": "conectando"}
        return True, ""

    def parar(self):
        self.chamadas.append(("parar", None))
        self.e = {"estado": "encerrada", "motivo": "Encerrado por quem está no computador."}


def _ate(cond, segundos=15):
    fim = time.time() + segundos
    while not cond():
        assert time.time() < fim, "demorou demais"
        time.sleep(0.05)


def _receber_ate(url, pedido, tipo):
    for _ in range(5):
        for m in _req(url, "/receber?pedido=" + pedido)[1].get("msgs", []):
            if m["dados"].get("tipo") == tipo:
                return m["dados"]
    raise AssertionError("não chegou " + tipo)


def teste_id_e_senha_do_servico():
    arq = os.path.join(DADOS, "id.json")
    boot = [1000]
    e = encontro.Encontro(GerenteFalso(), url="http://127.0.0.1:9/api", arquivo=arq, boot=lambda: boot[0])
    i, c = e.id, e.chave
    assert len(i) == 9 and i.isdigit() and len(c) >= 32
    assert encontro.Encontro(GerenteFalso(), arquivo=arq, boot=lambda: boot[0]).id == i        # serviço reiniciou: mesmo ID
    boot[0] = 2000
    assert encontro.Encontro(GerenteFalso(), arquivo=arq, boot=lambda: boot[0]).id != i        # PC reiniciou: ID novo
    st = e.estado()
    assert st["id"] == encontro.formatar_id(i) and len(st["senha"]) == 9 and st["senha"] in st["texto"] and st["id"] in st["texto"]
    s = e.senha
    assert e.trocar_senha() != s
    assert e.trocar_id() != i


def teste_pedido_pelo_id():
    p, url = _servidor()
    g = GerenteFalso()
    agora = [time.time()]
    e = encontro.Encontro(g, url=url, arquivo=os.path.join(DADOS, "id2.json"), agora=lambda: agora[0])
    e.iniciar()
    try:
        _ate(lambda: e.situacao == "online")
        senha = e.senha
        cod, d = _req(url, "/conectar", {"id": e.id})
        assert cod == 200
        conv = _receber_ate(url, d["pedido"], "convite")
        assert conv["codigo"] == "MPCCFALSO"
        assert g.chamadas[0] == ("pedir", e.turn) and e.turn and g.chamadas[1] == ("senha", {"senha": senha})
        # outro técnico enquanto este atende: recusado com motivo
        d2 = _req(url, "/conectar", {"id": e.id})[1]
        assert "outro atendimento" in _receber_ate(url, d2["pedido"], "erro")["msg"]
        _req(url, "/responder", {"pedido": d["pedido"], "dados": {"tipo": "resposta", "codigo": "MPCRX"}})
        _ate(lambda: ("resposta", {"codigo": "MPCRX"}) in g.chamadas)
        _req(url, "/responder", {"pedido": d["pedido"], "dados": {"tipo": "resposta", "codigo": "MPCRY"}})
        time.sleep(0.5)
        assert ("resposta", {"codigo": "MPCRY"}) not in g.chamadas                 # uma resposta por pedido
        g.e = {"estado": "ativa"}
        time.sleep(1.5)
        g.e = {"estado": "encerrada", "motivo": "fim"}
        _ate(lambda: e.senha != senha)                                            # senha usada não vale de novo

        # três senhas erradas trocam a senha; o técnico fica sabendo
        senha = e.senha
        for n in range(3):
            g.e = {"estado": "parada"}
            d = _req(url, "/conectar", {"id": e.id}, ip=f"10.9.9.{n}")[1]
            _receber_ate(url, d["pedido"], "convite")
            g.e = {"estado": "encerrada", "senha_errada": True, "motivo": "A senha não conferiu."}
            assert "Senha incorreta" in _receber_ate(url, d["pedido"], "erro")["msg"]
            assert (e.senha == senha) == (n < 2)

        # técnico que some antes de responder não prende o computador
        g.e = {"estado": "parada"}
        d = _req(url, "/conectar", {"id": e.id})[1]
        _receber_ate(url, d["pedido"], "convite")
        agora[0] += encontro.ESPERA_RESPOSTA + 1
        _ate(lambda: g.chamadas[-1] == ("parar", None))
    finally:
        e.parar()
        p.kill()


def teste_id_trocado_vale_na_hora():
    """Quem digita o ID novo logo depois de "Trocar ID" encontra o computador; o antigo deixa de valer."""
    p, url = _servidor()
    e = encontro.Encontro(GerenteFalso(), url=url, arquivo=os.path.join(DADOS, "id4.json"))
    e.iniciar()
    try:
        _ate(lambda: e.situacao == "online")
        antigo = e.id
        time.sleep(0.3)                                   # o laço principal já está na espera longa do ID antigo
        novo = e.trocar_id()
        assert novo != antigo and e.situacao == "online"
        assert _req(url, "/conectar", {"id": novo})[0] == 200
        _ate(lambda: _req(url, "/conectar", {"id": antigo})[0] == 404, 5)
    finally:
        e.parar()
        p.kill()


def teste_conexao_cai_para_o_endereco_que_funciona():
    """Endereço sem saída (IPv6 quebrado) não segura o pedido: passa ao próximo e lembra a família que funcionou."""
    p, url = _servidor()
    porta = int(url.split(":")[2].split("/")[0])
    morto = socket.socket()
    morto.bind(("127.0.0.1", 0))
    porta_morta = morto.getsockname()[1]
    morto.close()
    original = socket.getaddrinfo
    socket.getaddrinfo = lambda h, pt, *a, **k: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", porta_morta, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", porta))]
    encontro._familia_boa[0] = socket.AF_INET6
    try:
        s = encontro._conectar(("servidor.teste", porta), 10)
        assert s.getpeername()[1] == porta and s.gettimeout() == 10
        s.close()
        assert encontro._familia_boa[0] == socket.AF_INET
        e = encontro.Encontro(GerenteFalso(), url=f"http://servidor.teste:{porta}/api", arquivo=os.path.join(DADOS, "id5.json"))
        assert e._post("/registrar", {"id": e.id, "chave": e.chave})["ok"]
    finally:
        socket.getaddrinfo = original
        p.kill()


def teste_turn_vai_para_o_agente():
    g = remoto.Gerente(comando=["agente"])
    t = {"urls": ["turn:203.0.113.9:3478?transport=udp", "stun:x", "turn:a b"], "username": "1:u", "credential": "c+/="}
    assert g._linha_de_comando(True, t) == ["agente", "--turn", "turn:203.0.113.9:3478?transport=udp", "--turn-usuario", "1:u", "--turn-senha", "c+/="]
    assert g._linha_de_comando(True, None) == ["agente"]
    assert g._linha_de_comando(False, t) == ["agente", "--sem-stun"]
    s = remoto.Sessao()
    s.definir_senha("abcd-efgh")
    assert s.senha == "ABCDEFGH"
    for ruim in ("curta", "ABCD-EFGH-JKLM"):
        try:
            s.definir_senha(ruim)
            raise AssertionError("aceitou " + ruim)
        except remoto.Erro:
            pass


def teste_ponta_a_ponta_pelo_id():
    """Serviço com o agente de verdade (outro processo) e um técnico em aiortc, como faz o navegador."""
    import asyncio
    from aiortc import RTCPeerConnection, RTCSessionDescription

    p, url = _servidor(turn=False)
    g = remoto.Gerente(comando=[sys.executable, os.path.join(RAIZ, "src", "remoto_agente.py"), "--sem-stun"])
    e = encontro.Encontro(g, url=url, arquivo=os.path.join(DADOS, "id3.json"))
    e.iniciar()

    async def tecnico(senha):
        d = _req(url, "/conectar", {"id": e.id})[1]
        conv = await asyncio.get_running_loop().run_in_executor(None, _receber_ate, url, d["pedido"], "convite")
        oferta = remoto.ler_convite(conv["codigo"])
        tec, msgs = RTCPeerConnection(), []

        @tec.on("datachannel")
        def _canal(c):
            c.on("message", lambda m: msgs.append(json.loads(m)))
            c.send(json.dumps({"t": "prova", "mac": r["prova"]}))

        await tec.setRemoteDescription(RTCSessionDescription(sdp=oferta, type="offer"))
        await tec.setLocalDescription(await tec.createAnswer())
        r = remoto.resposta_do_tecnico(senha, oferta, tec.localDescription.sdp)
        _req(url, "/responder", {"pedido": d["pedido"], "dados": {"tipo": "resposta", "codigo": r["codigo"]}})
        return tec, msgs, d["pedido"], oferta, r

    async def rodar():
        _ate(lambda: e.situacao == "online")
        # senha errada: o técnico recebe o motivo pelo servidor
        tec, msgs, pedido, _, _ = await tecnico("ZZZZ-ZZZZ")
        erro = await asyncio.get_running_loop().run_in_executor(None, _receber_ate, url, pedido, "erro")
        assert "Senha incorreta" in erro["msg"]
        await tec.close()
        _ate(lambda: g.estado()["estado"] == "encerrada")

        senha = e.estado()["senha"]
        tec, msgs, pedido, oferta, r = await tecnico(senha)
        fim = time.time() + 30
        while g.estado()["estado"] != "aguardando_aceite":
            assert time.time() < fim, g.estado()
            await asyncio.sleep(0.1)
        assert g.estado()["conferencia"] == r["conferencia"]
        while not any(m["t"] == "prova" for m in msgs):
            await asyncio.sleep(0.05)
        mac = next(m["mac"] for m in msgs if m["t"] == "prova")
        assert remoto.conferir_atendido(senha, oferta, tec.localDescription.sdp, mac)
        assert g.ordem("aceitar") == (True, "")
        while not any(m["t"] == "ativa" for m in msgs):
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.2)                  # o vigia vê a sessão ativa
        g.parar()
        await tec.close()
        _ate(lambda: e.estado()["senha"] != senha)

    try:
        asyncio.run(rodar())
    finally:
        e.parar()
        g.parar()
        p.kill()


if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("teste_"):
                f()
                print("ok", nome)
        print("todos os testes do encontro passaram")
    finally:
        shutil.rmtree(DADOS, ignore_errors=True)
