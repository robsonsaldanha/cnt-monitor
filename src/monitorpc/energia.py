"""Planos de energia e configurações do Windows, sem abrir as Configurações do Windows."""
import os
import re
import json
import subprocess

from . import sessao

WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0

PLANOS_CONHECIDOS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Equilibrado",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "Alto desempenho",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Economia de energia",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "Desempenho máximo",
}
ALTO_DESEMPENHO = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
DESEMPENHO_MAXIMO = "e9a42b02-d5df-448d-aa00-03f14749eb61"

MIN = [1, 2, 3, 5, 10, 15, 20, 25, 30, 45, 60, 120, 180, 300, 0]
AJUSTES = [
    # chave, título, subgrupo, ajuste, tipo, opções
    ("tela", "Desligar a tela após", "SUB_VIDEO", "VIDEOIDLE", "min", MIN),
    ("suspender", "Suspender (dormir) após", "SUB_SLEEP", "STANDBYIDLE", "min", MIN),
    ("hibernar", "Hibernar após", "SUB_SLEEP", "HIBERNATEIDLE", "min", MIN),
    ("disco", "Desligar o disco rígido após", "SUB_DISK", "DISKIDLE", "min", MIN),
    ("cpu_max", "Desempenho máximo do processador", "SUB_PROCESSOR", "PROCTHROTTLEMAX", "pct",
     [50, 60, 70, 80, 90, 99, 100]),
    ("cpu_min", "Desempenho mínimo do processador", "SUB_PROCESSOR", "PROCTHROTTLEMIN", "pct",
     [0, 5, 10, 20, 50, 100]),
    ("tampa", "Ao fechar a tampa", "SUB_BUTTONS", "LIDACTION", "enum",
     {0: "Não fazer nada", 1: "Suspender", 2: "Hibernar", 3: "Desligar"}),
    ("botao", "Ao apertar o botão de energia", "SUB_BUTTONS", "PBUTTONACTION", "enum",
     {0: "Não fazer nada", 1: "Suspender", 2: "Hibernar", 3: "Desligar", 4: "Desligar a tela"}),
]

# Modo de energia do Windows 11 (Configurações > Energia > Modo de energia)
MODOS = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "Melhor eficiência de energia",
    "00000000-0000-0000-0000-000000000000": "Equilibrado",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "Melhor desempenho",
    "ded574b5-45a0-4f42-8737-46345c09c238": "Melhor desempenho (máximo)",
}

AJUSTES_WINDOWS = [
    # chave, título, descrição
    ("modo_jogo", "Modo de Jogo do Windows", "Dá prioridade ao jogo e evita atualizações do Windows durante a partida."),
    ("notificacoes", "Notificações do Windows", "Avisos que aparecem no canto da tela."),
    ("transparencia", "Efeitos de transparência", "Desligar deixa a interface um pouco mais leve."),
    ("hibernacao", "Hibernação", "Permite hibernar (salva tudo no disco e desliga). Desligar libera espaço no disco C:."),
    ("inicializacao_rapida", "Inicialização rápida", "Liga o PC mais rápido. Às vezes causa problemas com atualizações e dual boot."),
]


def _rodar(args, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=SEM_JANELA)
        txt = r.stdout.decode("cp850", errors="replace") if r.stdout else ""
        return r.returncode, txt
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def planos():
    if not WIN:
        return []
    _, txt = _rodar(["powercfg", "/list"])
    out = []
    for linha in txt.splitlines():
        m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s+\((.+?)\)(\s*\*)?", linha)
        if m:
            guid = m.group(1).lower()
            out.append({"guid": guid, "nome": m.group(2).strip(), "ativo": bool(m.group(3)),
                        "conhecido": PLANOS_CONHECIDOS.get(guid)})
    return out


def plano_ativo():
    if not WIN:
        return None
    return next((p for p in planos() if p["ativo"]), None)


def ativar_plano(guid):
    c, txt = _rodar(["powercfg", "/setactive", guid])
    return c == 0, txt.strip()


def criar_plano(base):
    """Cria (copia) um plano oculto do Windows, ex.: Alto desempenho ou Desempenho máximo."""
    c, txt = _rodar(["powercfg", "-duplicatescheme", base])
    m = re.search(r"([0-9a-fA-F-]{36})", txt)
    return (m.group(1).lower() if m else None), txt.strip()


def _ler_ajuste(sub, aj):
    c, txt = _rodar(["powercfg", "/q", "SCHEME_CURRENT", sub, aj])
    if c != 0:
        return None
    hexes = re.findall(r":\s*0x([0-9a-fA-F]{8})\s*$", txt, re.M)
    if len(hexes) < 2:
        return None
    return {"tomada": int(hexes[-2], 16), "bateria": int(hexes[-1], 16)}


def ajustes():
    out = []
    for chave, tit, sub, aj, tipo, op in AJUSTES:
        v = _ler_ajuste(sub, aj)
        if v is None:
            continue
        if tipo == "min":
            v = {k: (x // 60 if x else 0) for k, x in v.items()}
        out.append({"chave": chave, "titulo": tit, "tipo": tipo, "valor": v,
                    "opcoes": op if isinstance(op, list) else [{"v": k, "t": t} for k, t in op.items()]})
    return out


def mudar_ajuste(chave, onde, valor):
    item = next((a for a in AJUSTES if a[0] == chave), None)
    if not item or onde not in ("tomada", "bateria"):
        return False, "Ajuste desconhecido."
    _, _, sub, aj, tipo, _op = item
    valor = int(valor)
    if tipo == "min":
        valor = valor * 60
    cmd = "/setacvalueindex" if onde == "tomada" else "/setdcvalueindex"
    c, txt = _rodar(["powercfg", cmd, "SCHEME_CURRENT", sub, aj, str(valor)])
    if c != 0:
        return False, txt.strip() or "O Windows recusou essa mudança."
    _rodar(["powercfg", "/setactive", "SCHEME_CURRENT"])
    return True, "Salvo."


# ---------------- modo de energia (overlay do Windows 10/11) ----------------
def _guid_struct(s):
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]
    u = uuid.UUID(s)
    return GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def modo_energia():
    if not WIN:
        return None
    try:
        import ctypes
        import uuid
        g = _guid_struct("00000000-0000-0000-0000-000000000000")
        if ctypes.WinDLL("powrprof").PowerGetEffectiveOverlayScheme(ctypes.byref(g)) != 0:
            return None
        s = str(uuid.UUID(bytes_le=bytes(g)))
        return {"guid": s, "nome": MODOS.get(s, s)}
    except Exception:
        return None


def mudar_modo_energia(guid):
    try:
        import ctypes
        g = _guid_struct(guid)
        r = ctypes.WinDLL("powrprof").PowerSetActiveOverlayScheme(ctypes.byref(g))
        return r == 0, "" if r == 0 else f"erro {r}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ---------------- ajustes do Windows ----------------
def _reg_hklm(caminho, nome, padrao=None):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, caminho, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return winreg.QueryValueEx(k, nome)[0]
    except OSError:
        return padrao


def _set_hklm(caminho, nome, valor):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, caminho, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, nome, 0, winreg.REG_DWORD, int(valor))


GAMEBAR = r"Software\Microsoft\GameBar"
NOTIF = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
TEMA = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
HIBER = r"SYSTEM\CurrentControlSet\Control\Power"
RAPIDA = r"SYSTEM\CurrentControlSet\Control\Session Manager\Power"


def ajustes_windows():
    if not WIN:
        return []
    valores = {
        "modo_jogo": sessao.ler_usuario(GAMEBAR, "AutoGameModeEnabled", 1) != 0,
        "notificacoes": sessao.ler_usuario(NOTIF, "ToastEnabled", 1) != 0,
        "transparencia": sessao.ler_usuario(TEMA, "EnableTransparency", 1) != 0,
        "hibernacao": _reg_hklm(HIBER, "HibernateEnabled", 0) != 0,
        "inicializacao_rapida": _reg_hklm(RAPIDA, "HiberbootEnabled", 0) != 0,
    }
    return [{"chave": k, "titulo": t, "descricao": d, "ativo": valores[k]} for k, t, d in AJUSTES_WINDOWS]


def mudar_windows(chave, ativo):
    if not WIN:
        return False, "Só no Windows."
    v = 1 if ativo else 0
    try:
        if chave == "modo_jogo":
            sessao.gravar_usuario(GAMEBAR, "AutoGameModeEnabled", v)
            sessao.gravar_usuario(GAMEBAR, "AllowAutoGameMode", v)
        elif chave == "notificacoes":
            sessao.gravar_usuario(NOTIF, "ToastEnabled", v)
        elif chave == "transparencia":
            sessao.gravar_usuario(TEMA, "EnableTransparency", v)
        elif chave == "hibernacao":
            c, txt = _rodar(["powercfg", "/hibernate", "on" if ativo else "off"])
            if c != 0:
                return False, txt.strip()
        elif chave == "inicializacao_rapida":
            _set_hklm(RAPIDA, "HiberbootEnabled", v)
        else:
            return False, "Ajuste desconhecido."
    except OSError as e:
        return False, str(e)
    return True, "Salvo."


# ---------------- brilho (notebooks) ----------------
def brilho():
    if not WIN:
        return None
    c, txt = _rodar(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | "
                     "Select-Object -First 1).CurrentBrightness"], timeout=15)
    try:
        return int(txt.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def mudar_brilho(nivel):
    nivel = max(0, min(100, int(nivel)))
    c, txt = _rodar(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
                     f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=0; Brightness={nivel}}} | Out-Null"],
                    timeout=15)
    return c == 0, txt.strip()


def estado():
    pl = planos()
    return {"planos": pl, "ativo": next((p for p in pl if p["ativo"]), None), "ajustes": ajustes(),
            "modo": modo_energia(), "modos": [{"guid": k, "nome": v} for k, v in MODOS.items()],
            "windows": ajustes_windows(), "brilho": brilho(),
            "tem_alto": any(p["guid"] == ALTO_DESEMPENHO or p["conhecido"] == "Alto desempenho" for p in pl),
            "tem_maximo": any(p["guid"] == DESEMPENHO_MAXIMO or (p["conhecido"] == "Desempenho máximo") for p in pl)}


def garantir_alto_desempenho():
    """GUID de um plano de alto desempenho (cria se o Windows esconder)."""
    for p in planos():
        if p["guid"] in (ALTO_DESEMPENHO, DESEMPENHO_MAXIMO) or "desempenho" in p["nome"].lower() and "equil" not in p["nome"].lower():
            return p["guid"]
    g, _ = criar_plano(ALTO_DESEMPENHO)
    return g


def _json(x):
    return json.dumps(x, ensure_ascii=False)
