"""Sensores no Linux: /sys/class/hwmon (o que o lm-sensors mostra), bateria e nvidia-smi.

Mesma lista de sensores do LibreHardwareMonitor no Windows ({id, hw, tipo_hw, nome, tipo, tipo_en, valor, un}),
então a tela Hardware, os alertas, o mini painel e as curvas de ventoinha funcionam iguais.
Ventoinhas: os canais pwmN que o chip deixa escrever viram controles com SetSoftware/SetDefault."""
import os
import glob
import time

from . import ler

HWMON = "/sys/class/hwmon"
CHIPS = {
    "coretemp": "Processador", "k10temp": "Processador", "zenpower": "Processador", "cpu_thermal": "Processador",
    "amdgpu": "Placa de vídeo", "radeon": "Placa de vídeo", "nouveau": "Placa de vídeo", "i915": "Vídeo integrado",
    "xe": "Vídeo integrado", "nvme": "Armazenamento", "drivetemp": "Armazenamento", "acpitz": "Placa-mãe",
    "iwlwifi_1": "Rede", "iwlwifi": "Rede", "mt7921_phy0": "Rede", "r8169_0_": "Rede", "spd5118": "Memória RAM",
    "jc42": "Memória RAM", "BAT0": "Bateria", "BAT1": "Bateria", "pch_cannonlake": "Placa-mãe",
}
# arquivo -> (tipo, tipo em inglês, unidade, divisor)
TIPOS = {"temp": ("Temperatura", "Temperature", "°C", 1000), "fan": ("Ventoinha", "Fan", "RPM", 1),
         "in": ("Tensão", "Voltage", "V", 1000), "power": ("Consumo", "Power", "W", 1_000_000),
         "curr": ("Corrente", "Current", "A", 1000), "freq": ("Frequência", "Clock", "MHz", 1_000_000)}


def _tipo_hw(chip):
    if chip in CHIPS:
        return CHIPS[chip]
    if chip.startswith(("nct", "it8", "f71", "asus", "dell", "thinkpad", "hp", "applesmc", "w83", "gigabyte")):
        return "Placa-mãe"
    if chip.startswith(("nvme", "drivetemp")):
        return "Armazenamento"
    return "Placa-mãe"


def _nome(chip, base, rotulo, tipo_hw):
    r = rotulo or base
    if tipo_hw == "Processador":
        if r.lower().startswith("package id"):
            return "CPU Package" + ("" if r.endswith(" 0") else f" {r.split()[-1]}")
        if r.lower() in ("tctl", "tdie"):
            return "CPU Tctl/Tdie"
        if r.lower().startswith("core "):
            return "CPU " + r
    if tipo_hw == "Placa de vídeo" and r.lower() in ("edge", "junction"):
        return {"edge": "GPU Core", "junction": "GPU Hot Spot"}[r.lower()]
    return r


class ControlePwm:
    """Um canal pwm do hwmon com a mesma interface do controle do LHM: SetSoftware(%) e SetDefault()."""

    def __init__(self, caminho):
        self.caminho = caminho
        self._enable = caminho + "_enable"
        self._original = ler(self._enable)

    def SetSoftware(self, pct):  # noqa: N802 (mesmo nome do LibreHardwareMonitor)
        with open(self._enable, "w") as f:
            f.write("1")                             # 1 = controle manual
        with open(self.caminho, "w") as f:
            f.write(str(round(max(0.0, min(100.0, float(pct))) * 255 / 100)))

    def SetDefault(self):  # noqa: N802
        with open(self._enable, "w") as f:
            f.write(self._original if self._original not in (None, "1") else "2")    # 2 = automático do chip


class Sensores:
    def __init__(self, raiz=HWMON):
        import threading
        self.raiz = raiz
        self.lock = threading.RLock()
        self.ts = 0
        self.fontes = []
        self.avisos = []
        self.lista = []
        self._minmax = {}
        self._pc = None
        self.pawnio = None
        self._ctrl = {}
        self._ult_nv = 0
        self._nv = []

    def iniciar(self):
        if os.path.isdir(self.raiz) and os.listdir(self.raiz):
            self.fontes = ["hwmon (lm-sensors)"]
        else:
            self.avisos.append("O kernel não mostra sensores de hardware neste computador (comum em máquinas virtuais).")

    def _ler_hwmon(self):
        out = []
        chips = {}
        for pasta in sorted(glob.glob(os.path.join(self.raiz, "hwmon*"))):
            chip = ler(os.path.join(pasta, "name"), os.path.basename(pasta))
            n = chips[chip] = chips.get(chip, 0) + 1
            chip_id = chip if n == 1 else f"{chip}{n}"
            tipo_hw = _tipo_hw(chip)
            hw = ler(os.path.join(pasta, "device", "model")) or chip
            for arq in sorted(glob.glob(os.path.join(pasta, "*_input"))):
                base = os.path.basename(arq)[:-6]          # temp1, fan2, in0...
                pref = base.rstrip("0123456789")
                if pref not in TIPOS:
                    continue
                v = ler(arq)
                try:
                    valor = float(v) / TIPOS[pref][3]
                except (TypeError, ValueError):
                    continue
                tipo, tipo_en, un, _ = TIPOS[pref]
                rotulo = ler(os.path.join(pasta, base + "_label"))
                num = base[len(pref):]
                out.append({"id": f"/hwmon/{chip_id}/{ 'fan' if pref == 'fan' else pref}/{num}", "hw": hw, "sub": "",
                            "tipo_hw": tipo_hw, "nome": _nome(chip, base, rotulo, tipo_hw), "tipo": tipo, "tipo_en": tipo_en,
                            "valor": round(valor, 2), "un": un, "fonte": "hwmon"})
                if pref == "temp":
                    for lim, nome in (("crit", "Critical"), ("max", "High Temp")):
                        lv = ler(os.path.join(pasta, f"{base}_{lim}"))
                        try:
                            if lv and 0 < float(lv) / 1000 < 150:
                                out.append({"id": f"/hwmon/{chip_id}/temp/{num}/{lim}", "hw": hw, "sub": "", "tipo_hw": tipo_hw,
                                            "nome": f"{_nome(chip, base, rotulo, tipo_hw)} {nome}", "tipo": tipo,
                                            "tipo_en": tipo_en, "valor": round(float(lv) / 1000, 1), "un": un, "fonte": "hwmon"})
                        except ValueError:
                            pass
            for pwm in sorted(glob.glob(os.path.join(pasta, "pwm[0-9]"))) + sorted(glob.glob(os.path.join(pasta, "pwm[0-9][0-9]"))):
                v = ler(pwm)
                try:
                    pct = round(int(v) * 100 / 255, 1)
                except (TypeError, ValueError):
                    continue
                num = os.path.basename(pwm)[3:]
                cid = f"/hwmon/{chip_id}/control/{num}"
                if os.access(pwm, os.W_OK) and os.path.exists(pwm + "_enable"):
                    self._ctrl.setdefault(cid, ControlePwm(pwm))
                out.append({"id": cid, "hw": hw, "sub": "", "tipo_hw": tipo_hw, "nome": f"Ventoinha {num}",
                            "tipo": "Controle", "tipo_en": "Control", "valor": pct, "un": "%", "fonte": "hwmon"})
        return out

    def _ler_bateria(self):
        out = []
        for pasta in glob.glob("/sys/class/power_supply/BAT*"):
            nome = os.path.basename(pasta)
            cap = ler(os.path.join(pasta, "capacity"))
            if cap and cap.isdigit():
                out.append({"id": f"/bateria/{nome}/nivel", "hw": ler(os.path.join(pasta, "model_name")) or "Bateria",
                            "sub": "", "tipo_hw": "Bateria", "nome": "Carga", "tipo": "Nível", "tipo_en": "Level",
                            "valor": float(cap), "un": "%", "fonte": "power_supply"})
            pot = ler(os.path.join(pasta, "power_now"))
            if pot and pot.isdigit() and int(pot):
                out.append({"id": f"/bateria/{nome}/consumo", "hw": ler(os.path.join(pasta, "model_name")) or "Bateria",
                            "sub": "", "tipo_hw": "Bateria", "nome": "Consumo", "tipo": "Consumo", "tipo_en": "Power",
                            "valor": round(int(pot) / 1e6, 2), "un": "W", "fonte": "power_supply"})
        return out

    def ler(self):
        from ..temperatura import SensoresWindows as Base
        lista = self._ler_hwmon() + self._ler_bateria()
        if not any(s["tipo_en"] == "Temperature" and s["tipo_hw"] == "Placa de vídeo" for s in lista):
            lista += Base._ler_nvidia(self)       # NVIDIA com o driver proprietário não aparece no hwmon
        agora = time.time()
        limpa = []
        for s in lista:
            if s["tipo_en"] == "Temperature":
                if s["valor"] <= 0 or s["valor"] > 150:
                    continue
                n = s["nome"].lower()
                s["limite"] = any(w in n for w in ("critical", "high temp", "limit", "threshold"))
            limpa.append(s)
        for s in limpa:
            mm = self._minmax.setdefault(s["id"], [s["valor"], s["valor"]])
            mm[0], mm[1] = min(mm[0], s["valor"]), max(mm[1], s["valor"])
            s["min"], s["max"] = mm
        with self.lock:
            self.lista = limpa
            self.ts = agora
        return limpa

    def controles(self):
        return dict(self._ctrl)

    def resumo(self):
        from ..temperatura import SensoresWindows as Base
        return Base.resumo(self)

    def estado(self):
        from ..temperatura import SensoresWindows as Base
        return Base.estado(self)
