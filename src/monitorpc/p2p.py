"""Canal direto (P2P) entre dois CNT Monitor para a API de chamadas, pelo ID do compartilhamento online.

O PC A (quem monitora) digita o ID do PC B. O ponto de encontro do acesso remoto (servidor-vps/encontro.js) só
leva a oferta e a resposta WebRTC de um ao outro; as chamadas vão por um canal de dados direto entre os dois PCs
(ou pelo retransmissor, se a rede não deixar). A chave da API nunca passa pelo servidor: quem pede prova que a
conhece por uma assinatura amarrada aos certificados dos dois lados (como a senha do acesso remoto, remoto.py).

  PC A:  POST /conectar {id, finalidade} -> GET /receber (convite) -> POST /responder -> canal -> prova -> pedidos
  PC B:  GET /esperar (pedido) -> POST /enviar (convite) -> resposta -> canal -> confere a prova -> atende
"""
import re
import json
import time
import hmac
import asyncio
import hashlib
import secrets
import threading
import concurrent.futures
import urllib.error
import urllib.parse
import urllib.request

from . import config, encontro, remoto

FINALIDADE = "chamadas"
URL = encontro.SERVIDOR
STUN = ("stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478")
PEDACO = 12000                   # caracteres por mensagem do canal (o aiortc aceita 64 KB por mensagem)
PRAZO_CONEXAO = 45               # do pedido até o canal provar a chave
PRAZO_PEDIDO = 30
OCIOSO_CLIENTE = 60              # o canal sem pedidos por tanto tempo é fechado (o monitoramento pede a cada 30 s)
OCIOSO_SERVIDOR = 150
MAX_SESSOES = 4
FALHAS_MAX = 10                  # chaves erradas em 5 minutos antes de recusar novos canais
_falhas = []


class Falha(RuntimeError):
    """Mensagem pronta para mostrar a quem está usando."""


def id_de(texto):
    """O ID do compartilhamento online (9 números, com ou sem espaços/traços), ou "" se o texto não for um."""
    d = re.sub(r"[\s-]", "", str(texto or ""))
    return d if re.fullmatch(r"\d{9}", d) else ""


def prova(chave, papel, sdp_servidor, sdp_cliente):
    msg = f"cnt-chamadas-v1|{papel}|{remoto.impressao(sdp_servidor)}|{remoto.impressao(sdp_cliente)}"
    return hmac.new(chave.encode("utf-8"), msg.encode("ascii"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- mensagens em pedaços
def _quebrar(texto):
    i = secrets.token_hex(6)
    n = max(1, -(-len(texto) // PEDACO))
    return [f"{i}|{k}|{n}|{texto[k * PEDACO:(k + 1) * PEDACO]}" for k in range(n)]


class _Montador:
    def __init__(self):
        self._p = {}

    def add(self, texto):
        """Devolve o objeto quando a última parte chega; None enquanto faltam partes; ValueError se estiver estragada."""
        try:
            i, k, n, d = str(texto).split("|", 3)
            k, n = int(k), int(n)
        except ValueError:
            raise ValueError("mensagem inválida") from None
        if not 0 <= k < n <= 4000 or len(self._p) > 16:
            raise ValueError("mensagem inválida")
        e = self._p.setdefault(i, {})
        e[k] = d
        if len(e) < n:
            return None
        del self._p[i]
        obj = json.loads("".join(e[x] for x in range(n)))
        if not isinstance(obj, dict):
            raise ValueError("mensagem inválida")
        return obj


def _mandar(canal, obj):
    for parte in _quebrar(json.dumps(obj, separators=(",", ":"))):
        canal.send(parte)


# ---------------------------------------------------------------- laço de eventos próprio
_laco = None
_trava = threading.Lock()


def _loop():
    global _laco
    with _trava:
        if _laco is None:
            l = asyncio.new_event_loop()
            threading.Thread(target=l.run_forever, daemon=True, name="p2p-chamadas").start()
            asyncio.run_coroutine_threadsafe(_varrer(), l)
            _laco = l
        return _laco


def _rodar(coro, prazo):
    f = asyncio.run_coroutine_threadsafe(coro, _loop())
    try:
        return f.result(prazo)
    except concurrent.futures.TimeoutError:
        f.cancel()
        raise Falha("O outro computador não respondeu a tempo.") from None


def _http(metodo, rota, corpo=None, params=None, timeout=15):
    q = "?" + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(URL.rstrip("/") + rota + q, method=metodo,
                                 data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "CNTMonitor/" + config.VERSAO})
    try:
        with encontro._abrir(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("erro")
        except ValueError:
            msg = None
        raise Falha(msg or f"O servidor do compartilhamento online respondeu {e.code}.") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Falha(f"Sem conexão com o servidor do compartilhamento online ({getattr(e, 'reason', e)}).") from None


def _servidores(turn):
    from aiortc import RTCIceServer
    lista = [RTCIceServer(urls=u) for u in STUN]
    if isinstance(turn, dict) and turn.get("urls") and turn.get("username") and turn.get("credential"):
        urls = [u for u in turn["urls"] if isinstance(u, str) and u.startswith(("turn:", "turns:")) and " " not in u]
        # o aiortc só usa o primeiro endereço: pelo TCP o retransmissor passa também onde o UDP é bloqueado
        urls.sort(key=lambda u: 2 if u.startswith("turns:") else 0 if "transport=tcp" in u else 1)
        if urls:
            lista.append(RTCIceServer(urls=urls, username=str(turn["username"]), credential=str(turn["credential"])))
    return lista


def _pc(turn):
    from aiortc import RTCConfiguration, RTCPeerConnection
    return RTCPeerConnection(RTCConfiguration(iceServers=_servidores(turn)))


# ---------------------------------------------------------------- PC A: pede ao PC B
class _Canal:
    """Uma conexão aberta com um ID: o PC A manda pedidos e o PC B responde."""

    def __init__(self, id_, chave):
        self.id, self.chave = id_, chave
        self.pc = self.canal = None
        self.morto = False
        self.ultimo = time.time()
        self.futuros = {}
        self._montador = _Montador()
        self._pronto = None

    def vivo(self):
        return (not self.morto and self.pc is not None and self.pc.connectionState not in ("failed", "closed")
                and self.canal is not None and self.canal.readyState == "open")

    async def abrir(self):
        from aiortc import RTCSessionDescription
        loop = asyncio.get_running_loop()
        self._pronto = loop.create_future()
        fim = time.time() + PRAZO_CONEXAO
        d = await loop.run_in_executor(None, lambda: _http("POST", "/conectar", {"id": self.id, "finalidade": FINALIDADE}))
        pedido = d.get("pedido")
        sdp = await self._convite(loop, pedido, fim)
        self.pc = _pc(d.get("turn"))
        self.pc.on("datachannel", self._novo_canal)
        self.pc.on("connectionstatechange", self._estado)
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        self.sdp_srv, self.sdp_cli = sdp, self.pc.localDescription.sdp
        await loop.run_in_executor(None, lambda: _http("POST", "/responder", {"pedido": pedido, "dados": {"tipo": "resposta", "sdp": self.sdp_cli}}))
        try:
            await asyncio.wait_for(self._pronto, max(5, fim - time.time()))
        except asyncio.TimeoutError:
            raise Falha("Não consegui abrir a conexão direta. Em algumas redes (4G nos dois lados) ela não é possível.") from None
        self.ultimo = time.time()

    async def _convite(self, loop, pedido, fim):
        while time.time() < fim:
            r = await loop.run_in_executor(None, lambda: _http("GET", "/receber", params={"pedido": pedido}, timeout=40))
            for m in r.get("msgs") or []:
                dados = (m or {}).get("dados")
                dados = dados if isinstance(dados, dict) else {}
                if dados.get("tipo") == "erro":
                    raise Falha(str(dados.get("msg") or "O outro computador recusou a conexão."))
                if isinstance(dados.get("sdp"), str) and dados.get("tipo") == "convite":
                    return dados["sdp"]
                if dados.get("codigo"):         # o outro lado atendeu como acesso remoto: versão sem o canal de chamadas
                    await loop.run_in_executor(None, lambda: _http("POST", "/responder", {"pedido": pedido, "dados": {"tipo": "desistiu"}}))
                    raise Falha("O outro computador está com uma versão do CNT Monitor sem esta função. Atualize-o.")
        raise Falha("O outro computador não respondeu. Confira o ID e se o CNT Monitor está aberto lá.")

    def _novo_canal(self, canal):
        if canal.label != "chamadas" or self.canal is not None:
            return
        self.canal = canal
        canal.on("message", self._msg)
        asyncio.ensure_future(self._provar())

    async def _provar(self):
        for _ in range(300):
            if self.canal.readyState == "open":
                _mandar(self.canal, {"t": "prova", "mac": prova(self.chave, "cliente", self.sdp_srv, self.sdp_cli)})
                return
            await asyncio.sleep(0.1)

    def _estado(self):
        if self.pc.connectionState in ("failed", "closed"):
            self._falhar(Falha("A conexão com o outro computador caiu."))

    def _falhar(self, erro):
        self.morto = True
        if self._pronto and not self._pronto.done():
            self._pronto.set_exception(erro)
        for f in self.futuros.values():
            if not f.done():
                f.set_exception(erro)

    def _msg(self, texto):
        try:
            obj = self._montador.add(texto)
        except ValueError:
            return self._falhar(Falha("O outro computador mandou uma mensagem inválida."))
        if obj is None:
            return
        t = obj.get("t")
        if t == "ok":
            esperado = prova(self.chave, "servidor", self.sdp_srv, self.sdp_cli)
            if hmac.compare_digest(esperado, str(obj.get("mac", ""))):
                if not self._pronto.done():
                    self._pronto.set_result(True)
            else:
                self._falhar(Falha("O outro computador não provou ter a chave. A conexão foi cancelada."))
        elif t == "erro":
            self._falhar(Falha(str(obj.get("msg") or "O outro computador recusou a conexão.")))
        elif t == "res":
            f = self.futuros.get(obj.get("i"))
            if f and not f.done():
                f.set_result(obj)

    async def requisitar(self, rota, corpo, params):
        i = secrets.token_hex(6)
        f = self.futuros[i] = asyncio.get_running_loop().create_future()
        try:
            _mandar(self.canal, {"t": "req", "i": i, "rota": rota, "m": "POST" if corpo is not None else "GET",
                                 "q": params or {}, "c": corpo})
            r = await asyncio.wait_for(f, PRAZO_PEDIDO)
        except asyncio.TimeoutError:
            raise Falha("O outro computador não respondeu a tempo.") from None
        finally:
            self.futuros.pop(i, None)
        self.ultimo = time.time()
        d = r.get("d") if isinstance(r.get("d"), dict) else {}
        if int(r.get("cod") or 0) >= 400:
            raise Falha(str(d.get("erro") or f"O outro computador respondeu {r.get('cod')}."))
        return d

    async def fechar(self):
        self.morto = True
        if self.pc:
            try:
                await self.pc.close()
            except Exception:  # noqa: BLE001
                pass


_canais = {}                     # (id, chave) -> _Canal; só mexido dentro do laço
_travas = {}


async def _pedir(id_, chave, rota, corpo, params):
    k = (id_, chave)
    trava = _travas.setdefault(k, asyncio.Lock())
    async with trava:
        c = _canais.get(k)
        if c is None or not c.vivo():
            if c:
                await c.fechar()
            c = _Canal(id_, chave)
            try:
                await c.abrir()
            except BaseException:
                await c.fechar()
                raise
            _canais[k] = c
    try:
        return await c.requisitar(rota, corpo, params)
    except Falha:
        if c.morto:
            _canais.pop(k, None)
            await c.fechar()
        raise


async def _varrer():
    while True:
        await asyncio.sleep(15)
        for k, c in list(_canais.items()):
            if c.morto or time.time() - c.ultimo > OCIOSO_CLIENTE:
                _canais.pop(k, None)
                await c.fechar()


def pedir(id_, chave, rota, corpo=None, params=None):
    """Chamado de qualquer thread: faz o pedido ao PC `id_` pelo canal direto. Falha com RuntimeError (Falha)."""
    try:
        import aiortc  # noqa: F401
    except ImportError:
        raise Falha("Este computador não tem o componente da conexão direta (aiortc). Reinstale o CNT Monitor.") from None
    return _rodar(_pedir(id_, chave, rota, corpo, params), PRAZO_CONEXAO + PRAZO_PEDIDO + 10)


# ---------------------------------------------------------------- PC B: atende o PC A
class _SessaoB:
    def __init__(self, dono, pedido):
        self.dono, self.pedido = dono, pedido
        self.pc = self.canal = None
        self.sdp_srv = self.sdp_cli = ""
        self.provado = False
        self.fechada = False
        self.ultimo = time.time()
        self._montador = _Montador()

    def msg(self, texto):
        self.ultimo = time.time()
        try:
            obj = self._montador.add(texto)
        except ValueError:
            return asyncio.ensure_future(self.fechar())
        if obj is not None:
            asyncio.ensure_future(self._tratar(obj))

    async def _tratar(self, obj):
        t = obj.get("t")
        if not self.provado:
            if t != "prova":
                return await self.fechar()
            chave = self.dono.chave()
            if not chave:
                return await self._recusar("O Call flow está desabilitado nesse computador.")
            if not self.dono.pode_tentar():
                return await self._recusar("Muitas tentativas com chave errada. Espere 5 minutos.")
            esperado = prova(chave, "cliente", self.sdp_srv, self.sdp_cli)
            if not hmac.compare_digest(esperado, str(obj.get("mac", ""))):
                _falhas.append(time.time())
                return await self._recusar("Chave inválida ou vencida. Peça a chave atual ao outro computador.")
            self.provado = True
            return _mandar(self.canal, {"t": "ok", "mac": prova(chave, "servidor", self.sdp_srv, self.sdp_cli)})
        if t == "req":
            q = obj.get("q") if isinstance(obj.get("q"), dict) else {}
            c = obj.get("c") if isinstance(obj.get("c"), dict) else None
            cod, d = await asyncio.get_running_loop().run_in_executor(
                None, self.dono.atender, str(obj.get("rota") or ""), str(obj.get("m") or "GET"), q, c)
            if not self.fechada:
                _mandar(self.canal, {"t": "res", "i": obj.get("i"), "cod": cod, "d": d})

    async def _recusar(self, msg):
        _mandar(self.canal, {"t": "erro", "msg": msg})
        await asyncio.sleep(1)               # a mensagem sai antes de a conexão fechar
        await self.fechar()

    async def fechar(self):
        if self.fechada:
            return
        self.fechada = True
        self.dono.sessoes.pop(self.pedido, None)
        if self.pc:
            try:
                await self.pc.close()
            except Exception:  # noqa: BLE001
                pass


class Servidor:
    """O lado de quem é monitorado. `atender(rota, metodo, q, corpo) -> (cod, dict)`, `chave() -> str` (vazio com o
    Call flow desligado) e `enviar(pedido, dados)` (o ponto de encontro) vêm do motor."""

    def __init__(self, atender, chave, enviar):
        self.atender, self.chave, self._enviar = atender, chave, enviar
        self.sessoes = {}                # pedido -> _SessaoB (só mexido dentro do laço)
        self._vigia = False

    def pode_tentar(self):
        agora = time.time()
        _falhas[:] = [x for x in _falhas if agora - x < 300]
        return len(_falhas) < FALHAS_MAX

    def dele(self, pedido):
        return pedido in self.sessoes

    def pedido(self, pedido, turn):
        self._solto(self._abrir(pedido, turn))

    def resposta(self, pedido, dados):
        self._solto(self._receber(pedido, dados))

    def _solto(self, coro):
        asyncio.run_coroutine_threadsafe(coro, _loop()).add_done_callback(lambda f: f.exception())

    async def _abrir(self, pedido, turn):
        loop = asyncio.get_running_loop()
        if not self.chave():
            return await loop.run_in_executor(None, self._enviar, pedido, {"tipo": "erro", "msg": "O Call flow está desabilitado nesse computador."})
        if len(self.sessoes) >= MAX_SESSOES:
            return await loop.run_in_executor(None, self._enviar, pedido, {"tipo": "erro", "msg": "Esse computador já tem conexões demais. Tente daqui a pouco."})
        s = self.sessoes[pedido] = _SessaoB(self, pedido)
        try:
            s.pc = _pc(turn)
            s.canal = s.pc.createDataChannel("chamadas")
            s.canal.on("message", s.msg)
            s.pc.on("connectionstatechange", lambda: asyncio.ensure_future(s.fechar()) if s.pc.connectionState in ("failed", "closed") else None)
            await s.pc.setLocalDescription(await s.pc.createOffer())
            s.sdp_srv = s.pc.localDescription.sdp
            await loop.run_in_executor(None, self._enviar, pedido, {"tipo": "convite", "sdp": s.sdp_srv})
        except Exception:  # noqa: BLE001
            await s.fechar()
            raise
        asyncio.ensure_future(self._vigiar(s))

    async def _receber(self, pedido, dados):
        from aiortc import RTCSessionDescription
        s = self.sessoes.get(pedido)
        if not s or s.sdp_cli or not isinstance(dados, dict):
            return
        if dados.get("tipo") == "desistiu":
            return await s.fechar()
        sdp = dados.get("sdp")
        try:
            if dados.get("tipo") != "resposta" or not isinstance(sdp, str) or len(sdp) > 60000:
                raise ValueError
            remoto.impressao(sdp)
            s.sdp_cli = sdp
            await s.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        except Exception:  # noqa: BLE001  (resposta que não serve)
            await s.fechar()

    async def _vigiar(self, s):
        inicio = time.time()
        while not s.fechada:
            await asyncio.sleep(2)
            agora = time.time()
            if (not s.provado and agora - inicio > PRAZO_CONEXAO) or (s.provado and agora - s.ultimo > OCIOSO_SERVIDOR):
                await s.fechar()
