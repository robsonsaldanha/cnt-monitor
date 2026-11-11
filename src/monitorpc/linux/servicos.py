"""Serviços do systemd: lista, iniciar/parar/reiniciar e ligar/desligar na inicialização.
Os essenciais para o sistema ficam protegidos (só leitura), como no Windows."""
import time
import threading

from . import rodar

_cache = {"t": 0, "lista": []}
_lock = threading.Lock()
STATUS = {"running": "Em execução", "stopped": "Parado", "start_pending": "Iniciando", "stop_pending": "Parando",
          "failed": "Falhou"}
INICIO = {"automatic": "Automático", "manual": "Manual", "disabled": "Desativado"}
ESSENCIAIS = {"dbus", "systemd-journald", "systemd-logind", "systemd-udevd", "systemd-resolved", "systemd-networkd",
              "systemd-timesyncd", "networkmanager", "network-manager", "polkit", "udisks2", "accounts-daemon",
              "gdm", "gdm3", "sddm", "lightdm", "display-manager", "cnt-monitor", "cnt-monitor-encontro", "apparmor",
              "ufw", "upower", "rtkit-daemon", "wpa_supplicant", "ssh", "sshd", "cron", "rsyslog", "snapd",
              "getty@tty1", "user@1000", "systemd-user-sessions", "plymouth", "bluetooth", "cups", "power-profiles-daemon"}


def ler_units(units, arquivos):
    """Saída de `systemctl list-units` e `list-unit-files` -> lista no formato da tela Serviços."""
    inicio = {}
    for linha in arquivos.splitlines():
        p = linha.split()
        if len(p) >= 2 and p[0].endswith(".service"):
            inicio[p[0]] = p[1]
    out = []
    for linha in units.splitlines():
        p = linha.split(None, 4)
        if len(p) < 4 or not p[0].endswith(".service"):
            continue
        unit, _load, ativo, sub = p[:4]
        nome = unit[:-8]
        st = {"running": "running", "exited": "stopped", "dead": "stopped", "failed": "failed",
              "start": "start_pending", "auto-restart": "start_pending", "stop": "stop_pending"}.get(sub, "stopped")
        if ativo == "activating":
            st = "start_pending"
        ini = inicio.get(unit, "static")
        tipo = "automatic" if ini in ("enabled", "enabled-runtime", "alias") else "disabled" if ini in ("disabled", "masked") else "manual"
        out.append({"nome": nome, "exibicao": (p[4] if len(p) > 4 else nome)[:200], "status": st,
                    "status_txt": STATUS.get(st, st), "inicio": tipo, "inicio_txt": INICIO.get(tipo, tipo), "pid": 0,
                    "usuario": "", "descricao": (p[4] if len(p) > 4 else "")[:400], "binpath": "", "exe": "",
                    "protegido": protecao(nome)})
    return out


def protecao(nome, _binpath=None):
    n = (nome or "").lower()
    if n in ESSENCIAIS or n.startswith(("systemd-", "dbus", "getty@", "user@", "user-runtime-dir@")):
        return "Serviço essencial"
    return None


def listar(forcar=False):
    with _lock:
        if not forcar and time.time() - _cache["t"] < 20:
            return _cache["lista"]
    c1, units = rodar(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"], 15)
    _c2, arqs = rodar(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"], 15)
    out = ler_units(units, arqs) if c1 == 0 else []
    with _lock:
        _cache.update(t=time.time(), lista=out)
    return out


def acao(nome, oque, inicio=None):
    s = next((x for x in listar(True) if x["nome"].lower() == (nome or "").lower()), None)
    if not s:
        return False, "Serviço não encontrado."
    if s["protegido"]:
        return False, f'"{s["exibicao"]}" é um serviço essencial e está protegido. Mexer nele pode deixar o sistema instável.'
    unit = s["nome"] + ".service"
    if oque in ("iniciar", "parar", "reiniciar"):
        cmd, msg = {"iniciar": ("start", "Serviço iniciado."), "parar": ("stop", "Serviço parado."),
                    "reiniciar": ("restart", "Serviço reiniciado.")}[oque]
        c, txt = rodar(["systemctl", cmd, unit], 60)
    elif oque == "inicio":
        mapa = {"automatico": "enable", "atrasado": "enable", "manual": "disable", "desativado": "disable"}
        if inicio not in mapa:
            return False, "Tipo de inicialização inválido."
        c, txt = rodar(["systemctl", mapa[inicio], unit], 30)
        msg = "Tipo de inicialização alterado."
    else:
        return False, "Ação desconhecida."
    with _lock:
        _cache["t"] = 0
    return (True, msg) if c == 0 else (False, f"O systemd não deixou: {txt.strip()[-200:]}")
