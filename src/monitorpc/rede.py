"""Rede por processo.

- Conexões abertas (TCP/UDP) com o PID dono: psutil.
- Bytes enviados/recebidos por processo e por IP remoto: ETW do Windows
  (provedor Microsoft-Windows-Kernel-Network). Precisa de administrador,
  por isso roda dentro do serviço.
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

    CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.POINTER(EVENT_RECORD))

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

    KERNEL_NETWORK = "7DD42A49-5329-4832-8DFD-43D979153A88"
    # id do evento -> (é envio?, é IPv6?)
    EVENTOS = {10: (True, False), 11: (False, False), 26: (True, True), 27: (False, True),
               42: (True, False), 43: (False, False), 58: (True, True), 59: (False, True)}


class MonitorRede:
    """Conta bytes por (pid, IP local, IP remoto) usando ETW."""
    SESSAO = "MonitorPC-Rede"

    def __init__(self):
        self.ok = False
        self.erro = None
        self.eventos = 0
        self._acc = {}
        self._th = None
        self._handle = None
        self._cb = None
        self._locais = set()
        self._ult_locais = 0

    # -- callback: chamado para cada envio/recebimento; precisa ser leve
    def _evento(self, prec):
        try:
            r = prec.contents
            k = EVENTOS.get(r.EventHeader.EventDescriptor.Id)
            if k is None or not r.UserData:
                return
            envio, v6 = k
            n = 40 if v6 else 16
            if r.UserDataLength < n:
                return
            buf = ctypes.string_at(r.UserData, n)
            pid, tam = struct.unpack_from("<II", buf, 0)
            if v6:
                chave = (pid, buf[8:24], buf[24:40])
            else:
                chave = (pid, buf[8:12], buf[12:16])
            acc = self._acc
            v = acc.get(chave)
            if v is None:
                v = acc[chave] = [0, 0]
            v[0 if envio else 1] += tam
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
        pr.p.BufferSize = 64
        pr.p.MinimumBuffers = 8
        pr.p.MaximumBuffers = 64
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
        locais = self._ips_locais()
        out = {}
        for (pid, d, s, ), (env, rec) in list(acc.items()):
            try:
                fam = socket.AF_INET6 if len(d) == 16 else socket.AF_INET
                ip_d = socket.inet_ntop(fam, d)
                ip_s = socket.inet_ntop(fam, s)
            except (OSError, ValueError):
                continue
            if ip_d.startswith("::ffff:"):
                ip_d = ip_d[7:]
            if ip_s.startswith("::ffff:"):
                ip_s = ip_s[7:]
            remoto = ip_d
            if ip_d in locais and ip_s not in locais:
                remoto = ip_s
            if tipo_ip(remoto) == "este PC":
                continue  # tráfego interno do próprio PC não conta
            k = (pid, remoto)
            v = out.get(k)
            if v is None:
                out[k] = [env, rec]
            else:
                v[0] += env
                v[1] += rec
        return [(k[0], k[1], v[0], v[1]) for k, v in out.items()]
