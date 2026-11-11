"""Energia no Linux: perfis do power-profiles-daemon (o "Modo de energia" do GNOME/KDE) ou, sem ele, o governor
da CPU; brilho pelo /sys/class/backlight."""
import os
import glob

from . import ler, rodar

NOMES = {"power-saver": "Economia de energia", "balanced": "Equilibrado", "performance": "Alto desempenho",
         "powersave": "Economia de energia (governor)", "schedutil": "Equilibrado (governor)",
         "ondemand": "Equilibrado (governor)", "conservative": "Econômico (governor)"}
ALTO_DESEMPENHO = "performance"
DESEMPENHO_MAXIMO = "performance"
MODOS = {}
GAMEBAR = NOTIF = ""


def _ppd():
    c, txt = rodar(["powerprofilesctl", "list"], timeout=5)
    if c != 0:
        return None
    out = []
    for linha in txt.splitlines():
        s = linha.strip()
        if s.endswith(":") and not s.startswith(("CpuDriver", "PlatformDriver", "Degraded")):
            ativo = s.startswith("*")
            g = s.lstrip("* ").rstrip(":")
            out.append({"guid": g, "nome": NOMES.get(g, g), "ativo": ativo, "conhecido": NOMES.get(g)})
    return out


def _governors():
    cpus = glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq")
    if not cpus:
        return []
    disp = (ler(os.path.join(cpus[0], "scaling_available_governors"), "") or "").split()
    atual = ler(os.path.join(cpus[0], "scaling_governor"))
    return [{"guid": g, "nome": NOMES.get(g, g), "ativo": g == atual, "conhecido": NOMES.get(g)} for g in disp]


def planos():
    p = _ppd()
    return p if p is not None else _governors()


def plano_ativo():
    return next((p for p in planos() if p["ativo"]), None)


def ativar_plano(guid):
    guid = str(guid or "")
    if guid not in [p["guid"] for p in planos()]:
        return False, "Perfil de energia desconhecido."
    if _ppd() is not None:
        c, txt = rodar(["powerprofilesctl", "set", guid], timeout=10)
        return c == 0, txt.strip() or "Perfil ativado."
    try:
        for f in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor"):
            with open(f, "w") as a:
                a.write(guid)
    except OSError as e:
        return False, f"O Linux não deixou trocar o governor: {e.strerror or e}"
    return True, "Perfil ativado."


def criar_plano(base):
    return None, "No Linux os perfis de energia são fixos (economia, equilibrado e desempenho)."


def ajustes():
    return []


def mudar_ajuste(chave, onde, valor):
    return False, "No Linux esse ajuste fica nas Configurações do próprio ambiente (GNOME, KDE, Cinnamon)."


def modo_energia():
    return None


def mudar_modo_energia(guid):
    return ativar_plano(guid)


def ajustes_windows():
    return []


def mudar_windows(chave, ativo):
    return False, "Ajuste exclusivo do Windows."


def _backlight():
    return sorted(glob.glob("/sys/class/backlight/*"))


def brilho():
    for b in _backlight():
        try:
            return round(int(ler(os.path.join(b, "brightness"))) * 100 / max(1, int(ler(os.path.join(b, "max_brightness")))))
        except (TypeError, ValueError):
            continue
    return None


def mudar_brilho(nivel):
    nivel = max(1, min(100, int(nivel)))
    for b in _backlight():
        try:
            m = int(ler(os.path.join(b, "max_brightness")))
            with open(os.path.join(b, "brightness"), "w") as f:
                f.write(str(round(m * nivel / 100)))
            return True, "Brilho alterado."
        except (OSError, TypeError, ValueError):
            continue
    return False, "Este computador não tem controle de brilho pelo sistema (comum em monitores de mesa)."


def estado():
    pl = planos()
    return {"planos": pl, "ativo": next((p for p in pl if p["ativo"]), None), "ajustes": [], "modo": None, "modos": [],
            "windows": [], "brilho": brilho(), "tem_alto": any(p["guid"] == "performance" for p in pl),
            "tem_maximo": any(p["guid"] == "performance" for p in pl), "sistema": "linux"}


def garantir_alto_desempenho():
    return next((p["guid"] for p in planos() if p["guid"] == "performance"), None)
