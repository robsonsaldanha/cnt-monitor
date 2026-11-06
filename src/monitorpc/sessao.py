"""Coisas da sessão do usuário vistas pelo serviço (que roda como SYSTEM):
usuários logados (para mexer nas configurações deles) e abrir programas na tela do usuário."""
import os
import re

WIN = os.name == "nt"


def sids_logados():
    """SIDs dos usuários com o registro carregado (logados)."""
    if not WIN:
        return []
    import winreg
    out = []
    i = 0
    while True:
        try:
            sid = winreg.EnumKey(winreg.HKEY_USERS, i)
        except OSError:
            break
        i += 1
        if re.match(r"^S-1-5-21-[\d-]+$", sid):
            out.append(sid)
    return out


def ler_usuario(caminho, nome, padrao=None):
    """Lê um valor DWORD em HKCU de cada usuário logado (devolve o primeiro encontrado)."""
    if not WIN:
        return padrao
    import winreg
    for sid in sids_logados():
        try:
            with winreg.OpenKey(winreg.HKEY_USERS, sid + "\\" + caminho) as k:
                return winreg.QueryValueEx(k, nome)[0]
        except OSError:
            continue
    return padrao


def gravar_usuario(caminho, nome, valor):
    """Grava um DWORD em HKCU de todos os usuários logados."""
    if not WIN:
        return 0
    import winreg
    n = 0
    for sid in sids_logados():
        try:
            with winreg.CreateKeyEx(winreg.HKEY_USERS, sid + "\\" + caminho, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, nome, 0, winreg.REG_DWORD, int(valor))
                n += 1
        except OSError:
            continue
    return n


def abrir_na_tela_do_usuario(comando):
    """Abre um programa na sessão do usuário logado (o serviço não tem tela própria)."""
    if not WIN:
        return False
    try:
        import win32ts
        import win32process
        import win32profile
        import win32con
        sessao = win32ts.WTSGetActiveConsoleSessionId()
        if sessao == 0xFFFFFFFF:
            return False
        token = win32ts.WTSQueryUserToken(sessao)
        amb = win32profile.CreateEnvironmentBlock(token, False)
        si = win32process.STARTUPINFO()
        si.lpDesktop = "winsta0\\default"
        win32process.CreateProcessAsUser(token, None, comando, None, None, False,
                                         win32con.CREATE_UNICODE_ENVIRONMENT | win32con.DETACHED_PROCESS,
                                         amb, None, si)
        return True
    except Exception as e:  # noqa: BLE001
        print("Não consegui abrir na sessão do usuário:", e)
        return False
