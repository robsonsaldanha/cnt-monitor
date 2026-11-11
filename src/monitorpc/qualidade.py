"""Qualidade da rede medida a partir deste PC.

- Ping ICMP (IPv4/IPv6), traceroute e Path MTU pela API ICMP do Windows (iphlpapi).
- Estatísticas de latência: mínimo, média, mediana, máximo, P95, P99, desvio-padrão, jitter e perda (inclusive em rajada).
- TCP por conexão (RTT, retransmissões, ACKs duplicados, timeouts, RTO, MSS): GetPerTcpConnectionEStats (precisa de admin).
- Contadores do sistema: TCP, UDP e IP (GetTcpStatisticsEx etc.) e placas de rede (psutil).
- Wi-Fi: sinal, RSSI, taxa, canal e redes no mesmo canal (wlanapi).
- DNS (consulta direta aos servidores do Windows), HTTP/TLS (tempos de cada etapa) e teste de velocidade (Cloudflare).
- País e operadora (AS) do IP com a base DB-IP Lite embutida (CC BY 4.0), sem consultar a internet.
- Sondas: pinga só os IPs que alguém está olhando, e a "Análise de rede", que grava uma captura contínua.
- Teste real até um destino por ICMP, TCP (tempo de conexão) ou UDP. No UDP, silêncio não é perda: só vira perda
  quando o destino responde a outras sondas; se nunca responde, a referência é o eco UDP do servidor da CNT.
"""
import os
import ssl
import json
import math
import time
import uuid
import random
import socket
import struct
import ipaddress
import threading
import statistics
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import psutil

from . import config

WIN = os.name == "nt"

# códigos de estado da API ICMP (ipexport.h)
IP_SUCCESS = 0
IP_DEST_NET_UNREACHABLE = 11002
IP_DEST_HOST_UNREACHABLE = 11003
IP_DEST_PORT_UNREACHABLE = 11005
IP_PACKET_TOO_BIG = 11009
IP_REQ_TIMED_OUT = 11010
IP_TTL_EXPIRED_TRANSIT = 11013
STATUS = {IP_SUCCESS: "ok", IP_DEST_NET_UNREACHABLE: "rede inacessível", IP_DEST_HOST_UNREACHABLE: "destino inacessível",
          IP_DEST_PORT_UNREACHABLE: "porta inacessível", IP_PACKET_TOO_BIG: "pacote grande demais",
          IP_REQ_TIMED_OUT: "sem resposta", IP_TTL_EXPIRED_TRANSIT: "salto"}


# ================================================================ cálculos (puros, testáveis)
def percentil(vals, p):
    """Percentil p (0–100) com interpolação linear; vals não precisa estar ordenado."""
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    i = int(k)
    return s[i] if i + 1 >= len(s) else s[i] + (s[i + 1] - s[i]) * (k - i)


def resumo(amostras):
    """amostras: RTTs em ms, com None para pacote perdido, na ordem em que foram medidos."""
    n = len(amostras)
    ok = [a for a in amostras if a is not None]
    perdidos = n - len(ok)
    rajada = atual = 0
    for a in amostras:
        atual = atual + 1 if a is None else 0
        rajada = max(rajada, atual)
    difs = [abs(b - a) for a, b in zip(amostras, amostras[1:]) if a is not None and b is not None]
    r2 = lambda v: None if v is None else round(v, 2)  # noqa: E731
    return {"n": n, "perdidos": perdidos, "perda": round(perdidos * 100 / n, 2) if n else None, "perda_rajada": rajada,
            "min": r2(min(ok) if ok else None), "media": r2(sum(ok) / len(ok) if ok else None),
            "mediana": r2(percentil(ok, 50)), "max": r2(max(ok) if ok else None),
            "p95": r2(percentil(ok, 95)), "p99": r2(percentil(ok, 99)),
            "desvio": r2(statistics.pstdev(ok) if len(ok) > 1 else (0.0 if ok else None)),
            "jitter": r2(sum(difs) / len(difs) if difs else None), "jitter_max": r2(max(difs) if difs else None)}


def mos(latencia, jitter, perda):
    """MOS e fator R estimados pelo modelo E simplificado (o mesmo usado por PingPlotter e afins).
    latência efetiva = latência + 2·jitter + 10 ms; R = 93,2 − atraso − 2,5·perda%."""
    if latencia is None:
        return None, None
    ef = latencia + 2 * (jitter or 0) + 10
    r = 93.2 - (ef / 40 if ef < 160 else (ef - 120) / 10) - 2.5 * (perda or 0)
    r = max(0.0, min(100.0, r))
    return round(1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r), 2), round(r, 1)


def nota_bufferbloat(ms):
    """Aumento da latência sob carga -> nota (mesma escala dos testes de bufferbloat conhecidos)."""
    if ms is None:
        return None
    for limite, nota in ((5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")):
        if ms < limite:
            return nota
    return "F"


def pontuacoes(r, bufferbloat=None, disponibilidade=None):
    """Pontuações de 0 a 100 (maior = melhor), a partir de um resumo() e, se houver, bufferbloat e disponibilidade.
    - Tempo real: MOS de 1 a 4,4 levado para 0–100.
    - Estabilidade: 100 − (2·desvio + 0,5·jitter máximo + 5·maior rajada de perda).
    - Sem congestionamento: 100 − bufferbloat/2.
    - Confiabilidade: 100 − 20 × (% de tempo fora do ar).
    - Saúde: média das que existirem."""
    out = {}
    m, rf = mos(r.get("mediana"), r.get("jitter"), r.get("perda"))
    if m is not None:
        out["tempo_real"] = round(max(0, min(100, (m - 1) / 3.4 * 100)))
        out["mos"], out["fator_r"] = m, rf
    if r.get("desvio") is not None:
        out["estabilidade"] = round(max(0, 100 - min(100, 2 * r["desvio"] + 0.5 * (r.get("jitter_max") or 0)
                                                     + 5 * r.get("perda_rajada", 0))))
    if bufferbloat is not None:
        out["congestionamento"] = round(max(0, 100 - min(100, max(0, bufferbloat) / 2)))
    if disponibilidade is not None:
        out["confiabilidade"] = round(max(0, 100 - min(100, (100 - disponibilidade) * 20)))
    notas = [out[k] for k in ("tempo_real", "estabilidade", "congestionamento", "confiabilidade") if k in out]
    if notas:
        out["saude"] = round(sum(notas) / len(notas))
    return out


def quedas(fora, passo=1.0, minimo=3):
    """fora: lista de bool por segundo (True = sem resposta de nenhum destino da internet).
    Queda = pelo menos `minimo` segundos seguidos fora. Devolve disponibilidade, quedas, MTBF e MTTR."""
    eventos = []
    i = 0
    while i < len(fora):
        if fora[i]:
            j = i
            while j < len(fora) and fora[j]:
                j += 1
            if j - i >= minimo:
                eventos.append({"inicio": i, "dur": (j - i) * passo})
            i = j
        else:
            i += 1
    total = len(fora) * passo
    parado = sum(e["dur"] for e in eventos)
    return {"disponibilidade": round((total - parado) * 100 / total, 3) if total else None, "quedas": len(eventos),
            "tempo_fora": parado, "maior_queda": max((e["dur"] for e in eventos), default=0),
            "mttr": round(parado / len(eventos), 1) if eventos else None,
            "mtbf": round((total - parado) / len(eventos), 1) if eventos else None, "eventos": eventos[:200]}


def bufferbloat(linhas, ci, carga_i=1):
    """linhas [[t, carga, ..., rtt...]]: compara a mediana do RTT (coluna ci) com o link ocioso (≤10% do pico de carga)
    e sob carga (≥60% do pico, acima de 1 Mbit/s). Devolve (ocioso, carregado, aumento) em ms."""
    pico = max((l[carga_i] or 0 for l in linhas), default=0)
    if pico < 125_000:
        return None, None, None
    oc = [l[ci] for l in linhas if l[ci] is not None and (l[carga_i] or 0) <= 0.1 * pico]
    ca = [l[ci] for l in linhas if l[ci] is not None and (l[carga_i] or 0) >= 0.6 * pico]
    if len(oc) < 5 or len(ca) < 5:
        return None, None, None
    a, b = percentil(oc, 50), percentil(ca, 50)
    return round(a, 1), round(b, 1), round(b - a, 1)


def canal_wifi(freq_khz):
    """Frequência central (kHz) -> (canal, banda)."""
    f = freq_khz // 1000
    if 2412 <= f <= 2472:
        return (f - 2407) // 5, "2,4 GHz"
    if f == 2484:
        return 14, "2,4 GHz"
    if 5150 <= f <= 5895:
        return (f - 5000) // 5, "5 GHz"
    if 5925 <= f <= 7125:
        return (f - 5950) // 5, "6 GHz"
    return None, None


def alvo_valido(texto):
    """IP ou nome de host -> IP (str). Levanta ValueError com mensagem para o usuário."""
    t = str(texto or "").strip()
    if not t or len(t) > 253:
        raise ValueError("Informe um IP ou um nome.")
    try:
        return str(ipaddress.ip_address(t))
    except ValueError:
        pass
    if not all(c.isalnum() or c in "-." for c in t):
        raise ValueError(f"“{t}” não é um IP nem um nome válido.")
    try:
        return socket.getaddrinfo(t, None, proto=socket.IPPROTO_TCP)[0][4][0]
    except OSError:
        raise ValueError(f"Não consegui resolver “{t}”.") from None


# ================================================================ ICMP: ping, traceroute, MTU
if WIN:
    import ctypes
    from ctypes import c_ubyte, c_ushort, c_ulong, c_void_p, c_long, wintypes as wt

    _ip = ctypes.WinDLL("iphlpapi", use_last_error=True)

    class _OPCOES(ctypes.Structure):
        _fields_ = [("Ttl", c_ubyte), ("Tos", c_ubyte), ("Flags", c_ubyte), ("OptionsSize", c_ubyte), ("OptionsData", c_void_p)]

    class _RESPOSTA(ctypes.Structure):
        _fields_ = [("Address", c_ulong), ("Status", c_ulong), ("RoundTripTime", c_ulong), ("DataSize", c_ushort),
                    ("Reserved", c_ushort), ("Data", c_void_p), ("Options", _OPCOES)]

    class _SOCKADDR6(ctypes.Structure):
        _fields_ = [("family", c_ushort), ("port", c_ushort), ("flowinfo", c_ulong), ("addr", c_ubyte * 16), ("scope", c_ulong)]

    _ip.IcmpCreateFile.restype = wt.HANDLE
    _ip.Icmp6CreateFile.restype = wt.HANDLE
    _ip.IcmpCloseHandle.argtypes = [wt.HANDLE]
    _ip.IcmpSendEcho.argtypes = [wt.HANDLE, c_ulong, c_void_p, c_ushort, ctypes.POINTER(_OPCOES), c_void_p, c_ulong, c_ulong]
    _ip.IcmpSendEcho.restype = c_ulong
    _ip.Icmp6SendEcho2.argtypes = [wt.HANDLE, wt.HANDLE, c_void_p, c_void_p, ctypes.POINTER(_SOCKADDR6),
                                   ctypes.POINTER(_SOCKADDR6), c_void_p, c_ushort, ctypes.POINTER(_OPCOES), c_void_p,
                                   c_ulong, c_ulong]
    _ip.Icmp6SendEcho2.restype = c_ulong
    _INVALIDO = wt.HANDLE(-1).value


def ping(ip, timeout_ms=1000, ttl=128, tam=32, nao_fragmentar=False):
    """Um eco ICMP. Devolve {"rtt": ms | None, "status": código, "de": IP que respondeu | None}."""
    if not WIN:
        return {"rtt": None, "status": -1, "de": None}
    a = ipaddress.ip_address(ip)
    dados = (b"CNT-Monitor-" * (tam // 12 + 1))[:tam]
    op = _OPCOES(Ttl=ttl, Flags=2 if nao_fragmentar else 0)
    if a.version == 4:
        h = _ip.IcmpCreateFile()
        if not h or h == _INVALIDO:
            return {"rtt": None, "status": ctypes.get_last_error(), "de": None}
        try:
            buf = ctypes.create_string_buffer(ctypes.sizeof(_RESPOSTA) + tam + 64)
            n = _ip.IcmpSendEcho(h, struct.unpack("<I", a.packed)[0], dados, tam, ctypes.byref(op), buf, len(buf), timeout_ms)
            erro = ctypes.get_last_error()
            r = _RESPOSTA.from_buffer_copy(buf)
        finally:
            _ip.IcmpCloseHandle(h)
        if n == 0 and not r.Address:
            return {"rtt": None, "status": erro or IP_REQ_TIMED_OUT, "de": None}
        de = socket.inet_ntoa(struct.pack("<I", r.Address)) if r.Address else None
        return {"rtt": float(r.RoundTripTime) if r.Status in (IP_SUCCESS, IP_TTL_EXPIRED_TRANSIT) else None,
                "status": r.Status, "de": de}
    h = _ip.Icmp6CreateFile()
    if not h or h == _INVALIDO:
        return {"rtt": None, "status": ctypes.get_last_error(), "de": None}
    try:
        org = _SOCKADDR6(family=socket.AF_INET6)
        dst = _SOCKADDR6(family=socket.AF_INET6)
        dst.addr[:] = list(a.packed)
        buf = ctypes.create_string_buffer(40 + tam + 64)
        n = _ip.Icmp6SendEcho2(h, None, None, None, ctypes.byref(org), ctypes.byref(dst), dados, tam, ctypes.byref(op),
                               buf, len(buf), timeout_ms)
        erro = ctypes.get_last_error()
    finally:
        _ip.IcmpCloseHandle(h)
    # ICMPV6_ECHO_REPLY é empacotado: endereço (26 bytes: porta, flowinfo, 16 de IP, scope), Status, RoundTripTime
    raw = buf.raw
    end = raw[6:22]
    status, rtt = struct.unpack_from("<II", raw, 26)
    if n == 0 and not any(end):
        return {"rtt": None, "status": erro or IP_REQ_TIMED_OUT, "de": None}
    return {"rtt": float(rtt) if status in (IP_SUCCESS, IP_TTL_EXPIRED_TRANSIT) else None, "status": status,
            "de": str(ipaddress.IPv6Address(end)) if any(end) else None}


def traceroute(ip, saltos=30, timeout_ms=1500, tentativas=3):
    """Saltos até o destino. Pinga todos os TTLs ao mesmo tempo (fica pronto em ~2 s, não em 30)."""
    ip = str(ipaddress.ip_address(ip))

    def salto(ttl):
        rs = [ping(ip, timeout_ms, ttl) for _ in range(tentativas)]
        de = next((r["de"] for r in rs if r["de"]), None)
        return {"ttl": ttl, "ip": de, "rtts": [r["rtt"] for r in rs],
                "chegou": any(r["status"] == IP_SUCCESS for r in rs), **resumo([r["rtt"] for r in rs])}

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(salto, range(1, saltos + 1)))
    out = []
    for r in res:
        out.append(r)
        if r["chegou"]:
            break
    return {"destino": ip, "chegou": bool(out and out[-1]["chegou"]), "saltos": out}


def gateway():
    """Primeiro salto até a internet (o roteador), descoberto com TTL 1."""
    for destino in ("1.1.1.1", "8.8.8.8"):
        r = ping(destino, 800, ttl=1)
        if r["de"]:
            return r["de"]
    return None


def path_mtu(ip):
    """Maior pacote IPv4 que passa sem fragmentar (busca binária com o bit DF). None se o destino não responde a ping."""
    ip = str(ipaddress.ip_address(ip))
    if ipaddress.ip_address(ip).version != 4:
        return {"mtu": None, "msg": "Path MTU só para IPv4."}
    if ping(ip, 1500, tam=548 - 28, nao_fragmentar=True)["status"] != IP_SUCCESS:
        return {"mtu": None, "msg": "O destino não responde a ping."}
    lo, hi = 548 - 28, 9000 - 28
    while lo < hi:
        meio = (lo + hi + 1) // 2
        r = ping(ip, 1500, tam=meio, nao_fragmentar=True)
        if r["status"] == IP_SUCCESS:
            lo = meio
        else:
            hi = meio - 1
    return {"mtu": lo + 28, "msg": None}


# ================================================================ TCP por conexão (EStats)
_PATH_ROD = ("FastRetran", "Timeouts", "SubsequentTimeouts", "CurTimeoutCount", "AbruptTimeouts", "PktsRetrans",
             "BytesRetrans", "DupAcksIn", "SacksRcvd", "SackBlocksRcvd", "CongSignals", "PreCongSumCwnd", "PreCongSumRtt",
             "PostCongSumRtt", "PostCongCountRtt", "EcnSignals", "EceRcvd", "SendStall", "QuenchRcvd", "RetranThresh",
             "SndDupAckEpisodes", "SumBytesReordered", "NonRecovDa", "NonRecovDaEpisodes", "AckAfterFr", "DsackDups",
             "SampleRtt", "SmoothedRtt", "RttVar", "MaxRtt", "MinRtt", "SumRtt", "CountRtt", "CurRto", "MaxRto", "MinRto",
             "CurMss", "MaxMss", "MinMss", "SpuriousRtoDetections")
_ESTATS_PATH = 3          # TcpConnectionEstatsPath
_ligadas = set()


def tcp_estats(lip, lport, rip, rport):
    """RTT e retransmissões de uma conexão TCP IPv4 (precisa de administrador). None se indisponível."""
    if not WIN:
        return None
    try:
        a_l, a_r = ipaddress.ip_address(lip), ipaddress.ip_address(rip)
    except ValueError:
        return None
    if a_l.version != 4 or a_r.version != 4:
        return None
    linha = (c_ulong * 5)(5, struct.unpack("<I", a_l.packed)[0], socket.htons(lport),
                          struct.unpack("<I", a_r.packed)[0], socket.htons(rport))
    chave = (lip, lport, rip, rport)
    if chave not in _ligadas:
        rw = c_ubyte(1)
        if _ip.SetPerTcpConnectionEStats(linha, _ESTATS_PATH, ctypes.byref(rw), 0, 1, 0) != 0:
            return None
        _ligadas.add(chave)
        if len(_ligadas) > 2000:
            _ligadas.clear()
    rod = (c_ulong * len(_PATH_ROD))()
    if _ip.GetPerTcpConnectionEStats(linha, _ESTATS_PATH, None, 0, 0, None, 0, 0, rod, 0, ctypes.sizeof(rod)) != 0:
        return None
    d = dict(zip(_PATH_ROD, rod))
    sem = 0xFFFFFFFF
    if not d["CountRtt"]:
        return {"rtt": None, "retransmissoes": d["PktsRetrans"], "dup_acks": d["DupAcksIn"], "timeouts": d["Timeouts"]}
    return {"rtt": d["SmoothedRtt"], "rtt_min": None if d["MinRtt"] == sem else d["MinRtt"], "rtt_max": d["MaxRtt"],
            "rtt_var": d["RttVar"], "rto": d["CurRto"], "mss": d["CurMss"], "retransmissoes": d["PktsRetrans"],
            "bytes_retransmitidos": d["BytesRetrans"], "retransmissoes_rapidas": d["FastRetran"],
            "dup_acks": d["DupAcksIn"], "timeouts": d["Timeouts"], "sinais_congestionamento": d["CongSignals"],
            "rto_falsos": d["SpuriousRtoDetections"], "amostras": d["CountRtt"]}


# ================================================================ contadores do sistema
_TCP = ("rto_algoritmo", "rto_min", "rto_max", "max_conn", "aberturas_ativas", "aberturas_passivas", "falhas_conexao",
        "resets_estabelecidas", "estabelecidas", "segmentos_in", "segmentos_out", "retransmitidos", "erros_in",
        "resets_enviados", "conexoes")
_UDP = ("datagramas_in", "sem_porta", "erros_in", "datagramas_out", "enderecos")
_IP = ("encaminhamento", "ttl_padrao", "recebidos", "erros_cabecalho", "erros_endereco", "encaminhados",
       "protocolo_desconhecido", "descartados_in", "entregues", "pedidos_out", "descartes_rota", "descartados_out",
       "sem_rota", "remontagem_timeout", "remontagem_pedidos", "remontagem_ok", "remontagem_falhas", "fragmentados_ok",
       "fragmentacao_falhas", "fragmentos_criados", "interfaces", "enderecos", "rotas")


def contadores():
    """Contadores acumulados do Windows (IPv4 + IPv6) e das placas de rede."""
    out = {"tcp": {}, "udp": {}, "ip": {}}
    if WIN:
        for nome, func, campos in (("tcp", _ip.GetTcpStatisticsEx, _TCP), ("udp", _ip.GetUdpStatisticsEx, _UDP),
                                   ("ip", _ip.GetIpStatisticsEx, _IP)):
            for fam in (socket.AF_INET, socket.AF_INET6):
                st = (c_ulong * len(campos))()
                if func(st, fam) == 0:
                    for k, v in zip(campos, st):
                        out[nome][k] = out[nome].get(k, 0) + v
    placas = {}
    try:
        io_ = psutil.net_io_counters(pernic=True)
        st = psutil.net_if_stats()
    except Exception:
        io_, st = {}, {}
    for nome, c in io_.items():
        s = st.get(nome)
        if s is None or not s.isup or "loopback" in nome.lower():
            continue
        placas[nome] = {"env": c.bytes_sent, "rec": c.bytes_recv, "pk_env": c.packets_sent, "pk_rec": c.packets_recv,
                        "erros_in": c.errin, "erros_out": c.errout, "descartes_in": c.dropin, "descartes_out": c.dropout,
                        "velocidade": s.speed, "duplex": {2: "full", 1: "half"}.get(int(s.duplex), "?"), "mtu": s.mtu}
    out["placas"] = placas
    out["t"] = time.time()
    return out


def diferenca(antes, depois):
    """Diferença entre duas leituras de contadores(): taxas de retransmissão, erros, descartes e fragmentação."""
    if not antes or not depois:
        return None
    dt = max(0.001, depois["t"] - antes["t"])
    d = lambda g, k: max(0, depois[g].get(k, 0) - antes[g].get(k, 0))  # noqa: E731
    seg_out = d("tcp", "segmentos_out")
    dg_in = d("udp", "datagramas_in")
    out = {"seg": round(dt, 1),
           "tcp": {"segmentos_out": seg_out, "retransmitidos": d("tcp", "retransmitidos"),
                   "taxa_retransmissao": round(d("tcp", "retransmitidos") * 100 / seg_out, 3) if seg_out else None,
                   "falhas_conexao": d("tcp", "falhas_conexao"), "resets": d("tcp", "resets_estabelecidas"),
                   "resets_enviados": d("tcp", "resets_enviados"), "erros_in": d("tcp", "erros_in"),
                   "conexoes_novas_s": round((d("tcp", "aberturas_ativas") + d("tcp", "aberturas_passivas")) / dt, 2),
                   "estabelecidas": depois["tcp"].get("estabelecidas")},
           "udp": {"datagramas_in": dg_in, "datagramas_out": d("udp", "datagramas_out"), "erros_in": d("udp", "erros_in"),
                   "sem_porta": d("udp", "sem_porta"),
                   "taxa_erro": round(d("udp", "erros_in") * 100 / dg_in, 3) if dg_in else None},
           "ip": {"descartados_in": d("ip", "descartados_in"), "descartados_out": d("ip", "descartados_out"),
                  "erros_cabecalho": d("ip", "erros_cabecalho"), "fragmentados": d("ip", "fragmentados_ok"),
                  "falhas_fragmentacao": d("ip", "fragmentacao_falhas"), "falhas_remontagem": d("ip", "remontagem_falhas")},
           "placas": []}
    for nome, b in depois["placas"].items():
        a = antes["placas"].get(nome)
        if not a:
            continue
        x = {k: max(0, b[k] - a[k]) for k in ("env", "rec", "pk_env", "pk_rec", "erros_in", "erros_out",
                                             "descartes_in", "descartes_out")}
        x.update(nome=nome, velocidade=b["velocidade"], duplex=b["duplex"], mtu=b["mtu"],
                 uso=round((x["env"] + x["rec"]) * 8 / dt / (b["velocidade"] * 1e6) * 100, 2) if b["velocidade"] else None,
                 pps=round((x["pk_env"] + x["pk_rec"]) / dt, 1))
        out["placas"].append(x)
    return out


# ================================================================ Wi-Fi (wlanapi)
if WIN:
    class _GUID(ctypes.Structure):
        _fields_ = [("d1", c_ulong), ("d2", c_ushort), ("d3", c_ushort), ("d4", c_ubyte * 8)]

    class _IFINFO(ctypes.Structure):
        _fields_ = [("guid", _GUID), ("desc", ctypes.c_wchar * 256), ("estado", c_ulong)]

    class _IFLIST(ctypes.Structure):
        _fields_ = [("n", c_ulong), ("i", c_ulong), ("itens", _IFINFO * 1)]

    class _SSID(ctypes.Structure):
        _fields_ = [("tam", c_ulong), ("ssid", c_ubyte * 32)]

    class _ASSOC(ctypes.Structure):
        _fields_ = [("ssid", _SSID), ("bss_tipo", c_ulong), ("bssid", c_ubyte * 6), ("phy", c_ulong), ("phy_i", c_ulong),
                    ("qualidade", c_ulong), ("rx", c_ulong), ("tx", c_ulong)]

    class _CONEXAO(ctypes.Structure):
        _fields_ = [("estado", c_ulong), ("modo", c_ulong), ("perfil", ctypes.c_wchar * 256), ("assoc", _ASSOC)]

    class _BSS(ctypes.Structure):
        _fields_ = [("ssid", _SSID), ("phy_id", c_ulong), ("bssid", c_ubyte * 6), ("bss_tipo", c_ulong), ("phy", c_ulong),
                    ("rssi", c_long), ("qualidade", c_ulong), ("reg", ctypes.c_bool), ("beacon", c_ushort),
                    ("ts", ctypes.c_uint64), ("ts_host", ctypes.c_uint64), ("cap", c_ushort), ("freq", c_ulong),
                    ("taxas_n", c_ulong), ("taxas", c_ushort * 126), ("ie_off", c_ulong), ("ie_tam", c_ulong)]

    try:
        _wl = ctypes.WinDLL("wlanapi")
    except OSError:
        _wl = None

PHY = {4: "802.11a", 5: "802.11b", 6: "802.11g", 7: "802.11n", 8: "802.11ac", 9: "802.11ad", 10: "Wi-Fi 6 (ax)",
       11: "Wi-Fi 6E", 12: "Wi-Fi 7 (be)"}


def wifi():
    """Placas Wi-Fi conectadas: SSID, BSSID, sinal, RSSI, taxas, canal e redes vizinhas no mesmo canal."""
    if not WIN or _wl is None:
        return []
    h = wt.HANDLE()
    ver = c_ulong()
    if _wl.WlanOpenHandle(2, None, ctypes.byref(ver), ctypes.byref(h)) != 0:
        return []
    out = []
    try:
        lst = ctypes.c_void_p()
        if _wl.WlanEnumInterfaces(h, None, ctypes.byref(lst)) != 0:
            return []
        n = ctypes.cast(lst, ctypes.POINTER(c_ulong))[0]
        base = lst.value + 8
        for i in range(n):
            inf = _IFINFO.from_address(base + i * ctypes.sizeof(_IFINFO))
            g = _GUID.from_buffer_copy(ctypes.string_at(ctypes.addressof(inf.guid), ctypes.sizeof(_GUID)))
            item = {"placa": inf.desc, "conectado": inf.estado == 1}
            if inf.estado == 1:
                item.update(_wifi_conexao(h, g))
            out.append(item)
        _wl.WlanFreeMemory(lst)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _wl.WlanCloseHandle(h, None)
    return out


def _consultar(h, g, opcode, tipo):
    tam = c_ulong()
    p = ctypes.c_void_p()
    if _wl.WlanQueryInterface(h, ctypes.byref(g), opcode, None, ctypes.byref(tam), ctypes.byref(p), None) != 0:
        return None
    try:
        if issubclass(tipo, ctypes.Structure):
            return tipo.from_buffer_copy(ctypes.string_at(p.value, ctypes.sizeof(tipo)))
        return tipo.from_address(p.value).value
    finally:
        _wl.WlanFreeMemory(p)


def _wifi_conexao(h, g):
    c = _consultar(h, g, 7, _CONEXAO)               # wlan_intf_opcode_current_connection
    if c is None:
        return {}
    a = c.assoc
    bssid = ":".join(f"{b:02x}" for b in a.bssid)
    d = {"ssid": bytes(a.ssid.ssid[:a.ssid.tam]).decode("utf-8", "replace"), "bssid": bssid,
         "padrao": PHY.get(a.phy, str(a.phy)), "sinal": a.qualidade, "rx_mbps": a.rx / 1000, "tx_mbps": a.tx / 1000,
         "rssi": _consultar(h, g, 0x10000102, c_long), "canal": _consultar(h, g, 8, c_ulong)}
    lst = ctypes.c_void_p()
    if _wl.WlanGetNetworkBssList(h, ctypes.byref(g), None, 3, False, None, ctypes.byref(lst)) == 0:
        try:
            n = ctypes.cast(lst, ctypes.POINTER(c_ulong))[1]
            base = lst.value + 8
            redes = []
            for i in range(n):
                b = _BSS.from_address(base + i * ctypes.sizeof(_BSS))
                canal, banda = canal_wifi(b.freq)
                redes.append({"ssid": bytes(b.ssid.ssid[:b.ssid.tam]).decode("utf-8", "replace"),
                              "bssid": ":".join(f"{x:02x}" for x in b.bssid), "rssi": b.rssi, "canal": canal, "banda": banda})
            meu = next((r for r in redes if r["bssid"] == bssid), None)
            if meu:
                d["banda"] = meu["banda"]
                d["canal"] = d["canal"] or meu["canal"]
                d["mesmo_canal"] = [r for r in redes if r["canal"] == meu["canal"] and r["bssid"] != bssid]
                d["canais_vizinhos"] = [r for r in redes if r["canal"] and meu["canal"] and r["banda"] == meu["banda"]
                                        and 0 < abs(r["canal"] - meu["canal"]) < 5 and meu["banda"] == "2,4 GHz"]
            d["redes_visiveis"] = len(redes)
        finally:
            _wl.WlanFreeMemory(lst)
    return d


# ================================================================ DNS
def servidores_dns():
    """Servidores DNS configurados no Windows (fixos ou do DHCP), sem repetir."""
    if not WIN:
        return []
    import winreg
    out = []
    for raiz in (r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces",
                 r"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters\Interfaces"):
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, raiz)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(k, sub) as s:
                    for nome in ("NameServer", "DhcpNameServer"):
                        try:
                            v = winreg.QueryValueEx(s, nome)[0]
                        except OSError:
                            continue
                        for ip in str(v).replace(",", " ").split():
                            try:
                                ip = str(ipaddress.ip_address(ip.split("%")[0]))
                            except ValueError:
                                continue
                            if ip not in out and not ip.startswith("fec0:"):
                                out.append(ip)
            except OSError:
                pass
    return out


def pergunta_dns(nome, tid, tipo=1):
    q = b"".join(bytes([len(p)]) + p for p in nome.encode("ascii").split(b".") if p) + b"\0"
    return struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", tipo, 1)


RCODE = {0: "ok", 1: "formato", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "não implementado", 5: "recusado"}


def consulta_dns(servidor, nome, timeout=2.0):
    """Uma consulta A direto ao servidor (sem o cache do Windows). {"ms", "rcode", "truncada"} ou ms=None no timeout."""
    fam = socket.AF_INET6 if ":" in servidor else socket.AF_INET
    tid = random.randint(0, 0xFFFF)
    with socket.socket(fam, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        ini = time.perf_counter()
        try:
            s.sendto(pergunta_dns(nome, tid), (servidor, 53))
            while True:
                dados = s.recv(4096)
                if len(dados) >= 12 and struct.unpack_from(">H", dados)[0] == tid:
                    fl = struct.unpack_from(">H", dados, 2)[0]
                    return {"ms": round((time.perf_counter() - ini) * 1000, 1), "rcode": RCODE.get(fl & 0xF, str(fl & 0xF)),
                            "truncada": bool(fl & 0x200)}
        except (socket.timeout, OSError):
            return {"ms": None, "rcode": "sem resposta", "truncada": False}


def teste_dns(nomes=("www.google.com", "www.microsoft.com", "cloudflare.com", "www.youtube.com")):
    """Mede cada servidor do Windows: nomes comuns (cache) e nomes que não existem (resolução completa)."""
    res = []
    for srv in servidores_dns()[:4]:
        comuns = [consulta_dns(srv, n) for n in nomes]
        novos = [consulta_dns(srv, f"cnt-{uuid.uuid4().hex[:10]}.example.com") for _ in range(3)]
        todos = comuns + novos
        ok = [r for r in todos if r["ms"] is not None]
        res.append({"servidor": srv, "comuns": resumo([r["ms"] for r in comuns]),
                    "sem_cache": resumo([r["ms"] for r in novos]),
                    "sucesso": round(sum(1 for r in comuns if r["rcode"] == "ok") * 100 / len(comuns)),
                    "timeouts": len(todos) - len(ok), "servfail": sum(1 for r in todos if r["rcode"] == "SERVFAIL"),
                    "nxdomain": sum(1 for r in todos if r["rcode"] == "NXDOMAIN"),
                    "truncadas": sum(1 for r in todos if r["truncada"])})
    ini = time.perf_counter()
    try:
        socket.getaddrinfo(nomes[0], 443)
        sistema = round((time.perf_counter() - ini) * 1000, 1)
    except OSError:
        sistema = None
    return {"servidores": res, "windows_com_cache": sistema}


# ================================================================ HTTP / TLS
def medir_http(url, timeout=10.0, limite=2 * 2**20):
    """Tempo de cada etapa: DNS, TCP, TLS, primeiro byte (TTFB) e total."""
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Use um endereço http:// ou https://.")
    porta = u.port or (443 if u.scheme == "https" else 80)
    t0 = time.perf_counter()
    info = socket.getaddrinfo(u.hostname, porta, type=socket.SOCK_STREAM)[0]
    t1 = time.perf_counter()
    s = socket.socket(info[0], socket.SOCK_STREAM)
    s.settimeout(timeout)
    out = {"ip": info[4][0]}
    try:
        s.connect(info[4])
        t2 = time.perf_counter()
        t3 = t2
        if u.scheme == "https":
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
            t3 = time.perf_counter()
            out.update(tls_versao=s.version(), cifra=s.cipher()[0])
        caminho = (u.path or "/") + ("?" + u.query if u.query else "")
        s.sendall(f"GET {caminho} HTTP/1.1\r\nHost: {u.hostname}\r\nUser-Agent: CNT-Monitor/{config.VERSAO}\r\n"
                  f"Accept: */*\r\nConnection: close\r\n\r\n".encode())
        primeiro = s.recv(65536)
        t4 = time.perf_counter()
        total = len(primeiro)
        while total < limite:
            b = s.recv(65536)
            if not b:
                break
            total += len(b)
        t5 = time.perf_counter()
    finally:
        s.close()
    linha = primeiro.split(b"\r\n", 1)[0].decode("latin-1")
    partes = linha.split()
    ms = lambda a, b: round((b - a) * 1000, 1)  # noqa: E731
    out.update(status=int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else None, dns=ms(t0, t1),
               tcp=ms(t1, t2), tls=ms(t2, t3) if u.scheme == "https" else None, ttfb=ms(t3, t4), total=ms(t0, t5),
               bytes=total)
    return out


# ================================================================ teste de velocidade (Cloudflare)
CF = "https://speed.cloudflare.com"
UA = f"CNT-Monitor/{config.VERSAO}"     # o User-Agent padrão do Python é bloqueado (403)


class Velocidade:
    """Download e upload com 4 conexões em paralelo e ping durante a carga (bufferbloat). Só roda quando pedem."""
    FLUXOS = 4

    def __init__(self, ping_fn=None):
        self.ping = ping_fn or (lambda: ping("1.1.1.1", 1000)["rtt"])
        self.estado = {"fase": "parado"}
        self._parar = threading.Event()

    def iniciar(self, segundos=10):
        if self.estado.get("fase") not in ("parado", "pronto", "erro"):
            return False
        self._parar.clear()
        self.estado = {"fase": "latencia", "inicio": time.time()}
        threading.Thread(target=self._rodar, args=(segundos,), daemon=True).start()
        return True

    def cancelar(self):
        self._parar.set()

    def _pings(self, fim, lista):
        while time.time() < fim and not self._parar.is_set():
            lista.append(self.ping())
            time.sleep(0.25)

    def _fase(self, nome, segundos, trabalho):
        fim = time.time() + segundos
        bytes_ = [0]
        por_seg = {}
        lock = threading.Lock()
        rtts = []

        def conta(n):
            with lock:
                bytes_[0] += n
                k = int(time.time())
                por_seg[k] = por_seg.get(k, 0) + n

        self.estado["fase"] = nome
        th = [threading.Thread(target=trabalho, args=(fim, conta), daemon=True) for _ in range(self.FLUXOS)]
        th.append(threading.Thread(target=self._pings, args=(fim, rtts), daemon=True))
        ini = time.time()
        for t in th:
            t.start()
        for t in th:
            t.join(segundos + 15)
        dur = max(0.5, time.time() - ini)
        cheios = [v for k, v in por_seg.items() if int(ini) < k < int(ini + dur)]
        return {"mbps": round(bytes_[0] * 8 / dur / 1e6, 1), "pico_mbps": round(max(cheios, default=0) * 8 / 1e6, 1),
                "bytes": bytes_[0], "latencia": resumo(rtts)}

    def _baixar(self, fim, conta):
        while time.time() < fim and not self._parar.is_set():
            try:
                req = urllib.request.Request(f"{CF}/__down?bytes=50000000", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    while time.time() < fim and not self._parar.is_set():
                        b = r.read(65536)
                        if not b:
                            break
                        conta(len(b))
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

    def _enviar(self, fim, conta):
        bloco = os.urandom(64 * 1024)

        def corpo():                      # sem tamanho: vai em partes (chunked) e pode parar a qualquer momento
            enviados = 0
            while time.time() < fim and not self._parar.is_set() and enviados < 64 * 2**20:
                conta(len(bloco))
                enviados += len(bloco)
                yield bloco

        while time.time() < fim and not self._parar.is_set():
            try:
                req = urllib.request.Request(f"{CF}/__up", data=corpo(), method="POST",
                                             headers={"Content-Type": "application/octet-stream", "User-Agent": UA})
                urllib.request.urlopen(req, timeout=15).read()
            except Exception:  # noqa: BLE001
                time.sleep(0.2)

    def _rodar(self, segundos):
        try:
            ocioso = []
            for _ in range(10):
                ocioso.append(self.ping())
                time.sleep(0.2)
            base = resumo(ocioso)
            self.estado["latencia"] = base
            down = self._fase("download", segundos, self._baixar)
            self.estado["download"] = down
            up = self._fase("upload", segundos, self._enviar)
            self.estado["upload"] = up
            lat = base["mediana"]
            bb = [x["latencia"]["mediana"] - lat for x in (down, up) if lat is not None and x["latencia"]["mediana"] is not None]
            pior = max(bb) if bb else None
            self.estado.update(fase="pronto" if not self._parar.is_set() else "parado", fim=time.time(),
                               bufferbloat=None if pior is None else round(pior, 1), nota=nota_bufferbloat(pior),
                               assimetria=round(down["mbps"] / up["mbps"], 1) if up["mbps"] else None)
        except Exception as e:  # noqa: BLE001
            self.estado.update(fase="erro", erro=str(e))


# ================================================================ TCP e UDP até um destino
# Eco UDP do servidor da CNT: devolve o datagrama que começa com "CNTE" (até 64 bytes). Só é usado quando o destino
# não devolve nada, e a medida aparece separada: o caminho até o servidor não é o caminho até o destino.
ECO_UDP = (os.environ.get("MONITORPC_ECO_HOST") or "monitor.cntoficial.com.br", int(os.environ.get("MONITORPC_ECO_PORTA") or 48790))
ECO_PREFIXO = b"CNTE"


def _familia(ip):
    return socket.AF_INET6 if ":" in ip else socket.AF_INET


def sonda_tcp(ip, porta, timeout=2.0):
    """Tempo para abrir uma conexão TCP na porta (o "ping" de quem não responde a ICMP). Conexão recusada (RST)
    também é resposta do destino e mede o tempo de ida e volta."""
    s = socket.socket(_familia(ip), socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.perf_counter()
    try:
        s.connect((ip, porta))
        tipo = "aberta"
    except ConnectionRefusedError:
        tipo = "recusada"
    except OSError:
        return {"tipo": "silencio", "rtt": None}
    finally:
        s.close()
    return {"tipo": tipo, "rtt": round((time.perf_counter() - t0) * 1000, 2)}


def sonda_udp(ip, porta, timeout=1.0, carga=b"CNT-Monitor", confere=None):
    """Um datagrama UDP. Mede o tempo quando volta uma resposta do programa do outro lado ou um "porta inacessível"
    (o ICMP que o próprio destino devolve; o Windows entrega como ConnectionResetError). Silêncio NÃO é perda:
    servidores de chamada, de jogo e firewalls descartam calados o que não esperavam."""
    s = socket.socket(_familia(ip), socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, porta))
        t0 = time.perf_counter()
        s.send(carga)
        try:
            dados = s.recv(2048)
            tipo = "resposta" if confere is None or dados == confere else "silencio"
        except ConnectionResetError:
            tipo = "porta_inacessivel"
        except socket.timeout:
            tipo = "silencio"
        rtt = round((time.perf_counter() - t0) * 1000, 2)
        return {"tipo": tipo, "rtt": None if tipo == "silencio" else rtt}
    except OSError:
        return {"tipo": "erro", "rtt": None}
    finally:
        s.close()


def sonda_eco(seq=0, timeout=1.0, eco=None):
    """Ida e volta UDP até o eco do servidor da CNT (referência da saída UDP desta internet)."""
    host, porta = eco or ECO_UDP
    try:
        ip = socket.getaddrinfo(host, porta, type=socket.SOCK_DGRAM)[0][4][0]
    except OSError:
        return {"tipo": "erro", "rtt": None}
    carga = ECO_PREFIXO + struct.pack(">Q", seq)
    return sonda_udp(ip, porta, timeout, carga, confere=carga)


def jitter_movel(rtts, janela=10):
    """Jitter de cada ponto: média de |rtt - rtt anterior| nos últimos `janela` pares sem perda (None se ainda não há)."""
    out, difs, ant = [], deque(maxlen=janela), None
    for r in rtts:
        if r is not None and ant is not None:
            difs.append(abs(r - ant))
        if r is not None:
            ant = r
        out.append(round(sum(difs) / len(difs), 2) if difs else None)
    return out


def resumo_udp(amostras, tipos):
    """UDP: se o destino respondeu a alguma sonda, quem ficou sem resposta é perda de verdade; se nunca respondeu,
    não dá para falar em perda ("sem devolução UDP")."""
    respondeu = any(t in ("resposta", "porta_inacessivel") for t in tipos)
    r = resumo(amostras if respondeu else [a for a in amostras if a is not None])
    r["sem_devolucao"] = not respondeu
    if not respondeu:
        r["perda"] = None
    return r


class TesteDestino:
    """Teste real até um destino por ICMP, TCP ou UDP, 2 medidas por segundo, pelo tempo escolhido."""
    INTERVALO = 0.5
    PROTOCOLOS = ("icmp", "tcp", "udp")

    def __init__(self, icmp=None, tcp=None, udp=None, eco=None):
        self._icmp = icmp or (lambda ip: {"tipo": "resposta", "rtt": ping(ip, 1000)["rtt"]})
        self._tcp = tcp or sonda_tcp
        self._udp = udp or sonda_udp
        self._eco = eco or sonda_eco
        self.estado = {"fase": "parado"}
        self._cancelar = threading.Event()
        self._lock = threading.Lock()

    def iniciar(self, destino, protocolo, porta=0, segundos=30):
        if protocolo not in self.PROTOCOLOS:
            raise ValueError("Escolha ICMP, TCP ou UDP.")
        try:
            porta, segundos = int(porta or 0), int(segundos or 30)
        except (TypeError, ValueError):
            raise ValueError("Porta ou duração inválida.") from None
        if protocolo != "icmp" and not 1 <= porta <= 65535:
            raise ValueError("Informe a porta (1 a 65535) para testar TCP ou UDP.")
        segundos = max(5, min(300, segundos))
        ip = alvo_valido(destino)
        with self._lock:
            if self.estado.get("fase") == "medindo":
                return False, "Já tem um teste rodando."
            self._cancelar.clear()
            self.estado = {"fase": "medindo", "destino": str(destino).strip()[:120], "ip": ip, "protocolo": protocolo,
                           "porta": porta if protocolo != "icmp" else None, "segundos": segundos, "inicio": time.time(),
                           "amostras": [], "tipos": {}}
        threading.Thread(target=self._rodar, daemon=True).start()
        return True, "Teste começou."

    def cancelar(self):
        self._cancelar.set()

    def _medir(self, e):
        if e["protocolo"] == "icmp":
            return self._icmp(e["ip"])
        return (self._tcp if e["protocolo"] == "tcp" else self._udp)(e["ip"], e["porta"])

    def _rodar(self):
        e = self.estado
        try:
            fim = e["inicio"] + e["segundos"]
            while time.time() < fim and not self._cancelar.is_set():
                t = time.time()
                r = self._medir(e)
                tipo = r.get("tipo") or ("resposta" if r.get("rtt") is not None else "silencio")
                e["amostras"].append([round(t, 2), r.get("rtt"), tipo])
                e["tipos"][tipo] = e["tipos"].get(tipo, 0) + 1
                self._cancelar.wait(max(0.0, self.INTERVALO - (time.time() - t)))
            rtts = [a[1] for a in e["amostras"]]
            r = resumo_udp(rtts, [a[2] for a in e["amostras"]]) if e["protocolo"] == "udp" else resumo(rtts)
            r["mos"], r["fator_r"] = mos(r.get("mediana"), r.get("jitter"), r.get("perda"))
            e["resultado"] = r
            if r.get("sem_devolucao") and not self._cancelar.is_set():
                ref = [self._eco(i) for i in range(10)]
                e["vps"] = {**resumo([x["rtt"] for x in ref]), "servidor": ECO_UDP[0],
                            "respondeu": any(x["rtt"] is not None for x in ref)}
            e["fase"] = "cancelado" if self._cancelar.is_set() else "pronto"
        except Exception as ex:  # noqa: BLE001
            e.update(fase="erro", erro=str(ex))
        e["fim"] = time.time()

    def ver(self):
        e = dict(self.estado)
        am = list(e.get("amostras") or [])
        jit = jitter_movel([a[1] for a in am])
        e["serie"] = [[a[0], a[1], j, a[2]] for a, j in zip(am, jit)]
        e.pop("amostras", None)
        return e


# ================================================================ país e operadora (DB-IP Lite)
class GeoIP:
    """Base DB-IP Lite (país + AS) que vai junto com o programa. IP Geolocation by DB-IP (CC BY 4.0)."""

    def __init__(self, pasta=None):
        self.pasta = pasta or os.path.join(config.pasta_recursos(), "geoip")
        self._r = None
        self._cache = {}
        self.lock = threading.Lock()

    def _abrir(self):
        if self._r is None:
            self._r = {}
            try:
                import maxminddb
            except ImportError:
                return self._r
            for k, arq in (("pais", "dbip-country-lite.mmdb"), ("as", "dbip-asn-lite.mmdb")):
                p = os.path.join(self.pasta, arq)
                if os.path.exists(p):
                    try:
                        self._r[k] = maxminddb.open_database(p)
                    except Exception:  # noqa: BLE001
                        pass
        return self._r

    @property
    def disponivel(self):
        return bool(self._abrir())

    def info(self, ip):
        if ip in self._cache:
            return self._cache[ip]
        with self.lock:
            r = self._abrir()
            out = None
            try:
                if ipaddress.ip_address(ip).is_global:
                    p = r["pais"].get(ip) if "pais" in r else None
                    a = r["as"].get(ip) if "as" in r else None
                    if p or a:
                        nomes = ((p or {}).get("country") or {}).get("names") or {}
                        out = {"pais": nomes.get("pt-BR") or nomes.get("en"), "codigo": ((p or {}).get("country") or {}).get("iso_code"),
                               "asn": (a or {}).get("autonomous_system_number"),
                               "operadora": (a or {}).get("autonomous_system_organization")}
            except Exception:  # noqa: BLE001
                out = None
            if len(self._cache) > 20000:
                self._cache.clear()
            self._cache[ip] = out
            return out


# ================================================================ sondas e análise contínua
class Sondas:
    """Pinga 1x/s os IPs que alguém está olhando (detalhes de IP, programa ou processo) e os destinos da Análise."""
    MAX_VIGIA = 24
    GUARDA = 900          # amostras por IP (15 min)
    MAX_LINHAS = 86400    # captura de até 24 h a 1/s

    def __init__(self, ping_fn=None, carga_fn=None, pasta=None):
        self.ping = ping_fn or (lambda ip: ping(ip, 900)["rtt"])
        self.carga = carga_fn or (lambda: 0)
        self.pasta = pasta or os.path.join(config.BASE, "analises")
        self.lock = threading.Lock()
        self.vigia = {}          # ip -> expira
        self.amostras = {}       # ip -> deque[(t, rtt)]
        self.captura = None
        self._parar = threading.Event()
        self._ex = ThreadPoolExecutor(max_workers=12)

    def iniciar(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def parar(self):
        self._parar.set()
        if self.captura:
            self.parar_captura()

    def vigiar(self, ips, seg=15):
        t = time.time()
        with self.lock:
            for ip in ips:
                if ip in self.vigia or len(self.vigia) < self.MAX_VIGIA:
                    self.vigia[ip] = t + seg

    def resumo_ip(self, ip, janela=60):
        t = time.time()
        with self.lock:
            q = list(self.amostras.get(ip, ()))
        rec = [r for (tt, r) in q if tt >= t - janela]
        return {**resumo(rec), "serie": [[round(tt, 1), r] for tt, r in q[-300:]]} if rec else None

    def _alvos(self, t):
        with self.lock:
            for ip in [k for k, v in self.vigia.items() if v < t]:
                del self.vigia[ip]
            alvos = set(self.vigia)
            if self.captura:
                alvos |= set(self.captura["alvos"])
        return alvos

    def _loop(self):
        while not self._parar.is_set():
            ini = time.time()
            try:
                self.passo(ini)
            except Exception:  # noqa: BLE001
                pass
            self._parar.wait(max(0.05, 1 - (time.time() - ini)))

    def passo(self, t):
        alvos = sorted(self._alvos(t))
        res = dict(zip(alvos, self._ex.map(self.ping, alvos))) if alvos else {}
        with self.lock:
            for ip, rtt in res.items():
                self.amostras.setdefault(ip, deque(maxlen=self.GUARDA)).append((t, rtt))
            for ip in [k for k, q in self.amostras.items() if q and q[-1][0] < t - self.GUARDA and k not in res]:
                del self.amostras[ip]
            cap = self.captura
            if cap:
                cap["linhas"].append([round(t, 1), round(self.carga() or 0), *[res.get(a) for a in cap["alvos"]]])
                if len(cap["linhas"]) >= self.MAX_LINHAS or (cap["fim_max"] and t >= cap["fim_max"]):
                    threading.Thread(target=self.parar_captura, daemon=True).start()

    # -- análise contínua
    def iniciar_captura(self, alvos, nomes, minutos=0):
        if self.captura:
            return False, "Já tem uma análise gravando."
        cap = {"id": time.strftime("%Y%m%d-%H%M%S"), "inicio": time.time(), "alvos": alvos, "nomes": nomes,
               "linhas": [], "fim_max": time.time() + minutos * 60 if minutos else None,
               "contadores": contadores(), "wifi": [w for w in wifi() if w.get("conectado")]}
        with self.lock:
            if self.captura:
                return False, "Já tem uma análise gravando."
            self.captura = cap
        return True, "Análise de rede gravando."

    def parar_captura(self):
        with self.lock:
            cap, self.captura = self.captura, None
        if not cap:
            return None
        cap["fim"] = time.time()
        cap["sistema"] = diferenca(cap.pop("contadores"), contadores())
        os.makedirs(self.pasta, exist_ok=True)
        with open(os.path.join(self.pasta, cap["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(cap, f)
        self._limpar_antigas()
        return cap["id"]

    def _limpar_antigas(self, manter=30):
        try:
            arqs = sorted(a for a in os.listdir(self.pasta) if a.endswith(".json"))
            for a in arqs[:-manter]:
                os.remove(os.path.join(self.pasta, a))
        except OSError:
            pass

    def lista(self):
        out = []
        try:
            for a in sorted(os.listdir(self.pasta), reverse=True):
                if a.endswith(".json"):
                    try:
                        with open(os.path.join(self.pasta, a), encoding="utf-8") as f:
                            d = json.load(f)
                        out.append({"id": d["id"], "inicio": d["inicio"], "fim": d.get("fim"), "nomes": d["nomes"],
                                    "segundos": len(d["linhas"])})
                    except (OSError, ValueError, KeyError):
                        continue
        except OSError:
            pass
        return out

    def abrir(self, ident):
        if self.captura and ident in ("", "atual", self.captura["id"]):
            with self.lock:
                cap = dict(self.captura, linhas=list(self.captura["linhas"]))
            cap["sistema"] = diferenca(cap.pop("contadores"), contadores())
            cap["gravando"] = True
            return analisar(cap)
        if not ident or not all(c.isalnum() or c == "-" for c in ident):
            return None
        try:
            with open(os.path.join(self.pasta, ident + ".json"), encoding="utf-8") as f:
                return analisar(json.load(f))
        except (OSError, ValueError):
            return None

    def apagar(self, ident):
        if not ident or not all(c.isalnum() or c == "-" for c in ident):
            return False
        try:
            os.remove(os.path.join(self.pasta, ident + ".json"))
            return True
        except OSError:
            return False


def analisar(cap, pontos=600):
    """Resumo de uma captura: por destino (latência, jitter, perda, bufferbloat, pontuações), quedas e linha do tempo."""
    linhas = cap["linhas"]
    alvos = cap["alvos"]
    internet = [i for i, a in enumerate(alvos) if _eh_internet(a)] or list(range(len(alvos)))
    fora = [all(l[2 + i] is None for i in internet) for l in linhas]
    disp = quedas(fora)
    for e in disp["eventos"]:
        e["t"] = linhas[e["inicio"]][0]
    por = []
    for i, (ip, nome) in enumerate(zip(alvos, cap["nomes"])):
        r = resumo([l[2 + i] for l in linhas])
        oc, ca, bb = bufferbloat(linhas, 2 + i)
        por.append({"ip": ip, "nome": nome, **r, "lat_ociosa": oc, "lat_carga": ca, "bufferbloat": bb,
                    "nota_bufferbloat": nota_bufferbloat(bb),
                    "pontos": pontuacoes(r, bb, disp["disponibilidade"] if i in internet else None)})
    passo = max(1, math.ceil(len(linhas) / pontos))
    tempo = []
    for k in range(0, len(linhas), passo):
        bloco = linhas[k:k + passo]
        lin = [bloco[0][0], max(l[1] for l in bloco)]
        for i in range(len(alvos)):
            v = [l[2 + i] for l in bloco if l[2 + i] is not None]
            lin.append(round(percentil(v, 50), 1) if v else None)
            lin.append(round((len(bloco) - len(v)) * 100 / len(bloco)))
        tempo.append(lin)
    cargas = [l[1] for l in linhas]
    pc = [p["pontos"] for p in por if p["pontos"].get("saude") is not None]
    return {"id": cap["id"], "inicio": cap["inicio"], "fim": cap.get("fim"), "gravando": cap.get("gravando", False),
            "fim_max": cap.get("fim_max"), "segundos": len(linhas), "destinos": por, "disponibilidade": disp,
            "carga": {"pico": max(cargas, default=0), "media": round(sum(cargas) / len(cargas)) if cargas else 0},
            "saude": round(sum(p["saude"] for p in pc) / len(pc)) if pc else None,
            "sistema": cap.get("sistema"), "wifi": cap.get("wifi"), "tempo": tempo, "passo": passo}


def _eh_internet(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False
