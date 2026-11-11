"""Rede por processo.

- Conexões abertas (TCP/UDP) com o PID dono: psutil.
- Bytes enviados/recebidos por processo e por IP remoto: ETW do Windows
  (provedor Microsoft-Windows-Kernel-Network). Precisa de administrador,
  por isso roda dentro do serviço. Cada fluxo UDP também guarda o intervalo
  entre os pacotes que chegam (jitter de chegada e o maior silêncio).
- Nome dos IPs (DNS reverso) em segundo plano, com cache.
"""
import os
import time
import socket
import struct
import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor

import psutil

WIN = os.name == "nt"

PORTAS = {
    20: "FTP", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP", 68: "DHCP",
    80: "HTTP", 110: "POP3", 123: "NTP", 135: "RPC", 137: "NetBIOS", 138: "NetBIOS", 139: "NetBIOS",
    143: "IMAP", 161: "SNMP", 389: "LDAP", 443: "HTTPS", 445: "SMB", 465: "SMTPS", 587: "SMTP",
    993: "IMAPS", 995: "POP3S", 1433: "SQL Server", 1723: "PPTP", 1900: "SSDP", 3306: "MySQL",
    3389: "Área de Trabalho Remota", 5060: "SIP", 5061: "SIP-TLS", 5222: "XMPP", 5353: "mDNS",
    5432: "PostgreSQL", 5938: "TeamViewer", 6379: "Redis", 8080: "HTTP alt.", 8443: "HTTPS alt.",
    27017: "MongoDB", 21115: "RustDesk", 21116: "RustDesk", 21117: "RustDesk",
}

ESTADOS = {
    "ESTABLISHED": "Conectado", "LISTEN": "Aguardando", "TIME_WAIT": "Encerrando", "CLOSE_WAIT": "Encerrando",
    "SYN_SENT": "Conectando", "SYN_RECV": "Conectando", "FIN_WAIT1": "Encerrando", "FIN_WAIT2": "Encerrando",
    "LAST_ACK": "Encerrando", "CLOSING": "Encerrando", "CLOSE": "Fechado", "NONE": "—",
}


def tipo_ip(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "?"
    if a.is_loopback:
        return "este PC"
    if a.is_link_local:
        return "rede local"
    if a.is_private:
        return "rede local"
    if a.is_multicast:
        return "multicast"
    if a.is_unspecified:
        return "—"
    return "internet"


class DNS:
    """DNS reverso em segundo plano com cache de 1 hora."""

    def __init__(self):
        self.cache = {}
        self.pend = set()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dns")

    def _resolver(self, ip):
        try:
            nome = socket.gethostbyaddr(ip)[0]
        except Exception:
            nome = None
        with self.lock:
            self.cache[ip] = (time.time(), nome)
            self.pend.discard(ip)

    def nome(self, ip):
        if not ip or tipo_ip(ip) not in ("internet", "rede local"):
            return None
        with self.lock:
            c = self.cache.get(ip)
            if c and time.time() - c[0] < 3600:
                return c[1]
            if ip in self.pend or len(self.pend) > 200:
                return c[1] if c else None
            self.pend.add(ip)
        self.pool.submit(self._resolver, ip)
        return c[1] if c else None


def conexoes():
    out = []
    try:
        lista = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return out
    for c in lista:
        tcp = c.type == socket.SOCK_STREAM
        v6 = c.family == socket.AF_INET6
        l = c.laddr if c.laddr else None
        r = c.raddr if c.raddr else None
        out.append({
            "pid": c.pid or 0,
            "proto": ("TCP" if tcp else "UDP") + ("v6" if v6 else ""),
            "lip": l.ip if l else "", "lport": l.port if l else 0,
            "rip": r.ip if r else "", "rport": r.port if r else 0,
            "estado": ESTADOS.get(c.status, c.status) if tcp else ("Aguardando" if not r else "—"),
        })
    return out


# ------------------------------------------------------------------ ETW
if WIN:
    import ctypes
    from ctypes import wintypes as wt

    c_ulong, c_ushort, c_ubyte, c_long = ctypes.c_ulong, ctypes.c_ushort, ctypes.c_ubyte, ctypes.c_long
    c_uint64, c_int64, c_void_p = ctypes.c_uint64, ctypes.c_int64, ctypes.c_void_p

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]

    def _guid(s):
        import uuid
        u = uuid.UUID(s)
        g = GUID()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4 = (c_ubyte * 8)(*u.bytes[8:])
        return g

    class WNODE_HEADER(ctypes.Structure):
        _fields_ = [("BufferSize", c_ulong), ("ProviderId", c_ulong), ("HistoricalContext", c_uint64),
                    ("TimeStamp", c_int64), ("Guid", GUID), ("ClientContext", c_ulong), ("Flags", c_ulong)]

    class EVENT_TRACE_PROPERTIES(ctypes.Structure):
        _fields_ = [("Wnode", WNODE_HEADER), ("BufferSize", c_ulong), ("MinimumBuffers", c_ulong),
                    ("MaximumBuffers", c_ulong), ("MaximumFileSize", c_ulong), ("LogFileMode", c_ulong),
                    ("FlushTimer", c_ulong), ("EnableFlags", c_ulong), ("AgeLimit", c_long),
                    ("NumberOfBuffers", c_ulong), ("FreeBuffers", c_ulong), ("EventsLost", c_ulong),
                    ("BuffersWritten", c_ulong), ("LogBuffersLost", c_ulong), ("RealTimeBuffersLost", c_ulong),
                    ("LoggerThreadId", c_void_p), ("LogFileNameOffset", c_ulong), ("LoggerNameOffset", c_ulong)]

    class PROPS(ctypes.Structure):
        _fields_ = [("p", EVENT_TRACE_PROPERTIES), ("nome", ctypes.c_wchar * 260), ("arq", ctypes.c_wchar * 260)]

    class EVENT_TRACE_HEADER(ctypes.Structure):
        _fields_ = [("Size", c_ushort), ("HeaderType", c_ubyte), ("MarkerFlags", c_ubyte),
                    ("Type", c_ubyte), ("Level", c_ubyte), ("Version", c_ushort),
                    ("ThreadId", c_ulong), ("ProcessId", c_ulong), ("TimeStamp", c_int64),
                    ("Guid", GUID), ("ProcessorTime", c_uint64)]

    class EVENT_TRACE(ctypes.Structure):
        _fields_ = [("Header", EVENT_TRACE_HEADER), ("InstanceId", c_ulong), ("ParentInstanceId", c_ulong),
                    ("ParentGuid", GUID), ("MofData", c_void_p), ("MofLength", c_ulong), ("ClientContext", c_ulong)]

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ushort) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour",
                                                    "wMinute", "wSecond", "wMilliseconds")]

    class TIME_ZONE_INFORMATION(ctypes.Structure):
        _fields_ = [("Bias", c_long), ("StandardName", ctypes.c_wchar * 32), ("StandardDate", SYSTEMTIME),
                    ("StandardBias", c_long), ("DaylightName", ctypes.c_wchar * 32), ("DaylightDate", SYSTEMTIME),
                    ("DaylightBias", c_long)]

    class TRACE_LOGFILE_HEADER(ctypes.Structure):
        _fields_ = [("BufferSize", c_ulong), ("Version", c_ulong), ("ProviderVersion", c_ulong),
                    ("NumberOfProcessors", c_ulong), ("EndTime", c_int64), ("TimerResolution", c_ulong),
                    ("MaximumFileSize", c_ulong), ("LogFileMode", c_ulong), ("BuffersWritten", c_ulong),
                    ("LogInstanceGuid", GUID), ("LoggerName", c_void_p), ("LogFileName", c_void_p),
                    ("TimeZone", TIME_ZONE_INFORMATION), ("BootTime", c_int64), ("PerfFreq", c_int64),
                    ("StartTime", c_int64), ("ReservedFlags", c_ulong), ("BuffersLost", c_ulong)]

    class EVENT_DESCRIPTOR(ctypes.Structure):
        _fields_ = [("Id", c_ushort), ("Version", c_ubyte), ("Channel", c_ubyte), ("Level", c_ubyte),
                    ("Opcode", c_ubyte), ("Task", c_ushort), ("Keyword", c_uint64)]

    class EVENT_HEADER(ctypes.Structure):
        _fields_ = [("Size", c_ushort), ("HeaderType", c_ushort), ("Flags", c_ushort), ("EventProperty", c_ushort),
                    ("ThreadId", c_ulong), ("ProcessId", c_ulong), ("TimeStamp", c_int64), ("ProviderId", GUID),
                    ("EventDescriptor", EVENT_DESCRIPTOR), ("ProcessorTime", c_uint64), ("ActivityId", GUID)]

    class EVENT_RECORD(ctypes.Structure):
        _fields_ = [("EventHeader", EVENT_HEADER), ("ProcessorNumber", c_ubyte), ("Alignment", c_ubyte),
                    ("LoggerId", c_ushort), ("ExtendedDataCount", c_ushort), ("UserDataLength", c_ushort),
                    ("ExtendedData", c_void_p), ("UserData", c_void_p), ("UserContext", c_void_p)]

    CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.c_void_p)

    class EVENT_TRACE_LOGFILEW(ctypes.Structure):
        _fields_ = [("LogFileName", ctypes.c_wchar_p), ("LoggerName", ctypes.c_wchar_p), ("CurrentTime", c_int64),
                    ("BuffersRead", c_ulong), ("ProcessTraceMode", c_ulong), ("CurrentEvent", EVENT_TRACE),
                    ("LogfileHeader", TRACE_LOGFILE_HEADER), ("BufferCallback", c_void_p),
                    ("BufferSize", c_ulong), ("Filled", c_ulong), ("EventsLost", c_ulong),
                    ("EventRecordCallback", CALLBACK), ("IsKernelTrace", c_ulong), ("Context", c_void_p)]

    _adv = ctypes.WinDLL("advapi32")
    _adv.StartTraceW.argtypes = [ctypes.POINTER(c_uint64), ctypes.c_wchar_p, ctypes.POINTER(PROPS)]
    _adv.StartTraceW.restype = c_ulong
    _adv.ControlTraceW.argtypes = [c_uint64, ctypes.c_wchar_p, ctypes.POINTER(PROPS), c_ulong]
    _adv.ControlTraceW.restype = c_ulong
    _adv.EnableTraceEx2.argtypes = [c_uint64, ctypes.POINTER(GUID), c_ulong, c_ubyte, c_uint64, c_uint64,
                                    c_ulong, c_void_p]
    _adv.EnableTraceEx2.restype = c_ulong
    _adv.OpenTraceW.argtypes = [ctypes.POINTER(EVENT_TRACE_LOGFILEW)]
    _adv.OpenTraceW.restype = c_uint64
    _adv.ProcessTrace.argtypes = [ctypes.POINTER(c_uint64), c_ulong, c_void_p, c_void_p]
    _adv.ProcessTrace.restype = c_ulong
    _adv.CloseTrace.argtypes = [c_uint64]
    _adv.CloseTrace.restype = c_ulong

    _U16 = struct.Struct("<H").unpack_from
    _U64 = struct.Struct("<Q").unpack_from
    _I64 = struct.Struct("<q").unpack_from
    _f = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(_f))
    FREQ = _f.value or 10_000_000          # o relógio dos eventos (QPC): o mesmo de time.perf_counter()
    KERNEL_NETWORK = "7DD42A49-5329-4832-8DFD-43D979153A88"
    # id do evento -> (é envio?, é IPv6?, é UDP?)
    EVENTOS = {10: (True, False, False), 11: (False, False, False), 26: (True, True, False), 27: (False, True, False),
               42: (True, False, True), 43: (False, False, True), 58: (True, True, True), 59: (False, True, True)}


class MonitorRede:
    """Conta bytes por (pid, IP local, IP remoto) usando ETW."""
    SESSAO = "MonitorPC-Rede"

    def __init__(self):
        self.ok = False
        self.erro = None
        self.eventos = 0
        self._acc = {}
        self.detalhe = []
        self._th = None
        self._handle = None
        self._cb = None
        self._locais = set()
        self._ult_locais = 0
        self.fluxos = []
        self._ult = {}               # fluxo UDP -> (hora do último pacote que chegou, intervalo anterior)
        self._ult_poda = 0

    # -- callback: chamado para cada envio/recebimento; precisa ser leve
    def _evento(self, addr):
        # Lê o EVENT_RECORD direto da memória (mais rápido que montar a estrutura):
        # hora (QPC) no offset 16, Id do evento no 40, UserDataLength no 86 e o ponteiro UserData no 96.
        try:
            cab = ctypes.string_at(addr, 104)
            k = EVENTOS.get(_U16(cab, 40)[0])
            if k is None:
                return
            envio, v6, udp = k
            n = 44 if v6 else 20                      # PID, tamanho, destino, origem, porta destino, porta origem
            ud = _U64(cab, 96)[0]
            if not ud or _U16(cab, 86)[0] < n:
                return
            buf = ctypes.string_at(ud, n)
            pid, tam = struct.unpack_from("<II", buf, 0)
            if v6:
                chave = (pid, buf[8:24], buf[24:40], buf[40:44], udp)
            else:
                chave = (pid, buf[8:12], buf[12:16], buf[16:20], udp)
            acc = self._acc
            v = acc.get(chave)
            if v is None:
                v = acc[chave] = [0, 0, 0, 0, 0, 0, 0]     # bytes e pacotes (env, rec), variação, n, maior intervalo
            i = 0 if envio else 1
            v[i] += tam
            v[i + 2] += 1
            if udp and not envio:
                ts = _I64(cab, 16)[0]
                u = self._ult.get(chave)
                if u is None:
                    self._ult[chave] = (ts, None)
                else:
                    gap = ts - u[0]
                    if gap > v[6]:
                        v[6] = gap
                    if gap > 2 * FREQ:              # voltou depois de um silêncio: recomeça a medir o ritmo
                        self._ult[chave] = (ts, None)
                    else:
                        if u[1] is not None:
                            v[4] += abs(gap - u[1])
                            v[5] += 1
                        self._ult[chave] = (ts, gap)
            self.eventos += 1
        except Exception:
            pass

    def _props(self):
        pr = PROPS()
        pr.p.Wnode.BufferSize = ctypes.sizeof(PROPS)
        pr.p.Wnode.Flags = 0x00020000  # WNODE_FLAG_TRACED_GUID
        pr.p.Wnode.ClientContext = 1   # QPC
        pr.p.Wnode.Guid = _guid("6c1b6f3a-6b4e-4c62-9d3c-2f6f0b1d7a11")
        pr.p.LogFileMode = 0x00000100  # EVENT_TRACE_REAL_TIME_MODE
        pr.p.FlushTimer = 1
        pr.p.BufferSize = 64        # KB
        pr.p.MinimumBuffers = 16
        pr.p.MaximumBuffers = 256
        pr.p.LoggerNameOffset = ctypes.sizeof(EVENT_TRACE_PROPERTIES)
        pr.p.LogFileNameOffset = 0
        return pr

    def iniciar(self):
        if not WIN:
            self.erro = "Disponível só no Windows"
            return False
        try:
            pr = self._props()
            _adv.ControlTraceW(0, self.SESSAO, ctypes.byref(pr), 1)  # para sessão antiga
            pr = self._props()
            h = c_uint64(0)
            st = _adv.StartTraceW(ctypes.byref(h), self.SESSAO, ctypes.byref(pr))
            if st != 0:
                self.erro = f"StartTrace falhou ({st}). O serviço precisa rodar como administrador."
                return False
            self._handle = h.value
            g = _guid(KERNEL_NETWORK)
            st = _adv.EnableTraceEx2(self._handle, ctypes.byref(g), 1, 5, 0x30, 0, 0, None)
            if st != 0:
                self.erro = f"EnableTraceEx2 falhou ({st})"
                self.parar()
                return False
            self._cb = CALLBACK(self._evento)
            lf = EVENT_TRACE_LOGFILEW()
            lf.LoggerName = self.SESSAO
            lf.ProcessTraceMode = 0x00000100 | 0x10000000  # REAL_TIME | EVENT_RECORD
            lf.EventRecordCallback = self._cb
            self._lf = lf
            th = _adv.OpenTraceW(ctypes.byref(lf))
            if th == 0xFFFFFFFFFFFFFFFF:
                self.erro = f"OpenTrace falhou ({ctypes.GetLastError()})"
                self.parar()
                return False
            self._trace = c_uint64(th)
            self._th = threading.Thread(target=self._loop, name="etw-rede", daemon=True)
            self._th.start()
            self.ok = True
            return True
        except Exception as e:  # noqa: BLE001
            self.erro = f"ETW indisponível: {e}"
            return False

    def _loop(self):
        try:
            _adv.ProcessTrace(ctypes.byref(self._trace), 1, None, None)
        except Exception as e:  # noqa: BLE001
            self.erro = f"ETW parou: {e}"
        finally:
            self.ok = False

    def parar(self):
        if not WIN:
            return
        try:
            pr = self._props()
            _adv.ControlTraceW(0, self.SESSAO, ctypes.byref(pr), 1)
        except Exception:
            pass
        try:
            if getattr(self, "_trace", None) is not None:
                _adv.CloseTrace(self._trace.value)
        except Exception:
            pass

    def estatisticas(self):
        """Eventos perdidos pela sessão ETW (para diagnóstico)."""
        if not WIN or not self.ok:
            return None
        try:
            pr = self._props()
            if _adv.ControlTraceW(0, self.SESSAO, ctypes.byref(pr), 0) != 0:
                return None
            return {"eventos_perdidos": pr.p.EventsLost, "buffers_perdidos": pr.p.RealTimeBuffersLost,
                    "buffers": pr.p.NumberOfBuffers}
        except Exception:
            return None

    def _ips_locais(self):
        if time.time() - self._ult_locais > 60:
            s = set()
            try:
                for lst in psutil.net_if_addrs().values():
                    for a in lst:
                        if a.family in (socket.AF_INET, socket.AF_INET6):
                            s.add(a.address.split("%")[0])
            except Exception:
                pass
            self._locais = s
            self._ult_locais = time.time()
        return self._locais

    def coletar(self):
        """Devolve [(pid, ip_remoto, env, rec)] desde a última chamada (bytes)."""
        acc, self._acc = self._acc, {}
        lista, self.detalhe, self.fluxos = agrupar(acc, self._ips_locais(), FREQ)
        agora = time.perf_counter()
        if agora - self._ult_poda > 10:           # fluxos parados há mais de 10 s saem do controle de ritmo
            self._ult_poda = agora
            limite = (agora - 10) * FREQ
            for k in [k for k, u in list(self._ult.items()) if u[0] < limite]:
                self._ult.pop(k, None)
        return lista


def servico(porta, udp):
    """Nome do serviço pela porta conhecida (QUIC é o HTTP/3 sobre UDP 443)."""
    if udp and porta == 443:
        return "QUIC (HTTP/3)"
    if 27000 <= porta <= 27100:
        return "Steam/Valve (jogo)"
    if 3478 <= porta <= 3481 or 19302 <= porta <= 19309:
        return "STUN/TURN (chamadas)"
    return PORTAS.get(porta)


def agrupar(acc, locais, freq=10_000_000):
    """acc {(pid, destino, origem, portas, udp): [env, rec, pacotes_env, pacotes_rec, (variação, n, maior intervalo)]}
    do ETW -> ([(pid, ip_remoto, env, rec)], [(ip_remoto, porta_remota, porta_local, udp, env, rec, pk_env, pk_rec)],
    [(pid, ip_remoto, porta_remota, porta_local, udp, env, rec, pk_env, pk_rec, variação_ms, n, maior_intervalo_ms)]).
    Variação: soma de |intervalo - intervalo anterior| entre pacotes UDP que chegam (dividida por n = jitter)."""
    out = {}
    det = {}
    flu = {}
    ms = 1000 / freq
    for (pid, d, s, portas, udp), v in list(acc.items()):
        env, rec, pe, pr = v[:4]
        var, nvar, gap = v[4:7] if len(v) >= 7 else (0, 0, 0)
        try:
            fam = socket.AF_INET6 if len(d) == 16 else socket.AF_INET
            ip_d = socket.inet_ntop(fam, d)
            ip_s = socket.inet_ntop(fam, s)
            p_d, p_s = struct.unpack(">HH", portas)
        except (OSError, ValueError, struct.error):
            continue
        if ip_d.startswith("::ffff:"):
            ip_d = ip_d[7:]
        if ip_s.startswith("::ffff:"):
            ip_s = ip_s[7:]
        remoto, p_rem, p_loc = ip_d, p_d, p_s
        if ip_d in locais and ip_s not in locais:
            remoto, p_rem, p_loc = ip_s, p_s, p_d
        if tipo_ip(remoto) == "este PC":
            continue  # tráfego interno do próprio PC não conta
        v = out.setdefault((pid, remoto), [0, 0])
        v[0] += env
        v[1] += rec
        x = det.setdefault((remoto, p_rem, p_loc, udp), [0, 0, 0, 0])
        for i, n in enumerate((env, rec, pe, pr)):
            x[i] += n
        f = flu.setdefault((pid, remoto, p_rem, p_loc, udp), [0, 0, 0, 0, 0.0, 0, 0.0])
        for i, n in enumerate((env, rec, pe, pr, var * ms, nvar)):
            f[i] += n
        f[6] = max(f[6], gap * ms)
    return ([(k[0], k[1], v[0], v[1]) for k, v in out.items()],
            [(*k, *v) for k, v in det.items()],
            [(*k, *v) for k, v in flu.items()])


class FluxosVivos:
    """Últimos 10 s de cada fluxo (processo, IP e portas remota/local, TCP ou UDP), para a tabela de conexões em
    andamento: taxa, pacotes por segundo, jitter de chegada (UDP) e o maior silêncio."""
    JANELA = 10

    def __init__(self):
        self.lock = threading.Lock()
        self.f = {}          # chave -> deque[(t, env, rec, pe, pr, var_ms, n, gap_ms)]

    def add(self, t, fluxos):
        from collections import deque
        with self.lock:
            for pid, ip, p_rem, p_loc, udp, *v in fluxos:
                self.f.setdefault((pid, ip, p_rem, p_loc, udp), deque()).append((t, *v))

    def lista(self, agora=None):
        agora = agora or time.time()
        out = []
        with self.lock:
            for k in list(self.f):
                q = self.f[k]
                while q and q[0][0] < agora - self.JANELA:
                    q.popleft()
                if not q:
                    del self.f[k]
                    continue
                rec2 = [x for x in q if x[0] >= agora - 2]
                nvar = sum(x[6] for x in q)
                out.append({"pid": k[0], "ip": k[1], "rport": k[2], "lport": k[3], "udp": k[4],
                            "env": round(sum(x[1] for x in rec2) / 2), "rec": round(sum(x[2] for x in rec2) / 2),
                            "pps": round(sum(x[3] + x[4] for x in rec2) / 2, 1),
                            "jitter": round(sum(x[5] for x in q) / nvar, 2) if nvar >= 5 else None,
                            "silencio": round(max(x[7] for x in q), 1) if k[4] else None,
                            "bytes_10s": sum(x[1] + x[2] for x in q)})
        return out


class TrafegoIP:
    """Série por segundo de cada IP remoto (TCP × UDP, bytes e pacotes) e tráfego por serviço."""
    SEG = 600          # 10 minutos por IP
    MAX_IPS = 400

    def __init__(self):
        self.lock = threading.Lock()
        self.ips = {}                 # ip -> {"seg": {ts: [tcp_e, tcp_r, udp_e, udp_r, pk_e, pk_r]}, "tot": [...], "serv": {}}
        self.servicos = {}            # ts -> {servico: bytes}

    def add(self, t, detalhe):
        ts = int(t)
        with self.lock:
            sv = self.servicos.setdefault(ts, {})
            for ip, p_rem, p_loc, udp, env, rec, pe, pr in detalhe:
                x = self.ips.get(ip)
                if x is None:
                    if len(self.ips) >= self.MAX_IPS:
                        self._podar(ts)
                    x = self.ips[ip] = {"seg": {}, "tot": [0] * 6, "serv": {}, "ult": ts}
                s = x["seg"].setdefault(ts, [0] * 6)
                o = 2 if udp else 0
                s[o] += env
                s[o + 1] += rec
                s[4] += pe
                s[5] += pr
                for i, n in ((o, env), (o + 1, rec), (4, pe), (5, pr)):
                    x["tot"][i] += n
                x["ult"] = ts
                nome = servico(p_rem, udp) or servico(p_loc, udp) or ("Outro UDP" if udp else "Outro TCP")
                a = x["serv"].setdefault(nome, [0, 0])
                a[0] += env
                a[1] += rec
                sv[nome] = sv.get(nome, 0) + env + rec
            limite = ts - self.SEG
            for k in [k for k in self.servicos if k < limite]:
                del self.servicos[k]

    def _podar(self, ts):
        velho = min(self.ips, key=lambda k: self.ips[k]["ult"])
        del self.ips[velho]

    def resumo(self, ip, agora=None):
        """Totais, PPS, pico, sustentado e a série dos últimos 10 minutos de um IP (ou None)."""
        ts = int(agora or time.time())
        with self.lock:
            x = self.ips.get(ip)
            if not x:
                return None
            for k in [k for k in x["seg"] if k < ts - self.SEG]:
                del x["seg"][k]
            serie = sorted(x["seg"].items())
            tot, serv = list(x["tot"]), {k: list(v) for k, v in x["serv"].items()}
        return {"tot": dict(zip(("tcp_env", "tcp_rec", "udp_env", "udp_rec", "pacotes_env", "pacotes_rec"), tot)),
                "servicos": [{"nome": k, "env": v[0], "rec": v[1]} for k, v in sorted(serv.items(), key=lambda kv: -sum(kv[1]))],
                **estatisticas_serie(serie, ts), "serie": [[k, *v] for k, v in serie]}

    def por_ip(self):
        """{ip: {tcp, udp, pps}} do último segundo completo (para a tabela de IPs)."""
        ts = int(time.time()) - 1
        out = {}
        with self.lock:
            for ip, x in self.ips.items():
                s = x["seg"].get(ts)
                tot = x["tot"]
                out[ip] = {"tcp_tot": tot[0] + tot[1], "udp_tot": tot[2] + tot[3],
                           "pps": (s[4] + s[5]) if s else 0, "servicos": sorted(x["serv"], key=lambda k: -sum(x["serv"][k]))[:3]}
        return out

    def protocolos(self):
        """Série por segundo (10 min) de bytes por serviço, para o gráfico por protocolo."""
        with self.lock:
            itens = sorted(self.servicos.items())
        nomes = {}
        for _, d in itens:
            for k, v in d.items():
                nomes[k] = nomes.get(k, 0) + v
        top = sorted(nomes, key=lambda k: -nomes[k])[:7]
        return {"nomes": top, "totais": {k: nomes[k] for k in top},
                "serie": [[ts, *[d.get(k, 0) for k in top]] for ts, d in itens]}


def estatisticas_serie(serie, agora):
    """serie [(ts, [tcp_e, tcp_r, udp_e, udp_r, pk_e, pk_r])] -> pico, sustentado (média de 60 s), burst e PPS."""
    bps = [sum(v[:4]) for _, v in serie]
    ult60 = [sum(v[:4]) for k, v in serie if k > agora - 60]
    pps = [v[4] + v[5] for k, v in serie if k > agora - 60]
    sust = sum(ult60) / 60 if ult60 else 0
    pico = max(bps) if bps else 0
    return {"pico": pico, "sustentado": round(sust), "burst": round(pico / sust, 1) if sust else None,
            "pps": round(sum(pps) / 60, 1) if pps else 0, "pps_pico": max(pps) if pps else 0}


if not WIN:     # Linux: captura AF_PACKET + tabela de sockets do kernel no lugar do ETW
    from .linux.rede import MonitorRede, FREQ  # noqa: E402,F401,F811
