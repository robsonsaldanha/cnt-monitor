"""Acesso remoto ponta a ponta, sem servidor no meio: códigos, prova da senha e regras da sessão.

Quem pede ajuda gera um CONVITE (a oferta WebRTC compactada) e uma senha aleatória; o técnico
devolve uma RESPOSTA. A tela e os comandos vão direto de um PC ao outro (DTLS-SRTP).

O convite viaja por um canal qualquer (WhatsApp, e-mail) e pode ser adulterado, então ele sozinho
não prova nada. A segurança vem de três coisas, todas aqui:
  1. a senha nunca viaja: dela e das impressões digitais dos dois certificados da conexão sai uma
     chave (scrypt), e cada lado prova que a tem (HMAC). Com alguém no meio as impressões não
     batem, a chave sai outra e a prova falha;
  2. uma tentativa só: senha errada encerra a sessão e o convite deixa de valer;
  3. aceite de quem está no computador, com um código de conferência igual nos dois lados.
Nada disso é gravado em disco.
"""
import os
import re
import sys
import hmac
import json
import time
import zlib
import base64
import hashlib
import secrets
import threading
import subprocess

from . import config, sessao

ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sem 0/O e 1/I: a senha costuma ser ditada
TAMANHO_SENHA = 12                              # 12 de 32 símbolos = 60 bits
VALIDADE_CONVITE = 600                          # segundos para colar a resposta
ESPERA_CONEXAO = 60                             # da resposta até a prova da senha
ESPERA_ACEITE = 120
DURACAO_MAXIMA = 4 * 3600

_MAX_CODIGO = 40000
_MAX_SDP = 60000
_RE_IMPRESSAO = re.compile(r"^a=fingerprint:sha-256 ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){31})\s*$", re.M)
PAPEIS = ("tecnico", "atendido")


class Erro(ValueError):
    """Mensagem pronta para mostrar a quem está usando."""


# ---------------------------------------------------------------- senha
def nova_senha():
    return "".join(secrets.choice(ALFABETO) for _ in range(TAMANHO_SENHA))


def limpar_senha(txt):
    """Aceita a senha como a pessoa digitar: com traços, espaços ou minúsculas."""
    return "".join(ch for ch in str(txt or "").upper() if ch in ALFABETO)


def formatar_senha(senha):
    return "-".join(senha[i:i + 4] for i in range(0, len(senha), 4))


# ---------------------------------------------------------------- códigos
def _codigo(prefixo, sdp):
    if not isinstance(sdp, str) or not sdp or len(sdp) > _MAX_SDP:
        raise Erro("Não consegui montar o código.")
    bruto = zlib.compress(json.dumps({"v": 1, "sdp": sdp}, separators=(",", ":")).encode("utf-8"), 9)
    return prefixo + base64.urlsafe_b64encode(bruto).decode("ascii").rstrip("=")


def _ler(prefixo, codigo, nome):
    invalido = Erro(f"Esse {nome} não é válido. Copie o texto inteiro e cole de novo.")
    txt = "".join(str(codigo or "").split())       # quebras de linha de e-mail/WhatsApp
    if len(txt) > _MAX_CODIGO or not txt.startswith(prefixo):
        raise invalido
    try:
        corpo = txt[len(prefixo):]
        bruto = base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4))
        d = zlib.decompressobj()
        dados = d.decompress(bruto, _MAX_SDP + 200)   # limite: código pequeno não vira gigabytes
        if d.unconsumed_tail or not d.eof:
            raise invalido
        obj = json.loads(dados.decode("utf-8"))
    except (ValueError, zlib.error):
        raise invalido from None
    if not isinstance(obj, dict) or obj.get("v") != 1 or not isinstance(obj.get("sdp"), str) or not obj["sdp"]:
        raise invalido
    impressao(obj["sdp"])
    return obj["sdp"]


def convite(sdp):
    return _codigo("MPC1-", sdp)


def ler_convite(codigo):
    return _ler("MPC1-", codigo, "convite")


def resposta(sdp):
    return _codigo("MPC2-", sdp)


def ler_resposta(codigo):
    return _ler("MPC2-", codigo, "código de resposta")


# ---------------------------------------------------------------- prova da senha
def impressao(sdp):
    """Impressão digital SHA-256 do certificado anunciado no SDP (a mesma que o DTLS confere)."""
    achadas = {x.upper() for x in _RE_IMPRESSAO.findall(sdp.replace("\r\n", "\n"))}
    if len(achadas) != 1:
        raise Erro("O código não traz a identidade da conexão. Gere outro.")
    return achadas.pop()


def chave(senha, sdp_atendido, sdp_tecnico):
    """Chave da sessão: só sai igual nos dois lados se a senha for a mesma e se os dois estiverem
    falando com o certificado um do outro (sem ninguém no meio)."""
    sal = hashlib.sha256(("monitor-pc-remoto-v1|" + impressao(sdp_atendido) + "|" + impressao(sdp_tecnico)).encode()).digest()
    return hashlib.scrypt(limpar_senha(senha).encode(), salt=sal, n=2 ** 15, r=8, p=1, maxmem=2 ** 26, dklen=32)


def prova(k, papel):
    if papel not in PAPEIS:
        raise ValueError(papel)
    return hmac.new(k, b"prova|" + papel.encode(), hashlib.sha256).hexdigest()


def confere(k, papel, mac):
    return isinstance(mac, str) and hmac.compare_digest(prova(k, papel), mac.lower())


def conferencia(sdp_atendido, sdp_tecnico):
    """Código de 6 dígitos mostrado nos dois lados antes do aceite."""
    h = hashlib.sha256(("conferencia|" + impressao(sdp_atendido) + "|" + impressao(sdp_tecnico)).encode()).digest()
    n = int.from_bytes(h[:8], "big") % 1000000
    return f"{n // 1000:03d} {n % 1000:03d}"


# ---------------------------------------------------------------- sessão (lado de quem é atendido)
class Sessao:
    """Regras de uma sessão, do convite ao encerramento. Estados:
    convite -> conectando -> aguardando_aceite -> ativa -> encerrada
    Uma sessão não volta atrás: passo fora de ordem ou repetido é recusado, passo atrasado encerra."""

    def __init__(self, agora=time.time):
        self._agora = agora
        self.senha = nova_senha()
        self.estado = "convite"
        self.motivo = ""
        self.conferencia = ""
        self._desde = agora()

    def _passar(self, estado):
        self.estado = estado
        self._desde = self._agora()

    def encerrar(self, motivo=""):
        if self.estado != "encerrada":
            self._passar("encerrada")
            self.motivo = motivo
            self.senha = ""

    def conferir_prazo(self):
        """Encerra se o prazo do estado atual venceu. Devolve o estado."""
        limite = {"convite": (VALIDADE_CONVITE, "O convite venceu. Gere outro."),
                  "conectando": (ESPERA_CONEXAO, "A conexão não se completou. Em algumas redes (4G nos dois lados) "
                                                 "a ligação direta não é possível."),
                  "aguardando_aceite": (ESPERA_ACEITE, "Ninguém permitiu o acesso a tempo."),
                  "ativa": (DURACAO_MAXIMA, "A sessão chegou ao tempo máximo.")}.get(self.estado)
        if limite and self._agora() - self._desde > limite[0]:
            self.encerrar(limite[1])
        return self.estado

    def _exigir(self, estado):
        if self.conferir_prazo() != estado:
            raise Erro(self.motivo or "Esse convite já foi usado. Gere outro.")

    def receber_resposta(self, codigo):
        """O código de resposta do técnico. Vale uma vez."""
        self._exigir("convite")
        sdp = ler_resposta(codigo)      # código mal colado não queima o convite: dá para colar de novo
        self._passar("conectando")
        return sdp

    def conferir_prova(self, sdp_atendido, sdp_tecnico, mac):
        """A prova de que o técnico sabe a senha. Uma tentativa só. Devolve a prova deste lado."""
        self._exigir("conectando")
        try:
            k = chave(self.senha, sdp_atendido, sdp_tecnico)
            certo = confere(k, "tecnico", mac)
        except Erro:
            certo = False
        if not certo:
            self.encerrar("A senha não conferiu. Por segurança o convite foi cancelado: gere outro.")
            raise Erro(self.motivo)
        self.conferencia = conferencia(sdp_atendido, sdp_tecnico)
        self._passar("aguardando_aceite")
        return prova(k, "atendido")

    def aceitar(self):
        self._exigir("aguardando_aceite")
        self._passar("ativa")

    @property
    def ativa(self):
        """Só com isto verdadeiro sai vídeo e entra mouse/teclado."""
        return self.conferir_prazo() == "ativa"


# ---------------------------------------------------------------- lado do técnico
def resposta_do_tecnico(senha, sdp_convite, sdp_tecnico):
    """O que o técnico devolve: o código de resposta e a prova de que sabe a senha."""
    if len(limpar_senha(senha)) != TAMANHO_SENHA:
        raise Erro(f"A senha tem {TAMANHO_SENHA} letras e números. Confira com quem pediu ajuda.")
    k = chave(senha, sdp_convite, sdp_tecnico)
    return {"codigo": resposta(sdp_tecnico), "prova": prova(k, "tecnico"),
            "conferencia": conferencia(sdp_convite, sdp_tecnico)}


def conferir_atendido(senha, sdp_convite, sdp_tecnico, mac):
    """O outro lado também tem de provar a senha: é isso que garante ao técnico que não há ninguém no meio."""
    return confere(chave(senha, sdp_convite, sdp_tecnico), "atendido", mac)


# ---------------------------------------------------------------- lado do serviço
class Gerente:
    """Liga o agente (remoto_agente) só enquanto dura um pedido de ajuda e repassa as ordens a ele."""

    def __init__(self, comando=None):
        self._comando = comando
        self._cond = threading.Condition()
        self._proc = None
        self._estado = {"estado": "parada"}
        self._respostas = {}      # número da ordem -> erro ("" = deu certo)
        self._seq = 0

    def _linha_de_comando(self, stun):
        if self._comando:
            cmd = list(self._comando)
        elif getattr(sys, "frozen", False):
            cmd = [os.path.join(config.pasta_programa(), "remoto", "MonitorPCRemoto.exe")]
        else:
            cmd = [sys.executable, os.path.join(config.pasta_recursos(), "remoto_agente.py")]
        return cmd if stun else cmd + ["--sem-stun"]

    def _viva(self):
        return self._proc is not None and self._proc.poll() is None and self._estado.get("estado") != "encerrada"

    def _abrir(self, cmd):
        if self._comando is None and sessao.em_servico():
            return sessao.abrir_como_sistema(cmd)     # o serviço não tem tela: o agente vai para a de quem usa o PC
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", bufsize=1, creationflags=0x08000000 if os.name == "nt" else 0)

    def _matar(self):
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _ler(self, p):
        for linha in p.stdout:
            try:
                d = json.loads(linha)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            with self._cond:
                if p is not self._proc:
                    return
                if "estado" in d:
                    self._estado = {k: d.get(k, "") for k in ("estado", "motivo", "conferencia", "convite", "senha")}
                if d.get("id") is not None:
                    self._respostas[d["id"]] = str(d.get("erro") or "")
                self._cond.notify_all()
        with self._cond:
            if p is self._proc and self._estado.get("estado") != "encerrada":
                self._estado = {"estado": "encerrada", "motivo": "O acesso remoto parou de funcionar."}
                self._cond.notify_all()

    def pedir(self, stun=True):
        """Abre um pedido de ajuda (ou mantém o que já está aberto)."""
        with self._cond:
            if self._viva():
                return True, ""
            self._matar()
            try:
                p = self._abrir(self._linha_de_comando(stun))
            except OSError as e:
                return False, f"Não consegui iniciar o acesso remoto: {e}"
            self._proc, self._estado, self._respostas = p, {"estado": "iniciando"}, {}
            threading.Thread(target=self._ler, args=(p,), daemon=True, name="remoto-agente").start()
            self._cond.wait_for(lambda: self._estado.get("estado") != "iniciando", 30)
            if self._estado.get("estado") != "convite":
                self._matar()
                self._estado = {"estado": "encerrada", "motivo": "Não consegui preparar o convite."}
                return False, self._estado["motivo"]
        return True, ""

    def ordem(self, cmd, **dados):
        """Manda uma ordem ao agente e espera ele responder. Devolve (ok, mensagem)."""
        with self._cond:
            if not self._viva():
                return False, "Não há pedido de ajuda aberto."
            self._seq += 1
            n = self._seq          # cada ordem espera a própria resposta, não a de outra
            try:
                self._proc.stdin.write(json.dumps({"cmd": cmd, "id": n, **dados}) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                return False, "O acesso remoto parou de funcionar."
            p = self._proc
            self._cond.wait_for(lambda: n in self._respostas or p.poll() is not None, 15)
            erro = self._respostas.pop(n, None)
            if erro is None:
                return False, "O acesso remoto não respondeu."
            return (False, erro) if erro else (True, "")

    def estado(self):
        with self._cond:
            return dict(self._estado)

    def parar(self):
        self.ordem("encerrar")
        with self._cond:
            self._matar()
