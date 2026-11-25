"""Licença Premium para empresas: entra com a conta CNT da empresa, guarda a permissão assinada e diz quais funções
Premium estão liberadas. Tudo o que o programa já oferece de graça continua sem licença e sem conta.

Dois jeitos de obter a permissão:
  - CONECTAR À EMPRESA (o jeito comercial): o administrador cadastra o ID deste computador no painel da empresa
    (cntoficial.com.br/<empresa>) e aqui a pessoa digita só esse endereço. O hub devolve uma chave do computador (guardada
    neste PC, sem senha de ninguém) e, com ela, a permissão assinada abaixo. Também libera o envio do uso do computador.
  - entrar com e-mail e senha da conta CNT (rotas /v1/licenca/entrar): continua existindo, sem tela.

Como funciona a permissão (o servidor é o hub em cntoficial.com.br, rotas /v1/licenca):
  - entrar: e-mail e senha da conta CNT + identificação deste computador -> o hub devolve uma PERMISSÃO assinada (Ed25519)
    com validade de até 7 dias. A senha nunca é guardada.
  - a permissão é conferida AQUI com a chave pública embutida: ninguém consegue inventar ou editar uma, e a de um
    computador não vale em outro (leva a identificação do computador);
  - renovar: o programa troca a permissão por outra sozinho antes de vencer; sem internet ela vale até o vencimento;
  - revogada ou vencida a licença no hub, a próxima renovação recusa e as funções Premium se fecham.
Uso nas funções Premium: `if licenca.tem("chamadas-historico"): ...` ou `licenca.exigir("chamadas-historico")`.
"""
import base64
import binascii
import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import config, maquina

HUB = "https://cntoficial.com.br/v1/licenca"
HUB_EMPRESA = "https://cntoficial.com.br/v1/empresa"
# Chave pública Ed25519 (base64, 32 bytes) que confere as permissões. A privada fica só no hub (.secrets na VPS).
# Se o hub trocar a chave, esta linha muda junto numa versão nova; a chave atual vem de GET /v1/licenca/chave.
PUBLICA = "WiMOL168VWVp75bH2WAd8pRzdV4av3Z9mDxUQf0AT/I="
ARQUIVO = os.path.join(config.BASE, "licenca.json")
ARQ_MAQUINA = os.path.join(config.BASE, "licenca_maquina.id")
VERIFICAR_CADA = 3600              # o motor chama renovar_se_preciso() de hora em hora
RENOVAR_ANTES = 3 * 24 * 3600      # renova quando faltam menos de 3 dias (a permissão dura até 7)
TOLERANCIA_RELOGIO = 300           # segundos de diferença aceitos entre o relógio daqui e o do hub

# o programa só reconhece estas funções; o texto é o que a tela mostra
FUNCOES = {
    "chamadas-historico": "Chamadas: histórico salvo na nuvem",
    "chamadas-relatorios": "Chamadas: relatórios e exportação avançados",
    "chamadas-multi-pc": "Chamadas: monitorar vários computadores",
}

_trava = threading.Lock()


class LicencaErro(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


class PremiumNecessario(Exception):
    """A função pedida é Premium e este computador não tem licença válida para ela."""


# ------------------------------------------------------------ identificação deste computador

def _maquina():
    """Texto estável deste computador. Sem SMBIOS, um identificador sorteado e guardado."""
    try:
        i = maquina.identidade()
    except Exception:  # noqa: BLE001
        i = ""
    if i:
        return i
    try:
        with open(ARQ_MAQUINA, encoding="utf-8") as f:
            v = f.read().strip()
        if len(v) >= 8:
            return v
    except OSError:
        pass
    v = "sorteado:" + uuid.uuid4().hex
    try:
        os.makedirs(config.BASE, exist_ok=True)
        with open(ARQ_MAQUINA, "w", encoding="utf-8") as f:
            f.write(v)
    except OSError:
        pass
    return v


def _idx_maquina():
    return hashlib.sha256(("maq:" + _maquina()).encode("utf-8")).hexdigest()


# ------------------------------------------------------------ permissão assinada

def ler_permissao(token, publica=None, agora=None):
    """Conteúdo da permissão se a assinatura confere, é deste computador e está no prazo; senão None."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        corpo, sig = str(token or "").split(".")
        chave = Ed25519PublicKey.from_public_bytes(base64.b64decode(publica or PUBLICA))
        chave.verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), corpo.encode("ascii"))
        p = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))
    except (InvalidSignature, ValueError, binascii.Error, TypeError, KeyError):
        return None
    agora = time.time() if agora is None else agora
    try:
        if p.get("v") != 1 or p.get("maq") != _idx_maquina():
            return None
        if float(p["nbf"]) > agora + TOLERANCIA_RELOGIO or float(p["exp"]) <= agora:
            return None
        p["funcoes"] = [f for f in p.get("funcoes", []) if f in FUNCOES]
    except (TypeError, ValueError, KeyError):
        return None
    return p


# ------------------------------------------------------------ arquivo local

def _ler_arquivo():
    try:
        with open(ARQUIVO, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _gravar(dados):
    os.makedirs(os.path.dirname(ARQUIVO), exist_ok=True)
    tmp = ARQUIVO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, ARQUIVO)


def _payload_atual():
    return ler_permissao(_ler_arquivo().get("permissao"))


def tem(funcao):
    """True se este computador tem licença válida que libera a função."""
    p = _payload_atual()
    return bool(p) and funcao in p["funcoes"]


def token():
    """A permissão atual (para falar com o hub em nome da licença), ou "" se não houver uma válida."""
    d = _ler_arquivo()
    return d["permissao"] if d.get("permissao") and ler_permissao(d["permissao"]) else ""


def exigir(funcao):
    if not tem(funcao):
        raise PremiumNecessario(FUNCOES.get(funcao, funcao))


def estado():
    """Resumo para a tela e para o /api/licenca. Sem a permissão em si (ela não precisa sair do computador)."""
    d = _ler_arquivo()
    p = ler_permissao(d.get("permissao"))
    r = {"entrou": bool(d.get("permissao")), "valida": bool(p), "email": d.get("email", ""), "empresa": d.get("empresa", ""),
         "funcoes": [{"id": f, "nome": FUNCOES[f]} for f in (p["funcoes"] if p else [])],
         "expira_em": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(float(p["exp"]))) + "Z" if p else None,
         "mensagem": d.get("mensagem", ""), "conectado": bool(d.get("chave")), "empresa_slug": d.get("slug", ""), "computador": d.get("computador", "")}
    if d.get("permissao") and not p and not r["mensagem"]:
        r["mensagem"] = "A permissão venceu ou não vale neste computador. Entre de novo."
    return r


# ------------------------------------------------------------ conversa com o hub

def _pedir(rota, corpo):
    req = urllib.request.Request(f"{HUB}/{rota}", data=json.dumps(corpo).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "CNTMonitor/" + config.VERSAO})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read(1 << 16))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(1 << 14)).get("erro") or ""
        except Exception:  # noqa: BLE001
            msg = ""
        raise LicencaErro(msg or f"O servidor da CNT recusou o pedido ({e.code}).", e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LicencaErro(f"Sem conexão com o servidor da CNT: {getattr(e, 'reason', e)}", 0)


def _guardar(resp, email=None):
    p = ler_permissao(resp.get("permissao"))
    if not p:
        raise LicencaErro("O servidor devolveu uma permissão que não confere. Nada foi guardado.")
    antigo = _ler_arquivo()
    novo = {"permissao": resp["permissao"], "email": email or antigo.get("email", ""), "empresa": str(resp.get("empresa") or antigo.get("empresa", ""))[:160],
            "renovada_em": int(time.time()), "mensagem": ""}
    for k in ("chave", "slug", "computador"):
        if antigo.get(k):
            novo[k] = antigo[k]
    _gravar(novo)
    return p


def entrar(email, senha):
    """(ok, mensagem). A senha só vai ao hub, por HTTPS, e não é guardada."""
    email, senha = str(email or "").strip().lower(), str(senha or "")
    if "@" not in email or not senha:
        return False, "Digite o e-mail e a senha da conta CNT da sua empresa."
    try:
        with _trava:
            resp = _pedir("entrar", {"email": email, "senha": senha, "maquina": _maquina(), "nome": socket.gethostname()[:120]})
            _guardar(resp, email)
        return True, "Licença ativada neste computador."
    except LicencaErro as e:
        return False, str(e)


def renovar():
    """(ok, mensagem). Recusa definitiva do hub (licença revogada, vencida ou computador liberado) fecha as funções."""
    with _trava:
        d = _ler_arquivo()
        if d.get("chave"):
            return _renovar_com_chave(d)
        if not d.get("permissao"):
            return False, "Este computador não está com licença."
        try:
            _guardar(_pedir("renovar", {"permissao": d["permissao"], "maquina": _maquina()}))
            return True, "Licença renovada."
        except LicencaErro as e:
            if e.status in (401, 403):
                d["permissao"], d["mensagem"] = "", str(e)
                _gravar(d)
            return False, str(e)


def renovar_se_preciso():
    """Chamado de hora em hora pelo motor: renova se faltam menos de 3 dias ou se a permissão já não vale."""
    try:
        d = _ler_arquivo()
        if d.get("chave") and not ler_permissao(d.get("permissao")):     # conectado à empresa: tenta sempre que não houver permissão válida
            renovar()
            return
        if not d.get("permissao"):
            return
        p = ler_permissao(d["permissao"])
        if not p or float(p["exp"]) - time.time() < RENOVAR_ANTES:
            renovar()
    except Exception:  # noqa: BLE001  (sem internet ou arquivo ruim: tenta de novo na próxima hora)
        pass


def pedir_com_chave(base, rota, corpo, chave, timeout=20):
    """POST ao hub autenticado pela chave do computador (usado também pelo envio do uso do computador)."""
    req = urllib.request.Request(f"{base}/{rota}", data=json.dumps(corpo).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + chave, "User-Agent": "CNTMonitor/" + config.VERSAO})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(1 << 16))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(1 << 14)).get("erro") or ""
        except Exception:  # noqa: BLE001
            msg = ""
        raise LicencaErro(msg or f"O servidor da CNT recusou o pedido ({e.code}).", e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LicencaErro(f"Sem conexão com o servidor da CNT: {getattr(e, 'reason', e)}", 0)


def chave_computador():
    return _ler_arquivo().get("chave", "")


def conectado():
    return bool(chave_computador())


def chave_invalida(motivo):
    """O hub não reconhece mais a chave (conexão reiniciada ou computador removido): precisa conectar de novo."""
    with _trava:
        d = _ler_arquivo()
        d.pop("chave", None)
        d["permissao"], d["mensagem"] = "", motivo or "Conecte este computador à empresa de novo."
        _gravar(d)


def _renovar_com_chave(d):
    try:
        _guardar(pedir_com_chave(HUB_EMPRESA, "renovar", {}, d["chave"]))
        return True, "Licença renovada."
    except LicencaErro as e:
        if e.status == 401:                       # chave não vale mais
            d.pop("chave", None)
            d["permissao"], d["mensagem"] = "", str(e)
            _gravar(d)
        elif e.status == 403:                     # suspenso ou empresa sem assinatura: mantém a conexão e fecha as funções
            d["permissao"], d["mensagem"] = "", str(e)
            _gravar(d)
        return False, str(e)


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


def slug_do_endereco(texto):
    """cntoficial.com.br/acme, https://cntoficial.com.br/acme/ ou só acme -> acme (ou "" se não parecer um endereço da empresa)."""
    t = str(texto or "").strip().lower()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^(www\.)?cntoficial\.com\.br/?", "", t).strip("/ ")
    t = t.split("/")[0].split("?")[0].split("#")[0]
    return t if _SLUG.match(t) and "--" not in t else ""


def parear(endereco, id_remoto, ficha=None):
    """Conecta este computador à empresa: o administrador já cadastrou o ID no painel. (ok, mensagem)."""
    slug = slug_do_endereco(endereco)
    id_remoto = re.sub(r"\D", "", str(id_remoto or ""))
    if not slug:
        return False, "Digite o endereço da empresa, como cntoficial.com.br/nomedaempresa."
    if len(id_remoto) != 9:
        return False, "Este computador ainda não tem o ID do acesso remoto. Espere o programa ficar online e tente de novo."
    try:
        with _trava:
            r = _pedir_empresa("parear", {"slug": slug, "id_remoto": id_remoto, "maquina": _maquina(), "nome": socket.gethostname()[:120],
                                          "versao": config.VERSAO, "info": ficha or {}})
            antigo = _ler_arquivo()
            antigo.update({"chave": r["chave"], "slug": slug, "empresa": str(r.get("empresa") or "")[:160], "computador": str(r.get("computador") or "")[:120],
                           "permissao": "", "mensagem": ""})
            _gravar(antigo)
        ok, msg = renovar()
        if not ok:      # conectou, mas a empresa ainda não tem assinatura ativa (ou foi suspenso): o motivo aparece na tela
            return True, f"Conectado à {r.get('empresa')}, mas as funções Premium ainda não foram liberadas: {msg}"
        return True, f"Computador conectado à {r.get('empresa')}."
    except LicencaErro as e:
        return False, str(e)


def _pedir_empresa(rota, corpo):
    req = urllib.request.Request(f"{HUB_EMPRESA}/{rota}", data=json.dumps(corpo).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "CNTMonitor/" + config.VERSAO})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read(1 << 16))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(1 << 14)).get("erro") or ""
        except Exception:  # noqa: BLE001
            msg = ""
        raise LicencaErro(msg or f"O servidor da CNT recusou o pedido ({e.code}).", e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LicencaErro(f"Sem conexão com o servidor da CNT: {getattr(e, 'reason', e)}", 0)


def sair():
    with _trava:
        try:
            os.remove(ARQUIVO)
        except FileNotFoundError:
            pass
    return True, "Licença removida deste computador."
