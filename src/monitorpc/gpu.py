"""Placas de vídeo: lista (DXGI), uso por GPU e por processo (contadores "GPU Engine"
do Windows, os mesmos do Gerenciador de Tarefas) e memória de vídeo por processo."""
import os
import re
import time
import threading
from collections import defaultdict

WIN = os.name == "nt"
_RE_ENG = re.compile(r"pid_(\d+)_luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)_phys_\d+_eng_\d+_engtype_(.*)")
_RE_PMEM = re.compile(r"pid_(\d+)_luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)")
_RE_AMEM = re.compile(r"luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)")

if WIN:
    import ctypes
    from ctypes import wintypes as wt

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

    class _DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                    ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]

    class _ITEM(ctypes.Structure):
        _fields_ = [("nome", ctypes.c_wchar_p), ("status", ctypes.c_ulong), ("valor", ctypes.c_double)]


def listar_adaptadores():
    """[{luid:(alto,baixo), nome, mem_dedicada, mem_compartilhada}] via DXGI."""
    if not WIN:
        return []
    import uuid
    dxgi = ctypes.WinDLL("dxgi")
    u = uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387")  # IDXGIFactory1
    iid = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    fab = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(fab)) != 0 or not fab:
        return []
    vt = ctypes.cast(fab, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    enum1 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(vt[12])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    out = []
    i = 0
    while i < 16:
        ad = ctypes.c_void_p()
        if enum1(fab, i, ctypes.byref(ad)) != 0 or not ad:
            break
        avt = ctypes.cast(ad, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        desc1 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_DESC1))(avt[10])
        d = _DESC1()
        if desc1(ad, ctypes.byref(d)) == 0 and not (d.Flags & 2):  # ignora adaptador de software
            out.append({"luid": (d.AdapterLuid.HighPart & 0xFFFFFFFF, d.AdapterLuid.LowPart), "nome": d.Description.strip(),
                        "mem_dedicada": int(d.DedicatedVideoMemory), "mem_compartilhada": int(d.SharedSystemMemory),
                        "fabricante": {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel"}.get(d.VendorId, "")})
        release(avt[2])(ad)
        i += 1
    release(vt[2])(fab)
    return out


class _PDH:
    def __init__(self, caminhos):
        self.pdh = ctypes.WinDLL("pdh")
        self.q = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.q)) != 0:
            raise OSError("PdhOpenQuery")
        self.c = {}
        for k, p in caminhos.items():
            h = ctypes.c_void_p()
            st = self.pdh.PdhAddEnglishCounterW(self.q, ctypes.c_wchar_p(p), None, ctypes.byref(h))
            if st == 0:
                self.c[k] = h
        if not self.c:
            raise OSError("contadores de GPU indisponíveis")
        self.pdh.PdhCollectQueryData(self.q)

    def coletar(self):
        self.pdh.PdhCollectQueryData(self.q)
        out = {}
        for k, h in self.c.items():
            tam = wt.DWORD(0)
            n = wt.DWORD(0)
            st = self.pdh.PdhGetFormattedCounterArrayW(h, 0x8200, ctypes.byref(tam), ctypes.byref(n), None)
            if (st & 0xFFFFFFFF) != 0x800007D2 or not tam.value:
                out[k] = []
                continue
            buf = ctypes.create_string_buffer(tam.value)
            st = self.pdh.PdhGetFormattedCounterArrayW(h, 0x8200, ctypes.byref(tam), ctypes.byref(n), buf)
            if st != 0:
                out[k] = []
                continue
            itens = ctypes.cast(buf, ctypes.POINTER(_ITEM))
            out[k] = [(itens[i].nome, itens[i].valor) for i in range(n.value) if itens[i].status in (0, 1)]
        return out


class MonitorGPU:
    def __init__(self):
        self.lock = threading.Lock()
        self.ok = False
        self.erro = None
        self.adaptadores = []
        self.por_pid = {}       # pid -> {"uso": %, "mem": bytes, "luid": luid principal}
        self.por_gpu = {}       # luid -> {"uso", "engines": {tipo: %}, "mem_ded", "mem_comp"}
        self._pdh = None
        self._ult_lista = 0

    def iniciar(self):
        if not WIN:
            self.erro = "Só no Windows"
            return
        try:
            self.adaptadores = listar_adaptadores()
            self._ult_lista = time.time()
        except Exception as e:  # noqa: BLE001
            self.erro = f"DXGI: {e}"
        try:
            self._pdh = _PDH({"eng": r"\GPU Engine(*)\Utilization Percentage",
                              "pmem": r"\GPU Process Memory(*)\Dedicated Usage",
                              "aded": r"\GPU Adapter Memory(*)\Dedicated Usage",
                              "acomp": r"\GPU Adapter Memory(*)\Shared Usage"})
            self.ok = True
        except Exception as e:  # noqa: BLE001
            self.erro = f"Contadores de GPU: {e}"

    def ler(self):
        if not self._pdh:
            return
        if time.time() - self._ult_lista > 600:
            try:
                self.adaptadores = listar_adaptadores()
            except Exception:
                pass
            self._ult_lista = time.time()
        d = self._pdh.coletar()
        soma_tipo = defaultdict(float)           # (luid, tipo) -> %
        pid_eng = defaultdict(float)             # (pid, luid, tipo) -> %
        for nome, v in d.get("eng", []):
            m = _RE_ENG.match(nome or "")
            if not m:
                continue
            pid, alto, baixo, tipo = int(m.group(1)), int(m.group(2), 16), int(m.group(3), 16), m.group(4)
            tipo = tipo.split("#")[0]
            luid = (alto, baixo)
            soma_tipo[(luid, tipo)] += v
            pid_eng[(pid, luid, tipo)] += v
        por_gpu = {}
        for (luid, tipo), v in soma_tipo.items():
            g = por_gpu.setdefault(luid, {"uso": 0.0, "engines": {}, "mem_ded": 0, "mem_comp": 0})
            g["engines"][tipo] = min(100.0, v)
            g["uso"] = max(g["uso"], min(100.0, v))
        for chave, k in (("aded", "mem_ded"), ("acomp", "mem_comp")):
            for nome, v in d.get(chave, []):
                m = _RE_AMEM.search(nome or "")
                if m:
                    luid = (int(m.group(1), 16), int(m.group(2), 16))
                    por_gpu.setdefault(luid, {"uso": 0.0, "engines": {}, "mem_ded": 0, "mem_comp": 0})[k] += int(v)
        por_pid = {}
        for (pid, luid, _tipo), v in pid_eng.items():
            p = por_pid.setdefault(pid, {"uso": 0.0, "mem": 0, "luid": luid})
            if v > p["uso"]:
                p["uso"] = min(100.0, v)
                p["luid"] = luid
        for nome, v in d.get("pmem", []):
            m = _RE_PMEM.match(nome or "")
            if m:
                pid = int(m.group(1))
                por_pid.setdefault(pid, {"uso": 0.0, "mem": 0, "luid": (int(m.group(2), 16), int(m.group(3), 16))})["mem"] += int(v)
        with self.lock:
            self.por_gpu = por_gpu
            self.por_pid = por_pid

    def resumo(self, sensores=None):
        """Lista de GPUs com uso, memória e temperatura (cruzando o nome com os sensores)."""
        with self.lock:
            por_gpu = dict(self.por_gpu)
        out = []
        for i, a in enumerate(self.adaptadores):
            g = por_gpu.get(a["luid"], {})
            temp = None
            if sensores:
                chave = _norm(a["nome"])
                for s in sensores:
                    if s["tipo_en"] == "Temperature" and not s.get("limite") and s["tipo_hw"] in ("Placa de vídeo", "Vídeo integrado") \
                            and (_norm(s["hw"]) in chave or chave in _norm(s["hw"])):
                        if temp is None or "core" in s["nome"].lower():
                            temp = s["valor"]
            out.append({"id": "%08X%08X" % a["luid"], "indice": i, "nome": a["nome"], "fabricante": a["fabricante"],
                        "uso": round(g.get("uso", 0.0), 1), "engines": {k: round(v, 1) for k, v in g.get("engines", {}).items()},
                        "mem_ded_usada": g.get("mem_ded", 0), "mem_ded_total": a["mem_dedicada"],
                        "mem_comp_usada": g.get("mem_comp", 0), "mem_comp_total": a["mem_compartilhada"], "temp": temp})
        return out

    def processo(self, pid):
        with self.lock:
            return self.por_pid.get(pid)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("(r)", "").replace("(tm)", ""))
