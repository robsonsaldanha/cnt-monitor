"""Rede no Linux: o que o Windows dá pelo ETW, pela API ICMP, pelo EStats e pelo wlanapi.

- Tráfego por programa: um socket AF_PACKET (root) lê o cabeçalho de cada pacote IP/TCP/UDP de todas as placas
  e a porta local diz de qual processo ele é (tabela de sockets do kernel, via psutil).
- Ping: socket ICMP próprio (com TTL e "não fragmentar"), para o traceroute e o Path MTU funcionarem igual.
- RTT e retransmissões por conexão TCP: `ss -ti` (os mesmos números que o kernel usa).
- Contadores TCP/UDP/IP: /proc/net/snmp e /proc/net/snmp6.
- Wi-Fi: `iw`. DNS: `resolvectl` (systemd-resolved) ou /etc/resolv.conf.
"""
import os
import re
import time
import socket
import struct
import ipaddress
import threading

import psutil

from . import rodar

FREQ = 1_000_000_000            # hora dos pacotes em nanossegundos (time.monotonic_ns)
ETH_P_ALL = 0x0003
PACOTE_HOST, PACOTE_SAINDO = 0, 4       # sll_pkttype: chegou para este PC / saiu dele
CAB = 128                        # bytes lidos de cada pacote: cabeçalhos IP e TCP/UDP (+ 12 do RTP)


def ler_pacote(p):
    """Pacote IP (sem o cabeçalho da placa) -> (origem, destino, porta origem, porta destino, udp, bytes de dados,
    12 primeiros bytes de dados) ou None. `p` pode vir cortado em CAB bytes: o tamanho real sai do cabeçalho IP."""
    if len(p) < 20:
        return None
    v = p[0] >> 4
    if v == 4:
        ihl = (p[0] & 15) * 4
        proto, total = p[9], struct.unpack_from(">H", p, 2)[0]
        if struct.unpack_from(">H", p, 6)[0] & 0x1FFF:      # pedaço de um datagrama fragmentado (sem portas)
            return None
        org, dst, ini = p[12:16], p[16:20], ihl
    elif v == 6:
        if len(p) < 40:
            return None
        proto, total = p[6], 40 + struct.unpack_from(">H", p, 4)[0]
        org, dst, ini = p[8:24], p[24:40], 40
    else:
        return None
    if proto not in (6, 17) or len(p) < ini + 8:
        return None
    sport, dport = struct.unpack_from(">HH", p, ini)
    if proto == 17:
        dados = struct.unpack_from(">H", p, ini + 4)[0] - 8
        return org, dst, sport, dport, True, max(0, dados), p[ini + 8:ini + 20]
    if len(p) < ini + 13:
        return None
    off = (p[ini + 12] >> 4) * 4
    return org, dst, sport, dport, False, max(0, total - ini - off), p[ini + off:ini + off + 12]


class PortasPid:
    """Porta local -> processo dono, pela tabela de sockets do kernel (atualizada no máximo 2x por segundo)."""

    def __init__(self, conexoes=None):
        self._conexoes = conexoes or (lambda: psutil.net_connections(kind="inet"))
        self._mapa = {}
        self._ult = 0.0
        self._lock = threading.Lock()

    def _refazer(self):
        m = {}
        try:
            lista = self._conexoes()
        except (psutil.Error, OSError):
            return
        for c in lista:
            if not c.pid or not c.laddr:
                continue
            udp = c.type == socket.SOCK_DGRAM
            ip = c.laddr.ip.split("%")[0]
            m[(udp, ip, c.laddr.port)] = c.pid
            m.setdefault((udp, None, c.laddr.port), c.pid)      # socket em 0.0.0.0/:: ou porta usada só por ele
        self._mapa, self._ult = m, time.monotonic()

    def pid(self, udp, ip_local, porta):
        m = self._mapa
        p = m.get((udp, ip_local, porta)) or m.get((udp, None, porta))
        if p is None and time.monotonic() - self._ult > 0.5:
            with self._lock:
                if time.monotonic() - self._ult > 0.5:
                    self._refazer()
            m = self._mapa
            p = m.get((udp, ip_local, porta)) or m.get((udp, None, porta))
        return p

    def manter(self):
        if time.monotonic() - self._ult > 2:
            with self._lock:
                self._refazer()


def _socket_pacotes():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(ETH_P_ALL))
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 2 ** 20)
    s.settimeout(1.0)
    return s


class Placas:
    """Placas que contam: as que têm endereço IP (fora a de loopback). O mesmo pacote passa por mais de uma
    interface quando há bridge, bonding ou a placa "acelerada" das nuvens (Azure, AWS): a escrava, sem IP, mostraria
    de novo o que a placa com IP já mostrou. Numa VPN o túnel e a placa física têm IP, mas carregam pacotes
    diferentes (o da placa é o do programa da VPN), então nada é contado duas vezes."""

    def __init__(self):
        self._com_ip, self._ult = set(), 0.0

    def conta(self, nome):
        agora = time.monotonic()
        if agora - self._ult > 30 or (nome not in self._com_ip and agora - self._ult > 2):
            try:
                stats = psutil.net_if_stats()
                self._com_ip = {n for n, lst in psutil.net_if_addrs().items()
                                if n != "lo" and "loopback" not in getattr(stats.get(n), "flags", "")
                                and any(a.family in (socket.AF_INET, socket.AF_INET6) for a in lst)}
            except Exception:  # noqa: BLE001
                pass
            self._ult = agora
        return nome in self._com_ip


class MonitorRede:
    """Mesma interface do monitor por ETW (coletar() -> [(pid, ip_remoto, env, rec)], detalhe, fluxos)."""

    def __init__(self):
        self.ok = False
        self.erro = None
        self.eventos = 0
        self._acc = {}
        self.detalhe = []
        self.fluxos = []
        self._ult = {}               # fluxo UDP -> (hora do último pacote que chegou, intervalo anterior)
        self._ult_poda = 0
        self._locais = set()
        self._ult_locais = 0
        self._th = None
        self._portas = PortasPid()
        self._sock = None
        self._placas = Placas()

    def _ips_locais(self):
        if time.time() - self._ult_locais > 60:
            s = set()
            try:
                for lst in psutil.net_if_addrs().values():
                    s.update(a.address.split("%")[0] for a in lst if a.family in (socket.AF_INET, socket.AF_INET6))
            except Exception:  # noqa: BLE001
                pass
            self._locais, self._ult_locais = s, time.time()
        return self._locais

    def iniciar(self):
        if self.ok:
            return True
        try:
            self._sock = _socket_pacotes()
        except PermissionError:
            self.erro = "Ler o tráfego por programa precisa do serviço rodando como root."
            return False
        except OSError as e:
            self.erro = f"Não consegui ler os pacotes de rede: {e}"
            return False
        self._th = threading.Thread(target=self._loop, name="rede-pacotes", daemon=True)
        self.ok = True
        self.erro = None
        self._th.start()
        return True

    def _loop(self):
        buf = bytearray(CAB)
        vista = memoryview(buf)
        s = self._sock
        try:
            while self.ok:
                try:
                    n, end = s.recvfrom_into(buf, CAB, socket.MSG_TRUNC)
                except socket.timeout:
                    self._portas.manter()
                    continue
                if end[2] not in (PACOTE_HOST, PACOTE_SAINDO) or not self._placas.conta(end[0]):
                    continue
                self._pacote(bytes(vista[:min(n, CAB)]), end[2] == PACOTE_SAINDO, time.monotonic_ns())
        except OSError as e:
            self.erro = f"A leitura dos pacotes parou: {e}"
        finally:
            self.ok = False

    def _pacote(self, p, saindo, ts):
        x = ler_pacote(p)
        if x is None:
            return
        org, dst, sport, dport, udp, tam, _ = x
        if not udp and tam == 0:
            return              # só ACK: o ETW também não conta (não há dados)
        local_ip, local_porta = (org, sport) if saindo else (dst, dport)
        try:
            ip_txt = socket.inet_ntop(socket.AF_INET6 if len(local_ip) == 16 else socket.AF_INET, local_ip)
        except (OSError, ValueError):
            return
        pid = self._portas.pid(udp, ip_txt, local_porta)
        if not pid:
            return              # encaminhado (contêiner, VM, roteamento) ou socket que já fechou
        chave = (pid, dst, org, struct.pack(">HH", dport, sport), udp)
        v = self._acc.get(chave)
        if v is None:
            v = self._acc[chave] = [0, 0, 0, 0, 0, 0, 0]
        i = 0 if saindo else 1
        v[i] += tam
        v[i + 2] += 1
        if udp and not saindo:
            u = self._ult.get(chave)
            if u is None:
                self._ult[chave] = (ts, None)
            else:
                gap = ts - u[0]
                if gap > v[6]:
                    v[6] = gap
                if gap > 2 * FREQ:
                    self._ult[chave] = (ts, None)
                else:
                    if u[1] is not None:
                        v[4] += abs(gap - u[1])
                        v[5] += 1
                    self._ult[chave] = (ts, gap)
        self.eventos += 1

    def parar(self):
        self.ok = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def estatisticas(self):
        return None

    def coletar(self):
        from .. import rede
        acc, self._acc = self._acc, {}
        lista, self.detalhe, self.fluxos = rede.agrupar(acc, self._ips_locais(), FREQ)
        agora = time.monotonic_ns()
        if agora - self._ult_poda > 10 * FREQ:
            self._ult_poda = agora
            limite = agora - 10 * FREQ
            for k in [k for k, u in list(self._ult.items()) if u[0] < limite]:
                self._ult.pop(k, None)
        return lista


class CapturaUDP:
    """Datagramas UDP IPv4 que chegam a este PC (AF_PACKET), só enquanto há programa vigiado.
    Mesma interface da captura por SIO_RCVALL do Windows: ao_pacote(hora, origem, destino, porta org, porta dst, rtp)."""

    def __init__(self, ao_pacote):
        self.ao_pacote = ao_pacote
        self.ok = False
        self.erro = None
        self._sock = None

    def iniciar(self):
        if self.ok:
            return True
        try:
            self._sock = _socket_pacotes()
        except OSError as e:
            self.erro = f"Não consegui ler os pacotes de rede: {e.strerror or e}"
            return False
        self.ok = True
        threading.Thread(target=self._ler, daemon=True, name="captura-udp").start()
        return True

    def parar(self):
        self.ok = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _ler(self):
        from ..rede_programa import analisar_ipv4
        s, placas = self._sock, Placas()
        while self.ok:
            try:
                p, end = s.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if end[2] != PACOTE_HOST or not placas.conta(end[0]):
                continue
            pacote = analisar_ipv4(p)
            if pacote:
                try:
                    self.ao_pacote(time.perf_counter(), *pacote)
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------- ping (ICMP)
IP_SUCCESS, IP_DEST_NET_UNREACHABLE, IP_DEST_HOST_UNREACHABLE = 0, 11002, 11003
IP_DEST_PORT_UNREACHABLE, IP_PACKET_TOO_BIG, IP_REQ_TIMED_OUT, IP_TTL_EXPIRED_TRANSIT = 11005, 11009, 11010, 11013
_seq = [0]
_seq_lock = threading.Lock()


def _soma(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f">{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def _abrir_icmp(v6):
    fam, proto = (socket.AF_INET6, socket.IPPROTO_ICMPV6) if v6 else (socket.AF_INET, socket.IPPROTO_ICMP)
    try:
        return socket.socket(fam, socket.SOCK_RAW, proto), True
    except PermissionError:
        # sem root: o "ping sem privilégio" do Linux (net.ipv4.ping_group_range); o kernel troca o identificador
        return socket.socket(fam, socket.SOCK_DGRAM, proto), False


def ping(ip, timeout_ms=1000, ttl=128, tam=32, nao_fragmentar=False):
    """Um eco ICMP. Devolve {"rtt": ms | None, "status": código (os mesmos do Windows), "de": IP que respondeu}."""
    a = ipaddress.ip_address(ip)
    v6 = a.version == 6
    with _seq_lock:
        _seq[0] = (_seq[0] + 1) & 0xFFFF
        seq = _seq[0]
    ident = (os.getpid() ^ (threading.get_ident() >> 4)) & 0xFFFF
    dados = (b"CNT-Monitor-" * (tam // 12 + 1))[:tam]
    tipo_eco, tipo_resp = (128, 129) if v6 else (8, 0)
    cab = struct.pack(">BBHHH", tipo_eco, 0, 0, ident, seq)
    pacote = cab[:2] + struct.pack(">H", 0 if v6 else _soma(cab + dados)) + cab[4:] + dados
    try:
        s, bruto = _abrir_icmp(v6)
    except OSError as e:
        return {"rtt": None, "status": -1, "de": None, "erro": str(e)}
    try:
        if v6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, ttl)
        else:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            # IP_MTU_DISCOVER = 10, IP_PMTUDISC_DO = 2 (liga o bit "não fragmentar"); DONT = 0
            s.setsockopt(socket.IPPROTO_IP, 10, 2 if nao_fragmentar else 0)
        ini = time.perf_counter()
        fim = ini + timeout_ms / 1000
        try:
            s.sendto(pacote, (str(a), 0))
        except OSError as e:
            if e.errno == 90:        # EMSGSIZE: maior que o MTU da placa com "não fragmentar"
                return {"rtt": None, "status": IP_PACKET_TOO_BIG, "de": None}
            return {"rtt": None, "status": IP_DEST_NET_UNREACHABLE if e.errno in (101, 113) else IP_REQ_TIMED_OUT,
                    "de": None}
        while True:
            resta = fim - time.perf_counter()
            if resta <= 0:
                return {"rtt": None, "status": IP_REQ_TIMED_OUT, "de": None}
            s.settimeout(resta)
            try:
                r, de = s.recvfrom(2048)
            except socket.timeout:
                return {"rtt": None, "status": IP_REQ_TIMED_OUT, "de": None}
            except OSError:
                return {"rtt": None, "status": IP_REQ_TIMED_OUT, "de": None}
            rtt = round((time.perf_counter() - ini) * 1000, 2)
            st = _resposta(r, v6, bruto, ident, seq, tipo_resp)
            if st is not None:
                return {"rtt": rtt if st in (IP_SUCCESS, IP_TTL_EXPIRED_TRANSIT) else None, "status": st,
                        "de": de[0].split("%")[0]}
    finally:
        s.close()


def _resposta(r, v6, bruto, ident, seq, tipo_resp):
    """Código de status se `r` é a resposta (ou o erro) do nosso eco; None se é de outro ping."""
    if not v6 and bruto:
        if len(r) < 20:
            return None
        r = r[(r[0] & 15) * 4:]          # o socket bruto IPv4 entrega com o cabeçalho IP
    if len(r) < 8:
        return None
    tipo, cod = r[0], r[1]
    if tipo == tipo_resp:
        rid, rseq = struct.unpack_from(">HH", r, 4)
        return IP_SUCCESS if rseq == seq and (rid == ident or not bruto) else None
    # erro ICMP: carrega o começo do pacote original (cabeçalho IP + 8 bytes do nosso eco)
    orig = r[8:]
    if v6:
        orig = orig[40:] if len(orig) >= 48 else b""
    elif len(orig) >= 20:
        orig = orig[(orig[0] & 15) * 4:]
    if len(orig) < 8 or struct.unpack_from(">H", orig, 6)[0] != seq:
        return None
    if (not v6 and tipo == 11) or (v6 and tipo == 3):
        return IP_TTL_EXPIRED_TRANSIT
    if (not v6 and tipo == 3 and cod == 4) or (v6 and tipo == 2):
        return IP_PACKET_TOO_BIG
    if (not v6 and tipo == 3 and cod == 3) or (v6 and tipo == 1 and cod == 4):
        return IP_DEST_PORT_UNREACHABLE
    if (not v6 and tipo == 3) or (v6 and tipo == 1):
        return IP_DEST_HOST_UNREACHABLE if cod in (1, 3, 10) else IP_DEST_NET_UNREACHABLE
    return None


# ---------------------------------------------------------------- TCP por conexão (ss -ti)
_ss = {"t": 0.0, "mapa": {}}
_ss_lock = threading.Lock()
_RE_SS = re.compile(r"\b(rtt):([\d.]+)/([\d.]+)|\b(rto|mss|minrtt):([\d.]+)|\bretrans:\d+/(\d+)|\b(bytes_retrans):(\d+)")


def _ender(txt):
    ip, _, porta = txt.rpartition(":")
    ip = ip.strip("[]").split("%")[0]
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    try:
        return ip, int(porta)
    except ValueError:
        return None


def ler_ss(txt):
    """Saída de `ss -tinH` -> {(ip local, porta, ip remoto, porta): {rtt, rtt_var, rto, mss, rtt_min, retransmissoes}}."""
    out, chave = {}, None
    for linha in txt.splitlines():
        if not linha.strip():
            continue
        if not linha[0].isspace():
            p = linha.split()
            chave = None
            if len(p) >= 4:
                lo, re_ = _ender(p[-2]), _ender(p[-1])
                if lo and re_:
                    chave = (lo[0], lo[1], re_[0], re_[1])
            continue
        if chave is None:
            continue
        d = {"rtt": None, "retransmissoes": 0}
        for m in _RE_SS.finditer(linha):
            if m.group(1):
                d["rtt"], d["rtt_var"] = float(m.group(2)), float(m.group(3))
            elif m.group(4):
                d[{"rto": "rto", "mss": "mss", "minrtt": "rtt_min"}[m.group(4)]] = float(m.group(5))
            elif m.group(6):
                d["retransmissoes"] = int(m.group(6))
            elif m.group(7):
                d["bytes_retransmitidos"] = int(m.group(8))
        out[chave] = d
        chave = None
    return out


def tcp_estats(lip, lport, rip, rport):
    """RTT suavizado (ms) e retransmissões de uma conexão TCP, como o EStats do Windows. None se não achou."""
    with _ss_lock:
        if time.monotonic() - _ss["t"] > 1:
            c, txt = rodar(["ss", "-tinH", "state", "established"], timeout=5)
            _ss["mapa"] = ler_ss(txt) if c == 0 else {}
            _ss["t"] = time.monotonic()
        return _ss["mapa"].get((lip, lport, rip, rport))


# ---------------------------------------------------------------- contadores do kernel
_SNMP = {"tcp": {"RtoAlgorithm": "rto_algoritmo", "RtoMin": "rto_min", "RtoMax": "rto_max", "MaxConn": "max_conn",
                 "ActiveOpens": "aberturas_ativas", "PassiveOpens": "aberturas_passivas", "AttemptFails": "falhas_conexao",
                 "EstabResets": "resets_estabelecidas", "CurrEstab": "estabelecidas", "InSegs": "segmentos_in",
                 "OutSegs": "segmentos_out", "RetransSegs": "retransmitidos", "InErrs": "erros_in", "OutRsts": "resets_enviados"},
         "udp": {"InDatagrams": "datagramas_in", "NoPorts": "sem_porta", "InErrors": "erros_in", "OutDatagrams": "datagramas_out"},
         "ip": {"Forwarding": "encaminhamento", "DefaultTTL": "ttl_padrao", "InReceives": "recebidos",
                "InHdrErrors": "erros_cabecalho", "InAddrErrors": "erros_endereco", "ForwDatagrams": "encaminhados",
                "InUnknownProtos": "protocolo_desconhecido", "InDiscards": "descartados_in", "InDelivers": "entregues",
                "OutRequests": "pedidos_out", "OutDiscards": "descartados_out", "OutNoRoutes": "sem_rota",
                "ReasmTimeout": "remontagem_timeout", "ReasmReqds": "remontagem_pedidos", "ReasmOKs": "remontagem_ok",
                "ReasmFails": "remontagem_falhas", "FragOKs": "fragmentados_ok", "FragFails": "fragmentacao_falhas",
                "FragCreates": "fragmentos_criados"}}
_SNMP6 = {"Ip6InReceives": ("ip", "recebidos"), "Ip6InHdrErrors": ("ip", "erros_cabecalho"),
          "Ip6InDiscards": ("ip", "descartados_in"), "Ip6OutDiscards": ("ip", "descartados_out"),
          "Ip6ReasmFails": ("ip", "remontagem_falhas"), "Ip6FragOKs": ("ip", "fragmentados_ok"),
          "Ip6FragFails": ("ip", "fragmentacao_falhas"), "Udp6InDatagrams": ("udp", "datagramas_in"),
          "Udp6NoPorts": ("udp", "sem_porta"), "Udp6InErrors": ("udp", "erros_in"), "Udp6OutDatagrams": ("udp", "datagramas_out")}


def ler_snmp(txt, txt6=""):
    """/proc/net/snmp (+ snmp6) -> {"tcp": {...}, "udp": {...}, "ip": {...}} com os nomes usados no Windows.
    O TCP do Linux já soma IPv4 e IPv6; IP e UDP somam os dois arquivos."""
    out = {"tcp": {}, "udp": {}, "ip": {}}
    linhas = txt.splitlines()
    for cab, val in zip(linhas[::2], linhas[1::2]):
        g, _, nomes = cab.partition(":")
        g = g.strip().lower()
        if g not in _SNMP:
            continue
        for k, v in zip(nomes.split(), val.partition(":")[2].split()):
            if k in _SNMP[g]:
                try:
                    out[g][_SNMP[g][k]] = int(v)
                except ValueError:
                    pass
    for linha in txt6.splitlines():
        p = linha.split()
        if len(p) == 2 and p[0] in _SNMP6:
            g, k = _SNMP6[p[0]]
            try:
                out[g][k] = out[g].get(k, 0) + int(p[1])
            except ValueError:
                pass
    return out


def contadores_sistema():
    from . import ler
    return ler_snmp(ler("/proc/net/snmp", ""), ler("/proc/net/snmp6", ""))


# ---------------------------------------------------------------- Wi-Fi (iw)
def _qualidade(dbm):
    return max(0, min(100, 2 * (int(dbm) + 100))) if dbm is not None else None


def ler_iw_link(txt):
    d = {}
    for chave, rx in (("bssid", r"Connected to ([0-9a-f:]{17})"), ("ssid", r"SSID: (.*)"), ("freq", r"freq: ([\d.]+)"),
                      ("rssi", r"signal: (-?\d+) dBm"), ("rx", r"rx bitrate: ([\d.]+) MBit/s(.*)"),
                      ("tx", r"tx bitrate: ([\d.]+) MBit/s(.*)")):
        m = re.search(rx, txt)
        if m:
            d[chave] = m.groups() if chave in ("rx", "tx") else m.group(1).strip()
    return d


def _padrao(texto):
    t = texto or ""
    if "EHT" in t:
        return "Wi-Fi 7 (be)"
    if "HE-" in t:
        return "Wi-Fi 6 (ax)"
    if "VHT" in t:
        return "802.11ac"
    if "MCS" in t:
        return "802.11n"
    return "802.11a/b/g" if t.strip() else None


def ler_iw_scan(txt):
    from ..qualidade import canal_wifi
    redes, r = [], None
    for linha in txt.splitlines():
        m = re.match(r"BSS ([0-9a-f:]{17})", linha)
        if m:
            r = {"bssid": m.group(1), "ssid": "", "rssi": None, "canal": None, "banda": None}
            redes.append(r)
            continue
        if r is None:
            continue
        s = linha.strip()
        if s.startswith("freq:"):
            try:
                r["canal"], r["banda"] = canal_wifi(int(float(s.split()[1]) * 1000))
            except (ValueError, IndexError):
                pass
        elif s.startswith("signal:"):
            try:
                r["rssi"] = int(float(s.split()[1]))
            except (ValueError, IndexError):
                pass
        elif s.startswith("SSID:"):
            r["ssid"] = s[5:].strip()
    return redes


def wifi():
    """Placas Wi-Fi: SSID, BSSID, sinal, RSSI, taxas, canal e redes vizinhas no mesmo canal (como no Windows)."""
    from ..qualidade import canal_wifi
    c, txt = rodar(["iw", "dev"], timeout=5)
    if c != 0:
        return []
    out = []
    for placa in re.findall(r"Interface (\S+)", txt):
        _c, link = rodar(["iw", "dev", placa, "link"], timeout=5)
        d = ler_iw_link(link)
        item = {"placa": placa, "conectado": "bssid" in d}
        if item["conectado"]:
            rssi = int(d["rssi"]) if d.get("rssi") else None
            canal, banda = canal_wifi(int(float(d.get("freq") or 0) * 1000)) if d.get("freq") else (None, None)
            item.update(ssid=d.get("ssid", ""), bssid=d["bssid"], padrao=_padrao((d.get("tx") or ("", ""))[1]),
                        sinal=_qualidade(rssi), rssi=rssi, canal=canal, banda=banda,
                        rx_mbps=float(d["rx"][0]) if d.get("rx") else None, tx_mbps=float(d["tx"][0]) if d.get("tx") else None)
            _c, sc = rodar(["iw", "dev", placa, "scan", "dump"], timeout=10)
            redes = ler_iw_scan(sc)
            if redes:
                item["mesmo_canal"] = [r for r in redes if r["canal"] == canal and r["bssid"] != d["bssid"]]
                item["canais_vizinhos"] = [r for r in redes if r["canal"] and canal and r["banda"] == banda
                                           and 0 < abs(r["canal"] - canal) < 5 and banda == "2,4 GHz"]
                item["redes_visiveis"] = len(redes)
        out.append(item)
    return out


# ---------------------------------------------------------------- DNS
def servidores_dns():
    """Servidores DNS de verdade (os do systemd-resolved, não o 127.0.0.53 local), sem repetir."""
    out = []
    c, txt = rodar(["resolvectl", "dns"], timeout=5)
    fontes = [txt] if c == 0 else []
    from . import ler
    fontes.append("\n".join(l.split()[1] for l in (ler("/etc/resolv.conf", "") or "").splitlines()
                            if l.startswith("nameserver") and len(l.split()) > 1))
    for t in fontes:
        for tok in re.findall(r"[0-9a-fA-F:.%]+", t):
            try:
                ip = str(ipaddress.ip_address(tok.split("%")[0]))
            except ValueError:
                continue
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
        if out:
            break
    return out
