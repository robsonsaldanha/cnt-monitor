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
Nada disso é gravado em disco. Da sessão, só o histórico de arquivos transferidos fica guardado (90 dias).
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
TAMANHO_SENHA = 8                               # 8 de 32 símbolos = 40 bits; testar exige uma sessão por tentativa
                                                # (e o ponto de encontro limita as tentativas por ID)
VALIDADE_CONVITE = 600                          # segundos para colar a resposta
ESPERA_CONEXAO = 60                             # da resposta até a prova da senha
ESPERA_ACEITE = 120
DURACAO_MAXIMA = 4 * 3600

_MAX_CODIGO = 40000
_MAX_SDP = 60000
_RE_IMPRESSAO = re.compile(r"^a=fingerprint:sha-256 ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){31})\s*$", re.M)
_RE_OUTRA_IMPRESSAO = re.compile(r"^a=fingerprint:(?!sha-256 ).*\r?\n", re.M)
_RE_CAND_MDNS = re.compile(r"^(a=candidate:\S+ \d+ udp \d+ )[0-9A-Za-z-]+\.local( \d+ typ host[^\r\n]*)\r?\n", re.M | re.I)
_MAX_ENDERECOS = 6
PAPEIS = ("tecnico", "atendido")
# Os códigos só têm letras maiúsculas e números (base32). Com "_", "-" ou "*" no meio, WhatsApp, Telegram e
# clientes de e-mail tratam trechos como itálico/negrito e somem com os símbolos: o código chegava estragado.
PREFIXO_CONVITE, PREFIXO_RESPOSTA = "MPCC", "MPCR"
# Texto que quase todo SDP repete: serve de dicionário para a compactação e deixa o código mais curto.
# Mudar isto muda o formato: os dois lados precisam da mesma versão.
_DICIONARIO = (
    "a=rtcp-fb:101 nack pli\r\na=rtcp-fb:101 goog-remb\r\na=rtpmap:102 rtx/90000\r\na=fmtp:102 apt=101\r\n"
    "a=fmtp:99 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f\r\n"
    "a=fmtp:99 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f\r\n"
    "a=rtpmap:99 H264/90000\r\na=rtpmap:98 rtx/90000\r\na=fmtp:98 apt=97\r\na=rtpmap:97 VP8/90000\r\n"
    "a=rtcp-fb:97 nack\r\na=rtcp-fb:97 nack pli\r\na=rtcp-fb:97 goog-remb\r\n"
    "a=extmap:3 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time\r\n"
    "a=extmap:1 urn:ietf:params:rtp-hdrext:sdes:mid\r\na=msid-semantic:WMS *\r\na=msid-semantic: WMS\r\n"
    "a=ssrc-group:FID a=ssrc: cname: a=msid:\r\na=rtcp:9 IN IP4 0.0.0.0\r\na=rtcp-mux\r\na=rtcp-rsize\r\n"
    "a=sendrecv\r\na=recvonly\r\na=sendonly\r\na=ice-options:trickle\r\na=end-of-candidates\r\n"
    " typ srflx raddr 0.0.0.0 rport  typ srflx raddr 192.168. rport .local 1 udp 1694498815  generation 0 network-cost 999\r\n"
    " 1 udp 2130706431 192.168. typ host generation 0 network-id \r\na=candidate:"
    "a=ice-ufrag:\r\na=ice-pwd:\r\na=setup:actpass\r\na=setup:active\r\na=mid:1\r\na=sctp-port:5000\r\n"
    "a=max-message-size:262144\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 97 98 99 100 101 102\r\nc=IN IP4 192.168.\r\na=mid:0\r\n"
    "v=0\r\no=- IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0 1\r\na=fingerprint:sha-256 "
    '{"v":2,"sdp":"v=0\\r\\no=- ').encode("ascii")


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
    # a prova da senha e o DTLS usam a impressão SHA-256; as outras só deixariam o código mais comprido
    sdp = _RE_OUTRA_IMPRESSAO.sub("", sdp)
    c = zlib.compressobj(9, zdict=_DICIONARIO)
    bruto = c.compress(json.dumps({"v": 2, "sdp": sdp}, separators=(",", ":")).encode("utf-8")) + c.flush()
    return prefixo + base64.b32encode(bruto).decode("ascii").rstrip("=")


def _ler(prefixo, codigo, nome):
    # só letras e números contam: espaços, quebras de linha e símbolos postos pelo aplicativo de mensagem saem
    txt = re.sub(r"[^A-Za-z0-9]", "", str(codigo or "")[:4 * _MAX_CODIGO]).upper()
    outro, nome_outro = ((PREFIXO_RESPOSTA, "código de resposta") if prefixo == PREFIXO_CONVITE
                         else (PREFIXO_CONVITE, "convite"))
    if not txt:
        raise Erro(f"Cole o {nome}.")
    if re.fullmatch(r"\d{6}", txt):
        raise Erro(f"Esse é o código de conferência (6 números), que só serve para comparar os dois lados. "
                   f"O {nome} é o texto longo que começa com {prefixo}: use o botão Copiar.")
    if txt.startswith(("MPC1", "MPC2")):
        raise Erro(f"Esse {nome} foi gerado por uma versão antiga do CNT Monitor. "
                   "Atualize os dois computadores e gere outro.")
    if txt.startswith(outro):
        raise Erro(f"Esse texto é um {nome_outro}, não um {nome}.")
    if not txt.startswith(prefixo) or len(txt) > _MAX_CODIGO:
        raise Erro(f"Isso não é um {nome}. Ele começa com {prefixo}: copie pelo botão Copiar e cole inteiro.")
    cortado = Erro(f"O {nome} chegou cortado (falta o final). Copie pelo botão Copiar e cole inteiro.")
    alterado = Erro(f"O {nome} chegou alterado no caminho. Copie pelo botão Copiar e mande de novo "
                    "(de preferência num arquivo de texto ou por e-mail).")
    corpo = txt[len(prefixo):]
    if len(corpo) % 8 in (1, 3, 6):                 # tamanho que o base32 nunca produz: sobrou um pedaço de letra
        corpo = corpo[:-1]
    try:
        bruto = base64.b32decode(corpo + "=" * (-len(corpo) % 8))
        d = zlib.decompressobj(zdict=_DICIONARIO)
        dados = d.decompress(bruto, _MAX_SDP + 200)   # limite: código pequeno não vira gigabytes
        inteiro = d.eof and not d.unconsumed_tail
        obj = json.loads(dados.decode("utf-8")) if inteiro else None
    except (ValueError, zlib.error):                # o zlib confere a soma do conteúdo: qualquer troca cai aqui
        raise alterado from None
    if not inteiro:
        raise alterado if d.unconsumed_tail else cortado
    if not isinstance(obj, dict) or obj.get("v") != 2 or not isinstance(obj.get("sdp"), str) or not obj["sdp"]:
        raise alterado
    impressao(obj["sdp"])
    return obj["sdp"]


def convite(sdp):
    return _codigo(PREFIXO_CONVITE, sdp)


def ler_convite(codigo):
    return _ler(PREFIXO_CONVITE, codigo, "convite")


def resposta(sdp):
    return _codigo(PREFIXO_RESPOSTA, sdp)


def ler_resposta(codigo):
    return _ler(PREFIXO_RESPOSTA, codigo, "código de resposta")


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
        self.senha_errada = False
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

    def definir_senha(self, senha):
        """Senha escolhida pelo serviço (a do ID, mostrada na tela) no lugar da sorteada aqui."""
        self._exigir("convite")
        senha = limpar_senha(senha)
        if len(senha) != TAMANHO_SENHA:
            raise Erro("Senha inválida.")
        self.senha = senha

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
            self.senha_errada = True
            self.encerrar("A senha não conferiu. Por segurança a conexão foi cancelada.")
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
def enderecos_locais():
    """IPv4 das placas de rede ligadas deste PC (rede local, VPN), sem 127.x nem 169.254.x."""
    import ipaddress
    import psutil
    out = []
    try:
        ligadas = psutil.net_if_stats()
        for nome, enderecos in psutil.net_if_addrs().items():
            if nome in ligadas and not ligadas[nome].isup:
                continue
            for e in enderecos:
                if e.family == 2:
                    ip = ipaddress.ip_address(e.address)
                    if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified) and e.address not in out:
                        out.append(e.address)
    except Exception:  # noqa: BLE001  (sem a lista, o código sai como o navegador gerou)
        pass
    return out[:_MAX_ENDERECOS]


def com_enderecos(sdp, ips):
    """O navegador do técnico esconde o endereço local atrás de um nome "xxxx.local" (mDNS), e o outro
    computador quase nunca consegue descobrir que endereço é esse: a ligação pela rede local só fechava
    num sentido. Aqui cada nome é trocado pelos endereços reais deste PC (o navegador não diz qual nome
    é de qual placa, então vai um candidato por endereço; os que não existirem só não respondem)."""
    ips = [ip for ip in ips if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip)][:_MAX_ENDERECOS]
    if not ips:
        return sdp
    return _RE_CAND_MDNS.sub(lambda m: "".join(f"{m.group(1)}{ip}{m.group(2)}\r\n" for ip in ips), sdp)


def resposta_do_tecnico(senha, sdp_convite, sdp_tecnico, ips=()):
    """O que o técnico devolve: o código de resposta e a prova de que sabe a senha."""
    if len(limpar_senha(senha)) != TAMANHO_SENHA:
        raise Erro(f"A senha tem {TAMANHO_SENHA} letras e números. Confira com quem pediu ajuda.")
    k = chave(senha, sdp_convite, sdp_tecnico)
    return {"codigo": resposta(com_enderecos(sdp_tecnico, ips)), "prova": prova(k, "tecnico"),
            "conferencia": conferencia(sdp_convite, sdp_tecnico)}


def conferir_atendido(senha, sdp_convite, sdp_tecnico, mac):
    """O outro lado também tem de provar a senha: é isso que garante ao técnico que não há ninguém no meio."""
    return confere(chave(senha, sdp_convite, sdp_tecnico), "atendido", mac)


# ---------------------------------------------------------------- diagnóstico da conexão
_RE_CANDIDATO = re.compile(r"^a=candidate:\S+ \d+ (\w+) \d+ (\S+) (\d+) typ (\w+)", re.M | re.I)


def resumo_candidatos(sdp):
    """Quantos endereços de cada tipo o SDP oferece, sem mostrar os endereços ("host/udp 2, relay/udp 1").
    O mesmo endereço repetido em cada parte do SDP (vídeo, controle) conta uma vez."""
    vistos = {(p.lower(), t.lower(), ip, porta) for p, ip, porta, t in _RE_CANDIDATO.findall(sdp or "")}
    contas = {}
    for p, t, _ip, _porta in vistos:
        contas[f"{t}/{p}"] = contas.get(f"{t}/{p}", 0) + 1
    return ", ".join(f"{k} {n}" for k, n in sorted(contas.items())) or "nenhum endereço"


def rota(tipo_local, tipo_remoto):
    """Pelo par de endereços que o ICE escolheu: se um dos lados é o retransmissor, nada vai direto."""
    return "retransmitida" if "relay" in (tipo_local, tipo_remoto) else "direta"


def conclusao_teste(r):
    """Uma frase para quem testou a conexão. r: {servidor, stun, turn_udp, turn_tcp} (None = não testado)."""
    if not r.get("servidor"):
        return ("Sem contato com o servidor do acesso remoto. Confira a internet e se o firewall libera "
                "HTTPS para monitor.cntoficial.com.br.")
    udp, tcp = r.get("turn_udp"), r.get("turn_tcp")
    if udp is None and tcp is None:
        return "O servidor não mandou a credencial do retransmissor: só a conexão direta vai funcionar."
    if udp and tcp:
        return "Tudo certo: este computador alcança o retransmissor por UDP e por TCP."
    if tcp:
        return ("Esta rede bloqueia UDP. O acesso remoto vai pelo retransmissor por TCP: funciona, "
                "com um pouco mais de atraso.")
    if udp:
        return ("O retransmissor por TCP não respondeu (porta 3478). Funciona aqui, mas pode falhar se "
                "o UDP for bloqueado.")
    return ("Este computador não alcança o retransmissor (porta 3478 bloqueada em UDP e TCP). Só a "
            "conexão direta é possível, e ela costuma falhar em 4G/5G e em redes de empresa.")


# ---------------------------------------------------------------- atalhos, área de transferência e arquivos
MAX_MONITORES = 4           # uma trilha de vídeo por monitor
# Combinações que o técnico manda pela barra (fora da tela cheia o navegador fica com Win, Alt+Tab e afins).
# Lista fechada: o agente não aceita outro nome. Teclas pelo KeyboardEvent.code; "SAS" (Ctrl+Alt+Del) e
# "BLOQUEAR" (Win+L) o Windows não aceita simulados: o agente chama a função própria de cada um.
ATALHOS = {
    "ctrl_alt_del": "SAS",
    "ctrl_shift_esc": ("ControlLeft", "ShiftLeft", "Escape"),
    "win": ("MetaLeft",),
    "win_r": ("MetaLeft", "KeyR"),
    "win_e": ("MetaLeft", "KeyE"),
    "win_d": ("MetaLeft", "KeyD"),
    "win_l": "BLOQUEAR",
    "win_shift_s": ("MetaLeft", "ShiftLeft", "KeyS"),
    "alt_tab": ("AltLeft", "Tab"),
    "alt_f4": ("AltLeft", "F4"),
    "print": ("PrintScreen",),
    "colar": ("ControlLeft", "KeyV"),
}
# para_atendido = do técnico para o PC atendido; para_tecnico = o contrário
MODOS_AREA = ("ambos", "para_atendido", "para_tecnico", "desligada")
MAX_TEXTO_AREA = 100000
MAX_IMAGEM_AREA = 16 * 1024 * 1024
MAX_ARQUIVO = 8 * 1024 ** 3
TAM_PEDACO = 16384          # cabe em qualquer canal de dados (o limite do aiortc é 64 KB por mensagem)
PASTA_RECEBIDOS = "CNT Monitor"     # dentro de Downloads de quem é atendido
HISTORICO_DIAS = 90
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def area_permite(modo, sentido, ler_atendido=True):
    """Se a área de transferência pode ir no sentido dado. ler_atendido é o teto de quem é atendido:
    desligado, nada sai da área de transferência dele, seja qual for o modo que o técnico escolheu."""
    if sentido not in ("para_atendido", "para_tecnico") or modo not in MODOS_AREA:
        return False
    if sentido == "para_tecnico" and not ler_atendido:
        return False
    return modo in ("ambos", sentido)


def nome_seguro(nome):
    """Só o nome do arquivo (sem pastas), sem caracteres que o Windows recusa e sem nomes reservados."""
    nome = re.split(r"[\\/]", str(nome or ""))[-1]
    nome = "".join(ch for ch in nome if ord(ch) >= 32 and ch not in '<>:"|?*').strip(" .")
    base, ext = os.path.splitext(nome)
    if base.upper() in _RESERVADOS:
        base = "_" + base
    nome = (base[:150] + ext[:20]).strip(" .")
    return nome or "arquivo"


def caminho_livre(pasta, nome, existe=os.path.exists):
    """pasta\\nome, ou "nome (2).ext", "nome (3).ext"... se já existir: nunca sobrescreve."""
    base, ext = os.path.splitext(nome)
    c, n = os.path.join(pasta, nome), 1
    while existe(c) or existe(c + ".cntparte"):
        n += 1
        c = os.path.join(pasta, f"{base} ({n}){ext}")
    return c


def validar_caminho(p):
    """Caminho de arquivo ou pasta pedido pelo técnico: só caminho completo numa unidade (C:\\...).
    Recusa rede (\\\\servidor), dispositivos (\\\\?\\, \\\\.\\), fluxos alternativos (arquivo:fluxo) e
    caminhos relativos. Quem limita o que dá para ler é o Windows: o agente lê com as permissões de quem
    está no computador."""
    import ntpath
    if not isinstance(p, str) or not p or len(p) > 1000 or "\x00" in p:
        raise Erro("Caminho inválido.")
    if os.name != "nt":
        return _validar_caminho_linux(p)
    p = p.replace("/", "\\")
    if not re.match(r"^[A-Za-z]:\\", p) or ":" in p[2:] or any(ord(ch) < 32 for ch in p):
        raise Erro("Caminho inválido.")
    p = ntpath.normpath(p)
    if not re.match(r"^[A-Za-z]:\\", p):
        raise Erro("Caminho inválido.")
    return p[0].upper() + p[1:]


def _validar_caminho_linux(p):
    """Linux: só caminho absoluto (/home/...), sem caracteres de controle e fora de /proc, /sys e /dev
    (arquivos especiais que não terminam ou não são arquivos). As permissões são as de quem está no PC."""
    import posixpath
    if not p.startswith("/") or any(ord(ch) < 32 for ch in p):
        raise Erro("Caminho inválido.")
    p = posixpath.normpath(p)
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    if p.split("/")[1:2] in (["proc"], ["sys"], ["dev"]):
        raise Erro("Caminho inválido.")
    return p


def _arquivo_historico():
    return os.path.join(config.BASE, "arquivos_remotos.jsonl")


_lock_hist = threading.Lock()


def historico_gravar(item, agora=time.time):
    """Uma transferência concluída (mandada pelo agente). Guarda 90 dias."""
    if not isinstance(item, dict) or item.get("sentido") not in ("recebido", "enviado"):
        return
    try:
        tamanho = max(0, int(item.get("tamanho") or 0))
    except (TypeError, ValueError):
        return
    sha = str(item.get("sha256") or "")
    linha = {"ts": agora(), "sentido": item["sentido"], "nome": nome_seguro(item.get("nome")), "tamanho": tamanho,
             "sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "",
             "tecnico": re.sub(r"[^0-9 ]", "", str(item.get("tecnico") or ""))[:15],
             "caminho": str(item.get("caminho") or "")[:1000]}
    limite = agora() - HISTORICO_DIAS * 86400
    with _lock_hist:
        linhas = [x for x in _ler_historico() if x["ts"] >= limite] + [linha]
        os.makedirs(config.BASE, exist_ok=True)
        tmp = _arquivo_historico() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in linhas[-5000:])
        os.replace(tmp, _arquivo_historico())


def _ler_historico():
    out = []
    try:
        with open(_arquivo_historico(), encoding="utf-8") as f:
            for linha in f:
                try:
                    x = json.loads(linha)
                except ValueError:
                    continue
                if isinstance(x, dict) and isinstance(x.get("ts"), (int, float)):
                    out.append(x)
    except OSError:
        pass
    return out


def historico(limite=200, agora=time.time):
    """Transferências dos últimos 90 dias, a mais nova primeiro."""
    corte = agora() - HISTORICO_DIAS * 86400
    with _lock_hist:
        linhas = [x for x in _ler_historico() if x["ts"] >= corte]
    return sorted(linhas, key=lambda x: -x["ts"])[:limite]


# ---------------------------------------------------------------- lado do serviço
class Gerente:
    """Liga o agente (remoto_agente) só enquanto dura um pedido de ajuda e repassa as ordens a ele."""

    def __init__(self, comando=None, opcoes=None):
        self._comando = comando
        self._opcoes = opcoes or (lambda: {})     # {"ler_area": bool}: decisões de quem é atendido
        self._cond = threading.Condition()
        self._proc = None
        self._estado = {"estado": "parada"}
        self._respostas = {}      # número da ordem -> erro ("" = deu certo)
        self._seq = 0

    def _linha_de_comando(self, stun, turn=None):
        if self._comando:
            cmd = list(self._comando)
        elif getattr(sys, "frozen", False):
            cmd = [os.path.join(config.pasta_programa(), config.EXE_REMOTO)]
        else:
            cmd = [sys.executable, os.path.join(config.pasta_recursos(), "remoto_agente.py")]
        if not self._opcoes().get("ler_area", True):
            cmd = cmd + ["--sem-ler-area"]
        if not stun:
            return cmd + ["--sem-stun"]
        if isinstance(turn, dict) and turn.get("urls") and turn.get("username") and turn.get("credential"):
            urls = [u for u in turn["urls"] if isinstance(u, str) and u.startswith("turn:") and " " not in u]
            if urls:
                cmd += ["--turn", ",".join(urls), "--turn-usuario", str(turn["username"]), "--turn-senha", str(turn["credential"])]
        return cmd

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
            if isinstance(d.get("log"), str):      # negociação da conexão: vai para o servico.log
                print("remoto:", d["log"][:500], file=sys.stderr, flush=True)
                continue
            with self._cond:
                if p is not self._proc:
                    return
                if "estado" in d:
                    self._estado = {k: d.get(k, "") for k in ("estado", "motivo", "conferencia", "convite", "senha", "senha_errada",
                                                            "tecnico")}
                if d.get("id") is not None:
                    self._respostas[d["id"]] = str(d.get("erro") or "")
                if isinstance(d.get("arquivo"), dict):
                    try:
                        historico_gravar(d["arquivo"])
                    except OSError as e:
                        print("remoto: não consegui gravar o histórico de arquivos:", e, file=sys.stderr)
                self._cond.notify_all()
        with self._cond:
            if p is self._proc and self._estado.get("estado") != "encerrada":
                self._estado = {"estado": "encerrada", "motivo": "O acesso remoto parou de funcionar."}
                self._cond.notify_all()

    def pedir(self, stun=True, turn=None):
        """Abre um pedido de ajuda (ou mantém o que já está aberto). turn: credenciais do retransmissor de reserva."""
        with self._cond:
            if self._viva():
                return True, ""
            self._matar()
            try:
                p = self._abrir(self._linha_de_comando(stun, turn))
            except OSError as e:
                return False, f"Não consegui iniciar o acesso remoto: {e}"
            self._proc, self._estado, self._respostas = p, {"estado": "iniciando"}, {}
            threading.Thread(target=self._ler, args=(p,), daemon=True, name="remoto-agente").start()
            # Linux com Wayland: antes do convite a pessoa escolhe as telas no aviso do sistema (pode levar minutos)
            self._cond.wait_for(lambda: self._estado.get("estado") != "iniciando", 30 if os.name == "nt" else 300)
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

    def testar(self, turn=None, prazo=30):
        """Confere se este PC alcança o STUN e o retransmissor (UDP e TCP). Roda o agente com --testar,
        fora da sessão de quem usa o PC: não precisa de tela. Devolve {stun, turn_udp, turn_tcp}."""
        cmd = self._linha_de_comando(True, turn) + ["--testar"]
        try:
            p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                               timeout=prazo, creationflags=0x08000000 if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Erro(f"Não consegui testar a conexão: {e}") from None
        for linha in reversed((p.stdout or "").splitlines()):
            try:
                d = json.loads(linha)
            except ValueError:
                continue
            if isinstance(d, dict) and "stun" in d:
                return {k: d.get(k) for k in ("stun", "turn_udp", "turn_tcp")}
        raise Erro("O teste de conexão não respondeu.")

    def parar(self):
        self.ordem("encerrar")
        with self._cond:
            self._matar()
