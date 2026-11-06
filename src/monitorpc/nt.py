"""Leitura rápida de todos os processos.

No Windows usa NtQuerySystemInformation(SystemProcessInformation): uma única chamada
devolve todos os processos com CPU, memória (conjunto de trabalho privado, o mesmo
número do Gerenciador de Tarefas), threads, handles e E/S. Isso permite atualizar a
cada 250 ms gastando pouca CPU. Fora do Windows (ou se algo falhar) usa o psutil.
"""
import os
import struct
import threading

import psutil

WIN = os.name == "nt"
EPOCA_WIN = 11644473600  # segundos entre 1601 e 1970

# SYSTEM_PROCESS_INFORMATION (x64), 256 bytes
_S = struct.Struct("<IIqIIQqqqHH4xQi4xQQIIQQQI4xQQQQQQQQQqqqqqq")


class Proc:
    __slots__ = ("pid", "ppid", "nome", "ct", "cpu_s", "priv", "ws", "commit", "threads", "handles", "rd", "wr",
                 "sessao")


if WIN:
    import ctypes
    from ctypes import wintypes as wt

    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                                ctypes.POINTER(ctypes.c_ulong)]
    _ntdll.NtQuerySystemInformation.restype = ctypes.c_long
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.IsProcessCritical.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
    _k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]


class Leitor:
    def __init__(self):
        self.modo = "psutil"
        self._buf = None
        if WIN:
            try:
                self._buf = ctypes.create_string_buffer(2 * 1024 * 1024)
                if self._validar():
                    self.modo = "nt"
            except Exception:
                self.modo = "psutil"

    # ---------------- Windows ----------------
    def _ler_nt(self):
        tam = ctypes.c_ulong(0)
        while True:
            st = _ntdll.NtQuerySystemInformation(5, self._buf, len(self._buf), ctypes.byref(tam))
            if (st & 0xFFFFFFFF) == 0xC0000004:  # STATUS_INFO_LENGTH_MISMATCH
                self._buf = ctypes.create_string_buffer(max(len(self._buf) * 2, tam.value + 262144))
                continue
            if st != 0:
                raise OSError(f"NtQuerySystemInformation: {st & 0xFFFFFFFF:#x}")
            break
        dados = ctypes.string_at(self._buf, tam.value)
        out = []
        off = 0
        un = _S.unpack_from
        while True:
            v = un(dados, off)
            (prox, nthr, priv, _hf, _hw, _cyc, ct, ut, kt, nlen, _nmax, nptr, _prio, pid, ppid, handles, sessao,
             _key, _pvs, _vs, _pf, _pws, ws, _a, _b, _c, _d, _pfu, _ppfu, commit, _ro, _wo, _oo, rd, wr, _ot) = v
            p = Proc()
            p.pid = pid
            p.ppid = ppid
            if nptr and nlen:
                p.nome = ctypes.wstring_at(nptr, nlen // 2)
            else:
                p.nome = "System Idle Process" if pid == 0 else "?"
            p.ct = (ct / 1e7 - EPOCA_WIN) if ct else 0.0
            p.cpu_s = (ut + kt) / 1e7
            p.priv = priv
            p.ws = ws
            p.commit = commit
            p.threads = nthr
            p.handles = handles
            p.rd = rd
            p.wr = wr
            p.sessao = sessao
            out.append(p)
            if prox == 0:
                break
            off += prox
        return out

    def _validar(self):
        lst = self._ler_nt()
        eu = next((p for p in lst if p.pid == os.getpid()), None)
        if eu is None or eu.threads <= 0:
            return False
        me = psutil.Process()
        if eu.ppid != me.ppid() or eu.nome.lower() != me.name().lower():
            return False
        return abs(eu.ct - me.create_time()) < 2

    # ---------------- genérico (psutil) ----------------
    def _ler_psutil(self):
        out = []
        for ps in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
            i = ps.info
            p = Proc()
            p.pid = i["pid"]
            p.ppid = i.get("ppid") or 0
            p.nome = i.get("name") or "?"
            p.ct = i.get("create_time") or 0.0
            p.cpu_s = p.priv = p.ws = p.commit = p.threads = p.handles = p.rd = p.wr = 0
            p.sessao = 0
            try:
                with ps.oneshot():
                    t = ps.cpu_times()
                    p.cpu_s = t.user + t.system
                    m = ps.memory_info()
                    p.ws = m.rss
                    p.priv = m.rss
                    p.commit = getattr(m, "vms", m.rss)
                    p.threads = ps.num_threads()
                    try:
                        p.handles = ps.num_handles() if WIN else ps.num_fds()
                    except (psutil.Error, OSError, AttributeError):
                        pass
                    try:
                        io = ps.io_counters()
                        p.rd, p.wr = io.read_bytes, io.write_bytes
                    except (psutil.Error, OSError, AttributeError):
                        pass
            except (psutil.Error, OSError):
                pass
            out.append(p)
        return out

    def ler(self):
        if self.modo == "nt":
            try:
                return self._ler_nt()
            except Exception:
                self.modo = "psutil"
        return self._ler_psutil()


# ---------------- informações extras (só para processos novos) ----------------
_versoes = {}
_versoes_lock = threading.Lock()


def caminho_exe(pid):
    if not WIN:
        try:
            return psutil.Process(pid).exe()
        except (psutil.Error, OSError):
            return ""
    h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(h)


def eh_critico(pid):
    """Processo marcado como crítico pelo Windows (encerrar causa tela azul)."""
    if not WIN:
        return False
    h = _k32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    try:
        b = wt.BOOL(0)
        return bool(_k32.IsProcessCritical(h, ctypes.byref(b)) and b.value)
    except Exception:
        return False
    finally:
        _k32.CloseHandle(h)


def info_arquivo(caminho):
    """Descrição, empresa e versão do executável (cache por caminho)."""
    if not caminho or not WIN:
        return {}
    with _versoes_lock:
        if caminho in _versoes:
            return _versoes[caminho]
    d = {}
    try:
        import win32api
        tr = win32api.GetFileVersionInfo(caminho, "\\VarFileInfo\\Translation")
        if tr:
            lang, cp = tr[0]
            base = "\\StringFileInfo\\%04X%04X\\" % (lang, cp)
            for k, nome in (("FileDescription", "descricao"), ("CompanyName", "empresa"),
                            ("ProductName", "produto"), ("FileVersion", "versao")):
                try:
                    v = win32api.GetFileVersionInfo(caminho, base + k)
                    if v:
                        d[nome] = str(v).strip()
                except Exception:
                    pass
    except Exception:
        pass
    with _versoes_lock:
        _versoes[caminho] = d
    return d
