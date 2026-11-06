"""Motor de coleta: lê processos, agrupa por programa, soma rede/disco/CPU/memória,
lê sensores, guarda histórico e mantém o estado ao vivo para a interface."""
import os
import time
import platform
import threading
import traceback
from collections import deque, defaultdict

import psutil

from . import config, banco, rede, temperatura

WIN = os.name == "nt"
NCPU = psutil.cpu_count() or 1
LIMIAR_USS = 30 * 1024 * 1024
EU_SERVICO = "Monitor do PC"

RAIZES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "svchost.exe", "explorer.exe", "userinit.exe", "sihost.exe", "taskhostw.exe",
    "runtimebroker.exe", "lsass.exe", "dllhost.exe", "wmiprvse.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "applicationframehost.exe", "searchhost.exe", "systemd", "init",
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
    "docker desktop": "Docker Desktop", "com.docker.backend": "Docker Desktop", "teams": "Microsoft Teams",
    "ms-teams": "Microsoft Teams", "whatsapp": "WhatsApp", "steam": "Steam", "steamwebhelper": "Steam",
    "obs64": "OBS Studio", "monitorpc": EU_SERVICO, "monitorpcservico": EU_SERVICO, "lsass": "Segurança do Windows (LSASS)",
    "csrss": "Subsistema do Windows (CSRSS)", "wininit": "Inicialização do Windows", "winlogon": "Logon do Windows",
    "services": "Gerenciador de serviços", "smss": "Gerenciador de sessão", "spoolsv": "Spooler de impressão",
    "rustdesk": "RustDesk", "anydesk": "AnyDesk", "teamviewer": "TeamViewer", "zoom": "Zoom",
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "outlook": "Outlook", "olk": "Outlook",
    "notepad": "Bloco de Notas", "phpstorm64": "PhpStorm", "idea64": "IntelliJ IDEA", "devenv": "Visual Studio",
    "sublime_text": "Sublime Text", "telegram": "Telegram", "slack": "Slack", "postman": "Postman",
    "heidisql": "HeidiSQL", "dbeaver": "DBeaver", "filezilla": "FileZilla", "putty": "PuTTY", "winscp": "WinSCP",
}


def nome_amigavel(nome_proc, exe=""):
    base = (nome_proc or "?").lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base == "claude" and exe and (".local" in exe.lower() or "claude-code" in exe.lower()):
        return "Claude Code"
    if base in NOMES:
        return NOMES[base]
    n = nome_proc[:-4] if nome_proc.lower().endswith(".exe") else nome_proc
    return (n[:1].upper() + n[1:]) if n else "?"


def eh_admin():
    if not WIN:
        return os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class Motor:
    def __init__(self):
        banco.iniciar()
        self.lock = threading.RLock()
        self.parar_ev = threading.Event()
        self.rede = rede.MonitorRede()
        self.dns = rede.DNS()
        self.sens = temperatura.Sensores()
        self.inicio = time.time()
        self.estado = None
        self.procs = {}
        self.conns = []
        self.curto = deque(maxlen=180)
        self.erros = deque(maxlen=20)
        self._cache = {}
        self._uss = {}
        self._io = {}
        self._extra = {}
        self._pid_prog = {}
        self._disco_ant = None
        self._rede_ant = None
        self._t_ant = None
        self.vivos = {}      # (programa, ip) -> dados ao vivo
        self.pid_ips = {}    # pid -> {ip: [env_tot, rec_tot, env_bps, rec_bps, ult]}
        self._jan = []
        self._rem_hora = defaultdict(lambda: [0, 0])
        self._ult = {"g": time.time(), "p": time.time(), "t": time.time(), "r": time.time(), "l": 0}
        self._meus = {os.getpid()}
        self.ultima_amostra_s = 0
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)

    # ------------------------------------------------------------ ciclo
    def iniciar(self):
        self.rede.iniciar()
        self.sens.iniciar()
        threading.Thread(target=self._loop_sensores, name="sensores", daemon=True).start()
        threading.Thread(target=self._loop, name="coleta", daemon=True).start()

    def parar(self):
        self.parar_ev.set()
        self.rede.parar()

    def _loop_sensores(self):
        while not self.parar_ev.is_set():
            try:
                self.sens.ler()
            except Exception:
                self.erros.append(traceback.format_exc())
            self.parar_ev.wait(config.INTERVALO)

    def _loop(self):
        while not self.parar_ev.is_set():
            ini = time.time()
            try:
                self.amostrar()
            except Exception:
                self.erros.append(traceback.format_exc())
                traceback.print_exc()
            self.ultima_amostra_s = round(time.time() - ini, 2)
            self.parar_ev.wait(max(0.5, config.INTERVALO - (time.time() - ini)))

    # ------------------------------------------------------------ coleta
    def _uss_de(self, proc, pid, t):
        c = self._uss.get(pid)
        if c and t - c[0] < config.GRAVAR_CADA:
            return c[1]
        try:
            u = proc.memory_full_info().uss
        except (psutil.Error, OSError):
            u = None
        self._uss[pid] = (t, u)
        return u

    def _extra_de(self, proc, pid, ct):
        """Dados que não mudam: usuário, caminho."""
        e = self._extra.get(pid)
        if e and e[0] == ct:
            return e[1]
        d = {"usuario": "", "exe": ""}
        try:
            d["usuario"] = (proc.username() or "").split("\\")[-1]
        except (psutil.Error, OSError):
            pass
        try:
            d["exe"] = proc.exe() or ""
        except (psutil.Error, OSError):
            pass
        self._extra[pid] = (ct, d)
        return d

    def amostrar(self):
        t = time.time()
        dt = (t - self._t_ant) if self._t_ant else config.INTERVALO
        self._t_ant = t

        # rede por processo (ETW)
        trafego = self.rede.coletar() if self.rede.ok else []
        net_pid = defaultdict(lambda: [0, 0])
        for pid, ip, env, rec in trafego:
            net_pid[pid][0] += env
            net_pid[pid][1] += rec

        procs = {}
        for p in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
            info = p.info
            pid = info["pid"]
            if WIN and pid == 0:
                continue
            ct = info.get("create_time") or 0
            c = self._cache.get(pid)
            novo = c is None or c[0] != ct
            if novo:
                c = (ct, p)
                self._cache[pid] = c
                for d in (self._io, self._uss, self._extra):
                    d.pop(pid, None)
            proc = c[1]
            d = {"pid": pid, "ppid": info.get("ppid") or 0, "nome": info.get("name") or "?", "ct": ct,
                 "cpu": 0.0, "mem": 0, "privada": 0, "threads": 0, "handles": 0, "ler": 0.0, "gravar": 0.0,
                 "status": ""}
            try:
                with proc.oneshot():
                    try:
                        cp = proc.cpu_percent(None)
                        d["cpu"] = 0.0 if novo else min(100.0, cp / NCPU)
                    except (psutil.Error, OSError):
                        pass
                    try:
                        mi = proc.memory_info()
                        d["mem"] = mi.rss
                        d["privada"] = (getattr(mi, "private", None) or mi.vms) if WIN else mi.rss
                    except (psutil.Error, OSError):
                        pass
                    try:
                        d["threads"] = proc.num_threads()
                    except (psutil.Error, OSError):
                        pass
                    if WIN:
                        try:
                            d["handles"] = proc.num_handles()
                        except (psutil.Error, OSError):
                            pass
                    else:
                        try:
                            d["handles"] = proc.num_fds()
                        except (psutil.Error, OSError):
                            pass
                    try:
                        io = proc.io_counters()
                        ant = self._io.get(pid)
                        if ant and t > ant[0]:
                            d["ler"] = max(0.0, (io.read_bytes - ant[1]) / (t - ant[0]))
                            d["gravar"] = max(0.0, (io.write_bytes - ant[2]) / (t - ant[0]))
                        self._io[pid] = (t, io.read_bytes, io.write_bytes)
                    except (psutil.Error, OSError, AttributeError):
                        pass
                    try:
                        d["status"] = proc.status()
                    except (psutil.Error, OSError):
                        pass
            except (psutil.Error, OSError):
                pass
            if d["mem"] > LIMIAR_USS:
                u = self._uss_de(proc, pid, t)
                if u:
                    d["mem"] = u
            d.update(self._extra_de(proc, pid, ct))
            n = net_pid.get(pid)
            d["env"] = n[0] / dt if n else 0.0
            d["rec"] = n[1] / dt if n else 0.0
            procs[pid] = d

        for dct in (self._cache, self._uss, self._io, self._extra):
            for pid in [k for k in dct if k not in procs]:
                dct.pop(pid, None)

        # conexões
        conns = rede.conexoes()
        nconn = defaultdict(int)
        ips_pid = defaultdict(set)
        for c in conns:
            nconn[c["pid"]] += 1
            if c["rip"] and rede.tipo_ip(c["rip"]) != "este PC":
                ips_pid[c["pid"]].add(c["rip"])

        # agrupar por programa
        raiz = {}

        def achar(pid):
            caminho = []
            cur = pid
            while True:
                if cur in raiz:
                    r = raiz[cur]
                    break
                caminho.append(cur)
                pr = procs[cur]
                pp = pr["ppid"]
                pai = procs.get(pp)
                if (pai is None or pp == cur or pp in caminho or pai["nome"].lower() in RAIZES
                        or pai["ct"] > pr["ct"] + 1):
                    r = cur
                    break
                cur = pp
            for x in caminho:
                raiz[x] = r
            return r

        progs = {}
        for pid, d in procs.items():
            r = achar(pid)
            rp = procs[r]
            nome = EU_SERVICO if r in self._meus else nome_amigavel(rp["nome"], rp["exe"])
            d["programa"] = nome
            d["nconn"] = nconn.get(pid, 0)
            self._pid_prog[pid] = (t, nome)
            g = progs.get(nome)
            if g is None:
                g = progs[nome] = {"nome": nome, "mem": 0, "cpu": 0.0, "ler": 0.0, "gravar": 0.0, "env": 0.0,
                                   "rec": 0.0, "nproc": 0, "nconn": 0, "threads": 0, "handles": 0, "ips": set(),
                                   "exe": rp["exe"], "pids": []}
            g["mem"] += d["mem"]
            g["cpu"] += d["cpu"]
            g["ler"] += d["ler"]
            g["gravar"] += d["gravar"]
            g["env"] += d["env"]
            g["rec"] += d["rec"]
            g["nproc"] += 1
            g["nconn"] += d["nconn"]
            g["threads"] += d["threads"]
            g["handles"] += d["handles"]
            g["ips"] |= ips_pid.get(pid, set())
            g["pids"].append(pid)

        # tráfego por IP remoto (vivo + totais desde que o serviço iniciou)
        for k in [k for k, v in self._pid_prog.items() if t - v[0] > 300]:
            self._pid_prog.pop(k, None)
        for pid, ip, env, rec in trafego:
            pg = self._pid_prog.get(pid, (0, None))[1] or ("Sistema (kernel)" if pid in (0, 4) else f"PID {pid} (encerrado)")
            v = self.vivos.get((pg, ip))
            if v is None:
                v = self.vivos[(pg, ip)] = {"programa": pg, "ip": ip, "env_tot": 0, "rec_tot": 0, "env": 0.0,
                                            "rec": 0.0, "ult": t, "primeiro": t, "pids": set()}
            v["env_tot"] += env
            v["rec_tot"] += rec
            v["_env"] = v.get("_env", 0) + env
            v["_rec"] = v.get("_rec", 0) + rec
            v["ult"] = t
            v["pids"].add(pid)
            pi = self.pid_ips.setdefault(pid, {})
            x = pi.get(ip)
            if x is None:
                x = pi[ip] = [0, 0, 0.0, 0.0, t]
            x[0] += env
            x[1] += rec
            x[2] = env / dt
            x[3] = rec / dt
            x[4] = t
            hr = self._rem_hora[(int(t // 3600) * 3600, pg, ip)]
            hr[0] += env
            hr[1] += rec
            if pg in progs:
                progs[pg]["ips"].add(ip)
        for k, v in list(self.vivos.items()):
            v["env"] = v.pop("_env", 0) / dt
            v["rec"] = v.pop("_rec", 0) / dt
            if t - v["ult"] > 3600:
                self.vivos.pop(k, None)
        for pid in [p for p in self.pid_ips if p not in procs]:
            if all(t - x[4] > 600 for x in self.pid_ips[pid].values()):
                self.pid_ips.pop(pid, None)

        # totais do sistema
        vm = psutil.virtual_memory()
        usada = vm.total - vm.available
        soma = sum(g["mem"] for g in progs.values())
        sw = psutil.swap_memory()
        cpu_total = psutil.cpu_percent(None)
        nucleos = psutil.cpu_percent(None, percpu=True)
        try:
            freq = psutil.cpu_freq().current
        except Exception:
            freq = None
        dsk = psutil.disk_io_counters()
        ler = gravar = 0.0
        if dsk:
            if self._disco_ant and t > self._disco_ant[0]:
                ler = max(0.0, (dsk.read_bytes - self._disco_ant[1]) / (t - self._disco_ant[0]))
                gravar = max(0.0, (dsk.write_bytes - self._disco_ant[2]) / (t - self._disco_ant[0]))
            self._disco_ant = (t, dsk.read_bytes, dsk.write_bytes)
        net = psutil.net_io_counters()
        env = rec = 0.0
        if net:
            if self._rede_ant and t > self._rede_ant[0]:
                env = max(0.0, (net.bytes_sent - self._rede_ant[1]) / (t - self._rede_ant[0]))
                rec = max(0.0, (net.bytes_recv - self._rede_ant[2]) / (t - self._rede_ant[0]))
            self._rede_ant = (t, net.bytes_sent, net.bytes_recv)
        sres = self.sens.resumo()

        lista = []
        for g in progs.values():
            g["cpu"] = round(min(g["cpu"], 100.0), 2)
            g["nips"] = len(g["ips"])
            g["ips"] = sorted(g["ips"])[:50]
            lista.append(g)
        lista.append({"nome": banco.OUTROS, "mem": max(0, usada - soma), "cpu": None, "ler": None, "gravar": None,
                      "env": None, "rec": None, "nproc": 0, "nconn": 0, "threads": 0, "handles": 0, "nips": 0,
                      "ips": [], "exe": "", "pids": [], "outros": True})
        lista.sort(key=lambda g: g["mem"], reverse=True)

        estado = {
            "ts": t, "cpu": round(cpu_total, 1), "nucleos": [round(x, 1) for x in nucleos], "freq": freq,
            "n_nucleos": NCPU, "n_fisicos": psutil.cpu_count(logical=False),
            "mem_pct": round(vm.percent, 1), "mem_usada": usada, "mem_total": vm.total, "mem_disp": vm.available,
            "swap_pct": round(sw.percent, 1), "swap_usada": sw.used, "swap_total": sw.total,
            "ler": round(ler), "gravar": round(gravar), "env": round(env), "rec": round(rec),
            "nproc": len(procs), "nthreads": sum(d["threads"] for d in procs.values()), "nconn": len(conns),
            "ligado_desde": psutil.boot_time(), **sres,
            "rede_por_programa": self.rede.ok, "programas": lista,
        }
        with self.lock:
            self.estado = estado
            self.procs = procs
            self.conns = conns
            self.curto.append((int(t), estado["cpu"], estado["mem_pct"], ler + gravar, env + rec,
                               sres.get("temp_cpu"), sres.get("temp_gpu"), sres.get("uso_gpu")))
        self._jan.append(estado)
        self._historico(t, procs, progs)
        return estado

    # ------------------------------------------------------------ histórico
    def _historico(self, t, procs, progs):
        ts = int(t)
        if t - self._ult["g"] >= config.GRAVAR_CADA and self._jan:
            jan, self._jan = self._jan, []
            n = len(jan)
            med = lambda k: sum((e.get(k) or 0) for e in jan) / n  # noqa: E731
            mx = lambda k: max(((e.get(k)) for e in jan if e.get(k) is not None), default=None)  # noqa: E731
            geral = (ts, round(med("cpu"), 1), mx("mem_pct"), mx("mem_usada"), jan[-1]["mem_total"], mx("swap_pct"),
                     round(med("ler")), round(med("gravar")), round(med("env")), round(med("rec")),
                     mx("uso_gpu"), mx("temp_cpu"), mx("temp_gpu"), mx("temp_disco"))
            acc = defaultdict(lambda: {"mem": 0, "cpu": 0.0, "ler": 0.0, "gravar": 0.0, "env": 0.0, "rec": 0.0,
                                       "nproc": 0, "nconn": 0})
            for e in jan:
                for g in e["programas"]:
                    a = acc[g["nome"]]
                    a["mem"] = max(a["mem"], g["mem"])
                    for k in ("cpu", "ler", "gravar", "env", "rec"):
                        a[k] += (g[k] or 0) / n
                    a["nproc"] = max(a["nproc"], g["nproc"])
                    a["nconn"] = max(a["nconn"], g["nconn"])
            ordem = sorted(acc.items(), key=lambda kv: kv[1]["mem"], reverse=True)
            manter = [kv for i, kv in enumerate(ordem)
                      if i < 20 or kv[1]["cpu"] >= 1 or kv[1]["ler"] + kv[1]["gravar"] >= 100_000
                      or kv[1]["env"] + kv[1]["rec"] >= 20_000]
            apps = [(ts, nome, a["mem"], round(a["cpu"], 2), round(a["ler"]), round(a["gravar"]), round(a["env"]),
                     round(a["rec"]), a["nproc"], a["nconn"]) for nome, a in manter]
            banco.gravar(geral=geral, apps=apps)
            self._ult["g"] = t

        if t - self._ult["p"] >= config.GRAVAR_PROCS_CADA:
            todos = list(procs.values())
            esc = {}
            for chave, n in (("mem", 25), ("cpu", 12), ("ler", 8), ("gravar", 8), ("env", 8), ("rec", 8)):
                for d in sorted(todos, key=lambda x: x[chave], reverse=True)[:n]:
                    if d[chave] > 0:
                        esc[d["pid"]] = d
            banco.gravar(procs=[(ts, d["pid"], d["nome"], d["programa"], d["mem"], round(d["cpu"], 2),
                                 round(d["ler"]), round(d["gravar"]), round(d["env"]), round(d["rec"]))
                                for d in esc.values()])
            self._ult["p"] = t

        if t - self._ult["t"] >= config.GRAVAR_TEMPS_CADA:
            with self.sens.lock:
                temps = [(ts, s["id"], s["hw"], s["nome"], s["valor"]) for s in self.sens.lista
                         if s["tipo_en"] == "Temperature"]
            if temps:
                banco.gravar(temps=temps)
            self._ult["t"] = t

        if t - self._ult["r"] >= 60 and self._rem_hora:
            rem, self._rem_hora = self._rem_hora, defaultdict(lambda: [0, 0])
            banco.gravar(remotos=[(h, pg, ip, self.dns.nome(ip), v[0], v[1]) for (h, pg, ip), v in rem.items()])
            self._ult["r"] = t

        if t - self._ult["l"] >= config.LIMPAR_CADA:
            banco.limpar()
            self._ult["l"] = t

    # ------------------------------------------------------------ consultas ao vivo
    def agora(self):
        with self.lock:
            if not self.estado:
                return None
            e = dict(self.estado)
            e["programas"] = [{k: v for k, v in g.items() if k not in ("pids", "ips")} for g in e["programas"]]
            e["curto"] = list(self.curto)
            return e

    def processos(self):
        with self.lock:
            return [{k: v for k, v in d.items() if k != "ct"} | {"inicio": d["ct"]} for d in self.procs.values()]

    def _conn_fmt(self, c, procs):
        d = procs.get(c["pid"])
        rip = c["rip"]
        return {**c, "processo": d["nome"] if d else ("Sistema" if c["pid"] in (0, 4) else "?"),
                "programa": d["programa"] if d else ("Sistema (kernel)" if c["pid"] in (0, 4) else "?"),
                "host": self.dns.nome(rip) if rip else None, "tipo": rede.tipo_ip(rip) if rip else "",
                "servico": rede.PORTAS.get(c["rport"]) or rede.PORTAS.get(c["lport"]) or ""}

    def _ips_fmt(self, mapa):
        out = []
        for ip, x in mapa.items():
            out.append({"ip": ip, "host": self.dns.nome(ip), "tipo": rede.tipo_ip(ip), "env_tot": x["env_tot"],
                        "rec_tot": x["rec_tot"], "env": x["env"], "rec": x["rec"], "ult": x["ult"],
                        "primeiro": x.get("primeiro")})
        out.sort(key=lambda r: r["env_tot"] + r["rec_tot"], reverse=True)
        return out

    def processo(self, pid):
        with self.lock:
            d = self.procs.get(pid)
            if not d:
                return None
            d = dict(d)
            procs = self.procs
            conns = [self._conn_fmt(c, procs) for c in self.conns if c["pid"] == pid]
            filhos = [{"pid": x["pid"], "nome": x["nome"], "mem": x["mem"], "cpu": x["cpu"]}
                      for x in procs.values() if x["ppid"] == pid]
            pai = procs.get(d["ppid"])
            ips = {ip: {"env_tot": x[0], "rec_tot": x[1], "env": x[2], "rec": x[3], "ult": x[4]}
                   for ip, x in self.pid_ips.get(pid, {}).items()}
        try:
            p = psutil.Process(pid)
            d["cmdline"] = " ".join(p.cmdline())
            d["prioridade"] = str(p.nice())
            d["cpu_tempo"] = sum(p.cpu_times()[:2])
        except (psutil.Error, OSError):
            d["cmdline"] = ""
        d["inicio"] = d.pop("ct")
        d["pai"] = {"pid": pai["pid"], "nome": pai["nome"]} if pai else None
        d["filhos"] = sorted(filhos, key=lambda x: x["mem"], reverse=True)
        d["conexoes"] = conns
        d["ips"] = self._ips_fmt(ips)
        return d

    def programa(self, nome):
        with self.lock:
            g = next((x for x in (self.estado or {}).get("programas", []) if x["nome"] == nome), None)
            if g is None:
                g = {"nome": nome, "ausente": True, "pids": [], "ips": []}
            g = dict(g)
            pids = set(g.get("pids", []))
            procs = self.procs
            g["processos"] = sorted(
                [{k: v for k, v in procs[p].items() if k != "ct"} | {"inicio": procs[p]["ct"]} for p in pids if p in procs],
                key=lambda x: x["mem"], reverse=True)
            g["conexoes"] = [self._conn_fmt(c, procs) for c in self.conns if c["pid"] in pids]
            vivos = {ip: v for (pg, ip), v in self.vivos.items() if pg == nome}
        g.pop("pids", None)
        g["ips_vivos"] = self._ips_fmt(vivos)
        g["ips_7d"] = banco.remotos_programa(nome)
        for r in g["ips_7d"]:
            r["host"] = r["host"] or self.dns.nome(r["ip"])
            r["tipo"] = rede.tipo_ip(r["ip"])
        g["hist"] = banco.historico(24, nome)
        return g

    def rede_estado(self):
        with self.lock:
            procs = self.procs
            conns = [self._conn_fmt(c, procs) for c in self.conns]
            progs = [{k: g[k] for k in ("nome", "env", "rec", "nconn", "nips")}
                     for g in (self.estado or {}).get("programas", []) if not g.get("outros")]
            vivos = list(self.vivos.values())
            e = self.estado or {}
        rem = []
        for v in vivos:
            rem.append({"programa": v["programa"], "ip": v["ip"], "host": self.dns.nome(v["ip"]),
                        "tipo": rede.tipo_ip(v["ip"]), "env_tot": v["env_tot"], "rec_tot": v["rec_tot"],
                        "env": v["env"], "rec": v["rec"], "ult": v["ult"], "primeiro": v["primeiro"]})
        rem.sort(key=lambda r: r["env_tot"] + r["rec_tot"], reverse=True)
        try:
            ifs = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            io = psutil.net_io_counters(pernic=True)
            interfaces = []
            for nome, st in ifs.items():
                if not st.isup:
                    continue
                ipv4 = [a.address for a in addrs.get(nome, []) if a.family == 2]
                i = io.get(nome)
                interfaces.append({"nome": nome, "velocidade": st.speed, "ips": ipv4,
                                   "env_tot": i.bytes_sent if i else 0, "rec_tot": i.bytes_recv if i else 0})
        except Exception:
            interfaces = []
        progs.sort(key=lambda g: (g["env"] or 0) + (g["rec"] or 0), reverse=True)
        return {"ativo": self.rede.ok, "erro": self.rede.erro, "env": e.get("env"), "rec": e.get("rec"),
                "programas": progs, "conexoes": conns, "remotos": rem[:400], "interfaces": interfaces,
                "desde": self.inicio}

    def hardware(self):
        s = self.sens.estado()
        discos = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
                discos.append({"unidade": p.device, "ponto": p.mountpoint, "fs": p.fstype, "total": u.total,
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
        cpu_nome = next((x["hw"] for x in s["sensores"] if x["tipo_hw"] == "Processador"), None) or platform.processor()
        return {**s, "discos": discos, "bateria": bat, "cpu_nome": cpu_nome, "freq": freq,
                "nucleos": NCPU, "fisicos": psutil.cpu_count(logical=False),
                "ram_total": psutil.virtual_memory().total, "sistema": f"{platform.system()} {platform.release()} ({platform.version()})",
                "maquina": platform.node(), "ligado_desde": psutil.boot_time()}

    def status(self):
        return {"versao": config.VERSAO, "admin": eh_admin(), "rede_ok": self.rede.ok, "rede_erro": self.rede.erro,
                "eventos_rede": self.rede.eventos, "sensores": self.sens.fontes, "avisos": self.sens.avisos[-5:],
                "pawnio": self.sens.pawnio, "inicio": self.inicio, "tempo_amostra": self.ultima_amostra_s,
                "banco": banco.info(), "erros": list(self.erros)[-3:], "pid": os.getpid()}
