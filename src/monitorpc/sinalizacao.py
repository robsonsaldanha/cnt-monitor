"""Fluxo de chamadas VoIP ao vivo e histórico (no estilo do sngrep): SIP, PJSIP, WebRTC e HEP, em qualquer porta.

A captura lê os pacotes de todas as placas (socket bruto no Windows, AF_PACKET no Linux; administrador/root) e
reconhece pelo CONTEÚDO, não pela porta:
- SIP em UDP ou TCP (PJSIP é SIP): a primeira linha é um método conhecido ou "SIP/2.0 <código>".
- SIP dentro de WebSocket sem TLS (ws://, o WebRTC do Asterisk/Kamailio na porta 8088 etc.): o quadro é desembrulhado.
- HEPv3 (Asterisk res_hep, Kamailio siptrace, FreeSWITCH, OpenSIPS): é a saída para WSS e SIP-TLS, que vão
  criptografados e não dá para ler pela rede. O servidor manda uma cópia da sinalização já decifrada para cá.
- RTP: as portas de mídia saem do SDP de cada chamada; o cabeçalho (12 bytes) dá jitter, perda, codec e MOS.
Limites: SIP em TCP é lido por pacote (mensagem partida em dois segmentos não é remontada) e o socket bruto do
Windows só enxerga IPv4 e não pega o tráfego de loopback."""
import os
import re
import json
import time
import struct
import socket
import sqlite3
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request

import psutil

from . import config, encontro, p2p, qualidade
from .rede_programa import ler_rtp, FluxoRtp

WIN = os.name == "nt"
METODOS = ("INVITE", "ACK", "BYE", "CANCEL", "REGISTER", "OPTIONS", "SUBSCRIBE", "NOTIFY", "REFER", "MESSAGE", "INFO",
           "PRACK", "UPDATE", "PUBLISH")
COMPACTOS = {"i": "call-id", "f": "from", "t": "to", "v": "via", "m": "contact", "c": "content-type", "l": "content-length"}
MAX_TEXTO = 6000                 # bytes guardados de cada mensagem
MAX_MSGS = 300                   # por chamada
MAX_EM_MEMORIA = 1500
GUARDAR_FINAL_S = 8              # espera as retransmissões antes de dar a chamada por encerrada
SEM_RESPOSTA_S = 120
SEM_SINAL_S = 600                # chamada em andamento sem sinalização nem RTP
FICAR_NA_TELA_S = 1800
DIAS = 7
ESQUEMA = """CREATE TABLE IF NOT EXISTS chamadas(id TEXT PRIMARY KEY, inicio REAL, fim REAL, tipo TEXT, de TEXT, para TEXT,
    origem TEXT, destino TEXT, estado TEXT, duracao REAL, nmsg INTEGER, dados TEXT, desligou TEXT DEFAULT '', motivo TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS i_chamadas ON chamadas(inicio);"""
FALHAS = {404: "Não encontrada", 480: "Indisponível", 486: "Ocupado", 487: "Cancelada", 488: "Mídia recusada",
          600: "Ocupado", 603: "Recusada"}


# ---------------------------------------------------------------- SIP
def parece_sip(d):
    ini = d[:12]
    if ini.startswith(b"SIP/2.0 "):
        return True
    p = ini.split(b" ", 1)
    return len(p) == 2 and p[0].decode("ascii", "ignore") in METODOS and b"SIP/2.0" in d[:300].split(b"\n", 1)[0]


def _usuario(v):
    m = re.search(r"sips?:([^@;>\s]+)@", v or "")
    if m:
        return m.group(1)
    m = re.search(r"sips?:([^;>\s]+)", v or "")
    return m.group(1) if m else (v or "").strip()[:60]


def _sdp(corpo):
    """[(tipo, ip, porta, codecs)] do SDP: o endereço e a porta onde QUEM ENVIOU a mensagem recebe a mídia."""
    saida, ip_sessao, atual = [], None, None
    nomes = {}
    for ln in corpo.replace("\r", "").split("\n"):
        if ln.startswith("c=") and ln.count(" ") >= 2:
            ip = ln.split()[-1].split("/")[0]
            if atual is None:
                ip_sessao = ip
            else:
                atual["ip"] = ip
        elif ln.startswith("m="):
            p = ln[2:].split()
            if len(p) >= 4 and p[1].isdigit():
                atual = {"tipo": p[0], "porta": int(p[1]), "ip": ip_sessao, "pts": p[3:]}
                saida.append(atual)
        elif ln.startswith("a=rtpmap:"):
            n, _, resto = ln[9:].partition(" ")
            nomes[n] = resto.split("/")[0]
    return [{"tipo": m["tipo"], "ip": m["ip"] or ip_sessao, "porta": m["porta"],
             "codecs": ", ".join(nomes.get(x) or CODEC_FIXO.get(x, x) for x in m["pts"][:6])}
            for m in saida if m["porta"]]


CODEC_FIXO = {"0": "PCMU", "3": "GSM", "8": "PCMA", "9": "G722", "18": "G729"}


def ler_sip(dados):
    """Mensagem SIP -> dict (início, call_id, de, para, cseq, agente, sdp) ou None se não for SIP."""
    if not parece_sip(dados):
        return None
    txt = dados.decode("utf-8", "replace")
    cab, sep, corpo = txt.partition("\r\n\r\n")
    if not sep:
        cab, _, corpo = txt.partition("\n\n")
    linhas = cab.replace("\r\n", "\n").split("\n")
    ini = linhas[0].strip()
    m = {"codigo": None, "metodo": None, "uri": None, "sdp": []}
    if ini.startswith("SIP/2.0 "):
        p = ini.split(" ", 2)
        if not p[1].isdigit():
            return None
        m["codigo"], m["motivo"] = int(p[1]), p[2] if len(p) > 2 else ""
    else:
        p = ini.split(" ")
        if len(p) != 3 or p[2] != "SIP/2.0":
            return None
        m["metodo"], m["uri"] = p[0], p[1]
    cab_h, ult = {}, None
    for ln in linhas[1:]:
        if ln[:1] in " \t" and ult:
            cab_h[ult] += " " + ln.strip()
            continue
        k, sep, v = ln.partition(":")
        if not sep:
            continue
        k = k.strip().lower()
        k = COMPACTOS.get(k, k)
        if k not in cab_h:
            cab_h[k] = v.strip()
        ult = k
    if not cab_h.get("call-id"):
        return None
    cs = cab_h.get("cseq", "").split()
    m.update(call_id=cab_h["call-id"], de=_usuario(cab_h.get("from")), para=_usuario(cab_h.get("to")),
             cseq=" ".join(cs), cseq_metodo=cs[1] if len(cs) > 1 else m["metodo"],
             agente=cab_h.get("user-agent") or cab_h.get("server") or "", reason=cab_h.get("reason", ""))
    if "sdp" in cab_h.get("content-type", "").lower() or corpo.lstrip().startswith("v=0"):
        m["sdp"] = _sdp(corpo)
    m["rotulo"] = m["metodo"] or f"{m['codigo']} {m['motivo']}".strip()
    return m


def desembrulhar_ws(d):
    """Carga de um quadro WebSocket (texto/binário) que está inteiro neste segmento TCP; senão None."""
    if len(d) < 2 or d[0] not in (0x81, 0x82):
        return None
    n, mascarado, i = d[1] & 0x7F, bool(d[1] & 0x80), 2
    if n == 126 and len(d) >= 4:
        n, i = struct.unpack_from(">H", d, 2)[0], 4
    elif n == 127 and len(d) >= 10:
        n, i = struct.unpack_from(">Q", d, 2)[0], 10
    elif n >= 126:
        return None
    chave = d[i:i + 4] if mascarado else b""
    i += 4 if mascarado else 0
    carga = d[i:i + n]
    if len(carga) < n:
        return None
    return bytes(b ^ chave[k % 4] for k, b in enumerate(carga)) if mascarado else carga


def ler_hep3(d):
    """HEPv3 com SIP -> (hora, protocolo, origem, porta, destino, porta, mensagem) ou None."""
    if d[:4] != b"HEP3" or len(d) < 6:
        return None
    try:
        total, i, f = struct.unpack_from(">H", d, 4)[0], 6, {}
        while i + 6 <= min(total, len(d)):
            _, tipo, tam = struct.unpack_from(">HHH", d, i)
            if tam < 6:
                break
            f[tipo] = d[i + 6:i + tam]
            i += tam
        if f.get(11) != b"\x01" or 15 not in f:
            return None
        if f.get(1, b"\x02")[0] == 10:
            org, dst = socket.inet_ntop(socket.AF_INET6, f[5]), socket.inet_ntop(socket.AF_INET6, f[6])
        else:
            org, dst = socket.inet_ntoa(f[3]), socket.inet_ntoa(f[4])
        hora = struct.unpack(">I", f[9])[0] + struct.unpack(">I", f.get(10, b"\0\0\0\0"))[0] / 1e6 if 9 in f else None
        proto = {17: "UDP", 6: "TCP", 132: "SCTP"}.get(f.get(2, b"\x11")[0], "UDP")
        return (hora, "HEP/" + proto, org, struct.unpack(">H", f[7])[0], dst, struct.unpack(">H", f[8])[0], f[15])
    except (KeyError, struct.error, ValueError, OSError):
        return None


def ler_ip(p):
    """Pacote IP bruto -> (UDP|TCP, origem, porta, destino, porta, dados) ou None."""
    if not p:
        return None
    v = p[0] >> 4
    try:
        if v == 4:
            if len(p) < 20 or p[9] not in (6, 17):
                return None
            ihl, total = (p[0] & 15) * 4, struct.unpack_from(">H", p, 2)[0]
            proto, org, dst = p[9], socket.inet_ntoa(p[12:16]), socket.inet_ntoa(p[16:20])
            if struct.unpack_from(">H", p, 6)[0] & 0x1FFF:
                return None                          # pedaço de datagrama fragmentado (o primeiro passa)
            seg = p[ihl:min(len(p), total) if total else len(p)]
        elif v == 6:
            if len(p) < 40 or p[6] not in (6, 17):
                return None
            proto = p[6]
            org, dst = socket.inet_ntop(socket.AF_INET6, p[8:24]), socket.inet_ntop(socket.AF_INET6, p[24:40])
            seg = p[40:40 + struct.unpack_from(">H", p, 4)[0]]
        else:
            return None
        if proto == 17:
            if len(seg) < 8:
                return None
            sp, dp = struct.unpack_from(">HH", seg)
            return "UDP", org, sp, dst, dp, seg[8:]
        if len(seg) < 20:
            return None
        sp, dp = struct.unpack_from(">HH", seg)
        return "TCP", org, sp, dst, dp, seg[(seg[12] >> 4) * 4:]
    except (struct.error, ValueError, OSError):
        return None


def _ponto(ip, porta):
    return f"[{ip}]:{porta}" if ":" in ip else f"{ip}:{porta}"


# ---------------------------------------------------------------- captura
class Captura:
    """Pacotes IP de todas as placas, com os dados inteiros (a captura do monitor de programas só guarda 12 bytes)."""

    def __init__(self, ao_pacote):
        self.ao_pacote = ao_pacote
        self.ok, self.erro, self._socks = False, None, []

    def iniciar(self):
        if self.ok:
            return True
        try:
            return self._windows() if WIN else self._linux()
        except OSError as e:
            self.erro = f"Não consegui ler os pacotes de rede (precisa de administrador/root): {e.strerror or e}"
            return False

    def _windows(self):
        ligadas = psutil.net_if_stats()
        locais = [e.address for nome, ends in psutil.net_if_addrs().items() if nome not in ligadas or ligadas[nome].isup
                  for e in ends if e.family == socket.AF_INET and not e.address.startswith(("127.", "169.254."))]
        for ip in locais:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                s.bind((ip, 0))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 2 ** 20)
                s.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
                s.settimeout(1.0)
            except OSError as e:
                self.erro = f"Não consegui ler os pacotes da placa {ip}: {e.strerror or e}"
                continue
            self._socks.append(s)
        return self._comecar("Nenhuma placa de rede IPv4 ligada.")

    def _linux(self):
        s = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(3))      # ETH_P_ALL
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 2 ** 20)
        s.settimeout(1.0)
        self._socks.append(s)
        return self._comecar("")

    def _comecar(self, vazio):
        self.ok = bool(self._socks)
        if not self.ok:
            self.erro = self.erro or vazio
            return False
        self.erro = None
        for s in self._socks:
            threading.Thread(target=self._ler, args=(s,), daemon=True, name="voip-captura").start()
        return True

    def parar(self):
        self.ok = False
        for s in self._socks:
            try:
                if WIN:
                    s.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
                s.close()
            except OSError:
                pass
        self._socks = []

    def _ler(self, s):
        placas = None
        if not WIN:
            from .linux.rede import Placas
            placas = Placas()
        while self.ok:
            try:
                if WIN:
                    p, nome, tipo = s.recv(65535), None, 0
                else:
                    p, (nome, _, tipo, *_r) = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not WIN:
                if tipo not in (0, 4) or (nome == "lo" and tipo == 0) or (nome != "lo" and not placas.conta(nome)):
                    continue                          # só o que entra e sai; loopback uma vez só; escravas de bridge fora
            r = ler_ip(p)
            if r and r[5]:
                try:
                    self.ao_pacote(time.time(), r[0], r[1], r[2], r[3], r[4], r[5], time.perf_counter())
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------- chamadas
def _nova(id_, ts, tipo):
    return {"id": id_, "inicio": ts, "ultimo": ts, "tipo": tipo, "metodo": None, "de": "", "para": "", "origem": "",
            "destino": "", "estado": "", "final": False, "inicio_conv": None, "fim_conv": None, "agente": "",
            "msgs": [], "pontos": [], "midia": [], "rtp": {}, "rtp_t": {}, "ultimo_rtp": 0, "salva": False, "retx": 0,
            "desligou": "", "motivo": ""}


def _tipo(metodo):
    return "chamada" if metodo == "INVITE" else "registro" if metodo == "REGISTER" else "outros"


class Voip:
    def __init__(self, pasta=None, captura=True):
        self.pasta = pasta or config.BASE
        self.db = os.path.join(self.pasta, "voip.db")
        self.lock = threading.Lock()
        self.chamadas = {}
        self._midia = {}             # (ip, porta) -> id da chamada
        self.ativo, self.erro = False, None
        self.recebidos = {"sip": 0, "hep": 0, "ws": 0, "rtp": 0}
        self._cap = Captura(self.pacote) if captura else None
        self._parar = threading.Event()

    # ------------------------------------------------ controle
    def _con(self):
        os.makedirs(self.pasta, exist_ok=True)
        con = sqlite3.connect(self.db, timeout=15)
        con.executescript(ESQUEMA)
        colunas = {r[1] for r in con.execute("PRAGMA table_info(chamadas)")}
        for nome in ("desligou", "motivo"):
            if nome not in colunas:
                con.execute(f"ALTER TABLE chamadas ADD COLUMN {nome} TEXT DEFAULT ''")
        return con

    def iniciar(self):
        con = self._con()
        con.execute("DELETE FROM chamadas WHERE inicio < ?", (time.time() - DIAS * 86400,))
        con.commit()
        con.close()
        if self.ativo:
            return True, "A captura já está ligada."
        if self._cap is not None and not self._cap.iniciar():
            self.erro = self._cap.erro
            return False, self.erro
        self.ativo, self.erro = True, None
        self._parar.clear()
        threading.Thread(target=self._varrer_sempre, daemon=True, name="voip-varrer").start()
        return True, "Captura de chamadas ligada: SIP em qualquer porta, WebSocket sem TLS e HEP."

    def parar(self):
        if not self.ativo:
            return False, "A captura já está desligada."
        self.ativo = False
        self._parar.set()
        if self._cap is not None:
            self._cap.parar()
        self.varrer(tudo=True)
        return True, "Captura desligada. O histórico continua salvo."

    def apagar(self):
        with self.lock:
            self.chamadas.clear()
            self._midia.clear()
        con = self._con()
        con.execute("DELETE FROM chamadas")
        con.commit()
        con.close()

    # ------------------------------------------------ entrada de pacotes
    def pacote(self, ts, proto, org, sp, dst, dp, dados, mono=None):
        """Ponto único de entrada: a captura e os testes chamam por aqui."""
        via = proto
        if dados[:4] == b"HEP3":
            h = ler_hep3(dados)
            if not h:
                return
            ts, via, org, sp, dst, dp, dados = h[0] or ts, h[1], h[2], h[3], h[4], h[5], h[6]
            self.recebidos["hep"] += 1
        elif not parece_sip(dados):
            if proto == "TCP":
                interno = desembrulhar_ws(dados)
                if interno is None or not parece_sip(interno):
                    return
                dados, via = interno, "WS"
                self.recebidos["ws"] += 1
            else:
                return self._rtp(ts if mono is None else mono, org, sp, dst, dp, dados)
        m = ler_sip(dados)
        if m:
            self.recebidos["sip"] += 1
            self._sip(ts, via, _ponto(org, sp), _ponto(dst, dp), m, dados)

    def _sip(self, ts, via, org, dst, m, bruto):
        id_ = m["call_id"]
        with self.lock:
            c = self.chamadas.get(id_)
            if c is None:
                if len(self.chamadas) >= MAX_EM_MEMORIA:
                    self._abrir_espaco()
                c = self.chamadas[id_] = _nova(id_, ts, _tipo(m["cseq_metodo"]))
                c.update(metodo=m["cseq_metodo"], de=m["de"], para=m["para"], origem=org, destino=dst)
            elif c["tipo"] == "outros" and _tipo(m["cseq_metodo"]) != "outros":
                c["tipo"], c["metodo"] = _tipo(m["cseq_metodo"]), m["cseq_metodo"]
            for p in (org, dst):
                if p not in c["pontos"]:
                    c["pontos"].append(p)
            c["ultimo"], c["salva"] = ts, False
            c["agente"] = c["agente"] or m["agente"]
            chave = (m["rotulo"], m["cseq"], org, dst)
            repetida = any(x["_k"] == chave for x in c["msgs"][-40:])
            c["retx"] += repetida
            if len(c["msgs"]) < MAX_MSGS:
                c["msgs"].append({"ts": ts, "de": org, "para": dst, "rotulo": m["rotulo"], "cseq": m["cseq"], "via": via,
                                  "retx": repetida, "sdp": m["sdp"], "_k": chave,
                                  "txt": bruto[:MAX_TEXTO].decode("utf-8", "replace")})
            for s in m["sdp"]:
                if s["ip"] and s["tipo"] in ("audio", "video"):
                    self._midia[(s["ip"], s["porta"])] = id_
                    if s not in c["midia"]:
                        c["midia"].append(s)
            if not repetida:
                self._estado(c, m, ts, org)

    @staticmethod
    def _quem(c, m, org):
        """Quem mandou a mensagem, do ponto de vista da chamada: o chamador (quem fez o INVITE) ou o chamado."""
        if m["de"] and m["de"] == c["de"]:
            return "Chamador"
        if m["de"] and m["de"] == c["para"]:
            return "Chamado"
        return "Chamador" if org.rsplit(":", 1)[0] == c["origem"].rsplit(":", 1)[0] else "Chamado"

    def _estado(self, c, m, ts, org=""):
        cod, met = m["codigo"], m["cseq_metodo"]
        if c["tipo"] == "chamada":
            if m["metodo"] == "BYE" and not c["final"]:
                c.update(estado="Encerrada", final=True, fim_conv=ts)
                c["desligou"] = self._quem(c, m, org)
                c["motivo"] = "BYE enviado por " + (m["de"] or org) + (f" ({m['reason']})" if m.get("reason") else "")
            elif m["metodo"] == "CANCEL" and not c["final"]:
                c["estado"] = "Cancelando"
                c["desligou"], c["motivo"] = "Chamador", "CANCEL antes de ser atendida"
            elif m["metodo"] == "INVITE" and not c["estado"]:
                c["estado"] = "Chamando"
            elif cod and met == "INVITE" and not c["final"]:
                if cod in (180, 183):
                    c["estado"] = "Tocando"
                elif 200 <= cod < 300:
                    c["estado"] = "Em andamento"
                    c["inicio_conv"] = c["inicio_conv"] or ts
                elif cod >= 300:
                    c.update(estado=FALHAS.get(cod, f"Falhou {cod}"), final=True, fim_conv=ts)
                    if not c["desligou"]:
                        c["desligou"] = "Servidor" if cod >= 500 else "Chamado" if cod in (480, 486, 600, 603, 404) else "Rede/servidor"
                        c["motivo"] = f"{cod} {m.get('motivo') or ''}".strip() + f" vindo de {org}"
            elif cod and met == "BYE" and c["inicio_conv"] and cod < 300 and c["final"]:
                c["fim_conv"] = c["fim_conv"] or ts
        elif cod and cod >= 200 and met == c["metodo"]:
            c["estado"] = "Registrado" if cod < 300 and c["tipo"] == "registro" else "OK" if cod < 300 else \
                "Autenticando" if cod in (401, 407) else f"Falhou {cod}"
            c["final"] = cod not in (401, 407)
        elif m["metodo"] and not c["estado"]:
            c["estado"] = "Enviado"

    def _rtp(self, mono, org, sp, dst, dp, dados):
        if not self._midia:
            return
        id_ = self._midia.get((dst, dp)) or self._midia.get((org, sp))
        if id_ is None:
            return
        r = ler_rtp(dados)
        if not r:
            return
        pt, seq, ts_rtp, ssrc = r
        with self.lock:
            c = self.chamadas.get(id_)
            if c is None:
                return
            k = (_ponto(org, sp), _ponto(dst, dp), ssrc)
            f = c["rtp"].get(k)
            if f is None:
                if len(c["rtp"]) < 8:
                    c["rtp"][k] = FluxoRtp(pt, seq, ts_rtp, mono)
            else:
                f.add(seq, ts_rtp, mono)
            agora = time.time()
            c["rtp_t"].setdefault(k, [agora, agora])[1] = c["ultimo_rtp"] = agora
            self.recebidos["rtp"] += 1

    # ------------------------------------------------ manutenção
    def _abrir_espaco(self):
        for i in sorted(self.chamadas, key=lambda x: (not self.chamadas[x]["final"], self.chamadas[x]["ultimo"]))[:100]:
            self._guardar(self.chamadas[i])
            self._esquecer(i)

    def _esquecer(self, i):
        self.chamadas.pop(i, None)
        for k in [k for k, v in self._midia.items() if v == i]:
            del self._midia[k]

    def _varrer_sempre(self):
        while not self._parar.wait(2):
            try:
                self.varrer()
            except Exception:  # noqa: BLE001
                pass

    def varrer(self, tudo=False):
        agora = time.time()
        with self.lock:
            for i, c in list(self.chamadas.items()):
                parado = agora - max(c["ultimo"], c["ultimo_rtp"])
                if not c["final"] and (tudo or parado > (SEM_SINAL_S if c["estado"] == "Em andamento" else SEM_RESPOSTA_S)):
                    c["final"] = True
                    if c["tipo"] == "chamada":
                        c["estado"] = "Encerrada (sem BYE)" if c["estado"] == "Em andamento" else "Sem resposta"
                        c["desligou"] = c["desligou"] or "Sem sinal"
                        c["motivo"] = c["motivo"] or ("captura desligada com a chamada aberta" if tudo
                                                      else f"nenhum BYE nem RTP por {int(parado)} s")
                    c["fim_conv"] = c["fim_conv"] or c["ultimo"]
                if c["final"] and not c["salva"] and (tudo or agora - c["ultimo"] > GUARDAR_FINAL_S):
                    self._guardar(c)
                if c["salva"] and agora - c["ultimo"] > FICAR_NA_TELA_S:
                    self._esquecer(i)

    def _guardar(self, c):
        c["salva"] = True
        if c["tipo"] == "outros":
            return
        r = self._resumo(c)
        dados = json.dumps({"msgs": [{k: v for k, v in x.items() if k != "_k"} for x in c["msgs"]], "midia": c["midia"],
                            "rtp": self._fluxos(c), "agente": c["agente"], "pontos": c["pontos"]}, ensure_ascii=False)
        con = self._con()
        con.execute("INSERT OR REPLACE INTO chamadas(id, inicio, fim, tipo, de, para, origem, destino, estado, duracao, nmsg,"
                    " dados, desligou, motivo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (c["id"], c["inicio"], c["fim_conv"] or c["ultimo"], c["tipo"], c["de"], c["para"], c["origem"],
                     c["destino"], c["estado"], r["duracao"], len(c["msgs"]), dados, c["desligou"], c["motivo"]))
        con.commit()
        con.close()

    # ------------------------------------------------ consultas
    @staticmethod
    def _fluxos(c):
        saida = []
        for (org, dst, ssrc), f in c["rtp"].items():
            if f.recebidos >= 5:
                r = f.resumo()
                mos = qualidade.mos(0, r["jitter_ms"] or 0, r["perda"])[0] if f.valido() else None
                t0, t1 = c["rtp_t"].get((org, dst, ssrc), (None, None))
                saida.append({"de": org, "para": dst, "ssrc": ssrc, "mos": mos, "t0": t0, "t1": t1, **r})
        return saida

    def _resumo(self, c):
        ini = c["inicio_conv"]
        dur = ((c["fim_conv"] or (None if c["final"] else time.time()) or c["ultimo"]) - ini) if ini else 0
        fl = self._fluxos(c)
        mos = [f["mos"] for f in fl if f["mos"]]
        return {"id": c["id"], "inicio": c["inicio"], "ultimo": c["ultimo"], "tipo": c["tipo"], "de": c["de"],
                "para": c["para"], "origem": c["origem"], "destino": c["destino"], "estado": c["estado"],
                "ativa": not c["final"], "desligou": c["desligou"], "motivo": c["motivo"], "duracao": round(max(0, dur), 1), "nmsg": len(c["msgs"]), "retx": c["retx"],
                "mos": min(mos) if mos else None, "perda": max((f["perda"] for f in fl), default=None),
                "jitter_ms": max((f["jitter_ms"] or 0 for f in fl), default=None), "via": c["msgs"][0]["via"] if c["msgs"] else ""}

    @staticmethod
    def _casa(r, busca):
        b = (busca or "").strip().lower()
        return not b or any(b in str(r[k] or "").lower() for k in ("id", "de", "para", "origem", "destino", "estado"))

    def ao_vivo(self, busca=""):
        with self.lock:
            lista = [self._resumo(c) for c in self.chamadas.values()]
        lista = sorted((r for r in lista if self._casa(r, busca)), key=lambda r: -r["ultimo"])[:300]
        return {"ativo": self.ativo, "erro": self.erro, "recebidos": dict(self.recebidos), "chamadas": lista,
                "ativas": sum(1 for r in lista if r["ativa"] and r["tipo"] == "chamada")}

    def historico(self, busca="", tipo="", limite=300):
        con = self._con()
        con.row_factory = sqlite3.Row
        cols = "id, inicio, fim, tipo, de, para, origem, destino, estado, duracao, nmsg, desligou, motivo, dados"
        b = f"%{busca.strip()}%"
        linhas = con.execute(f"SELECT {cols} FROM chamadas WHERE (? = '' OR tipo = ?) AND (? = '%%' OR id LIKE ? OR de LIKE ? "
                             "OR para LIKE ? OR origem LIKE ? OR destino LIKE ? OR estado LIKE ? OR desligou LIKE ?) ORDER BY inicio DESC LIMIT ?",
                             (tipo, tipo, b, b, b, b, b, b, b, b, int(limite))).fetchall()
        con.close()
        saida = []
        for r in linhas:
            d = dict(r)
            extra = json.loads(d.pop("dados") or "{}")
            fl = extra.get("rtp") or []
            mos = [f["mos"] for f in fl if f.get("mos")]
            d.update(mos=min(mos) if mos else None, perda=max((f["perda"] for f in fl), default=None),
                     jitter_ms=max((f["jitter_ms"] or 0 for f in fl), default=None), agente=extra.get("agente", ""))
            saida.append(d)
        return saida

    def desde(self, ultimo="", limite=200):
        """Chamadas guardadas depois da que tem o Call-ID `ultimo` (na ordem em que foram guardadas). Sem `ultimo`, ou se
        ele já foi apagado daqui, começa do início do histórico."""
        con = self._con()
        r = con.execute("SELECT rowid FROM chamadas WHERE id = ?", (ultimo,)).fetchone() if ultimo else None
        linhas = con.execute(f"SELECT {', '.join(CAMPOS_MON)} FROM chamadas WHERE rowid > ? ORDER BY rowid LIMIT ?",
                             (r[0] if r else 0, max(1, min(int(limite), 500)))).fetchall()
        con.close()
        return {"chamadas": [dict(zip(CAMPOS_MON, x)) for x in linhas], "ultimo": ultimo}

    def detalhe(self, id_):
        with self.lock:
            c = self.chamadas.get(id_)
            if c is not None:
                return {**self._resumo(c), "pontos": list(c["pontos"]), "agente": c["agente"], "midia": list(c["midia"]),
                        "rtp": self._fluxos(c), "msgs": [{k: v for k, v in x.items() if k != "_k"} for x in c["msgs"]]}
        con = self._con()
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM chamadas WHERE id = ?", (id_,)).fetchone()
        con.close()
        if not r:
            return None
        d = dict(r)
        d.update(json.loads(d.pop("dados") or "{}"))
        d["ativa"] = False
        return d


# ---------------------------------------------------------------- chave da API (outro PC liga e lê a captura)
HORAS_CHAVE = 6                  # validade padrão
HORAS_CHAVE_MAX = 24 * 30


class ChaveApi:
    """A chave que o PC B passa para o PC A. Vale por `horas` desde que foi gerada, vira outra quando vence e também
    na virada do dia. Fica em arquivo próprio (não vai para o backup das configurações)."""

    def __init__(self, pasta=None, agora=time.time):
        self.arquivo = os.path.join(pasta or config.BASE, "voip_chave.json")
        self._agora = agora
        self._lock = threading.Lock()
        self.d = {}
        try:
            with open(self.arquivo, encoding="utf-8") as f:
                self.d = json.load(f)
        except (OSError, ValueError):
            pass
        if not isinstance(self.d, dict):
            self.d = {}

    @staticmethod
    def _dia(ts):
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    def _gerar(self, horas=None):
        agora = self._agora()
        horas = self.d.get("horas", HORAS_CHAVE) if horas is None else horas
        self.d = {"chave": "cnt_" + secrets.token_urlsafe(24), "horas": horas, "criada": agora,
                  "expira": agora + horas * 3600}
        try:
            os.makedirs(os.path.dirname(self.arquivo), exist_ok=True)
            with open(self.arquivo, "w", encoding="utf-8") as f:
                json.dump(self.d, f)
        except OSError:
            pass                 # sem gravar, a chave só dura até o serviço reiniciar

    def _em_dia(self):
        agora = self._agora()
        if (not self.d.get("chave") or agora >= self.d.get("expira", 0)
                or self._dia(agora) != self._dia(self.d.get("criada", 0))):
            self._gerar()

    def estado(self):
        with self._lock:
            self._em_dia()
            return {"chave": self.d["chave"], "horas": self.d["horas"], "criada": self.d["criada"],
                    "expira": self.d["expira"]}

    def nova(self):
        with self._lock:
            self._gerar()
        return self.estado()

    def definir_horas(self, horas):
        try:
            horas = float(horas)
        except (TypeError, ValueError):
            return False, "Informe a validade em horas."
        if not 1 <= horas <= HORAS_CHAVE_MAX:
            return False, f"A validade vai de 1 a {HORAS_CHAVE_MAX} horas."
        horas = int(horas) if horas == int(horas) else round(horas, 1)
        with self._lock:
            self._em_dia()
            self.d["horas"] = horas
            self.d["expira"] = self.d["criada"] + horas * 3600
            if self._agora() >= self.d["expira"]:
                self._gerar()
            else:
                self._gravar()
        return True, f"Chave válida por {horas} h a partir de agora."

    def _gravar(self):
        try:
            with open(self.arquivo, "w", encoding="utf-8") as f:
                json.dump(self.d, f)
        except OSError:
            pass

    def confere(self, dada):
        with self._lock:
            self._em_dia()
            chave = self.d["chave"]
        return bool(dada) and secrets.compare_digest(str(dada).encode("utf-8"), chave.encode("utf-8"))


def atender(voip, rota, metodo="GET", q=None, corpo=None):
    """A API de chamadas (a mesma de /api/chamadas/*) para quem pede pelo canal direto. Devolve (código, dict)."""
    q = q or {}
    try:
        limite = max(1, min(int(q.get("limite") or 0), 5000))
    except (TypeError, ValueError):
        limite = 0
    if rota == "estado":
        return 200, {**voip.ao_vivo(q.get("busca", "")), "alvo": None}
    if rota == "historico":
        return 200, voip.historico(q.get("busca", ""), q.get("tipo", ""), limite or 300)
    if rota == "sincronizar":
        return 200, voip.desde(q.get("desde", ""), limite or 200)
    if rota == "chamada":
        d = voip.detalhe(q.get("id", ""))
        return (200, d) if d else (404, {"erro": "chamada não encontrada"})
    if rota == "captura" and metodo == "POST":
        ok, msg = voip.iniciar() if (corpo or {}).get("ligar") else voip.parar()
        return 200, {"ok": ok, "msg": msg, "ativo": voip.ativo}
    return 404, {"erro": "recurso não encontrado"}


# ---------------------------------------------------------------- PC A lendo e controlando a captura do PC B
class ClienteRemoto:
    """Fala com a API de chamadas de outro CNT Monitor (/api/chamadas) usando a chave que ele gerou."""

    def __init__(self):
        self.url, self.chave, self.erro = "", "", None

    @property
    def ativo(self):
        return bool(self.url)

    def conectar(self, endereco, chave):
        """`endereco` é o ID do compartilhamento online do outro PC (conexão direta P2P); um endereço IP:porta ainda
        vale para quem alcança o outro PC pela rede."""
        ident = p2p.id_de(endereco)
        endereco = (endereco or "").strip().rstrip("/")
        if ident:
            url = "id:" + ident
        else:
            if endereco and "://" not in endereco:
                endereco = "http://" + endereco
            p = urllib.parse.urlparse(endereco)
            if p.scheme not in ("http", "https") or not p.hostname:
                return False, "ID inválido. Digite o ID do compartilhamento online do outro computador (9 números)."
            url = f"{p.scheme}://{p.netloc}"
        if not (chave or "").strip():
            return False, "Cole a chave que o outro computador gerou."
        antigo = (self.url, self.chave)
        self.url, self.chave = url, chave.strip()
        try:
            self.get("estado")
        except Exception as e:  # noqa: BLE001
            self.url, self.chave = antigo       # a tentativa falhou: a conexão que já estava boa continua
            return False, str(e)
        return True, "Conectado. Você está vendo as chamadas do outro computador."

    def desconectar(self):
        self.url = self.chave = ""

    def rotulo(self):
        if self.url.startswith("id:"):
            return "ID " + encontro.formatar_id(self.url[3:])
        return urllib.parse.urlparse(self.url).hostname or self.url

    def _pedir(self, rota, corpo=None, params=None):
        if self.url.startswith("id:"):
            return p2p.pedir(self.url[3:], self.chave, rota, corpo, params)
        q = "?" + urllib.parse.urlencode(params) if params else ""
        req = urllib.request.Request(f"{self.url}/api/chamadas/{rota}{q}", method="POST" if corpo is not None else "GET",
                                     data=json.dumps(corpo).encode() if corpo is not None else None,
                                     headers={"X-Voip-Key": self.chave, "Content-Type": "application/json",
                                              "User-Agent": "CNTMonitor/" + config.VERSAO})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("erro")
            except ValueError:
                msg = None
            raise RuntimeError(msg or f"O outro computador respondeu {e.code}.") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(f"Não consegui falar com o outro computador ({getattr(e, 'reason', e)}).") from None

    def get(self, rota, **params):
        return self._pedir(rota, None, {k: v for k, v in params.items() if v not in (None, "")})

    def post(self, rota, corpo):
        return self._pedir(rota, corpo)


# ---------------------------------------------------------------- monitoramento de vários computadores
INTERVALO_MON_S = 30
LOTE_MON = 200                   # chamadas por pedido
DIAS_MON = 30
ESQUEMA_MON = """CREATE TABLE IF NOT EXISTS computadores(id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, url TEXT, chave TEXT,
    ultimo TEXT DEFAULT '', sync REAL, erro TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS chamadas_pc(pc INTEGER, id TEXT, inicio REAL, fim REAL, tipo TEXT, de TEXT, para TEXT, origem TEXT,
    destino TEXT, estado TEXT, duracao REAL, nmsg INTEGER, desligou TEXT, motivo TEXT, dados TEXT, PRIMARY KEY(pc, id));
CREATE INDEX IF NOT EXISTS i_chamadas_pc ON chamadas_pc(inicio);"""
CAMPOS_MON = ("id", "inicio", "fim", "tipo", "de", "para", "origem", "destino", "estado", "duracao", "nmsg", "desligou", "motivo",
              "dados")


class Monitoramento:
    """Os computadores cadastrados (endereço + chave da API). A cada 30 s pede a cada um o que falta: manda o Call-ID da
    última chamada que já tem e o outro PC responde só com as que vêm depois dela."""

    def __init__(self, pasta=None, ativo=lambda: True):
        self.db = os.path.join(pasta or config.BASE, "voip_monitor.db")
        self._ativo = ativo
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._agora = threading.Event()

    def _con(self):
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        con = sqlite3.connect(self.db, timeout=15)
        con.executescript(ESQUEMA_MON)
        con.row_factory = sqlite3.Row
        return con

    def iniciar(self):
        threading.Thread(target=self._laco, daemon=True, name="voip-monitoramento").start()

    def parar(self):
        self._parar.set()
        self._agora.set()

    def _laco(self):
        while not self._parar.is_set():
            self._agora.wait(INTERVALO_MON_S)
            self._agora.clear()
            if self._parar.is_set():
                break
            try:
                if self._ativo():
                    self.sincronizar()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------ cadastro
    def listar(self):
        con = self._con()
        linhas = con.execute("SELECT c.id, c.nome, c.url, c.ultimo, c.sync, c.erro, "
                             "(SELECT COUNT(*) FROM chamadas_pc p WHERE p.pc = c.id) AS total FROM computadores c ORDER BY c.id").fetchall()
        con.close()
        return [dict(r) for r in linhas]

    def adicionar(self, nome, url, chave):
        c = ClienteRemoto()
        ok, msg = c.conectar(url, chave)
        if not ok:
            return False, msg
        nome = (nome or "").strip()[:60] or c.rotulo()
        con = self._con()
        if con.execute("SELECT 1 FROM computadores WHERE url = ?", (c.url,)).fetchone():
            con.close()
            return False, "Esse computador já está cadastrado."
        con.execute("INSERT INTO computadores(nome, url, chave) VALUES (?,?,?)", (nome, c.url, c.chave))
        con.commit()
        con.close()
        self._agora.set()
        return True, f"{nome} cadastrado. A primeira coleta começa agora."

    def remover(self, pc):
        con = self._con()
        con.execute("DELETE FROM chamadas_pc WHERE pc = ?", (pc,))
        con.execute("DELETE FROM computadores WHERE id = ?", (pc,))
        con.commit()
        con.close()

    def pedir_agora(self):
        self._agora.set()

    # ------------------------------------------------ coleta
    def sincronizar(self):
        with self._lock:
            con = self._con()
            pcs = [dict(r) for r in con.execute("SELECT id, nome, url, chave, ultimo FROM computadores")]
            con.close()
            for pc in pcs:
                self._um(pc)
            con = self._con()
            con.execute("DELETE FROM chamadas_pc WHERE inicio < ?", (time.time() - DIAS_MON * 86400,))
            con.commit()
            con.close()

    def _um(self, pc):
        cli = ClienteRemoto()
        cli.url, cli.chave = pc["url"], pc["chave"]
        ultimo, erro = pc["ultimo"], ""
        try:
            for _ in range(25):                       # no máximo 5000 chamadas por rodada; o resto vem na seguinte
                d = cli.get("sincronizar", desde=ultimo, limite=LOTE_MON)
                lista = d.get("chamadas") or []
                con = self._con()
                for c in lista:
                    con.execute("INSERT OR REPLACE INTO chamadas_pc(pc, id, inicio, fim, tipo, de, para, origem, destino, estado,"
                                " duracao, nmsg, desligou, motivo, dados) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (pc["id"], *(c.get(k) for k in CAMPOS_MON)))
                if lista:
                    ultimo = lista[-1]["id"]
                con.execute("UPDATE computadores SET ultimo = ? WHERE id = ?", (ultimo, pc["id"]))
                con.commit()
                con.close()
                if len(lista) < LOTE_MON:
                    break
        except RuntimeError as e:
            erro = str(e)
        con = self._con()
        con.execute("UPDATE computadores SET sync = ?, erro = ? WHERE id = ?", (time.time(), erro, pc["id"]))
        con.commit()
        con.close()

    # ------------------------------------------------ consultas
    def historico(self, pc="", busca="", limite=300):
        b = f"%{(busca or '').strip()}%"
        con = self._con()
        linhas = con.execute(
            "SELECT p.pc, c.nome AS pc_nome, p.id, p.inicio, p.fim, p.tipo, p.de, p.para, p.origem, p.destino, p.estado, p.duracao,"
            " p.nmsg, p.desligou, p.motivo FROM chamadas_pc p JOIN computadores c ON c.id = p.pc WHERE (? = '' OR p.pc = ?) AND"
            " (? = '%%' OR p.id LIKE ? OR p.de LIKE ? OR p.para LIKE ? OR p.origem LIKE ? OR p.destino LIKE ? OR p.estado LIKE ?"
            " OR p.desligou LIKE ? OR c.nome LIKE ?) ORDER BY p.inicio DESC LIMIT ?",
            (str(pc or ""), str(pc or ""), b, b, b, b, b, b, b, b, b, int(limite))).fetchall()
        con.close()
        return [dict(r) for r in linhas]

    def detalhe(self, pc, id_):
        con = self._con()
        r = con.execute("SELECT p.*, c.nome AS pc_nome FROM chamadas_pc p JOIN computadores c ON c.id = p.pc "
                        "WHERE p.pc = ? AND p.id = ?", (pc, id_)).fetchone()
        con.close()
        if not r:
            return None
        d = dict(r)
        d.update(json.loads(d.pop("dados") or "{}"))
        d["ativa"] = False
        return d
