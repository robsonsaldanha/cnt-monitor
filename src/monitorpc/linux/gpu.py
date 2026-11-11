"""GPU no Linux: placas pelo /sys/class/drm (AMD, Intel) e pelo nvidia-smi (NVIDIA com driver proprietário);
uso por processo pelo /proc/<pid>/fdinfo dos drivers DRM (amdgpu, i915, xe, msm...) e pelo nvidia-smi pmon."""
import os
import re
import glob
import time
import threading

from . import ler, rodar

FABRICANTES = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}
_RE_ENGINE = re.compile(r"^drm-engine-(\w+):\s+(\d+)\s*ns", re.M)
_RE_MEM = re.compile(r"^drm-(?:memory|resident)-(\w+):\s+(\d+)\s*(KiB|MiB)?", re.M)


def _int(v, padrao=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


def placas_drm(raiz="/sys/class/drm"):
    out = []
    for card in sorted(glob.glob(os.path.join(raiz, "card[0-9]"))):
        dev = os.path.join(card, "device")
        fab = FABRICANTES.get(ler(os.path.join(dev, "vendor"), ""), "")
        if not fab:
            continue
        nome = ler(os.path.join(dev, "product_name")) or f"{fab} {os.path.basename(card)}"
        out.append({"luid": os.path.basename(card), "nome": nome, "fabricante": fab, "dev": dev,
                    "mem_dedicada": _int(ler(os.path.join(dev, "mem_info_vram_total"))), "mem_compartilhada": 0})
    return out


class MonitorGPU:
    def __init__(self):
        self.lock = threading.Lock()
        self.ok = False
        self.erro = None
        self.adaptadores = []
        self.por_pid = {}
        self.por_gpu = {}
        self._ant = {}           # (pid, fd/engine) -> ns acumulados na leitura anterior
        self._t = 0.0
        self._nv = False

    def iniciar(self):
        self.adaptadores = placas_drm()
        c, txt = rodar(["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"], timeout=10)
        if c == 0:
            self._nv = True
            drm = [a for a in self.adaptadores if a["fabricante"] != "NVIDIA"]
            for linha in txt.strip().splitlines():
                p = [x.strip() for x in linha.split(",")]
                if len(p) >= 3:
                    drm.append({"luid": f"nv{p[0]}", "nome": p[1], "fabricante": "NVIDIA", "dev": None,
                                "mem_dedicada": _int(p[2]) * 2 ** 20, "mem_compartilhada": 0})
            self.adaptadores = drm
        self.ok = bool(self.adaptadores)
        if not self.ok:
            self.erro = "Nenhuma placa de vídeo encontrada (comum em máquinas virtuais)."

    def _ler_nvidia(self, por_gpu, por_pid):
        c, txt = rodar(["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used", "--format=csv,noheader,nounits"], timeout=5)
        for linha in txt.strip().splitlines() if c == 0 else []:
            p = [x.strip() for x in linha.split(",")]
            if len(p) >= 3:
                por_gpu[f"nv{p[0]}"] = {"uso": float(_int(p[1])), "engines": {"3D": float(_int(p[1]))},
                                        "mem_ded": _int(p[2]) * 2 ** 20, "mem_comp": 0}
        c, txt = rodar(["nvidia-smi", "pmon", "-c", "1", "-s", "um"], timeout=5)
        for linha in txt.splitlines() if c == 0 else []:
            p = linha.split()
            if len(p) >= 4 and p[0].isdigit() and p[1].isdigit():
                uso = float(p[3]) if p[3].replace(".", "").isdigit() else 0.0
                mem = _int(p[-2]) * 2 ** 20 if len(p) >= 9 and p[-2].isdigit() else 0
                d = por_pid.setdefault(int(p[1]), {"uso": 0.0, "mem": 0, "luid": f"nv{p[0]}"})
                d["uso"], d["mem"] = max(d["uso"], uso), d["mem"] + mem

    def _ler_fdinfo(self, por_pid, dt):
        atual = {}
        for pid_txt in os.listdir("/proc"):
            if not pid_txt.isdigit():
                continue
            pasta = f"/proc/{pid_txt}/fd"
            try:
                fds = os.listdir(pasta)
            except OSError:
                continue
            vistos = set()
            for fd in fds:
                try:
                    if not os.readlink(f"{pasta}/{fd}").startswith("/dev/dri/"):
                        continue
                    with open(f"/proc/{pid_txt}/fdinfo/{fd}") as f:
                        info = f.read()
                except OSError:
                    continue
                cliente = re.search(r"^drm-client-id:\s+(\d+)", info, re.M)
                if not cliente or cliente.group(1) in vistos:      # o mesmo cliente aberto em dois fds conta uma vez
                    continue
                vistos.add(cliente.group(1))
                pdev = re.search(r"^drm-pdev:\s+(\S+)", info, re.M)
                pid = int(pid_txt)
                for eng, ns in _RE_ENGINE.findall(info):
                    k = (pid, cliente.group(1), eng)
                    atual[k] = int(ns)
                    if k in self._ant and dt > 0:
                        uso = min(100.0, (int(ns) - self._ant[k]) / (dt * 1e9) * 100)
                        d = por_pid.setdefault(pid, {"uso": 0.0, "mem": 0, "luid": self._luid_pdev(pdev)})
                        d["uso"] = max(d["uso"], round(uso, 1))
                for tipo, v, un in _RE_MEM.findall(info):
                    if tipo in ("vram", "local", "gtt", "system", "memory"):
                        d = por_pid.setdefault(pid, {"uso": 0.0, "mem": 0, "luid": self._luid_pdev(pdev)})
                        d["mem"] += int(v) * (2 ** 20 if un == "MiB" else 1024 if un == "KiB" else 1)
        self._ant = atual

    def _luid_pdev(self, m):
        if m:
            for a in self.adaptadores:
                if a["dev"] and os.path.basename(os.path.realpath(a["dev"])) == m.group(1):
                    return a["luid"]
        return self.adaptadores[0]["luid"] if self.adaptadores else None

    def ler(self):
        if not self.ok:
            return
        agora = time.monotonic()
        dt, self._t = (agora - self._t if self._t else 0), agora
        por_gpu, por_pid = {}, {}
        for a in self.adaptadores:
            if a["dev"]:
                uso = _int(ler(os.path.join(a["dev"], "gpu_busy_percent")), None)
                por_gpu[a["luid"]] = {"uso": float(uso or 0), "engines": {"3D": float(uso)} if uso is not None else {},
                                      "mem_ded": _int(ler(os.path.join(a["dev"], "mem_info_vram_used"))),
                                      "mem_comp": _int(ler(os.path.join(a["dev"], "mem_info_gtt_used")))}
        if self._nv:
            self._ler_nvidia(por_gpu, por_pid)
        try:
            self._ler_fdinfo(por_pid, dt)
        except OSError:
            pass
        with self.lock:
            self.por_gpu, self.por_pid = por_gpu, por_pid

    def resumo(self, sensores=None):
        from ..gpu import _norm
        with self.lock:
            por_gpu = dict(self.por_gpu)
        out = []
        for i, a in enumerate(self.adaptadores):
            g = por_gpu.get(a["luid"], {})
            temp = None
            for s in sensores or []:
                if s["tipo_en"] == "Temperature" and not s.get("limite") and s["tipo_hw"] in ("Placa de vídeo", "Vídeo integrado") \
                        and (len(self.adaptadores) == 1 or _norm(s["hw"]) in _norm(a["nome"]) or _norm(a["nome"]) in _norm(s["hw"])):
                    if temp is None or "core" in s["nome"].lower():
                        temp = s["valor"]
            out.append({"id": a["luid"], "indice": i, "nome": a["nome"], "fabricante": a["fabricante"],
                        "uso": round(g.get("uso", 0.0), 1), "engines": {k: round(v, 1) for k, v in g.get("engines", {}).items()},
                        "mem_ded_usada": g.get("mem_ded", 0), "mem_ded_total": a["mem_dedicada"],
                        "mem_comp_usada": g.get("mem_comp", 0), "mem_comp_total": a["mem_compartilhada"], "temp": temp})
        return out

    def processo(self, pid):
        with self.lock:
            return self.por_pid.get(pid)
