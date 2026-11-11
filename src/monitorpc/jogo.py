"""Modo Jogo: deixa o PC pronto para jogar e desfaz tudo ao sair.

- fecha os programas escolhidos (e pode reabrir depois)
- muda para o plano de energia de alto desempenho
- dá prioridade alta para o programa em primeiro plano (o jogo)
- liga o Modo de Jogo do Windows e silencia as notificações
- mede o FPS (PresentMon, ferramenta oficial da Microsoft/Intel)
Também tem "limpar o cache de memória" (lista de espera), só para casos específicos.
"""
import os
import csv
import json
import time
import threading
import subprocess
from collections import deque, defaultdict

from . import config, energia, sessao

WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0
ESTADO = os.path.join(config.BASE, "modo_jogo.json")


# ---------------------------------------------------------------- memória
def _contadores_memoria():
    """Memória livre e em cache (lista de espera) em bytes, pelos contadores do Windows."""
    if not WIN:
        return {}
    try:
        from .gpu import _PDH
        p = _PDH({"livre": r"\Memory\Free & Zero Page List Bytes",
                  "espera": r"\Memory\Standby Cache Normal Priority Bytes",
                  "espera_r": r"\Memory\Standby Cache Reserve Bytes",
                  "espera_c": r"\Memory\Standby Cache Core Bytes"})
        d = p.coletar()
        v = {k: (lst[0][1] if lst else 0) for k, lst in d.items()}
        return {"livre": int(v.get("livre", 0)), "cache": int(v.get("espera", 0) + v.get("espera_r", 0) + v.get("espera_c", 0))}
    except Exception:
        return {}


def limpar_cache_memoria():
    """Esvazia a lista de espera (memória em cache) — o que ferramentas como o RAMMap fazem."""
    if not WIN:
        return False, "Só no Windows."
    import ctypes
    import win32api
    import win32security
    antes = _contadores_memoria()
    try:
        tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 0x20 | 0x8)
        luid = win32security.LookupPrivilegeValue(None, "SeProfileSingleProcessPrivilege")
        win32security.AdjustTokenPrivileges(tok, False, [(luid, 2)])
    except Exception as e:  # noqa: BLE001
        return False, f"Sem permissão: {e}"
    cmd = ctypes.c_int(4)  # MemoryPurgeStandbyList
    st = ctypes.WinDLL("ntdll").NtSetSystemInformation(80, ctypes.byref(cmd), ctypes.sizeof(cmd))
    if st != 0:
        return False, f"O Windows recusou (código {st & 0xFFFFFFFF:#x})."
    time.sleep(0.5)
    depois = _contadores_memoria()
    liberado = max(0, depois.get("livre", 0) - antes.get("livre", 0))
    return True, {"liberado": liberado, "antes": antes, "depois": depois}


# ---------------------------------------------------------------- FPS (PresentMon)
class FPS:
    def __init__(self):
        self.proc = None
        self.erro = None
        self.lock = threading.Lock()
        self.quadros = defaultdict(deque)   # pid -> deque[(t, ms)]
        self.nomes = {}
        self.ult_inicio = 0.0

    def exe(self):
        p = os.path.join(config.pasta_recursos(), "presentmon", "PresentMon.exe")
        return p if os.path.exists(p) else None

    def iniciar(self):
        if self.proc and self.proc.poll() is None:
            return True
        exe = self.exe()
        if not exe:
            self.erro = "PresentMon não está no pacote."
            return False
        self.ult_inicio = time.time()
        try:
            self.proc = subprocess.Popen([exe, "--output_stdout", "--stop_existing_session", "--session_name",
                                          "MonitorPC_FPS", "--v1_metrics", "--no_console_stats"],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=SEM_JANELA,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as e:  # noqa: BLE001
            self.erro = str(e)
            return False
        threading.Thread(target=self._ler, daemon=True).start()
        self.erro = None
        return True

    def _ler(self):
        p = self.proc
        cab = None
        try:
            for linha in p.stdout:
                if cab is None:
                    if "Application" in linha and "ProcessID" in linha:
                        cab = next(csv.reader([linha]))
                        i_app, i_pid = cab.index("Application"), cab.index("ProcessID")
                        i_ms = cab.index("MsBetweenPresents") if "MsBetweenPresents" in cab else (
                            cab.index("FrameTime") if "FrameTime" in cab else None)
                    continue
                if i_ms is None:
                    continue
                try:
                    c = next(csv.reader([linha]))
                    pid = int(c[i_pid])
                    ms = float(c[i_ms])
                except (ValueError, IndexError, StopIteration):
                    continue
                if c[i_app].lower() == "dwm.exe" or ms <= 0:
                    continue
                t = time.time()
                with self.lock:
                    q = self.quadros[pid]
                    q.append((t, ms))
                    self.nomes[pid] = c[i_app]
                    while q and q[0][0] < t - 2:
                        q.popleft()
        except Exception as e:  # noqa: BLE001
            self.erro = str(e)
        if p.poll() is not None and not self.erro:
            try:
                self.erro = (p.stderr.read() or "")[-300:] or None
            except Exception:
                pass

    def parar(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    def por_pid(self):
        """{pid: {"fps", "ms_quadro", "nome"}} do último segundo."""
        t = time.time()
        out = {}
        with self.lock:
            for pid, q in list(self.quadros.items()):
                rec = [ms for (tt, ms) in q if tt >= t - 1]
                if not rec:
                    if not q or q[-1][0] < t - 3:
                        self.quadros.pop(pid, None)
                    continue
                med = sum(rec) / len(rec)
                out[pid] = {"fps": round(1000 / med, 1) if med else 0, "ms_quadro": round(med, 2),
                            "nome": self.nomes.get(pid, "?")}
        return out

    @property
    def rodando(self):
        return bool(self.proc and self.proc.poll() is None)


# ---------------------------------------------------------------- Modo Jogo
class ModoJogo:
    def __init__(self, motor):
        self.m = motor
        self.fps = FPS()
        self.ativo = False
        self.inicio = None
        self.desfazer = {}
        self.log = []
        self.antes = None
        self.prioridades = {}
        self._carregar()

    def _carregar(self):
        try:
            with open(ESTADO, encoding="utf-8") as f:
                d = json.load(f)
            self.desfazer = d.get("desfazer", {})
            if d.get("ativo"):
                # serviço reiniciou com o modo jogo ligado: desfaz o que dá para desfazer
                self.ativo = True
                self.desativar()
        except (OSError, ValueError):
            pass

    def _salvar(self):
        try:
            with open(ESTADO, "w", encoding="utf-8") as f:
                json.dump({"ativo": self.ativo, "desfazer": self.desfazer, "inicio": self.inicio}, f)
        except OSError:
            pass

    def ativar(self, c):
        if self.ativo:
            return {"ok": True, "msg": "O Modo Jogo já está ligado."}
        self.log = []
        e = self.m.estado or {}
        self.antes = {"mem_usada": e.get("mem_usada"), "mem_pct": e.get("mem_pct"), "cpu": e.get("cpu"), "ts": time.time()}
        self.desfazer = {"fechados": []}
        if c.get("jogo_plano"):
            atual = energia.plano_ativo()
            alvo = energia.garantir_alto_desempenho()
            if alvo and atual and alvo != atual["guid"]:
                ok, _ = energia.ativar_plano(alvo)
                if ok:
                    self.desfazer["plano"] = atual["guid"]
                    self.log.append(f'Plano de energia: {atual["nome"]} → alto desempenho')
        if c.get("jogo_modo_windows"):
            antes = sessao.ler_usuario(energia.GAMEBAR, "AutoGameModeEnabled", 1)
            energia.mudar_windows("modo_jogo", True)
            self.desfazer["modo_windows"] = antes
            self.log.append("Modo de Jogo do Windows ligado")
        if c.get("jogo_silenciar"):
            antes = sessao.ler_usuario(energia.NOTIF, "ToastEnabled", 1)
            energia.mudar_windows("notificacoes", False)
            self.desfazer["notificacoes"] = antes
            self.log.append("Notificações silenciadas")
        nomes = set(c.get("jogo_fechar") or [])
        if nomes:
            with self.m.lock:
                progs = [g for g in (self.m.estado or {}).get("programas", []) if g["nome"] in nomes and not g.get("protegido")]
            for g in progs:
                self.desfazer["fechados"].append({"nome": g["nome"], "exe": g.get("exe")})
                if g["nome"] == "WSL (Linux)":
                    self.m._enfileirar({"tipo": "wsl_desligar"})
                    self.log.append("WSL desligado (wsl --shutdown)")
                    continue
                self.m._enfileirar({"tipo": "fechar_janelas", "pids": list(g["pids"]), "exe": g.get("exe")})
                threading.Timer(6, self._forcar, args=(g["nome"],)).start()
                self.log.append(f'{g["nome"]} fechado ({self._fmt(g["mem"])} de memória)')
        if c.get("jogo_fps"):
            if self.fps.iniciar():
                self.log.append("Medição de FPS ligada")
        self.ativo = True
        self.inicio = time.time()
        self._salvar()
        return {"ok": True, "msg": "Modo Jogo ligado. " + "; ".join(self.log)}

    def ajustar_fps(self, c):
        """Liga o PresentMon quando o Modo Jogo ou o mini painel precisam do FPS; desliga quando ninguém precisa."""
        quer = (self.ativo and c.get("jogo_fps")) or (c.get("mini_ativo") and "fps" in (c.get("mini_itens") or []))
        if quer and not self.fps.rodando:
            if time.time() - self.fps.ult_inicio > 30:   # se o PresentMon cai, não relança a cada 2 s
                self.fps.iniciar()
        elif not quer and self.fps.rodando:
            self.fps.parar()

    def _forcar(self, nome):
        """Se o programa não fechou pelo X em alguns segundos, encerra."""
        with self.m.lock:
            g = next((x for x in (self.m.estado or {}).get("programas", []) if x["nome"] == nome), None)
        if g and self.ativo:
            self.m._matar(list(g["pids"]))

    def tick(self, c):
        """Chamado a cada leitura: prioridade alta para o jogo (programa em primeiro plano)."""
        if not self.ativo or not c.get("jogo_prioridade"):
            return
        pid = self.m.frente_pid
        if not pid:
            return
        with self.m.lock:
            d = self.m.procs.get(pid)
            if not d or d["protegido"]:
                return
            pids = [p for p in self.m._grupo_pids(d["programa"])] if d else []
        import psutil
        for p in pids:
            if p in self.prioridades:
                continue
            try:
                pr = psutil.Process(p)
                self.prioridades[p] = pr.nice()
                pr.nice(psutil.HIGH_PRIORITY_CLASS)
            except Exception:
                self.prioridades[p] = None

    def desativar(self, reabrir=True):
        if not self.ativo:
            return {"ok": True, "msg": "O Modo Jogo já está desligado."}
        log = []
        d = self.desfazer or {}
        if d.get("plano"):
            ok, _ = energia.ativar_plano(d["plano"])
            log.append("plano de energia restaurado" if ok else "não consegui voltar o plano de energia")
        if "modo_windows" in d:
            energia.mudar_windows("modo_jogo", bool(d["modo_windows"]))
        if "notificacoes" in d:
            energia.mudar_windows("notificacoes", bool(d["notificacoes"]))
            log.append("notificações de volta")
        import psutil
        for p, n in list(self.prioridades.items()):
            if n is not None:
                try:
                    psutil.Process(p).nice(n)
                except Exception:
                    pass
        self.prioridades = {}
        if reabrir and d.get("fechados"):
            for f in d["fechados"]:
                if f.get("exe") and f["nome"] != "WSL (Linux)":
                    self.m._enfileirar({"tipo": "abrir_exe", "caminho": f["exe"], "minimizado": True})
            log.append(f'{len(d["fechados"])} programa(s) reabertos')
        self.ativo = False
        self.desfazer = {}
        self._salvar()
        return {"ok": True, "msg": "Modo Jogo desligado: " + (", ".join(log) or "tudo como antes") + "."}

    def estado(self):
        e = self.m.estado or {}
        fps = self.fps.por_pid() if self.fps.rodando else {}
        principal = None
        if fps:
            pid = self.m.frente_pid if self.m.frente_pid in fps else max(fps, key=lambda p: fps[p]["fps"])
            principal = {"pid": pid, **fps[pid]}
        lib = None
        if self.ativo and self.antes and self.antes.get("mem_usada") and e.get("mem_usada"):
            lib = {"memoria": self.antes["mem_usada"] - e["mem_usada"], "mem_pct_antes": self.antes["mem_pct"],
                   "mem_pct_agora": e.get("mem_pct")}
        return {"ativo": self.ativo, "inicio": self.inicio, "log": self.log, "liberado": lib, "fps": fps,
                "fps_principal": principal, "fps_rodando": self.fps.rodando, "fps_erro": self.fps.erro,
                "presentmon": bool(self.fps.exe()), "frente_pid": self.m.frente_pid}

    @staticmethod
    def _fmt(b):
        return f"{b / 2**30:.1f} GB" if b >= 2**30 else f"{b / 2**20:.0f} MB"
