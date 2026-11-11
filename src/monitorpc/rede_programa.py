"""Monitoramento detalhado da rede de um programa (até 4 ao mesmo tempo, para comparar).

Mostra que a rede não é só "download e upload": taxa de transferência por segundo, pacotes, protocolos,
latência, jitter e perda, com o que mais estava usando a rede no mesmo segundo. Fontes, todas dentro do
serviço (administrador):

- ETW Kernel-Network (rede.MonitorRede): bytes e pacotes de cada fluxo do programa, por protocolo e destino,
  e o intervalo entre os pacotes UDP que chegam (jitter de chegada e maior silêncio).
- Socket bruto (SIO_RCVALL), só enquanto há programa vigiado: o cabeçalho RTP dos pacotes UDP que chegam
  (vai aberto até em chamada criptografada, SRTP). Dele saem o jitter da RFC 3550, a perda e a ordem errada
  pela numeração e o codec pelo payload type. O conteúdo (voz, vídeo) não é lido: só os 12 bytes do cabeçalho.
- GetPerTcpConnectionEStats: RTT e retransmissões de cada conexão TCP do programa.
- Sondas a cada segundo nos destinos que o programa está usando: ICMP; quem não responde a ping passa a ser
  medido por TCP (tempo de conexão) ou UDP (resposta ou "porta inacessível"). UDP sem devolução não é perda:
  aí entra o eco do servidor da CNT como referência da saída de internet, separado do programa.
- Concorrência: quanto cada outro programa usou a rede em cada segundo.
"""
import os
import re
import json
import time
import socket
import struct
import ipaddress
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import psutil

from . import config, qualidade, rede

WIN = os.name == "nt"
MAX_PROGRAMAS = 4
MAX_SEGUNDOS = 4 * 3600
MANTER_SALVAS = 30
DESTINOS_SONDADOS = 3
FALHAS_PARA_TROCAR = 5        # sem resposta ao ping com o programa usando o destino: passa a medir por TCP/UDP
SONDA_LENTA_S = 5             # TCP/UDP e o eco da VPS: um a cada 5 s (não incomodam o destino)

COLUNAS = ("hora", "env_Bps", "rec_Bps", "tcp_env_Bps", "tcp_rec_Bps", "udp_env_Bps", "udp_rec_Bps", "pacotes_env",
           "pacotes_rec", "latencia_ms", "sonda", "sonda_sem_resposta", "jitter_chegada_ms", "maior_silencio_ms",
           "tcp_rtt_ms", "tcp_retransmissoes", "rtp_jitter_ms", "rtp_perdidos", "rtp_fora_de_ordem", "mos", "conexoes",
           "destinos")
C = {k: i for i, k in enumerate(COLUNAS)}

LIMITES = ("O conteúdo do que o programa manda e recebe não é lido (nem dá: quase tudo é criptografado). "
           "Só tamanhos, horários e, nas chamadas, o cabeçalho RTP.",
           "Jitter e perda de verdade só existem em chamadas (RTP). Nos outros programas o jitter é o da chegada dos "
           "pacotes UDP, e a perda é a das sondas até os destinos dele.",
           "O MOS é estimado (modelo E simplificado), não é uma nota medida por quem ouviu a chamada.",
           "UDP sem resposta não é perda: muitos servidores e firewalls descartam calados. Quando isso acontece, a "
           "referência pelo servidor da CNT aparece separada, porque mede a sua saída de internet, não o destino.")

# ---------------------------------------------------------------- RTP (RFC 3550)
CODEC = {0: "G.711 µ-law (PCMU)", 3: "GSM", 4: "G.723", 8: "G.711 A-law (PCMA)", 9: "G.722",
         13: "ruído de conforto (CN)", 18: "G.729", 34: "H.263"}
RELOGIO_PT = {0: 8000, 3: 8000, 4: 8000, 8: 8000, 9: 8000, 13: 8000, 18: 8000, 34: 90000}
RELOGIOS = (8000, 16000, 48000, 90000)


def ler_rtp(dados):
    """(payload type, sequência, timestamp, ssrc) do cabeçalho RTP, ou None. Na mesma porta de uma chamada também
    passam STUN, DTLS e RTCP (RFC 7983): o primeiro byte separa, e o payload type 72–76 é RTCP."""
    if len(dados) < 12 or dados[0] >> 6 != 2:
        return None
    pt = dados[1] & 0x7F
    if 72 <= pt <= 76:
        return None
    seq, ts, ssrc = struct.unpack_from(">HII", dados, 2)
    return pt, seq, ts, ssrc


class FluxoRtp:
    """Um fluxo RTP recebido (um SSRC): perda e ordem pela numeração, jitter entre chegadas (RFC 3550, A.8)."""
    MIN_PACOTES = 20

    def __init__(self, pt, seq, ts, chegada):
        self.pt, self.base, self.max, self.ciclos = pt, seq, seq, 0
        self.recebidos, self.fora, self.duplicados = 1, 0, 0
        self.jitter = 0.0                           # em unidades do relógio do RTP
        self.relogio = RELOGIO_PT.get(pt)
        self._ini = (chegada, ts)
        self._ult = (chegada, ts)
        self.visto = chegada

    def _hz(self):
        """Relógio do RTP: fixo nos codecs com payload type próprio; nos dinâmicos (Opus, vídeo) é estimado pela
        velocidade com que o timestamp anda e arredondado para o relógio padrão mais próximo."""
        if self.relogio:
            return self.relogio
        dt = self._ult[0] - self._ini[0]
        if dt < 1:
            return None
        hz = ((self._ult[1] - self._ini[1]) & 0xFFFFFFFF) / dt
        perto = min(RELOGIOS, key=lambda r: abs(r - hz))
        if abs(perto - hz) <= 0.15 * perto:
            self.relogio = perto
        return self.relogio

    def add(self, seq, ts, chegada):
        self.visto = chegada
        d = (seq - self.max) & 0xFFFF
        if d == 0:
            self.duplicados += 1
            return
        self.recebidos += 1
        if d < 3000:                               # adiante (com folga para pacotes perdidos)
            if seq < self.max:
                self.ciclos += 1                   # a numeração deu a volta (65535 -> 0)
            self.max = seq
        else:
            self.fora += 1                         # chegou depois de um mais novo
        hz = self._hz()
        if hz:
            dts = ((ts - self._ult[1] + 2 ** 31) & 0xFFFFFFFF) - 2 ** 31
            dif = (chegada - self._ult[0]) * hz - dts
            self.jitter += (abs(dif) - self.jitter) / 16
        self._ult = (chegada, ts)

    @property
    def esperados(self):
        return self.ciclos * 65536 + self.max - self.base + 1

    @property
    def perdidos(self):
        return max(0, self.esperados - self.recebidos)

    @property
    def jitter_ms(self):
        hz = self._hz()
        return round(self.jitter / hz * 1000, 2) if hz else None

    def valido(self):
        """Parece RTP de verdade: pacotes suficientes e numeração andando em ordem quase sempre."""
        return self.recebidos >= self.MIN_PACOTES and self.fora <= self.recebidos * 0.2 and self.esperados <= 4 * self.recebidos

    def resumo(self):
        hz = self._hz()
        codec = CODEC.get(self.pt) or (f"dinâmico {self.pt} ({'Opus, provável' if hz == 48000 else 'vídeo, provável' if hz == 90000 else 'relógio ' + str(hz) if hz else 'relógio ?'})")
        return {"pt": self.pt, "codec": codec, "relogio": hz, "recebidos": self.recebidos, "esperados": self.esperados,
                "perdidos": self.perdidos, "perda": round(self.perdidos * 100 / self.esperados, 2) if self.esperados else 0,
                "fora_de_ordem": self.fora, "duplicados": self.duplicados, "jitter_ms": self.jitter_ms}


class CapturaUDP:
    """Datagramas UDP de todas as placas IPv4 por socket bruto (SIO_RCVALL), só enquanto há programa vigiado.
    Lê só os 12 primeiros bytes de dados de cada datagrama (o cabeçalho RTP)."""

    def __init__(self, ao_pacote):
        self.ao_pacote = ao_pacote
        self.ok = False
        self.erro = None
        self._socks = []

    def iniciar(self):
        if not WIN or self.ok:
            return self.ok
        locais = []
        try:
            ligadas = psutil.net_if_stats()
            for nome, ends in psutil.net_if_addrs().items():
                if nome in ligadas and not ligadas[nome].isup:
                    continue
                locais += [e.address for e in ends if e.family == socket.AF_INET and not e.address.startswith(("127.", "169.254."))]
        except Exception as e:  # noqa: BLE001
            self.erro = str(e)
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
            threading.Thread(target=self._ler, args=(s, socket.inet_aton(ip)), daemon=True, name="captura-udp").start()
        self.ok = bool(self._socks)
        if not self.ok and not self.erro:
            self.erro = "Nenhuma placa de rede IPv4 ligada."
        return self.ok

    def parar(self):
        self.ok = False
        for s in self._socks:
            try:
                s.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except OSError:
                pass
            s.close()
        self._socks = []

    def _ler(self, s, meu):
        while self.ok:
            try:
                p = s.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            pacote = analisar_ipv4(p)
            if pacote and pacote[1] == meu:          # só o que chega a este PC (destino = esta placa)
                try:
                    self.ao_pacote(time.perf_counter(), *pacote)
                except Exception:  # noqa: BLE001
                    pass


def analisar_ipv4(p):
    """Pacote IPv4 bruto -> (origem, destino, porta origem, porta destino, 12 primeiros bytes de dados) se for UDP
    não fragmentado; senão None."""
    if len(p) < 28 or p[0] >> 4 != 4 or p[9] != 17:
        return None
    if struct.unpack_from(">H", p, 6)[0] & 0x1FFF:          # pedaço de um datagrama fragmentado
        return None
    ihl = (p[0] & 15) * 4
    if len(p) < ihl + 8:
        return None
    sport, dport = struct.unpack_from(">HH", p, ihl)
    return p[12:16], p[16:20], sport, dport, p[ihl + 8:ihl + 20]


# ---------------------------------------------------------------- monitoramento
def _publico(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_multicast or a.is_unspecified or a.is_link_local)


class Monitor:
    """Sessão de monitoramento de até 4 programas. O motor chama add_fluxos() a cada amostra; uma thread própria
    fecha cada segundo (sondas, TCP, RTP, concorrência) e guarda uma linha por programa."""

    def __init__(self, pids_de, uso_programas, conexoes, ping=None, tcp=None, udp=None, eco=None, estats=None,
                 captura=True, pasta=None, maquina=None, agora=time.time, automatico=True):
        self.pids_de = pids_de                       # nome do programa -> [pids]
        self.uso_programas = uso_programas           # -> [(nome, env, rec)] agora
        self.conexoes = conexoes                     # -> [{pid, proto, lip, lport, rip, rport, estado}]
        self.ping = ping or (lambda ip: qualidade.ping(ip, 800)["rtt"])
        self.tcp = tcp or (lambda ip, porta: qualidade.sonda_tcp(ip, porta, 1.5))
        self.udp = udp or (lambda ip, porta: qualidade.sonda_udp(ip, porta, 1.0))
        self.eco = eco or (lambda seq: qualidade.sonda_eco(seq))
        self.estats = estats or qualidade.tcp_estats
        self.pasta = pasta or os.path.join(config.BASE, "analises_programas")
        self.maquina = maquina or (lambda: {})
        self.agora = agora
        self.automatico = automatico     # False nos testes: cada segundo é fechado chamando passo()
        self.lock = threading.Lock()
        self.sessao = None
        self._acc = {}             # programa -> soma do segundo em andamento
        self._pid_prog = {}
        self._porta_prog = {}      # porta UDP local -> programa (para o RTP)
        self._rtp = {}             # programa -> {(porta local, ip remoto, porta remota, ssrc): FluxoRtp}
        self._rtp_ant = {}         # chave -> (perdidos, fora) no fim do segundo anterior
        self._retr_ant = {}        # conexão TCP -> retransmissões já contadas
        self._sondas = {}          # (programa, ip) -> {"modo", "falhas", "porta", "ult_lenta"}
        self._ex = ThreadPoolExecutor(max_workers=12, thread_name_prefix="rede-programa")
        self._captura = CapturaUDP(self._pacote_udp) if captura else None
        self._seq_eco = 0

    # ------------------------------------------------------------ comandos
    def adicionar(self, nome):
        nome = str(nome or "").strip()[:120]
        if not nome:
            return False, "Escolha um programa."
        with self.lock:
            if self.sessao and nome in self.sessao["programas"]:
                return True, f"{nome} já está sendo monitorado."
            if self.sessao and len(self.sessao["programas"]) >= MAX_PROGRAMAS:
                return False, f"Dá para monitorar até {MAX_PROGRAMAS} programas ao mesmo tempo. Tire um antes."
            if not self.sessao:
                self.sessao = {"id": time.strftime("%Y%m%d-%H%M%S"), "inicio": self.agora(), "programas": [],
                               "linhas": {}, "concorrencia": [], "destinos": {}, "vps": [], "rtp": {}, "maquina": self.maquina(),
                               "colunas": list(COLUNAS)}
                if self.automatico:
                    threading.Thread(target=self._loop, args=(self.sessao,), daemon=True, name="rede-programa").start()
                if self._captura:
                    self._ex.submit(self._captura.iniciar)
            s = self.sessao
            s["programas"].append(nome)
            s["linhas"][nome], s["destinos"][nome], s["rtp"][nome] = [], {}, {}
            self._acc[nome] = self._novo_acc()
            self._rtp[nome] = {}
        return True, f"Monitorando {nome}."

    def remover(self, nome):
        with self.lock:
            if not self.sessao or nome not in self.sessao["programas"]:
                return False, "Esse programa não está sendo monitorado."
            if len(self.sessao["programas"]) == 1:
                return False, "É o único programa: use Parar."
            self.sessao["programas"].remove(nome)     # as linhas dele ficam (entram no .log)
        return True, f"{nome} saiu do monitoramento."

    def parar(self):
        with self.lock:
            s, self.sessao = self.sessao, None
        if self._captura:
            self._captura.parar()
        if not s:
            return None
        s["fim"] = self.agora()
        s["linhas"] = {p: ls for p, ls in s["linhas"].items() if ls}     # tirado antes de medir: não fica
        s["programas"] = list(s["linhas"])
        for nome in s["linhas"]:
            s["rtp"][nome] = [dict(f.resumo(), ssrc=f"{k[3]:08x}", porta_local=k[0], ip=k[1], porta=k[2])
                              for k, f in self._rtp.get(nome, {}).items() if f.valido()]
        self._rtp, self._acc, self._sondas = {}, {}, {}
        os.makedirs(self.pasta, exist_ok=True)
        with open(os.path.join(self.pasta, s["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(s, f)
        try:
            arqs = sorted(a for a in os.listdir(self.pasta) if a.endswith(".json"))
            for a in arqs[:-MANTER_SALVAS]:
                os.remove(os.path.join(self.pasta, a))
        except OSError:
            pass
        return s["id"]

    # ------------------------------------------------------------ dados que chegam
    @staticmethod
    def _novo_acc():
        return {"b": [0] * 8, "var": 0.0, "nvar": 0, "gap": 0.0, "dest": {}}

    def add_fluxos(self, t, fluxos):
        """Fluxos do ETW desde a última amostra: (pid, ip, porta remota, porta local, udp, env, rec, pk_env, pk_rec,
        variação ms, n, maior intervalo ms)."""
        if not self.sessao:
            return
        pp = self._pid_prog
        with self.lock:
            for pid, ip, p_rem, p_loc, udp, env, rec, pe, pr, var, nvar, gap in fluxos:
                prog = pp.get(pid)
                a = self._acc.get(prog) if prog else None
                if a is None:
                    continue
                b = a["b"]
                b[0] += env
                b[1] += rec
                b[4 if udp else 2] += env
                b[5 if udp else 3] += rec
                b[6] += pe
                b[7] += pr
                a["var"] += var
                a["nvar"] += nvar
                a["gap"] = max(a["gap"], gap)
                d = a["dest"].setdefault(ip, [0, 0, set(), set()])
                d[0] += env
                d[1] += rec
                (d[3] if udp else d[2]).add(p_rem)

    def _pacote_udp(self, chegada, origem, _destino, sport, dport, cab):
        prog = self._porta_prog.get(dport)
        if not prog:
            return
        r = ler_rtp(cab)
        if not r:
            return
        pt, seq, ts, ssrc = r
        k = (dport, socket.inet_ntoa(origem), sport, ssrc)
        fluxos = self._rtp.get(prog)
        if fluxos is None:
            return
        f = fluxos.get(k)
        if f is None:
            if len(fluxos) < 64:
                fluxos[k] = FluxoRtp(pt, seq, ts, chegada)
            return
        f.add(seq, ts, chegada)

    # ------------------------------------------------------------ um segundo
    def _loop(self, s):
        while self.sessao is s:            # uma thread por sessão: termina sozinha quando a sessão para
            ini = time.time()
            try:
                self.passo(self.agora())
            except Exception as e:  # noqa: BLE001
                print("rede por programa:", e)
            time.sleep(max(0.05, 1 - (time.time() - ini)))

    def passo(self, t):
        with self.lock:
            s = self.sessao
            if not s:
                return
            progs = list(s["programas"])
            accs = {p: self._acc.get(p) or self._novo_acc() for p in progs}
            for p in progs:
                self._acc[p] = self._novo_acc()
        pid_prog = {}
        for p in progs:
            for pid in self.pids_de(p) or []:
                pid_prog.setdefault(pid, p)
        self._pid_prog = pid_prog
        conns = [c for c in (self.conexoes() or []) if c.get("pid") in pid_prog]
        self._porta_prog = {c["lport"]: pid_prog[c["pid"]] for c in conns if str(c.get("proto", "")).startswith("UDP")}

        sondas = {}
        for p in progs:
            dest = s["destinos"][p]
            for ip, (e, r, tcp_p, udp_p) in accs[p]["dest"].items():
                x = dest.setdefault(ip, {"env": 0, "rec": 0, "tcp": [], "udp": [], "host": None, "modo": "icmp", "rtt": None})
                x["env"] += e
                x["rec"] += r
                x["tcp"] = sorted(set(x["tcp"]) | tcp_p)[:8]
                x["udp"] = sorted(set(x["udp"]) | udp_p)[:8]
            ativos = sorted((ip for ip, v in accs[p]["dest"].items() if _publico(ip) and v[0] + v[1] > 0),
                            key=lambda ip: -(accs[p]["dest"][ip][0] + accs[p]["dest"][ip][1]))[:DESTINOS_SONDADOS]
            for ip in ativos:
                sondas[(p, ip)] = self._ex.submit(self._sondar, p, ip, dest[ip], t)
        res = {}
        for k, fut in sondas.items():
            try:
                res[k] = fut.result(timeout=2.5)
            except Exception:  # noqa: BLE001
                res[k] = (None, None)

        outros = {}
        try:
            for nome, env, rec in self.uso_programas() or []:
                if nome not in progs and env + rec > 0:
                    outros[nome] = round(env + rec)
        except Exception:  # noqa: BLE001
            pass
        top = dict(sorted(outros.items(), key=lambda kv: -kv[1])[:6])

        with self.lock:
            if self.sessao is not s:
                return
            for p in progs:
                a, b = accs[p], accs[p]["b"]
                medidas = [res[k] for k in res if k[0] == p and res[k][0] is not None]
                rtts = [m[1] for m in medidas if m[1] is not None]
                modo = medidas[0][0] if medidas else None
                tcp_rtt, retr = self._tcp(p, [c for c in conns if pid_prog[c["pid"]] == p])
                rj, rperd, rfora = self._rtp_segundo(p)
                linha = [round(t, 1), *b[:6], b[6], b[7],
                         round(min(rtts), 1) if rtts else None, modo, 1 if medidas and not rtts else 0,
                         round(a["var"] / a["nvar"], 2) if a["nvar"] >= 3 else None, round(a["gap"], 1) if a["gap"] else None,
                         tcp_rtt, retr, rj, rperd, rfora, None,
                         sum(1 for c in conns if pid_prog[c["pid"]] == p), len([ip for ip in a["dest"] if _publico(ip)])]
                ls = s["linhas"][p]
                ls.append(linha)
                linha[C["mos"]] = mos_janela(ls[-10:])
                if len(ls) > MAX_SEGUNDOS:
                    del ls[0]
            s["concorrencia"].append([round(t, 1), top])
            if len(s["concorrencia"]) > MAX_SEGUNDOS:
                del s["concorrencia"][0]
        self._referencia_vps(s, t)

    def _sondar(self, prog, ip, d, t):
        """Uma sonda ao destino. Devolve (modo, rtt ms | None) ou (None, None) quando é vez de não sondar."""
        st = self._sondas.setdefault((prog, ip), {"modo": "icmp", "falhas": 0, "ult": 0.0})
        if st["modo"] == "icmp":
            rtt = self.ping(ip)
            if rtt is None:
                st["falhas"] += 1
                if st["falhas"] >= FALHAS_PARA_TROCAR:     # não responde a ping: mede pelo próprio protocolo do programa
                    if d["tcp"]:
                        st.update(modo="tcp", porta=d["tcp"][0])
                    elif d["udp"]:
                        st.update(modo="udp", porta=d["udp"][0], silencios=0)
            else:
                st["falhas"] = 0
            d["modo"], d["rtt"] = st["modo"], rtt if rtt is not None else d.get("rtt")
            return "icmp", rtt
        if st["modo"] == "sem_devolucao" or t - st["ult"] < SONDA_LENTA_S:
            return None, None
        st["ult"] = t
        r = (self.tcp if st["modo"] == "tcp" else self.udp)(ip, st["porta"])
        if st["modo"] == "udp":
            if r["tipo"] == "silencio":
                st["silencios"] = st.get("silencios", 0) + 1
                if st["silencios"] >= 3 and not st.get("respondeu"):
                    st["modo"] = "sem_devolucao"             # nunca devolveu nada: não é perda, é filtro ou descarte
            elif r["rtt"] is not None:
                st["respondeu"] = True
        d["modo"] = st["modo"]
        if r["rtt"] is not None:
            d["rtt"] = r["rtt"]
        return (st["modo"] if st["modo"] != "sem_devolucao" else "udp"), r["rtt"]

    def _tcp(self, prog, conns):
        """RTT médio (suavizado pelo Windows) das conexões TCP abertas e retransmissões novas neste segundo."""
        rtts, novas = [], 0
        for c in [c for c in conns if c.get("proto") == "TCP" and c.get("estado") == "Conectado"][:8]:
            try:
                e = self.estats(c["lip"], c["lport"], c["rip"], c["rport"])
            except Exception:  # noqa: BLE001
                e = None
            if not e:
                continue
            if e.get("rtt") is not None:
                rtts.append(e["rtt"])
            k = (c["lip"], c["lport"], c["rip"], c["rport"])
            r = e.get("retransmissoes") or 0
            novas += max(0, r - self._retr_ant.get(k, r))
            self._retr_ant[k] = r
        if len(self._retr_ant) > 5000:
            self._retr_ant.clear()
        return (round(sum(rtts) / len(rtts), 1) if rtts else None), novas

    def _rtp_segundo(self, prog):
        """Jitter do fluxo RTP mais movimentado e o que se perdeu/chegou fora de ordem neste segundo."""
        fl = [(k, f) for k, f in list(self._rtp.get(prog, {}).items()) if f.valido()]
        if not fl:
            return None, None, None
        agora = time.perf_counter()
        ativos = [(k, f) for k, f in fl if agora - f.visto < 3]
        perd = fora = 0
        for k, f in fl:
            ap, af = self._rtp_ant.get(k, (0, 0))
            perd += f.perdidos - ap
            fora += f.fora - af
            self._rtp_ant[k] = (f.perdidos, f.fora)
        if not ativos:
            return None, max(0, perd), max(0, fora)
        principal = max(ativos, key=lambda kf: kf[1].recebidos)[1]
        return principal.jitter_ms, max(0, perd), max(0, fora)

    def _referencia_vps(self, s, t):
        """Quando algum destino UDP nunca devolve nada, mede a saída UDP pelo eco do servidor da CNT (a cada 5 s)."""
        if not any(st["modo"] == "sem_devolucao" for st in self._sondas.values()):
            return
        if s["vps"] and t - s["vps"][-1][0] < SONDA_LENTA_S:
            return
        self._seq_eco += 1
        r = self.eco(self._seq_eco)
        with self.lock:
            if self.sessao is s:
                s["vps"].append([round(t, 1), r.get("rtt")])

    # ------------------------------------------------------------ leitura
    def ao_vivo(self, janela=300):
        with self.lock:
            s = self.sessao
            if not s:
                return {"ativo": False, "limites": LIMITES}
            out = {"ativo": True, "id": s["id"], "inicio": s["inicio"], "segundos": max(len(v) for v in s["linhas"].values()),
                   "programas": [], "concorrencia": s["concorrencia"][-janela:], "vps": resumo_vps(s["vps"]),
                   "captura_rtp": {"ok": bool(self._captura and self._captura.ok), "erro": self._captura.erro if self._captura else None},
                   "colunas": list(COLUNAS), "limites": LIMITES}
            for p in s["programas"]:
                rtp = [dict(f.resumo(), ssrc=f"{k[3]:08x}", ip=k[1], porta=k[2]) for k, f in list(self._rtp.get(p, {}).items()) if f.valido()]
                out["programas"].append({"nome": p, "resumo": resumir(s["linhas"][p], rtp), "destinos": destinos_top(s["destinos"][p]),
                                         "rtp": rtp, "serie": s["linhas"][p][-janela:]})
        return out

    def lista(self):
        out = []
        try:
            for a in sorted(os.listdir(self.pasta), reverse=True):
                if not a.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.pasta, a), encoding="utf-8") as f:
                        d = json.load(f)
                    out.append({"id": d["id"], "inicio": d["inicio"], "fim": d.get("fim"), "programas": list(d["linhas"]),
                                "segundos": max((len(v) for v in d["linhas"].values()), default=0)})
                except (OSError, ValueError, KeyError):
                    continue
        except OSError:
            pass
        return out

    def _carregar(self, ident):
        if not ident or not re.fullmatch(r"[0-9-]{8,20}", ident):
            return None
        try:
            with open(os.path.join(self.pasta, ident + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def abrir(self, ident, pontos=900):
        d = self._carregar(ident)
        if not d:
            return None
        progs = []
        for p, ls in d["linhas"].items():
            passo = max(1, -(-len(ls) // pontos))
            progs.append({"nome": p, "resumo": resumir(ls, d["rtp"].get(p, [])), "destinos": destinos_top(d["destinos"].get(p, {})),
                          "rtp": d["rtp"].get(p, []), "serie": reduzir(ls, passo), "passo": passo})
        passo = max(1, -(-len(d["concorrencia"]) // pontos))
        return {"ativo": False, "id": d["id"], "inicio": d["inicio"], "fim": d.get("fim"), "programas": progs,
                "concorrencia": d["concorrencia"][::passo], "vps": resumo_vps(d["vps"]), "colunas": d.get("colunas", list(COLUNAS)),
                "segundos": max((len(v) for v in d["linhas"].values()), default=0), "limites": LIMITES}

    def apagar(self, ident):
        if not self._carregar(ident):
            return False
        try:
            os.remove(os.path.join(self.pasta, ident + ".json"))
            return True
        except OSError:
            return False

    def exportar(self, ident):
        """(nome do arquivo, texto do .log) de uma sessão salva, ou da que está gravando com ident "atual"."""
        if ident == "atual":
            with self.lock:
                d = json.loads(json.dumps(self.sessao)) if self.sessao else None
            if d:
                d["fim"] = self.agora()
                d["rtp"] = {p: [dict(f.resumo(), ssrc=f"{k[3]:08x}", ip=k[1], porta=k[2]) for k, f in list(self._rtp.get(p, {}).items())
                                if f.valido()] for p in d["linhas"]}
        else:
            d = self._carregar(ident)
        if not d:
            return None
        return f"cnt-monitor-rede-{d['id']}.log", texto_log(d)


# ---------------------------------------------------------------- cálculos (puros)
def mos_janela(linhas):
    """MOS dos últimos segundos: latência das sondas (ou RTT TCP), jitter do RTP (ou de chegada) e perda do RTP
    (ou das sondas)."""
    lat = [l[C["latencia_ms"]] for l in linhas if l[C["latencia_ms"]] is not None] or \
          [l[C["tcp_rtt_ms"]] for l in linhas if l[C["tcp_rtt_ms"]] is not None]
    if not lat:
        return None
    jit = [l[C["rtp_jitter_ms"]] for l in linhas if l[C["rtp_jitter_ms"]] is not None] or \
          [l[C["jitter_chegada_ms"]] for l in linhas if l[C["jitter_chegada_ms"]] is not None]
    pk = sum(l[C["pacotes_rec"]] for l in linhas)
    perdidos = sum(l[C["rtp_perdidos"]] or 0 for l in linhas)
    if any(l[C["rtp_perdidos"]] is not None for l in linhas):
        perda = perdidos * 100 / (pk + perdidos) if pk + perdidos else 0
    else:
        sond = [l for l in linhas if l[C["sonda"]]]
        perda = sum(l[C["sonda_sem_resposta"]] for l in sond) * 100 / len(sond) if sond else 0
    return qualidade.mos(qualidade.percentil(lat, 50), sum(jit) / len(jit) if jit else 0, perda)[0]


def resumir(linhas, rtp=()):
    if not linhas:
        return {"segundos": 0}
    n = len(linhas)
    col = lambda k: [l[C[k]] for l in linhas]  # noqa: E731
    sond = [l for l in linhas if l[C["sonda"]]]
    lat = qualidade.resumo([l[C["latencia_ms"]] for l in sond]) if sond else None
    jc = [v for v in col("jitter_chegada_ms") if v is not None]
    rj = [v for v in col("rtp_jitter_ms") if v is not None]
    tr = [v for v in col("tcp_rtt_ms") if v is not None]
    mos = [v for v in col("mos") if v is not None]
    rec_rtp = sum(f["recebidos"] for f in rtp)
    perd_rtp = sum(f["perdidos"] for f in rtp)
    out = {"segundos": n, "env_medio": round(sum(col("env_Bps")) / n), "rec_medio": round(sum(col("rec_Bps")) / n),
           "env_pico": max(col("env_Bps")), "rec_pico": max(col("rec_Bps")),
           "env_total": sum(col("env_Bps")), "rec_total": sum(col("rec_Bps")),
           "tcp_total": sum(col("tcp_env_Bps")) + sum(col("tcp_rec_Bps")), "udp_total": sum(col("udp_env_Bps")) + sum(col("udp_rec_Bps")),
           "pps_medio": round((sum(col("pacotes_env")) + sum(col("pacotes_rec"))) / n, 1),
           "latencia": lat, "jitter_chegada": round(sum(jc) / len(jc), 2) if jc else None, "jitter_chegada_max": max(jc) if jc else None,
           "maior_silencio": max((v for v in col("maior_silencio_ms") if v is not None), default=None),
           "tcp_rtt": round(sum(tr) / len(tr), 1) if tr else None, "tcp_retransmissoes": sum(col("tcp_retransmissoes")),
           "rtp_jitter": round(sum(rj) / len(rj), 2) if rj else None, "rtp_jitter_max": max(rj) if rj else None,
           "rtp_perda": round(perd_rtp * 100 / (rec_rtp + perd_rtp), 2) if rtp and rec_rtp + perd_rtp else None,
           "rtp_fora_de_ordem": sum(f["fora_de_ordem"] for f in rtp) if rtp else None,
           "mos": round(sum(mos) / len(mos), 2) if mos else None, "mos_min": min(mos) if mos else None}
    out["nota"] = round(max(0, min(100, (out["mos"] - 1) / 3.4 * 100))) if out["mos"] else None
    return out


def destinos_top(dest, n=12):
    lista = [{"ip": ip, **{k: v for k, v in d.items() if k != "host"}} for ip, d in dest.items()]
    return sorted(lista, key=lambda x: -(x["env"] + x["rec"]))[:n]


def resumo_vps(serie):
    if not serie:
        return None
    r = qualidade.resumo([x[1] for x in serie])
    r["serie"] = serie[-300:]
    r["servidor"] = qualidade.ECO_UDP[0]
    return r


def reduzir(linhas, passo):
    """Junta `passo` segundos em um ponto (soma nas taxas e contagens, máximo no silêncio, média no resto)."""
    if passo <= 1:
        return linhas
    somas = {C[k] for k in ("tcp_retransmissoes", "rtp_perdidos", "rtp_fora_de_ordem", "sonda_sem_resposta")}
    out = []
    for i in range(0, len(linhas), passo):
        bloco = linhas[i:i + passo]
        nova = [bloco[0][0]]
        for c in range(1, len(COLUNAS)):
            vs = [l[c] for l in bloco if l[c] is not None]
            if c == C["sonda"]:
                nova.append(next((v for v in vs if v), None))
            elif not vs:
                nova.append(None)
            elif c in somas:
                nova.append(sum(vs))
            elif c == C["maior_silencio_ms"]:
                nova.append(max(vs))
            else:
                nova.append(round(sum(vs) / len(vs), 2))
        out.append(nova)
    return out


def _hora(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def texto_log(d):
    """O .log que o técnico leva: cabeçalho com o resumo de cada programa e uma linha por segundo (separada por TAB,
    abre direto em planilha)."""
    m = d.get("maquina") or {}
    dur = round((d.get("fim") or d["inicio"]) - d["inicio"])
    ls = [f"# CNT Monitor {config.VERSAO} — monitoramento de rede por programa",
          f"# Computador: {m.get('maquina', '?')} · {m.get('sistema', '?')}",
          f"# Início: {_hora(d['inicio'])} · fim: {_hora(d.get('fim') or d['inicio'])} · duração: {dur // 3600:02d}:{dur // 60 % 60:02d}:{dur % 60:02d}",
          f"# Programas: {'; '.join(d['linhas'])}", "#"]
    for p, linhas in d["linhas"].items():
        rtp = d.get("rtp", {}).get(p, [])
        r = resumir(linhas, rtp)
        lat = r.get("latencia") or {}
        ls.append(f"# == {p} ==")
        if r["segundos"]:
            ls.append(f"# Taxa: ↑ média {r['env_medio']} B/s, pico {r['env_pico']} B/s · ↓ média {r['rec_medio']} B/s, pico "
                      f"{r['rec_pico']} B/s · {r['pps_medio']} pacotes/s · TCP {r['tcp_total']} B · UDP {r['udp_total']} B")
            ls.append(f"# Latência (sondas): mín {_fmt(lat.get('min'))} · mediana {_fmt(lat.get('mediana'))} · P95 {_fmt(lat.get('p95'))} · "
                      f"máx {_fmt(lat.get('max'))} ms · jitter {_fmt(lat.get('jitter'))} ms · perda {_fmt(lat.get('perda'))}%")
            ls.append(f"# Jitter de chegada (UDP): médio {_fmt(r['jitter_chegada'])} ms, máx {_fmt(r['jitter_chegada_max'])} ms · "
                      f"maior silêncio {_fmt(r['maior_silencio'])} ms · TCP: RTT {_fmt(r['tcp_rtt'])} ms, {r['tcp_retransmissoes']} retransmissões")
            ls.append(f"# MOS estimado: médio {_fmt(r['mos'])}, pior {_fmt(r['mos_min'])}")
        for f in rtp:
            ls.append(f"# RTP {f.get('ssrc', '')} de {f.get('ip', '')}:{f.get('porta', '')} · {f['codec']} · {f['recebidos']} pacotes · "
                      f"perda {_fmt(f['perda'])}% · fora de ordem {f['fora_de_ordem']} · jitter {_fmt(f['jitter_ms'])} ms (RFC 3550)")
        for x in destinos_top(d.get("destinos", {}).get(p, {})):
            ls.append(f"# Destino {x['ip']} · ↑ {x['env']} B · ↓ {x['rec']} B · TCP {x.get('tcp')} · UDP {x.get('udp')} · sonda {x.get('modo')}")
        ls.append("#")
    v = resumo_vps(d.get("vps") or [])
    if v:
        ls.append(f"# Referência UDP pelo servidor da CNT ({v['servidor']}), rota diferente: mede a saída de internet, não os destinos: "
                  f"mediana {_fmt(v['mediana'])} ms · jitter {_fmt(v['jitter'])} ms · perda {_fmt(v['perda'])}%")
        ls.append("#")
    for x in LIMITES:
        ls.append("# " + x)
    ls.append("#")
    ls.append("\t".join(("programa", *COLUNAS, "outros_programas_Bps")))
    conc = {int(t): o for t, o in d.get("concorrencia", [])}
    for p, linhas in d["linhas"].items():
        for l in linhas:
            outros = "; ".join(f"{k}={v}" for k, v in (conc.get(int(l[0])) or {}).items())
            ls.append("\t".join((p, _hora(l[0]), *(_fmt(x) for x in l[1:]), outros)))
    return "\n".join(ls) + "\n"


if not WIN:     # Linux: AF_PACKET no lugar do SIO_RCVALL
    from .linux.rede import CapturaUDP  # noqa: E402,F401,F811
