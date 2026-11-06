"""Temperatura e sensores de hardware.

Ordem de tentativa:
 1. LibreHardwareMonitor (CPU, GPU, SSD, placa-mãe, ventoinhas, clocks, consumo).
    A temperatura da CPU precisa do driver PawnIO (o instalador oferece).
 2. Complementos quando faltar algo: nvidia-smi (GPU NVIDIA) e zonas térmicas ACPI (WMI).
"""
import os
import json
import time
import threading
import subprocess

from . import config

WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0

TIPOS = {
    "Temperature": ("Temperatura", "°C"), "Load": ("Uso", "%"), "Clock": ("Frequência", "MHz"),
    "Fan": ("Ventoinha", "RPM"), "Power": ("Consumo", "W"), "Voltage": ("Tensão", "V"),
    "Data": ("Dados", "GB"), "SmallData": ("Dados", "MB"), "Throughput": ("Taxa", "B/s"),
    "Level": ("Nível", "%"), "Control": ("Controle", "%"), "Current": ("Corrente", "A"),
    "Energy": ("Energia", "mWh"), "Frequency": ("Frequência", "Hz"), "Factor": ("Fator", ""),
    "Noise": ("Ruído", "dBA"), "Conductivity": ("Condutividade", ""), "Flow": ("Fluxo", "L/h"),
    "Humidity": ("Umidade", "%"), "TimeSpan": ("Tempo", "s"),
}
TIPOS_HW = {
    "Cpu": "Processador", "GpuNvidia": "Placa de vídeo", "GpuAmd": "Placa de vídeo", "GpuIntel": "Vídeo integrado",
    "Memory": "Memória RAM", "Motherboard": "Placa-mãe", "SuperIO": "Placa-mãe", "Storage": "Armazenamento",
    "Network": "Rede", "Cooler": "Refrigeração", "EmbeddedController": "Placa-mãe", "Psu": "Fonte",
    "Battery": "Bateria",
}


def _rodar(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=SEM_JANELA)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


class Sensores:
    def __init__(self):
        self.lock = threading.RLock()
        self.ts = 0
        self.fontes = []
        self.avisos = []
        self.lista = []       # sensores atuais
        self._minmax = {}
        self._pc = None
        self._lhm = False
        self._ult_acpi = 0
        self._acpi = []
        self._ult_nv = 0
        self._nv = []
        self.pawnio = None

    # ---------------- LibreHardwareMonitor ----------------
    def _abrir_lhm(self):
        pasta = os.path.join(config.pasta_recursos(), "lhm")
        dll = os.path.join(pasta, "LibreHardwareMonitorLib.dll")
        if not os.path.exists(dll):
            self.avisos.append("LibreHardwareMonitor não encontrado no pacote.")
            return
        try:
            from pythonnet import load
            try:
                load("netfx")
            except Exception:
                pass
            import clr  # noqa: F401
            from System import AppDomain, ResolveEventHandler  # type: ignore
            from System.Reflection import Assembly  # type: ignore

            def resolver(_s, args):
                nome = str(args.Name).split(",")[0]
                p = os.path.join(pasta, nome + ".dll")
                return Assembly.LoadFrom(p) if os.path.exists(p) else None

            self._resolver = ResolveEventHandler(resolver)
            AppDomain.CurrentDomain.AssemblyResolve += self._resolver
            clr.AddReference(dll)
            from LibreHardwareMonitor.Hardware import Computer  # type: ignore
            pc = Computer()
            for a in ("IsCpuEnabled", "IsGpuEnabled", "IsMemoryEnabled", "IsMotherboardEnabled",
                      "IsStorageEnabled", "IsControllerEnabled", "IsBatteryEnabled", "IsPsuEnabled"):
                try:
                    setattr(pc, a, True)
                except Exception:
                    pass
            pc.Open()
            self._pc = pc
            self._lhm = True
            self.fontes.append("LibreHardwareMonitor")
        except Exception as e:  # noqa: BLE001
            self.avisos.append(f"LibreHardwareMonitor não carregou: {e}")

    def _ler_lhm(self):
        out = []

        def visitar(hw, pai=None):
            try:
                hw.Update()
            except Exception:
                return
            nome_hw = str(hw.Name)
            tipo_hw = TIPOS_HW.get(str(hw.HardwareType), str(hw.HardwareType))
            for s in hw.Sensors:
                try:
                    v = s.Value
                    if v is None:
                        continue
                    v = float(v)
                    t = str(s.SensorType)
                    rot, un = TIPOS.get(t, (t, ""))
                    out.append({"id": str(s.Identifier), "hw": pai or nome_hw, "sub": nome_hw if pai else "",
                                "tipo_hw": tipo_hw, "nome": str(s.Name), "tipo": rot, "tipo_en": t,
                                "valor": round(v, 2), "un": un, "fonte": "LHM"})
                except Exception:
                    continue
            for sub in hw.SubHardware:
                visitar(sub, nome_hw)

        for hw in self._pc.Hardware:
            visitar(hw)
        return out

    # ---------------- complementos ----------------
    def _ler_nvidia(self):
        if time.time() - self._ult_nv < 10:
            return self._nv
        self._ult_nv = time.time()
        txt = _rodar(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,"
                      "power.draw,fan.speed,clocks.gr", "--format=csv,noheader,nounits"])
        out = []
        for i, linha in enumerate(txt.strip().splitlines()):
            p = [x.strip() for x in linha.split(",")]
            if len(p) < 8:
                continue
            nome = p[0]

            def add(sid, rot, tipo, tipo_en, v, un):
                try:
                    out.append({"id": f"/nvsmi/{i}/{sid}", "hw": nome, "sub": "", "tipo_hw": "Placa de vídeo",
                                "nome": rot, "tipo": tipo, "tipo_en": tipo_en, "valor": float(v), "un": un,
                                "fonte": "nvidia-smi"})
                except ValueError:
                    pass
            add("temp", "GPU Core", "Temperatura", "Temperature", p[1], "°C")
            add("uso", "GPU Core", "Uso", "Load", p[2], "%")
            add("memu", "Memória de vídeo usada", "Dados", "SmallData", p[3], "MB")
            add("memt", "Memória de vídeo total", "Dados", "SmallData", p[4], "MB")
            add("pot", "Consumo", "Consumo", "Power", p[5], "W")
            add("fan", "Ventoinha", "Controle", "Control", p[6], "%")
            add("clk", "Clock da GPU", "Frequência", "Clock", p[7], "MHz")
        self._nv = out
        return out

    def _ler_acpi(self):
        if time.time() - self._ult_acpi < 30:
            return self._acpi
        self._ult_acpi = time.time()
        txt = _rodar(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      "Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
                      "-ErrorAction SilentlyContinue | Select-Object InstanceName,CurrentTemperature | ConvertTo-Json"])
        out = []
        try:
            dados = json.loads(txt) if txt.strip() else []
            if isinstance(dados, dict):
                dados = [dados]
            for i, d in enumerate(dados):
                c = (float(d["CurrentTemperature"]) / 10.0) - 273.15
                if -20 < c < 130:
                    nome = str(d.get("InstanceName", f"Zona {i}")).split("\\")[-1].replace("_0", "")
                    out.append({"id": f"/acpi/{i}", "hw": "Zonas térmicas (ACPI)", "sub": "", "tipo_hw": "Placa-mãe",
                                "nome": nome or f"Zona {i}", "tipo": "Temperatura", "tipo_en": "Temperature",
                                "valor": round(c, 1), "un": "°C", "fonte": "ACPI"})
        except Exception:
            pass
        self._acpi = out
        return out

    def _checar_pawnio(self):
        if not WIN:
            return
        txt = _rodar(["sc", "query", "PawnIO"], timeout=5)
        self.pawnio = "RUNNING" in txt or "STOPPED" in txt

    # ---------------- ciclo ----------------
    def iniciar(self):
        if not WIN:
            self.avisos.append("Sensores de temperatura só no Windows.")
            return
        self._checar_pawnio()
        self._abrir_lhm()

    def ler(self):
        lista = []
        if self._lhm:
            try:
                lista = self._ler_lhm()
            except Exception as e:  # noqa: BLE001
                self.avisos.append(f"Erro lendo sensores: {e}")
        if WIN:
            tem_gpu_temp = any(s["tipo_en"] == "Temperature" and s["tipo_hw"] == "Placa de vídeo" for s in lista)
            if not tem_gpu_temp:
                lista += self._ler_nvidia()
            tem_cpu_temp = any(s["tipo_en"] == "Temperature" and s["tipo_hw"] == "Processador" for s in lista)
            if not tem_cpu_temp:
                lista += self._ler_acpi()
        agora = time.time()
        for s in lista:
            mm = self._minmax.get(s["id"])
            v = s["valor"]
            if mm is None:
                mm = self._minmax[s["id"]] = [v, v]
            mm[0] = min(mm[0], v)
            mm[1] = max(mm[1], v)
            s["min"], s["max"] = mm
        with self.lock:
            self.lista = lista
            self.ts = agora
        return lista

    def resumo(self):
        """Temperatura principal da CPU e da GPU, uso da GPU."""
        with self.lock:
            lst = list(self.lista)
        temps = [s for s in lst if s["tipo_en"] == "Temperature"]

        def escolher(tipo_hw, preferidos):
            cand = [s for s in temps if s["tipo_hw"] == tipo_hw]
            for p in preferidos:
                for s in cand:
                    if s["nome"].lower() == p:
                        return s["valor"]
            return max((s["valor"] for s in cand), default=None)

        cpu = escolher("Processador", ["cpu package", "core max", "core average", "tctl/tdie", "cpu"])
        if cpu is None:
            acpi = [s["valor"] for s in temps if s["fonte"] == "ACPI"]
            cpu = max(acpi) if acpi else None
        gpu = escolher("Placa de vídeo", ["gpu core", "gpu hot spot"])
        disco = max((s["valor"] for s in temps if s["tipo_hw"] == "Armazenamento"), default=None)
        uso_gpu = max((s["valor"] for s in lst if s["tipo_en"] == "Load" and s["tipo_hw"] in ("Placa de vídeo", "Vídeo integrado")
                       and s["nome"].lower() in ("gpu core", "d3d 3d")), default=None)
        return {"temp_cpu": cpu, "temp_gpu": gpu, "temp_disco": disco, "uso_gpu": uso_gpu}

    def estado(self):
        with self.lock:
            return {"sensores": list(self.lista), "fontes": self.fontes, "avisos": self.avisos[-5:],
                    "pawnio": self.pawnio, "resumo": self.resumo()}
