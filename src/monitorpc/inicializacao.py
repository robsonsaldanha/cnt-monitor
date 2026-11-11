"""Programas que iniciam com o Windows (mesmas fontes do Gerenciador de Tarefas):
chaves Run do registro (computador e de cada usuário logado) e pastas Inicializar.
Ativar/desativar usa as chaves StartupApproved, exatamente como o Gerenciador faz
(não apaga nada e pode ser revertido)."""
import os
import re
import time
import struct
import hashlib

from . import nt

WIN = os.name == "nt"
APROVADO = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"


def _ler_valores(raiz, caminho):
    import winreg
    out = []
    try:
        with winreg.OpenKey(raiz, caminho, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            i = 0
            while True:
                try:
                    nome, valor, tipo = winreg.EnumValue(k, i)
                except OSError:
                    break
                out.append((nome, valor))
                i += 1
    except OSError:
        pass
    return out


def _estado(raiz, sub, nome):
    """True = ativado, False = desativado, None = sem registro (ativado)."""
    import winreg
    try:
        with winreg.OpenKey(raiz, APROVADO + "\\" + sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            v, _ = winreg.QueryValueEx(k, nome)
            if isinstance(v, bytes) and v:
                return not (v[0] & 1)
    except OSError:
        pass
    return None


def exe_do_comando(cmd):
    cmd = os.path.expandvars((cmd or "").strip())
    if not cmd:
        return "", ""
    if cmd.startswith('"'):
        exe = cmd[1:].split('"', 1)[0]
        args = cmd[len(exe) + 2:].strip()
        return exe, args
    partes = cmd.split(" ")
    for i in range(len(partes), 0, -1):
        cand = " ".join(partes[:i])
        for c in (cand, cand + ".exe"):
            if os.path.isfile(c):
                return c, " ".join(partes[i:])
    return partes[0], " ".join(partes[1:])


def _usuarios():
    """[(sid, nome, pasta_perfil)] dos usuários com registro carregado (logados)."""
    import winreg
    out = []
    i = 0
    while True:
        try:
            sid = winreg.EnumKey(winreg.HKEY_USERS, i)
        except OSError:
            break
        i += 1
        if not re.match(r"^S-1-5-21-[\d-]+$", sid):
            continue
        perfil = ""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList" + "\\" + sid) as k:
                perfil = os.path.expandvars(winreg.QueryValueEx(k, "ProfileImagePath")[0])
        except OSError:
            pass
        out.append((sid, os.path.basename(perfil) or sid, perfil))
    return out


def _alvo_atalho(caminho):
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        sh = win32com.client.Dispatch("WScript.Shell")
        a = sh.CreateShortcut(caminho)
        return a.TargetPath or "", a.Arguments or ""
    except Exception:
        return "", ""


def _id(*partes):
    return hashlib.sha1("|".join(partes).encode("utf-8", "replace")).hexdigest()[:16]


def listar():
    if not WIN:
        return []
    import winreg
    itens = []

    def add(nome, comando, origem, escopo, raiz_estado, sub_estado, nome_estado, chave_info, sid=None):
        exe, args = exe_do_comando(comando)
        est = _estado(raiz_estado, sub_estado, nome_estado)
        info = nt.info_arquivo(exe) if exe and os.path.isfile(exe) else {}
        itens.append({"id": _id(*chave_info), "nome": nome, "comando": comando, "exe": exe, "args": args,
                      "existe": bool(exe and os.path.isfile(exe)), "origem": origem, "escopo": escopo,
                      "ativo": est is not False, "descricao": info.get("descricao", ""), "empresa": info.get("empresa", ""),
                      "_alterar": (sid, sub_estado, nome_estado)})

    # computador (todos os usuários)
    for nome, cmd in _ler_valores(winreg.HKEY_LOCAL_MACHINE, RUN):
        add(nome, str(cmd), "Registro (Run)", "Todos os usuários", winreg.HKEY_LOCAL_MACHINE, "Run", nome, ("HKLM", "Run", nome))
    for nome, cmd in _ler_valores(winreg.HKEY_LOCAL_MACHINE, RUN32):
        add(nome, str(cmd), "Registro (Run 32 bits)", "Todos os usuários", winreg.HKEY_LOCAL_MACHINE, "Run32", nome, ("HKLM", "Run32", nome))
    comum = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs\Startup")
    for arq in _arquivos(comum):
        alvo, args = _alvo_atalho(arq) if arq.lower().endswith(".lnk") else (arq, "")
        cmd = f'"{alvo}" {args}'.strip() if alvo else arq
        add(os.path.splitext(os.path.basename(arq))[0], cmd, "Pasta Inicializar", "Todos os usuários",
            winreg.HKEY_LOCAL_MACHINE, "StartupFolder", os.path.basename(arq), ("HKLM", "Pasta", arq))

    # cada usuário logado
    for sid, usuario, perfil in _usuarios():
        try:
            raiz = winreg.OpenKey(winreg.HKEY_USERS, sid)
        except OSError:
            continue
        for nome, cmd in _ler_valores(raiz, RUN):
            add(nome, str(cmd), "Registro (Run)", usuario, raiz, "Run", nome, (sid, "Run", nome), sid)
        if perfil:
            pasta = os.path.join(perfil, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup")
            for arq in _arquivos(pasta):
                alvo, args = _alvo_atalho(arq) if arq.lower().endswith(".lnk") else (arq, "")
                cmd = f'"{alvo}" {args}'.strip() if alvo else arq
                add(os.path.splitext(os.path.basename(arq))[0], cmd, "Pasta Inicializar", usuario, raiz, "StartupFolder",
                    os.path.basename(arq), (sid, "Pasta", arq), sid)
        winreg.CloseKey(raiz)
    return itens


def _arquivos(pasta):
    try:
        return [os.path.join(pasta, f) for f in os.listdir(pasta)
                if f.lower() != "desktop.ini" and os.path.isfile(os.path.join(pasta, f))]
    except OSError:
        return []


def alterar(item, ativo):
    """Ativa/desativa como o Gerenciador de Tarefas: grava o estado em StartupApproved."""
    import winreg
    sid, sub, nome = item["_alterar"]
    raiz = winreg.HKEY_LOCAL_MACHINE if sid is None else winreg.OpenKey(winreg.HKEY_USERS, sid)
    if ativo:
        dado = b"\x02" + b"\x00" * 11
    else:
        ft = int((time.time() + nt.EPOCA_WIN) * 1e7)
        dado = b"\x03\x00\x00\x00" + struct.pack("<Q", ft)
    with winreg.CreateKeyEx(raiz, APROVADO + "\\" + sub, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, nome, 0, winreg.REG_BINARY, dado)
    return True


if not WIN:     # Linux: autostart do XDG
    from .linux.inicializacao import listar, alterar  # noqa: E402,F401,F811
