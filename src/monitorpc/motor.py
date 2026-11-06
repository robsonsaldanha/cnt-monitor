"""Motor de coleta do Monitor do PC.

- Leitura rápida (padrão 250 ms) de todos os processos numa única chamada do Windows.
- CPU, disco e rede são a média do último segundo (atualizada a cada leitura).
- Agrupa cada processo no programa que o abriu (árvore de processos).
- Rede por processo (ETW), GPU por processo (contadores do Windows), sensores (LHM).
- Ações: mostrar janela, abrir pasta, fechar/encerrar (com proteção do Windows).
"""
import os
import time
import queue
import uuid
import threading
import traceback
from collections import deque, defaultdict

import psutil

from . import config, configuracao, banco, rede, temperatura, nt, gpu, discos, inicializacao

WIN = os.name == "nt"
NCPU = psutil.cpu_count() or 1
EU = "Monitor do PC"

RAIZES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "svchost.exe", "explorer.exe", "userinit.exe", "sihost.exe", "taskhostw.exe",
    "runtimebroker.exe", "lsass.exe", "dllhost.exe", "wmiprvse.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "applicationframehost.exe", "searchhost.exe", "systemd", "init",
}

CRITICOS = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "memory compression",
    "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "securityhealthsystray.exe", "mpdefendercoreservice.exe",
    "sihost.exe", "ctfmon.exe", "explorer.exe", "runtimebroker.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "searchhost.exe", "textinputhost.exe", "audiodg.exe", "spoolsv.exe", "wmiprvse.exe",
    "dllhost.exe", "taskhostw.exe", "smartscreen.exe", "wudfhost.exe", "conhost.exe", "userinit.exe", "logonui.exe",
    "lockapp.exe", "systemsettings.exe", "applicationframehost.exe", "sgrmbroker.exe", "searchindexer.exe",
}

NOMES = {
    "chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox", "opera": "Opera", "brave": "Brave",
    "claude": "Claude", "maestri": "Maestri", "vynex forge": "Vynex Forge", "discord": "Discord",
    "onedrive": "OneDrive", "vmmemwsl": "WSL (Linux)", "vmmem": "WSL / Máquina virtual", "wsl": "WSL (Linux)",
    "wslservice": "WSL (Linux)", "wslhost": "WSL (Linux)", "msmpeng": "Antivírus (Defender)",
    "mpdefendercoreservice": "Antivírus (Defender)", "nissrv": "Antivírus (Defender)",
    "dwm": "Janelas do Windows (DWM)", "explorer": "Windows Explorer", "svchost": "Serviços do Windows",
    "code": "VS Code", "cursor": "Cursor", "spotify": "Spotify", "mobaxterm": "MobaXterm", "bun": "Bun",
    "node": "Node.js", "python": "Python", "pythonw": "Python", "taskmgr": "Gerenciador de Tarefas",
    "memory compression": "Compressão de memória", "system": "Sistema (kernel)", "registry": "Registro do Windows",
    "windowsterminal": "Terminal do Windows", "powershell": "PowerShell", "pwsh": "PowerShell",
    "cmd": "Prompt de comando", "sharex": "ShareX", "searchhost": "Pesquisa do Windows",
    "docker desktop": "Docker Desktop", "com.docker.backend": "Docker Desktop", "ms-teams": "Microsoft Teams",
    "whatsapp": "WhatsApp", "steam": "Steam", "steamwebhelper": "Steam", "obs64": "OBS Studio",
    "monitorpc": EU, "monitorpcservico": EU, "lsass": "Segurança do Windows (LSASS)",
    "csrss": "Subsistema do Windows (CSRSS)", "wininit": "Inicialização do Windows", "winlogon": "Logon do Windows",
    "services": "Gerenciador de serviços", "smss": "Gerenciador de sessão", "spoolsv": "Spooler de impressão",
    "rustdesk": "RustDesk", "anydesk": "AnyDesk", "teamviewer": "TeamViewer", "zoom": "Zoom",
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "outlook": "Outlook", "olk": "Outlook",
    "notepad": "Bloco de Notas", "system idle process": "Ociosidade do sistema",
}

PASTAS_PROTEGIDAS = [
    (os.environ.get("SystemRoot", r"C:\Windows").lower().rstrip("\\") + "\\", "Faz parte do Windows"),
    (r"c:\program files\windows defender", "Antivírus do Windows"),
    (r"c:\programdata\microsoft\windows defender", "Antivírus do Windows"),
    (r"c:\program files\windowsapps\microsoftwindows.", "Componente do Windows"),
]


def eh_admin():
    if not WIN:
        return os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class Taxa:
    """Taxa média (por segundo) das últimas leituras dentro de uma janela de tempo."""
    __slots__ = ("h",)

    def __init__(self):
        self.h = deque()

    def add(self, t, vals, janela):
        h = self.h
        while len(h) > 1 and h[1][0] <= t - janela:
            h.popleft()
        ref = h[0] if h else None
        h.append((t, vals))
        if ref is None or t <= ref[0]:
            return None
        dt = t - ref[0]
        return [max(0.0, (a - b) / dt) for a, b in zip(vals, ref[1])]


class Motor:
    def __init__(self):
        banco.iniciar()
        configuracao.carregar()
        self.lock = threading.RLock()
        self.parar_ev = threading.Event()
        self.leitor = nt.Leitor()
        self.rede = rede.MonitorRede()
        self.dns = rede.DNS()
        self.sens = temperatura.Sensores()
        self.gpu = gpu.MonitorGPU()
        self.discos = discos.Discos()
        self.inicio = time.time()
        self.estado = None
        self.procs = {}
        self.conns = []
        self.serie = deque(maxlen=900)
        self.erros = deque(maxlen=20)
        self.tempos = deque(maxlen=40)
        self._info = {}
        self._tx = {}
        self._tx_sis = Taxa()
        self._tx_nic = {}
        self._net_ev = defaultdict(deque)
        self._conn_pid = {}
        self._ips_pid = {}
        self._pid_prog = {}
        self._ult_seg = 0
        self.vivos = {}
        self.pid_ips = {}
        self._rem_hora = defaultdict(lambda: [0, 0])
        self._agg = None
        self._ult = {"g": time.time(), "p": time.time(), "t": time.time(), "r": time.time(), "l": 0}
        self._meus = {os.getpid()}
        self.janelas = {}
        self.janelas_ts = 0
        self.fila = queue.Queue()
        self.resultados = {}
        self.bandeja_ts = 0
        self._ini_cache = {}

    # ================================================================ ciclo
    def iniciar(self):
        c = configuracao.obter()
        if c["rede_por_programa"]:
            self.rede.iniciar()
        if c["sensores"]:
            self.sens.iniciar()
        if c["gpu_por_processo"]:
            self.gpu.iniciar()
        self.discos.atualizar_mapa(True)
        try:
            configuracao.aplicar_inicializacao()
        except Exception:
            pass
        for alvo, nome in ((self._loop_rapido, "coleta"), (self._loop_lento, "lento"), (self._loop_sensores, "sensores")):
            threading.Thread(target=alvo, name=nome, daemon=True).start()

    def parar(self):
        self.parar_ev.set()
        self.rede.parar()

    def _loop_rapido(self):
        while not self.parar_ev.is_set():
            ini = time.time()
            try:
                self.amostrar()
            except Exception:
                self.erros.append(traceback.format_exc())
                traceback.print_exc()
            gasto = time.time() - ini
            self.tempos.append(gasto)
            intervalo = configuracao.obter("intervalo_ms") / 1000.0
            self.parar_ev.wait(max(0.05, intervalo - gasto))

    def _loop_lento(self):
        while not self.parar_ev.is_set():
            try:
                c = configuracao.obter()
                conns = rede.conexoes()
                nconn = defaultdict(int)
                ips = defaultdict(set)
                for x in conns:
                    nconn[x["pid"]] += 1
                    if x["rip"] and rede.tipo_ip(x["rip"]) != "este PC":
                        ips[x["pid"]].add(x["rip"])
                        if c["dns_reverso"]:
                            self.dns.nome(x["rip"])
                with self.lock:
                    self.conns = conns
                    self._conn_pid = dict(nconn)
                    self._ips_pid = dict(ips)
                if c["rede_por_programa"] and not self.rede.ok:
                    self.rede.iniciar()
                elif not c["rede_por_programa"] and self.rede.ok:
                    self.rede.parar()
                    self.rede.ok = False
                self.discos.atualizar_mapa()
            except Exception:
                self.erros.append(traceback.format_exc())
            self.parar_ev.wait(2)

    def _loop_sensores(self):
        while not self.parar_ev.is_set():
            c = configuracao.obter()
            try:
                if c["sensores"]:
                    if not self.sens.fontes and not self.sens.avisos:
                        self.sens.iniciar()
                    self.sens.ler()
                if c["gpu_por_processo"]:
                    if not self.gpu.ok and self.gpu.erro is None:
                        self.gpu.iniciar()
                    self.gpu.ler()
            except Exception:
                self.erros.append(traceback.format_exc())
            self.parar_ev.wait(max(1, c["sensores_intervalo_s"]))

    # ================================================================ processos
    def _extra(self, pid, ct):
        e = self._info.get(pid)
        if e and e["ct"] == ct:
            return e
        exe = nt.caminho_exe(pid)
        usuario = ""
        try:
            usuario = (psutil.Process(pid).username() or "").split("\\")[-1]
        except (psutil.Error, OSError):
            pass
        e = {"ct": ct, "exe": exe, "usuario": usuario, "critico": nt.eh_critico(pid) if WIN else False}
        self._info[pid] = e
        return e

    def _protecao(self, pid, nome, exe, critico):
        if not WIN and (pid <= 2 or not exe):
            return "Sistema"
        if pid in (0, 4) or pid in self._meus:
            return "Sistema" if pid in (0, 4) else "Monitor do PC"
        if critico:
            return "Processo crítico do Windows"
        n = nome.lower()
        if n in CRITICOS:
            return "Processo essencial do Windows"
        if not exe:
            return "Processo protegido do sistema" if WIN else None
        e = exe.lower()
        for pasta, motivo in PASTAS_PROTEGIDAS:
            if e.startswith(pasta):
                return motivo
        if e.startswith(config.pasta_programa().lower() + "\\"):
            return "Monitor do PC"
        return None

    def _nome_programa(self, raiz):
        base = (raiz["nome"] or "?").lower()
        if base.endswith(".exe"):
            base = base[:-4]
        exe = raiz["exe"]
        if raiz["pid"] in self._meus or base in ("monitorpc", "monitorpcservico"):
            return EU
        if base == "claude" and exe and (".local" in exe.lower() or "claude-code" in exe.lower()):
            return "Claude Code"
        if base in NOMES:
            return NOMES[base]
        desc = nt.info_arquivo(exe).get("descricao", "") if exe else ""
        if desc and 2 <= len(desc) <= 40 and not desc.lower().startswith(("windows", "host", "microsoft®")):
            return desc
        n = raiz["nome"][:-4] if raiz["nome"].lower().endswith(".exe") else raiz["nome"]
        return (n[:1].upper() + n[1:]) if n else "?"

    def amostrar(self):
        t = time.time()
        c = configuracao.obter()
        J = config.JANELA_TAXA
        lista = self.leitor.ler()

        # rede por processo: média do último segundo
        if self.rede.ok:
            for pid, ip, env, rec in self.rede.coletar():
                self._net_ev[pid].append((t, env, rec))
                self._trafego_ip(t, pid, ip, env, rec)
        net_pid = {}
        for pid in list(self._net_ev):
            dq = self._net_ev[pid]
            while dq and dq[0][0] <= t - J:
                dq.popleft()
            if not dq:
                del self._net_ev[pid]
                continue
            net_pid[pid] = (sum(x[1] for x in dq) / J, sum(x[2] for x in dq) / J)

        procs = {}
        vivos = set()
        for p in lista:
            pid = p.pid
            if WIN and pid == 0:
                continue
            vivos.add(pid)
            e = self._extra(pid, p.ct)
            tx = self._tx.get(pid)
            if tx is None or tx[0] != p.ct:
                tx = (p.ct, Taxa())
                self._tx[pid] = tx
            r = tx[1].add(t, (p.cpu_s, p.rd, p.wr), J)
            cpu = min(100.0, r[0] / NCPU * 100) if r else 0.0
            if "protecao" not in e:
                e["protecao"] = self._protecao(pid, p.nome, e["exe"], e["critico"])
            n = net_pid.get(pid)
            g = self.gpu.processo(pid) if self.gpu.ok else None
            procs[pid] = {
                "pid": pid, "ppid": p.ppid, "nome": p.nome, "inicio": p.ct, "exe": e["exe"], "usuario": e["usuario"],
                "cpu": round(cpu, 2), "mem": p.priv, "ws": p.ws, "commit": p.commit, "threads": p.threads,
                "handles": p.handles, "ler": round(r[1]) if r else 0, "gravar": round(r[2]) if r else 0,
                "env": round(n[0]) if n else 0, "rec": round(n[1]) if n else 0,
                "gpu": round(g["uso"], 1) if g else 0.0, "gpu_mem": g["mem"] if g else 0,
                "nconn": self._conn_pid.get(pid, 0), "janelas": len(self.janelas.get(pid, ())),
                "protegido": e["protecao"], "sessao": p.sessao,
            }
        for d in (self._info, self._tx):
            for pid in [k for k in d if k not in vivos]:
                d.pop(pid, None)

        # árvore: programa = processo "raiz" (o primeiro abaixo do Windows/Explorer)
        raiz = {}

        def achar(pid):
            caminho = []
            cur = pid
            while True:
                if cur in raiz:
                    r_ = raiz[cur]
                    break
                caminho.append(cur)
                pr = procs[cur]
                pp = pr["ppid"]
                pai = procs.get(pp)
                if (pai is None or pp == cur or pp in caminho or pai["nome"].lower() in RAIZES
                        or pai["inicio"] > pr["inicio"] + 1):
                    r_ = cur
                    break
                cur = pp
            for x in caminho:
                raiz[x] = r_
            return r_

        progs = {}
        for pid, d in procs.items():
            rp = procs[achar(pid)]
            if "nome_prog" not in self._info.get(rp["pid"], {}):
                self._info.setdefault(rp["pid"], {"ct": rp["inicio"], "exe": rp["exe"], "usuario": "", "critico": False})
                self._info[rp["pid"]]["nome_prog"] = self._nome_programa(rp)
            nome = self._info[rp["pid"]]["nome_prog"]
            d["programa"] = nome
            d["raiz"] = rp["pid"]
            self._pid_prog[pid] = (t, nome)
            gp = progs.get(nome)
            if gp is None:
                gp = progs[nome] = {"nome": nome, "mem": 0, "cpu": 0.0, "ler": 0, "gravar": 0, "env": 0, "rec": 0,
                                    "gpu": 0.0, "gpu_mem": 0, "nproc": 0, "nconn": 0, "threads": 0, "handles": 0,
                                    "janelas": 0, "ips": set(), "exe": rp["exe"], "pid_raiz": rp["pid"], "pids": [],
                                    "n_protegidos": 0}
            for k in ("mem", "cpu", "ler", "gravar", "env", "rec", "gpu", "gpu_mem", "nconn", "threads", "handles", "janelas"):
                gp[k] += d[k]
            gp["nproc"] += 1
            gp["ips"] |= self._ips_pid.get(pid, set())
            gp["pids"].append(pid)
            if d["protegido"]:
                gp["n_protegidos"] += 1
        for pid in [k for k, v in self._pid_prog.items() if t - v[0] > 300]:
            self._pid_prog.pop(pid, None)

        # sistema: CPU por núcleo, memória, discos, rede por placa
        nuc = psutil.cpu_times(percpu=True)
        vals = []
        for x in nuc:
            ocupado = x.user + x.system
            vals += [ocupado, ocupado + x.idle]
        rs = self._tx_sis.add(t, vals, J)
        nucleos = []
        if rs:
            for i in range(0, len(rs), 2):
                nucleos.append(round(min(100.0, rs[i] / rs[i + 1] * 100), 1) if rs[i + 1] > 0 else 0.0)
        else:
            nucleos = [0.0] * len(nuc)
        cpu_total = round(sum(nucleos) / max(1, len(nucleos)), 1)
        vm = psutil.virtual_memory()
        usada = vm.total - vm.available
        sw = psutil.swap_memory()
        lista_discos = self.discos.ler(t, J)
        placas = []
        env = rec = 0.0
        try:
            nics = psutil.net_io_counters(pernic=True)
        except Exception:
            nics = {}
        for nome, io in nics.items():
            tx = self._tx_nic.setdefault(nome, Taxa())
            rr = tx.add(t, (io.bytes_sent, io.bytes_recv), J) or (0.0, 0.0)
            if "loopback" in nome.lower() or nome == "lo":
                continue
            env += rr[0]
            rec += rr[1]
            placas.append({"nome": nome, "env": round(rr[0]), "rec": round(rr[1]), "env_tot": io.bytes_sent,
                           "rec_tot": io.bytes_recv})
        sres = self.sens.resumo() if c["sensores"] else {}
        gpus = self.gpu.resumo(self.sens.lista) if self.gpu.ok else []
        if gpus:
            sres["uso_gpu"] = max(g["uso"] for g in gpus)
            temps_gpu = [g["temp"] for g in gpus if g["temp"] is not None]
            if temps_gpu:
                sres["temp_gpu"] = max(temps_gpu)

        lista_p = []
        for gp in progs.values():
            gp["cpu"] = round(min(gp["cpu"], 100.0), 2)
            gp["gpu"] = round(min(gp["gpu"], 100.0), 1)
            gp["nips"] = len(gp["ips"])
            gp["ips"] = sorted(gp["ips"])[:50]
            gp["protegido"] = gp["n_protegidos"] == gp["nproc"]
            lista_p.append(gp)
        soma = sum(gp["mem"] for gp in progs.values())
        lista_p.append({"nome": banco.OUTROS, "mem": max(0, usada - soma), "cpu": None, "ler": None, "gravar": None,
                        "env": None, "rec": None, "gpu": None, "gpu_mem": 0, "nproc": 0, "nconn": 0, "threads": 0,
                        "handles": 0, "janelas": 0, "nips": 0, "ips": [], "exe": "", "pids": [], "outros": True,
                        "protegido": True})
        lista_p.sort(key=lambda g: g["mem"], reverse=True)

        try:
            freq = psutil.cpu_freq().current
        except Exception:
            freq = None
        estado = {
            "ts": t, "intervalo_ms": c["intervalo_ms"], "cpu": cpu_total, "nucleos": nucleos, "freq": freq,
            "n_nucleos": NCPU, "n_fisicos": psutil.cpu_count(logical=False),
            "mem_pct": round(vm.percent, 1), "mem_usada": usada, "mem_total": vm.total, "mem_disp": vm.available,
            "commit": sum(d["commit"] for d in procs.values()),
            "swap_pct": round(sw.percent, 1), "swap_usada": sw.used, "swap_total": sw.total,
            "ler": sum(d["ler"] for d in lista_discos), "gravar": sum(d["gravar"] for d in lista_discos),
            "disco_ativo": max((d["ativo"] for d in lista_discos), default=0.0), "discos": lista_discos,
            "env": round(env), "rec": round(rec), "placas": placas, "gpus": gpus,
            "nproc": len(procs), "nthreads": sum(d["threads"] for d in procs.values()),
            "nhandles": sum(d["handles"] for d in procs.values()), "nconn": len(self.conns),
            "ligado_desde": psutil.boot_time(), "temp_cpu": sres.get("temp_cpu"), "temp_gpu": sres.get("temp_gpu"),
            "temp_disco": sres.get("temp_disco"), "uso_gpu": sres.get("uso_gpu"),
            "rede_por_programa": self.rede.ok, "gpu_por_processo": self.gpu.ok,
            "bandeja_online": time.time() - self.bandeja_ts < 40, "programas": lista_p,
        }
        with self.lock:
            self.estado = estado
            self.procs = procs
            if int(t) != self._ult_seg:
                self._ult_seg = int(t)
                self.serie.append([int(t), cpu_total, estado["mem_pct"], estado["ler"], estado["gravar"], estado["env"],
                                   estado["rec"], estado["temp_cpu"], estado["temp_gpu"], estado["uso_gpu"],
                                   {d["id"]: [d["ler"], d["gravar"], d["ativo"]] for d in lista_discos},
                                   {g["id"]: [g["uso"], g["mem_ded_usada"]] for g in gpus}, estado["disco_ativo"]])
        self._acumular(estado, progs)
        self._historico(t, procs)
        return estado

    def _trafego_ip(self, t, pid, ip, env, rec):
        pg = self._pid_prog.get(pid, (0, None))[1] or ("Sistema (kernel)" if pid in (0, 4) else "Processo já encerrado")
        v = self.vivos.get((pg, ip))
        if v is None:
            v = self.vivos[(pg, ip)] = {"programa": pg, "ip": ip, "env_tot": 0, "rec_tot": 0, "ult": t, "primeiro": t,
                                        "ev": deque()}
        v["env_tot"] += env
        v["rec_tot"] += rec
        v["ult"] = t
        v["ev"].append((t, env, rec))
        pi = self.pid_ips.setdefault(pid, {})
        x = pi.get(ip)
        if x is None:
            x = pi[ip] = {"env_tot": 0, "rec_tot": 0, "ult": t, "ev": deque()}
        x["env_tot"] += env
        x["rec_tot"] += rec
        x["ult"] = t
        x["ev"].append((t, env, rec))
        hr = self._rem_hora[(int(t // 3600) * 3600, pg, ip)]
        hr[0] += env
        hr[1] += rec

    @staticmethod
    def _taxa_ev(x, t):
        ev = x["ev"]
        while ev and ev[0][0] <= t - config.JANELA_TAXA:
            ev.popleft()
        return (sum(a[1] for a in ev) / config.JANELA_TAXA, sum(a[2] for a in ev) / config.JANELA_TAXA)

    # ================================================================ histórico
    def _acumular(self, e, progs):
        a = self._agg
        if a is None:
            a = self._agg = {"n": 0, "g": defaultdict(float), "gmax": {}, "p": {}}
        a["n"] += 1
        for k in ("cpu", "ler", "gravar", "env", "rec"):
            a["g"][k] += e[k] or 0
        for k in ("mem_pct", "mem_usada", "swap_pct", "uso_gpu", "temp_cpu", "temp_gpu", "temp_disco"):
            v = e.get(k)
            if v is not None:
                a["gmax"][k] = max(a["gmax"].get(k, v), v)
        a["gmax"]["mem_total"] = e["mem_total"]
        for nome, g in progs.items():
            p = a["p"].get(nome)
            if p is None:
                p = a["p"][nome] = {"mem": 0, "cpu": 0.0, "ler": 0.0, "gravar": 0.0, "env": 0.0, "rec": 0.0, "nproc": 0, "nconn": 0}
            p["mem"] = max(p["mem"], g["mem"])
            for k in ("cpu", "ler", "gravar", "env", "rec"):
                p[k] += g[k] or 0
            p["nproc"] = max(p["nproc"], g["nproc"])
            p["nconn"] = max(p["nconn"], g["nconn"])
        outros = next((g for g in e["programas"] if g.get("outros")), None)
        if outros:
            p = a["p"].setdefault(banco.OUTROS, {"mem": 0, "cpu": 0.0, "ler": 0.0, "gravar": 0.0, "env": 0.0, "rec": 0.0, "nproc": 0, "nconn": 0})
            p["mem"] = max(p["mem"], outros["mem"])

    def _historico(self, t, procs):
        ts = int(t)
        if t - self._ult["g"] >= config.GRAVAR_CADA and self._agg and self._agg["n"]:
            a, self._agg = self._agg, None
            n = a["n"]
            gm = a["gmax"]
            geral = (ts, round(a["g"]["cpu"] / n, 1), gm.get("mem_pct"), gm.get("mem_usada"), gm.get("mem_total"),
                     gm.get("swap_pct"), round(a["g"]["ler"] / n), round(a["g"]["gravar"] / n), round(a["g"]["env"] / n),
                     round(a["g"]["rec"] / n), gm.get("uso_gpu"), gm.get("temp_cpu"), gm.get("temp_gpu"), gm.get("temp_disco"))
            ordem = sorted(a["p"].items(), key=lambda kv: kv[1]["mem"], reverse=True)
            apps = []
            for i, (nome, p) in enumerate(ordem):
                med = {k: p[k] / n for k in ("cpu", "ler", "gravar", "env", "rec")}
                if i < 20 or med["cpu"] >= 1 or med["ler"] + med["gravar"] >= 100_000 or med["env"] + med["rec"] >= 20_000:
                    apps.append((ts, nome, p["mem"], round(med["cpu"], 2), round(med["ler"]), round(med["gravar"]),
                                 round(med["env"]), round(med["rec"]), p["nproc"], p["nconn"]))
            banco.gravar(geral=geral, apps=apps)
            self._ult["g"] = t

        if t - self._ult["p"] >= config.GRAVAR_PROCS_CADA:
            todos = list(procs.values())
            esc = {}
            for chave, n in (("mem", 25), ("cpu", 12), ("ler", 8), ("gravar", 8), ("env", 8), ("rec", 8), ("gpu", 6)):
                for d in sorted(todos, key=lambda x: x[chave], reverse=True)[:n]:
                    if d[chave] > 0:
                        esc[d["pid"]] = d
            banco.gravar(procs=[(ts, d["pid"], d["nome"], d["programa"], d["mem"], d["cpu"], d["ler"], d["gravar"],
                                 d["env"], d["rec"]) for d in esc.values()])
            self._ult["p"] = t

        if t - self._ult["t"] >= config.GRAVAR_TEMPS_CADA:
            with self.sens.lock:
                temps = [(ts, s["id"], s["hw"], s["nome"], s["valor"]) for s in self.sens.lista
                         if s["tipo_en"] == "Temperature" and not s.get("limite")]
            if temps:
                banco.gravar(temps=temps)
            self._ult["t"] = t

        if t - self._ult["r"] >= 60:
            rem, self._rem_hora = self._rem_hora, defaultdict(lambda: [0, 0])
            if rem:
                dns = configuracao.obter("dns_reverso")
                banco.gravar(remotos=[(h, pg, ip, self.dns.nome(ip) if dns else None, v[0], v[1])
                                      for (h, pg, ip), v in rem.items()])
            for k in [k for k, v in self.vivos.items() if t - v["ult"] > 3600]:
                self.vivos.pop(k, None)
            for pid in [p for p in self.pid_ips if p not in procs]:
                if all(t - x["ult"] > 600 for x in self.pid_ips[pid].values()):
                    self.pid_ips.pop(pid, None)
            self._ult["r"] = t

        if t - self._ult["l"] >= config.LIMPAR_CADA:
            banco.limpar(configuracao.obter("retencao_dias"))
            self._ult["l"] = t

    # ================================================================ consultas
    def agora(self):
        with self.lock:
            if not self.estado:
                return None
            e = dict(self.estado)
        e["programas"] = [{k: v for k, v in g.items() if k not in ("pids", "ips")} for g in e["programas"]]
        return e

    def processos(self):
        with self.lock:
            return list(self.procs.values())

    def serie_curta(self, seg):
        with self.lock:
            return list(self.serie)[-int(seg):]

    def _host(self, ip):
        return self.dns.nome(ip) if configuracao.obter("dns_reverso") else None

    def _conn_fmt(self, c, procs):
        d = procs.get(c["pid"])
        rip = c["rip"]
        return {**c, "processo": d["nome"] if d else ("Sistema" if c["pid"] in (0, 4) else "?"),
                "programa": d["programa"] if d else ("Sistema (kernel)" if c["pid"] in (0, 4) else "?"),
                "host": self._host(rip) if rip else None, "tipo": rede.tipo_ip(rip) if rip else "",
                "servico": rede.PORTAS.get(c["rport"]) or rede.PORTAS.get(c["lport"]) or ""}

    def _ips_fmt(self, mapa, t):
        out = []
        for ip, x in mapa.items():
            e, r = self._taxa_ev(x, t)
            out.append({"ip": ip, "host": self._host(ip), "tipo": rede.tipo_ip(ip), "env_tot": x["env_tot"],
                        "rec_tot": x["rec_tot"], "env": round(e), "rec": round(r), "ult": x["ult"],
                        "primeiro": x.get("primeiro")})
        out.sort(key=lambda r: r["env_tot"] + r["rec_tot"], reverse=True)
        return out

    def processo(self, pid):
        t = time.time()
        with self.lock:
            d = self.procs.get(pid)
            if not d:
                return None
            d = dict(d)
            procs = self.procs
            conns = [self._conn_fmt(c, procs) for c in self.conns if c["pid"] == pid]
            filhos = [{"pid": x["pid"], "nome": x["nome"], "mem": x["mem"], "cpu": x["cpu"]}
                      for x in procs.values() if x["ppid"] == pid and x["inicio"] >= d["inicio"] - 1]
            pai = procs.get(d["ppid"])
            ips = dict(self.pid_ips.get(pid, {}))
            janelas = list(self.janelas.get(pid, []))
        try:
            p = psutil.Process(pid)
            d["cmdline"] = " ".join(p.cmdline())
            d["prioridade"] = str(p.nice())
            d["cpu_tempo"] = sum(p.cpu_times()[:2])
        except (psutil.Error, OSError):
            d["cmdline"] = ""
        d["info"] = nt.info_arquivo(d["exe"])
        d["pai"] = {"pid": pai["pid"], "nome": pai["nome"]} if pai else None
        d["filhos"] = sorted(filhos, key=lambda x: x["mem"], reverse=True)
        d["conexoes"] = conns
        d["ips"] = self._ips_fmt(ips, t)
        d["lista_janelas"] = janelas
        return d

    def programa(self, nome):
        t = time.time()
        with self.lock:
            g = next((x for x in (self.estado or {}).get("programas", []) if x["nome"] == nome), None)
            g = dict(g) if g else {"nome": nome, "ausente": True, "pids": [], "ips": []}
            pids = set(g.get("pids", []))
            procs = self.procs
            g["processos"] = sorted([procs[p] for p in pids if p in procs], key=lambda x: x["mem"], reverse=True)
            g["conexoes"] = [self._conn_fmt(c, procs) for c in self.conns if c["pid"] in pids]
            vivos = {ip: v for (pg, ip), v in self.vivos.items() if pg == nome}
            g["lista_janelas"] = [{"pid": p, "titulo": tt} for p in pids for tt in self.janelas.get(p, [])]
        g.pop("pids", None)
        g["info"] = nt.info_arquivo(g.get("exe", ""))
        g["ips_vivos"] = self._ips_fmt(vivos, t)
        g["ips_7d"] = banco.remotos_programa(nome)
        for r in g["ips_7d"]:
            r["host"] = r["host"] or self._host(r["ip"])
            r["tipo"] = rede.tipo_ip(r["ip"])
        g["hist"] = banco.historico(24, nome)
        return g

    def rede_estado(self):
        t = time.time()
        with self.lock:
            procs = self.procs
            conns = [self._conn_fmt(c, procs) for c in self.conns]
            e = self.estado or {}
            progs = [{k: g[k] for k in ("nome", "env", "rec", "nconn", "nips")}
                     for g in e.get("programas", []) if not g.get("outros")]
            vivos = list(self.vivos.values())
        rem = []
        for v in vivos:
            en, rc = self._taxa_ev(v, t)
            rem.append({"programa": v["programa"], "ip": v["ip"], "host": self._host(v["ip"]), "tipo": rede.tipo_ip(v["ip"]),
                        "env_tot": v["env_tot"], "rec_tot": v["rec_tot"], "env": round(en), "rec": round(rc),
                        "ult": v["ult"], "primeiro": v["primeiro"]})
        rem.sort(key=lambda r: r["env_tot"] + r["rec_tot"], reverse=True)
        try:
            ifs = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
        except Exception:
            ifs, addrs = {}, {}
        placas = []
        for p in e.get("placas", []):
            st = ifs.get(p["nome"])
            if st is not None and not st.isup:
                continue
            placas.append({**p, "velocidade": st.speed if st else 0,
                           "ips": [a.address for a in addrs.get(p["nome"], []) if a.family == 2]})
        progs.sort(key=lambda g: (g["env"] or 0) + (g["rec"] or 0), reverse=True)
        return {"ativo": self.rede.ok, "erro": self.rede.erro, "env": e.get("env"), "rec": e.get("rec"),
                "programas": progs, "conexoes": conns, "remotos": rem[:500], "interfaces": placas, "desde": self.inicio}

    def hardware(self):
        import platform
        s = self.sens.estado()
        parts = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
                parts.append({"unidade": p.device, "ponto": p.mountpoint, "fs": p.fstype, "total": u.total,
                              "usado": u.used, "pct": u.percent})
            except Exception:
                continue
        bat = None
        try:
            b = psutil.sensors_battery()
            if b:
                bat = {"pct": b.percent, "tomada": b.power_plugged, "restante": b.secsleft}
        except Exception:
            pass
        try:
            f = psutil.cpu_freq()
            freq = {"atual": f.current, "max": f.max}
        except Exception:
            freq = None
        e = self.estado or {}
        cpu_nome = next((x["hw"] for x in s["sensores"] if x["tipo_hw"] == "Processador"), None) or platform.processor()
        temps_disco = [x for x in s["sensores"] if x["tipo_hw"] == "Armazenamento" and x["tipo_en"] == "Temperature" and not x.get("limite")]
        fisicos = []
        for d in e.get("discos", []):
            tmp = next((x["valor"] for x in temps_disco if gpu._norm(x["hw"]) and (gpu._norm(x["hw"]) in gpu._norm(d["nome"]) or gpu._norm(d["nome"]) in gpu._norm(x["hw"]))), None)
            fisicos.append({**d, "temp": tmp})
        return {**s, "particoes": parts, "discos": fisicos, "gpus": e.get("gpus", []), "gpu_erro": self.gpu.erro,
                "bateria": bat, "cpu_nome": cpu_nome, "freq": freq, "nucleos": NCPU, "fisicos": psutil.cpu_count(logical=False),
                "ram_total": psutil.virtual_memory().total, "sistema": f"{platform.system()} {platform.release()} ({platform.version()})",
                "maquina": platform.node(), "ligado_desde": psutil.boot_time()}

    def status(self):
        tm = list(self.tempos)
        return {"versao": config.VERSAO, "admin": eh_admin(), "modo_coleta": self.leitor.modo,
                "tempo_amostra_ms": round(sum(tm) / len(tm) * 1000, 1) if tm else None,
                "tempo_amostra_max_ms": round(max(tm) * 1000, 1) if tm else None,
                "rede_ok": self.rede.ok, "rede_erro": self.rede.erro, "eventos_rede": self.rede.eventos,
                "etw": self.rede.estatisticas(), "sensores": self.sens.fontes, "avisos": self.sens.avisos[-5:],
                "pawnio": self.sens.pawnio, "gpu_ok": self.gpu.ok, "gpu_erro": self.gpu.erro,
                "gpus": [a["nome"] for a in self.gpu.adaptadores], "bandeja_online": time.time() - self.bandeja_ts < 40,
                "inicio": self.inicio, "banco": banco.info(), "erros": list(self.erros)[-3:], "pid": os.getpid()}

    # ================================================================ inicialização do Windows
    def inicializacao(self):
        itens = inicializacao.listar()
        with self.lock:
            por_exe = defaultdict(list)
            for d in self.procs.values():
                if d["exe"]:
                    por_exe[d["exe"].lower()].append(d)
        self._ini_cache = {i["id"]: i for i in itens}
        out = []
        for i in itens:
            ps = por_exe.get((i["exe"] or "").lower(), [])
            x = {k: v for k, v in i.items() if not k.startswith("_")}
            x["rodando"] = bool(ps)
            x["programa"] = ps[0]["programa"] if ps else None
            x["mem"] = sum(p["mem"] for p in ps)
            x["cpu"] = round(sum(p["cpu"] for p in ps), 2)
            x["pids"] = [p["pid"] for p in ps]
            out.append(x)
        return out

    # ================================================================ ações
    def _enfileirar(self, acao):
        if time.time() - self.bandeja_ts > 40:
            return {"ok": False, "msg": "O ícone do Monitor do PC (perto do relógio) precisa estar aberto para isso. "
                                        "Abra o Monitor do PC pelo menu Iniciar e tente de novo."}
        acao["id"] = uuid.uuid4().hex[:12]
        self.resultados[acao["id"]] = None
        self.fila.put(acao)
        return {"ok": True, "id": acao["id"], "pendente": True}

    def _descendentes(self, pid):
        with self.lock:
            procs = self.procs
            filhos = defaultdict(list)
            for d in procs.values():
                filhos[d["ppid"]].append(d)
        out = []
        pilha = [pid]
        base = procs.get(pid)
        while pilha:
            p = pilha.pop()
            for f in filhos.get(p, []):
                if base and f["inicio"] >= base["inicio"] - 1 and f["pid"] not in out:
                    out.append(f["pid"])
                    pilha.append(f["pid"])
        return out

    def _matar(self, pids):
        mortos, protegidos, erros = [], [], []
        with self.lock:
            procs = dict(self.procs)
        for pid in pids:
            d = procs.get(pid)
            if not d:
                continue
            if d["protegido"]:
                protegidos.append(f'{d["nome"]} ({d["protegido"]})')
                continue
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - d["inicio"]) > 2:
                    continue
                p.kill()
                mortos.append(pid)
            except psutil.NoSuchProcess:
                mortos.append(pid)
            except (psutil.Error, OSError) as e:
                erros.append(f'{d["nome"]}: {e}')
        return mortos, protegidos, erros

    def acao(self, a):
        c = configuracao.obter()
        tipo = a.get("tipo")
        pid = int(a.get("pid") or 0)
        nome = a.get("programa")
        with self.lock:
            d = self.procs.get(pid) if pid else None
            g = next((x for x in (self.estado or {}).get("programas", []) if x["nome"] == nome), None) if nome else None
        if pid and d is None and tipo not in ("pasta", "abrir_exe"):
            return {"ok": False, "msg": "Esse processo não está mais aberto."}
        if pid and d and a.get("inicio") and abs(float(a["inicio"]) - d["inicio"]) > 2:
            return {"ok": False, "msg": "Esse processo já foi encerrado (o número dele foi reaproveitado)."}

        if tipo in ("encerrar", "encerrar_arvore", "forcar_programa"):
            if not c["permitir_encerrar"]:
                return {"ok": False, "msg": "Encerrar processos está desligado nas Configurações."}
            if tipo == "forcar_programa":
                if not g:
                    return {"ok": False, "msg": "Esse programa não está aberto."}
                pids = list(g["pids"])
            elif tipo == "encerrar_arvore":
                pids = self._descendentes(pid)[::-1] + [pid]
            else:
                pids = [pid]
            if d and d["protegido"] and tipo != "forcar_programa":
                return {"ok": False, "msg": f'Não é permitido encerrar "{d["nome"]}": {d["protegido"]}. '
                                            f'Encerrar isso pode travar ou corromper o Windows.'}
            mortos, protegidos, erros = self._matar(pids)
            msg = f"{len(mortos)} processo(s) encerrado(s)."
            if protegidos:
                msg += f" {len(protegidos)} protegido(s) do Windows foram mantidos."
            if erros:
                msg += " Erros: " + "; ".join(erros[:3])
            return {"ok": bool(mortos) or not (protegidos or erros), "msg": msg, "mortos": mortos}

        if tipo in ("mostrar", "fechar_janelas"):
            if g:
                pids, exe = list(g["pids"]), g["exe"]
                if tipo == "fechar_janelas":
                    with self.lock:
                        pids = [p for p in pids if not (self.procs.get(p) or {}).get("protegido")]
                    if not pids:
                        return {"ok": False, "msg": "Esse programa faz parte do Windows e não pode ser fechado por aqui."}
            elif d:
                pids, exe = [pid] + self._descendentes(pid), d["exe"]
                if tipo == "fechar_janelas" and d["protegido"]:
                    return {"ok": False, "msg": f'"{d["nome"]}" é protegido: {d["protegido"]}.'}
            else:
                return {"ok": False, "msg": "Nada para mostrar."}
            return self._enfileirar({"tipo": tipo, "pids": pids, "exe": exe})

        if tipo == "pasta":
            caminho = a.get("caminho") or (d or {}).get("exe") or (g or {}).get("exe")
            if not caminho:
                return {"ok": False, "msg": "O Windows não informa o local desse processo (protegido do sistema)."}
            return self._enfileirar({"tipo": "pasta", "caminho": caminho})

        if tipo == "abrir_exe":
            caminho = a.get("caminho") or (d or {}).get("exe") or (g or {}).get("exe")
            if not caminho:
                return {"ok": False, "msg": "Caminho do programa desconhecido."}
            return self._enfileirar({"tipo": "abrir_exe", "caminho": caminho})

        if tipo == "inicializacao":
            item = self._ini_cache.get(a.get("id"))
            if not item:
                self.inicializacao()
                item = self._ini_cache.get(a.get("id"))
            if not item:
                return {"ok": False, "msg": "Item de inicialização não encontrado."}
            try:
                inicializacao.alterar(item, bool(a.get("ativo")))
            except OSError as e:
                return {"ok": False, "msg": f"Não consegui alterar: {e}"}
            return {"ok": True, "msg": f'"{item["nome"]}" {"vai" if a.get("ativo") else "não vai mais"} iniciar com o Windows.'}

        return {"ok": False, "msg": "Ação desconhecida."}

    # --- comunicação com o ícone da bandeja (que roda na sessão do usuário)
    def pegar_fila(self, espera):
        self.bandeja_ts = time.time()
        acoes = []
        try:
            acoes.append(self.fila.get(timeout=espera))
            while True:
                acoes.append(self.fila.get_nowait())
        except queue.Empty:
            pass
        self.bandeja_ts = time.time()
        return acoes

    def resultado(self, rid, dados=None):
        if dados is not None:
            self.resultados[rid] = dados
            if len(self.resultados) > 200:
                for k in list(self.resultados)[:100]:
                    self.resultados.pop(k, None)
        return self.resultados.get(rid)

    def receber_janelas(self, mapa):
        self.bandeja_ts = time.time()
        with self.lock:
            self.janelas = {int(k): v for k, v in (mapa or {}).items()}
            self.janelas_ts = time.time()
