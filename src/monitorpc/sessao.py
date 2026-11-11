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


def em_servico():
    """Verdadeiro quando este processo é o serviço (sessão 0, sem tela)."""
    if not WIN:
        return False
    import win32ts
    return win32ts.ProcessIdToSessionId(os.getpid()) == 0


class _Processo:
    """O mínimo de subprocess.Popen que o acesso remoto usa: stdin, stdout, poll, wait e kill."""

    def __init__(self, handle, pid, stdin, stdout):
        self._h, self.pid, self.stdin, self.stdout, self.returncode = handle, pid, stdin, stdout, None

    def wait(self, timeout=None):
        import win32event
        import win32process
        if self.returncode is None:
            ms = win32event.INFINITE if timeout is None else int(timeout * 1000)
            if win32event.WaitForSingleObject(self._h, ms) == win32event.WAIT_OBJECT_0:
                self.returncode = win32process.GetExitCodeProcess(self._h)
        return self.returncode

    def poll(self):
        return self.wait(0)

    def kill(self):
        import win32process
        try:
            win32process.TerminateProcess(self._h, 1)
        except Exception as e:  # noqa: BLE001  (já terminou)
            raise OSError(str(e)) from None


def _abrir_com_token(token, comando, ambiente=None):
    """Abre `comando` (lista) com o token dado, sem janela, ligado a este processo por dois canos.
    Só as três pontas do filho são herdadas."""
    import msvcrt
    import subprocess
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32process
    import win32security
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.bInheritHandle = True
    le_filho, escreve_pai = win32pipe.CreatePipe(sa, 0)
    le_pai, escreve_filho = win32pipe.CreatePipe(sa, 0)
    nulo = win32file.CreateFile("NUL", win32con.GENERIC_WRITE, win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                                sa, win32con.OPEN_EXISTING, 0, None)
    try:
        for h in (escreve_pai, le_pai):
            win32api.SetHandleInformation(h, win32con.HANDLE_FLAG_INHERIT, 0)
        si = win32process.STARTUPINFO()
        si.lpDesktop = "winsta0\\default"
        si.dwFlags = win32con.STARTF_USESTDHANDLES
        si.hStdInput, si.hStdOutput, si.hStdError = le_filho, escreve_filho, nulo
        hproc, hthread, pid, _tid = win32process.CreateProcessAsUser(
            token, None, subprocess.list2cmdline(comando), None, None, True,
            win32con.CREATE_UNICODE_ENVIRONMENT | win32con.CREATE_NO_WINDOW, ambiente, None, si)
        hthread.Close()
    except Exception:
        escreve_pai.Close()
        le_pai.Close()
        raise
    finally:
        for h in (le_filho, escreve_filho, nulo):
            h.Close()
    stdin = os.fdopen(msvcrt.open_osfhandle(escreve_pai.Detach(), 0), "w", encoding="utf-8", buffering=1)
    stdout = os.fdopen(msvcrt.open_osfhandle(le_pai.Detach(), os.O_RDONLY), "r", encoding="utf-8")
    return _Processo(hproc, pid, stdin, stdout)


def abrir_como_sistema(comando):
    """Abre `comando` (lista) na tela de quem está usando o PC, mas com a conta do serviço (SYSTEM).
    O acesso remoto precisa disso para mostrar e operar a tela do UAC, que um programa comum não enxerga.
    Só funciona de dentro do serviço. Levanta OSError com a mensagem pronta."""
    try:
        import win32api
        import win32con
        import win32profile
        import win32security
        import win32ts
        sessao = win32ts.WTSGetActiveConsoleSessionId()
        if sessao == 0xFFFFFFFF:
            raise OSError("Ninguém está usando este computador.")
        try:
            win32ts.WTSQueryUserToken(sessao).Close()      # só com alguém logado: nunca na tela de entrada
        except Exception:  # noqa: BLE001
            raise OSError("Ninguém está usando este computador.") from None
        meu = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.MAXIMUM_ALLOWED)
        token = win32security.DuplicateTokenEx(meu, win32security.SecurityImpersonation, win32con.MAXIMUM_ALLOWED,
                                               win32security.TokenPrimary)
        win32security.SetTokenInformation(token, win32security.TokenSessionId, sessao)
        return _abrir_com_token(token, comando, win32profile.CreateEnvironmentBlock(token, False))
    except OSError:
        raise
    except Exception as e:  # noqa: BLE001
        raise OSError(f"Não consegui abrir o acesso remoto na tela do usuário: {e}") from None


if not WIN:     # Linux: loginctl e a sessão gráfica (X11 ou Wayland) de quem está usando o PC
    from .linux.sessao import abrir_na_tela_do_usuario, em_servico, abrir_como_sistema, sessao_grafica  # noqa: E402,F401
