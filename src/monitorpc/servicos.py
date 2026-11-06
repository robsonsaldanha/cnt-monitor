"""Serviços do Windows: lista, iniciar/parar/reiniciar e tipo de inicialização.
Serviços do próprio Windows ficam protegidos (só leitura)."""
import os
import time
import threading
import subprocess

import psutil

WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0
WINDIR = os.environ.get("SystemRoot", r"C:\Windows").lower()
_cache = {"t": 0, "lista": []}
_lock = threading.Lock()

STATUS = {"running": "Em execução", "stopped": "Parado", "start_pending": "Iniciando", "stop_pending": "Parando",
          "paused": "Pausado", "continue_pending": "Continuando", "pause_pending": "Pausando"}
INICIO = {"automatic": "Automático", "manual": "Manual", "disabled": "Desativado"}
PROTEGIDOS_NOMES = {"monitorpc", "pawnio", "windefend", "wdnissvc", "mpssvc", "bfe", "eventlog", "rpcss", "dcomlaunch",
                    "lsm", "samss", "winmgmt", "plugplay", "power", "profsvc", "schedule", "cryptsvc", "sense",
                    "securityhealthservice", "wscsvc", "audiosrv", "audioendpointbuilder", "themes", "dhcp", "dnscache",
                    "nsi", "netprofm", "nlasvc", "lanmanworkstation", "brokerinfrastructure", "systemeventsbroker",
                    "coremessagingregistrar", "usermanager", "staterepository", "wuauserv", "trustedinstaller"}


def _exe_de(binpath):
    b = (binpath or "").strip()
    if b.startswith('"'):
        return b[1:].split('"', 1)[0]
    return b.split(" -")[0].split(" /")[0].strip()


def protecao(nome, binpath):
    n = (nome or "").lower()
    if n in PROTEGIDOS_NOMES:
        return "Serviço essencial"
    exe = os.path.expandvars(_exe_de(binpath)).lower()
    if exe.startswith(WINDIR) or "\\windows defender\\" in exe or "\\microsoft\\windows defender" in exe:
        return "Serviço do Windows"
    if not exe:
        return "Serviço do Windows"
    return None


def listar(forcar=False):
    if not WIN:
        return []
    with _lock:
        if not forcar and time.time() - _cache["t"] < 20:
            return _cache["lista"]
    out = []
    for s in psutil.win_service_iter():
        try:
            d = s.as_dict()
        except Exception:
            continue
        exe = _exe_de(d.get("binpath"))
        out.append({"nome": d["name"], "exibicao": d.get("display_name") or d["name"], "status": d.get("status"),
                    "status_txt": STATUS.get(d.get("status"), d.get("status")), "inicio": d.get("start_type"),
                    "inicio_txt": INICIO.get(d.get("start_type"), d.get("start_type")), "pid": d.get("pid") or 0,
                    "usuario": d.get("username") or "", "descricao": (d.get("description") or "")[:400],
                    "binpath": d.get("binpath") or "", "exe": os.path.expandvars(exe),
                    "protegido": protecao(d["name"], d.get("binpath"))})
    with _lock:
        _cache.update(t=time.time(), lista=out)
    return out


def _sc(*args):
    r = subprocess.run(["sc.exe", *args], capture_output=True, timeout=30, creationflags=SEM_JANELA)
    return r.returncode, (r.stdout or b"").decode("cp850", "replace")


def acao(nome, oque, inicio=None):
    s = next((x for x in listar(True) if x["nome"].lower() == (nome or "").lower()), None)
    if not s:
        return False, "Serviço não encontrado."
    if s["protegido"]:
        return False, f'"{s["exibicao"]}" é um {s["protegido"].lower()} e está protegido. Mexer nele pode deixar o Windows instável.'
    import win32serviceutil
    try:
        if oque == "iniciar":
            win32serviceutil.StartService(s["nome"])
            msg = "Serviço iniciado."
        elif oque == "parar":
            win32serviceutil.StopService(s["nome"])
            msg = "Serviço parado."
        elif oque == "reiniciar":
            win32serviceutil.RestartService(s["nome"])
            msg = "Serviço reiniciado."
        elif oque == "inicio":
            mapa = {"automatico": "auto", "atrasado": "delayed-auto", "manual": "demand", "desativado": "disabled"}
            if inicio not in mapa:
                return False, "Tipo de inicialização inválido."
            c, txt = _sc("config", s["nome"], "start=", mapa[inicio])
            if c != 0:
                return False, txt.strip()[-200:]
            msg = "Tipo de inicialização alterado."
        else:
            return False, "Ação desconhecida."
    except Exception as e:  # noqa: BLE001
        return False, f"O Windows não deixou: {getattr(e, 'strerror', None) or e}"
    with _lock:
        _cache["t"] = 0
    return True, msg
