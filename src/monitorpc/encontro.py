"""ID e senha do acesso remoto, pelo ponto de encontro (servidor-vps/encontro.js).

O ponto de encontro só apresenta os dois PCs: leva o convite deste PC ao técnico e a resposta do técnico
para cá. A tela e os comandos vão direto de um PC ao outro, e a senha nunca passa por lá. Quem conecta
prova que sabe a senha pela conexão já criptografada, amarrada aos certificados dos dois lados
(remoto.py); o servidor não consegue se passar por ninguém.

  - ID: 9 números, troca quando o PC reinicia ou pelo botão. Fica em BASE/remoto_id.json junto com a chave
    que prova ao servidor que o ID é deste PC (a pasta só é legível pelo serviço e por administradores).
  - Senha: 8 letras e números, só na memória. Troca depois de cada sessão, depois de 3 senhas erradas,
    quando o serviço reinicia e pelo botão.
"""
import os
import json
import time
import socket
import secrets
import threading
import http.client
import urllib.error
import urllib.parse
import urllib.request

from . import config, remoto

SERVIDOR = "https://monitor.cntoficial.com.br/api"
ARQUIVO = os.path.join(config.BASE, "remoto_id.json")
ESPERA_RESPOSTA = 60        # do convite entregue até a resposta do técnico chegar
ERROS_PARA_TROCAR = 3
_ESPERA_LONGA = 40          # o servidor devolve vazio em 25 s
CONECTAR = 5                # por endereço: um IPv6 quebrado não segura cada pedido por 20 s ou mais
_familia_boa = [socket.AF_INET]


def _conectar(endereco, timeout=None, source_address=None, **_k):
    """socket.create_connection com prazo curto por endereço, começando pela família (IPv4/IPv6) que funcionou
    da última vez. O padrão do Windows tenta cada IPv6 por ~21 s antes de passar ao próximo: numa rede com
    IPv6 sem saída, cada pedido ao servidor levava mais de 40 s e o pedido de conexão chegava atrasado."""
    host, porta = endereco
    infos = socket.getaddrinfo(host, porta, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda i: i[0] != _familia_boa[0])
    total = timeout if isinstance(timeout, (int, float)) else None
    erro = None
    for fam, tipo, proto, _c, end in infos:
        s = socket.socket(fam, tipo, proto)
        try:
            s.settimeout(min(CONECTAR, total) if total else CONECTAR)
            if source_address:
                s.bind(source_address)
            s.connect(end)
            s.settimeout(total)
            _familia_boa[0] = fam
            return s
        except OSError as e:
            erro = e
            s.close()
    raise erro or OSError(f"Não encontrei o endereço de {host}.")


class _HTTP(http.client.HTTPConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _conectar


class _HTTPS(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _conectar


class _AbrirHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTP, req)


class _AbrirHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPS, req, context=self._context)


_abrir = urllib.request.build_opener(_AbrirHTTP, _AbrirHTTPS).open


def novo_id():
    return str(secrets.randbelow(900000000) + 100000000)


def formatar_id(i):
    i = str(i or "")
    return " ".join(i[k:k + 3] for k in range(0, len(i), 3))


def texto_para_copiar(i, senha):
    return f"CNT Monitor — acesso remoto\nID: {formatar_id(i)}\nSenha: {senha}"


def _boot():
    try:
        import psutil
        return int(psutil.boot_time())
    except Exception:  # noqa: BLE001
        return 0


class Encontro:
    """Mantém este PC registrado e atende os pedidos de conexão que chegam pelo ID."""

    def __init__(self, gerente, url=SERVIDOR, arquivo=ARQUIVO, boot=_boot, agora=time.time):
        self.gerente = gerente
        self.url = url.rstrip("/")
        self.arquivo = arquivo
        self._boot = boot
        self._agora = agora
        self._lock = threading.RLock()
        self.situacao, self.msg = "desligado", ""
        self.turn = None
        self.senha = remoto.nova_senha()
        self._erros = 0
        self._pedido = None              # pedido de conexão em andamento: {pedido, desde, respondeu}
        self._parar = threading.Event()
        self._novo = threading.Event()   # ID trocado: a espera longa em curso é abandonada
        self._ler_id()

    # ------------------------------------------------------------ ID e senha
    def _ler_id(self):
        d = {}
        try:
            with open(self.arquivo, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            pass
        boot = self._boot()
        if (isinstance(d, dict) and str(d.get("id", "")).isdigit() and len(str(d["id"])) == 9
                and isinstance(d.get("chave"), str) and len(d["chave"]) >= 32 and d.get("boot") == boot):
            self.id, self.chave = str(d["id"]), d["chave"]
        else:
            self._gerar_id(boot)

    def _gerar_id(self, boot=None):
        self.id, self.chave = novo_id(), secrets.token_urlsafe(32)
        try:
            os.makedirs(os.path.dirname(self.arquivo), exist_ok=True)
            with open(self.arquivo, "w", encoding="utf-8") as f:
                json.dump({"id": self.id, "chave": self.chave, "boot": self._boot() if boot is None else boot}, f)
        except OSError:
            pass                         # sem gravar, o ID só dura até o serviço reiniciar

    def trocar_id(self):
        """O ID novo é registrado aqui mesmo, antes de voltar à tela: se ficasse para o laço principal (que
        está no meio de uma espera longa), quem digitasse o ID novo logo em seguida ouviria "Não encontrei"."""
        with self._lock:
            antigo = (self.id, self.chave)
            self._gerar_id()
            self.situacao = "conectando"
        try:
            self._registrar()
        except Exception:  # noqa: BLE001  (sem internet agora: o laço principal tenta de novo)
            pass
        self._novo.set()
        threading.Thread(target=self._sair, args=antigo, daemon=True, name="encontro-sair").start()
        return self.id

    def _sair(self, i, c):
        try:
            self._post("/sair", {"id": i, "chave": c}, falha_ok=True)
        except Exception:  # noqa: BLE001  (sem internet: o servidor esquece o ID antigo sozinho)
            pass

    def trocar_senha(self):
        with self._lock:
            self.senha = remoto.nova_senha()
            self._erros = 0
        return self.senha

    def estado(self):
        with self._lock:
            return {"id": formatar_id(self.id), "senha": remoto.formatar_senha(self.senha), "situacao": self.situacao,
                    "msg": self.msg, "texto": texto_para_copiar(self.id, remoto.formatar_senha(self.senha)),
                    "servidor": self.url}

    # ------------------------------------------------------------ servidor
    def _post(self, rota, corpo, falha_ok=False):
        req = urllib.request.Request(self.url + rota, data=json.dumps(corpo).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "CNTMonitor/" + config.VERSAO})
        try:
            with _abrir(req, timeout=15) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read() or b"{}")
            except ValueError:
                d = {}
            if falha_ok:
                return d
            raise _Recusa(e.code, d.get("erro") or str(e)) from None

    def _registrar(self):
        while True:
            with self._lock:
                i, c = self.id, self.chave
            try:
                d = self._post("/registrar", {"id": i, "chave": c})
                with self._lock:
                    self.turn = d.get("turn")
                    self.situacao, self.msg = "online", ""
                return
            except _Recusa as e:
                if e.codigo != 409:
                    raise
                self._gerar_id()         # ID sorteado já existe lá: sorteia outro

    def _esperar(self):
        with self._lock:
            q = urllib.parse.urlencode({"id": self.id, "chave": self.chave})
        req = urllib.request.Request(f"{self.url}/esperar?{q}", headers={"User-Agent": "CNTMonitor/" + config.VERSAO})
        try:
            with _abrir(req, timeout=_ESPERA_LONGA) as r:
                return json.loads(r.read() or b"{}").get("msgs") or []
        except urllib.error.HTTPError as e:
            raise _Recusa(e.code, str(e)) from None

    def rodar(self):
        espera = 2
        while not self._parar.is_set():
            try:
                self._novo.clear()
                self._registrar()
                espera = 2
                while not self._parar.is_set() and not self._novo.is_set():
                    for m in self._esperar():
                        if not self._novo.is_set():
                            self.tratar(m)
            except _Recusa as e:
                if e.codigo == 404:      # o servidor esqueceu o registro (reiniciou, ID trocado): registra de novo na hora
                    continue
                self._fora(f"O servidor recusou: {e}")
            except Exception as e:  # noqa: BLE001  (sem internet, servidor fora do ar)
                self._fora(f"Sem conexão com o servidor do acesso remoto ({type(e).__name__}).")
            self._parar.wait(espera)
            espera = min(espera * 2, 60)

    def _fora(self, msg):
        with self._lock:
            self.situacao, self.msg = "fora", msg

    def iniciar(self):
        threading.Thread(target=self.rodar, daemon=True, name="encontro").start()
        threading.Thread(target=self._vigiar_sempre, daemon=True, name="encontro-vigia").start()

    def _vigiar_sempre(self):
        while not self._parar.wait(1):
            self.vigiar()

    def parar(self):
        self._parar.set()
        with self._lock:
            i, c = self.id, self.chave
        if self.situacao == "online":
            try:
                self._post("/sair", {"id": i, "chave": c}, falha_ok=True)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ pedidos de conexão
    def _enviar(self, pedido, dados):
        with self._lock:
            i, c = self.id, self.chave
        try:
            self._post("/enviar", {"id": i, "chave": c, "pedido": pedido, "dados": dados})
        except Exception:  # noqa: BLE001  (técnico desistiu; o vigia encerra o convite)
            pass

    def tratar(self, m):
        if not isinstance(m, dict):
            return
        pedido = m.get("pedido")
        if not isinstance(pedido, str) or not pedido:
            return
        if m.get("tipo") == "pedido":
            livre = self.gerente.estado().get("estado") in ("parada", "encerrada")
            if not livre:
                return self._enviar(pedido, {"tipo": "erro", "msg": "Esse computador já está em outro atendimento. Tente daqui a pouco."})
            with self._lock:
                turn, senha = self.turn, self.senha
            ok, msg = self.gerente.pedir(turn=turn)
            if ok:
                ok, msg = self.gerente.ordem("senha", senha=senha)
            convite = self.gerente.estado().get("convite") if ok else ""
            if not convite:
                self.gerente.parar()
                return self._enviar(pedido, {"tipo": "erro", "msg": msg or "O outro computador não conseguiu preparar a conexão."})
            with self._lock:
                self._pedido = {"pedido": pedido, "desde": self._agora(), "respondeu": False, "visto": "convite"}
            return self._enviar(pedido, {"tipo": "convite", "codigo": convite})
        if m.get("tipo") == "msg":
            d = m.get("dados") if isinstance(m.get("dados"), dict) else {}
            with self._lock:
                atual = self._pedido if self._pedido and self._pedido["pedido"] == pedido else None
            if not atual:
                return self._enviar(pedido, {"tipo": "erro", "msg": "Esse pedido de conexão venceu. Tente de novo."})
            if d.get("tipo") == "resposta" and not atual["respondeu"]:
                atual["respondeu"] = True
                ok, msg = self.gerente.ordem("resposta", codigo=str(d.get("codigo") or ""))
                if not ok:
                    self._enviar(pedido, {"tipo": "erro", "msg": msg})
            elif d.get("tipo") == "desistiu":
                self.gerente.parar()

    def vigiar(self):
        """Fim de sessão, senha errada e técnico que sumiu antes de responder."""
        with self._lock:
            atual = self._pedido
        if not atual:
            return
        e = self.gerente.estado()
        s = e.get("estado")
        if s == "convite" and not atual["respondeu"] and self._agora() - atual["desde"] > ESPERA_RESPOSTA:
            self.gerente.parar()
            s = "encerrada"
        if s in ("ativa", "aguardando_aceite"):
            atual["visto"] = s
        if s in ("encerrada", "parada"):
            if atual["visto"] != "ativa":    # o técnico fica sabendo por que não entrou (antes de ligar não há outro canal)
                msg = ("Senha incorreta. Confira a senha com a pessoa e tente de novo." if e.get("senha_errada")
                       else e.get("motivo") or "A conexão foi encerrada no outro computador.")
                threading.Thread(target=self._enviar, args=(atual["pedido"], {"tipo": "erro", "msg": msg}), daemon=True).start()
            with self._lock:
                self._pedido = None
                if e.get("senha_errada"):
                    self._erros += 1
                    if self._erros >= ERROS_PARA_TROCAR:
                        self.senha, self._erros = remoto.nova_senha(), 0
                elif atual["visto"] == "ativa":
                    self.senha, self._erros = remoto.nova_senha(), 0    # senha usada não vale de novo


class _Recusa(Exception):
    def __init__(self, codigo, msg):
        super().__init__(msg)
        self.codigo = codigo
