"""Motor de coleta do CNT Monitor.

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
from . import alertas, jogo, energia, servicos, atualizacao, sessao, regras, limpeza, vps, captura, ventoinhas, remoto, encontro
from . import rede_programa
from . import qualidade

WIN = os.name == "nt"
NCPU = psutil.cpu_count() or 1
# personalizar: ações feitas pelo ícone (na sessão do usuário) e os campos que passam para ele
ACOES_PERS = {"pers_estado", "pers_sistema", "pers_icone", "pers_explorer", "pers_tema", "pers_papel_proximo",
              "pers_perfil", "pers_lancador", "pers_escolher", "pers_ren_previa", "pers_ren_aplicar", "pers_ren_desfazer"}
CAMPOS_PERS = ("ident", "valor", "modo", "perfil", "icone", "visivel", "pasta", "regras", "para")
EU = "CNT Monitor"

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
        self.vent = ventoinhas.Ventoinhas(self.sens, lambda: configuracao.obter("ventoinhas"),
                                          lambda v: configuracao.salvar({"ventoinhas": v}))
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
        self.captura_estado = {}      # atalhos registrados, gravação em andamento (informado pela bandeja)
        self.captura_cache = {}       # id -> (bytes, tipo): imagens que a bandeja leu para o editor (últimas 3)
        self._ini_cache = {}
        self.frente_pid = None
        self.alertas = alertas.Alertas()
        self._ult_alerta = 0
        self._particoes = []
        self._ult_part = 0
        self.atualizador = atualizacao.Atualizador()
        self._ult_atualizacao = 0
        self._atu_falha = (None, 0)       # (versão, quando) da última instalação automática que falhou
        self.jogo = jogo.ModoJogo(self)
        self.regras = regras.MotorRegras()
        self.tarefas = limpeza.Tarefas()
        vps.vincular(configuracao.obter("vps"))
        self.ajuda = remoto.Gerente(opcoes=lambda: {"ler_area": configuracao.obter("remoto_permitir_ler_area")})
        # MONITORPC_ENCONTRO troca o servidor do ID (testes e demonstração); "0" desliga
        self._url_encontro = os.environ.get("MONITORPC_ENCONTRO") or encontro.SERVIDOR
        self.encontro = encontro.Encontro(self.ajuda, self._url_encontro)
        self.vps = vps.Gerenciador()
        self.vazamentos = []
        self._ult_vaz = 0
        self._vaz_avisados = {}
        self._ult_vps_limpa = 0
        self.navegar = None
        self.servidores = None
        self.trafego = rede.TrafegoIP()
        self.fluxos_vivos = rede.FluxosVivos()
        self.geo = qualidade.GeoIP()
        self.sondas = qualidade.Sondas(carga_fn=self._carga)
        self.velocidade = qualidade.Velocidade()
        self.teste = qualidade.TesteDestino()
        self.rprog = rede_programa.Monitor(
            self._grupo_pids, lambda: [(g["nome"], g["env"] or 0, g["rec"] or 0) for g in (self.estado or {}).get("programas", [])
                                       if not g.get("outros")],
            lambda: list(self.conns), maquina=self.info_maquina)
        self._cont = deque(maxlen=40)       # leituras dos contadores TCP/UDP/placas para as taxas recentes
        self._cache_rede = {}

    def _carga(self):
        e = self.estado or {}
        return (e.get("env") or 0) + (e.get("rec") or 0)

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
        if self._url_encontro != "0":
            self.encontro.iniciar()
        try:
            configuracao.aplicar_inicializacao()
        except Exception:
            pass
        for alvo, nome in ((self._loop_rapido, "coleta"), (self._loop_lento, "lento"), (self._loop_sensores, "sensores")):
            threading.Thread(target=alvo, name=nome, daemon=True).start()
        self.sondas.iniciar()
        if os.path.exists(atualizacao.FLAG_REABRIR):
            threading.Timer(12, self._reabrir_icone).start()

    def _reabrir_icone(self):
        """Depois de uma atualização automática, reabre o ícone na tela do usuário."""
        try:
            os.remove(atualizacao.FLAG_REABRIR)
        except OSError:
            pass
        self._abrir_icone()

    def _abrir_icone(self):
        exe = os.path.join(config.pasta_programa(), config.EXE_ICONE)
        if os.path.exists(exe) and time.time() - self.bandeja_ts > 30:
            sessao.abrir_na_tela_do_usuario(f'"{exe}" --bandeja')

    def parar(self):
        self.parar_ev.set()
        self.encontro.parar()
        self.ajuda.parar()
        self.vent.liberar()
        self.rede.parar()
        self.jogo.fps.parar()
        self.sondas.parar()
        self.velocidade.cancelar()
        self.teste.cancelar()
        self.rprog.parar()          # o monitoramento por programa em andamento fica salvo

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
                self.jogo.ajustar_fps(c)
                self._extras_lentos(c)
                if c["atualizacao_auto"] and time.time() - self._ult_atualizacao > atualizacao.VERIFICAR_CADA and time.time() - self.inicio > 120:
                    self._ult_atualizacao = time.time()
                    self._atualizar_sozinho(time.time())
            except Exception:
                self.erros.append(traceback.format_exc())
            self.parar_ev.wait(2)

    def _atualizar_sozinho(self, agora):
        info = self.atualizador.verificar()
        # instalar reinicia o serviço: não no meio de um jogo nem de um acesso remoto (derrubaria a sessão)
        if not info or self.jogo.ativo or self.ajuda.estado().get("estado") not in ("parada", "encerrada"):
            return
        versao, quando = self._atu_falha
        if versao == info["versao"] and agora - quando < atualizacao.REPETIR_FALHA:
            return      # já falhou há pouco: não baixa o instalador inteiro de novo a cada procura
        ok, _ = self.atualizador.instalar()
        self._atu_falha = (None, 0) if ok else (info["versao"], agora)

    def _notificar(self, titulo, msg, gravar=True, regra="sistema"):
        if gravar:
            banco.gravar_alerta(time.time(), regra, titulo, msg)
        if time.time() - self.bandeja_ts < 40:
            self.fila.put({"tipo": "notificar", "titulo": titulo, "msg": msg, "id": uuid.uuid4().hex[:12]})

    def _extras_lentos(self, c):
        """Regras por programa, vazamento de memória e VPS (a cada 2 s)."""
        t = time.time()
        if c["regras_prog"]:
            with self.lock:
                e, procs = self.estado, dict(self.procs)
            try:
                for titulo, msg in self.regras.aplicar(c["regras_prog"], e, procs):
                    self._notificar(titulo, msg, regra="regra")
            except Exception:
                self.erros.append(traceback.format_exc())
        if c["vazamento_ativo"] and t - self._ult_vaz > 300 and t - self.inicio > 60:
            self._ult_vaz = t
            try:
                self.vazamentos = banco.vazamentos()
                for v in self.vazamentos:
                    if t - self._vaz_avisados.get(v["nome"], 0) > 6 * 3600:
                        self._vaz_avisados[v["nome"]] = t
                        self._notificar(f'Possível vazamento de memória: {v["nome"]}',
                                        f'A memória cresce sem parar (+{v["taxa_mb_h"]} MB por hora; agora {v["atual_mb"]} MB). '
                                        f'Reiniciar o programa costuma resolver.', regra="vazamento")
            except Exception:
                self.erros.append(traceback.format_exc())
        elif not c["vazamento_ativo"]:
            self.vazamentos = []
        try:
            self.vps.sincronizar(c["vps"])
            while self.vps.eventos:
                titulo, msg = self.vps.eventos.pop(0)
                self._notificar(titulo, msg, regra="vps")
            if self.vps.para_gravar:
                linhas, self.vps.para_gravar = self.vps.para_gravar, []
                banco.vps_gravar(linhas)
            if self.vps._fp_pendentes:
                fps, self.vps._fp_pendentes = self.vps._fp_pendentes, {}
                lista = [dict(v, fingerprint=fps.get(v["id"], v["fingerprint"])) for v in configuracao.obter("vps")]
                configuracao.salvar({"vps": lista})
            if t - self._ult_vps_limpa > 3600:
                self._ult_vps_limpa = t
                banco.vps_limpar(c["retencao_dias"])
        except Exception:
            self.erros.append(traceback.format_exc())

    def config_mudou(self):
        if self.servidores:
            try:
                self.servidores.aplicar()
            except Exception:
                self.erros.append(traceback.format_exc())
        try:
            self.vps.sincronizar(configuracao.obter("vps"))
        except Exception:
            pass
        if self.ajuda.estado().get("estado") == "ativa":     # o teto da área de transferência vale na hora
            self.ajuda.ordem("area_permitir", valor=configuracao.obter("remoto_permitir_ler_area"))

    def _acao_ajuda(self, tipo, a):
        """Acesso remoto (pedir ajuda / ajudar alguém). O servidor só deixa chegar aqui o que vem do próprio PC."""
        def estado(ok=True, msg=""):
            return {"ok": ok, "msg": msg, "dados": self.ajuda.estado()}
        try:
            if tipo == "ajuda_estado":
                r = estado()
                r["dados"]["meu"] = self.encontro.estado()
                return r
            if tipo == "ajuda_trocar_id":
                self.encontro.trocar_id()
                return {"ok": True, "msg": "", "dados": {"meu": self.encontro.estado()}}
            if tipo == "ajuda_trocar_senha":
                self.encontro.trocar_senha()
                return {"ok": True, "msg": "", "dados": {"meu": self.encontro.estado()}}
            if tipo == "ajuda_pedir":
                return estado(*self.ajuda.pedir())
            if tipo == "ajuda_resposta":
                return estado(*self.ajuda.ordem("resposta", codigo=str(a.get("codigo") or "")))
            if tipo == "ajuda_aceitar":
                r = self.ajuda.ordem("aceitar")
                if r[0]:
                    self._abrir_icone()      # o aviso e o "Encerrar conexão do técnico" são do ícone: garante que está aberto
                return estado(*r)
            if tipo == "ajuda_arquivos":
                return {"ok": True, "msg": "", "dados": {"itens": remoto.historico()}}
            if tipo == "ajuda_encerrar":
                self.ajuda.parar()
                return estado()
            # lado do técnico: nada fica guardado, cada chamada traz o que precisa
            if tipo == "ajuda_tec_convite":
                return {"ok": True, "msg": "", "dados": {"sdp": remoto.ler_convite(a.get("codigo"))}}
            senha, oferta, resp = str(a.get("senha") or ""), str(a.get("oferta") or ""), str(a.get("resposta") or "")
            if tipo == "ajuda_tec_resposta":
                return {"ok": True, "msg": "", "dados": remoto.resposta_do_tecnico(senha, oferta, resp, remoto.enderecos_locais())}
            if tipo == "ajuda_tec_conferir":
                return {"ok": remoto.conferir_atendido(senha, oferta, resp, a.get("mac")), "msg": ""}
        except remoto.Erro as e:
            return {"ok": False, "msg": str(e)}
        return {"ok": False, "msg": "Ação desconhecida."}

    def remoto_rodando(self):
        s = self.servidores
        return {"ligado": bool(s and s.remoto), "erro": s.erro_remoto if s else None}

    def n_nucleos(self):
        return NCPU

    def info_maquina(self):
        import platform
        h = {}
        try:
            h = self.hardware()
        except Exception:
            pass
        return {"maquina": platform.node(), "sistema": h.get("sistema") or platform.platform(), "cpu": h.get("cpu_nome"),
                "nucleos": NCPU, "ram_total": psutil.virtual_memory().total, "gpus": [g.get("nome") for g in h.get("gpus", [])],
                "discos": [d.get("nome") for d in h.get("discos", [])], "versao": config.VERSAO}

    def _loop_sensores(self):
        while not self.parar_ev.is_set():
            c = configuracao.obter()
            try:
                if c["sensores"]:
                    if not self.sens.fontes and not self.sens.avisos:
                        self.sens.iniciar()
                    self.sens.ler()
                    self.vent.ciclo()
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
            return "Sistema" if pid in (0, 4) else "CNT Monitor"
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
            return "CNT Monitor"
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
        trafego = self.rede.coletar() if self.rede.ok else []
        if trafego:
            self.trafego.add(t, self.rede.detalhe)
            self.fluxos_vivos.add(t, self.rede.fluxos)
            self.rprog.add_fluxos(t, self.rede.fluxos)
        for pid, ip, env, rec in trafego:
            self._net_ev[pid].append((t, env, rec))
        net_pid = {}
        for pid in list(self._net_ev):
            dq = self._net_ev[pid]
            while dq and dq[0][0] <= t - J:
                dq.popleft()
            if not dq:
                del self._net_ev[pid]
                continue
            net_pid[pid] = (sum(x[1] for x in dq) / J, sum(x[2] for x in dq) / J)

        fps = self.jogo.fps.por_pid() if self.jogo.fps.rodando else {}
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
                "fps": fps[pid]["fps"] if pid in fps else None,
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
        # tráfego por IP: atribuído depois de montar a árvore, assim processos novos já têm programa
        for pid, ip, env, rec in trafego:
            self._trafego_ip(t, pid, ip, env, rec)
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
        try:
            self.jogo.tick(c)
            if t - self._ult_alerta >= 1:
                self._ult_alerta = t
                self._avaliar_alertas(c, estado)
        except Exception:
            self.erros.append(traceback.format_exc())
        return estado

    def _avaliar_alertas(self, c, e):
        if not c["alertas_ativos"]:
            return
        if time.time() - self._ult_part > 60:
            self._ult_part = time.time()
            parts = []
            for p in psutil.disk_partitions(all=False):
                try:
                    parts.append({"unidade": p.device, "pct": psutil.disk_usage(p.mountpoint).percent})
                except Exception:
                    continue
            self._particoes = parts
        for regra, titulo, msg in self.alertas.avaliar(c["alertas"], e, self._particoes):
            banco.gravar_alerta(time.time(), regra, titulo.replace("CNT Monitor — ", ""), msg)
            if time.time() - self.bandeja_ts < 40:
                self.fila.put({"tipo": "notificar", "titulo": titulo, "msg": msg, "id": uuid.uuid4().hex[:12]})

    def _grupo_pids(self, nome):
        g = next((x for x in (self.estado or {}).get("programas", []) if x["nome"] == nome), None)
        return list(g["pids"]) if g else []

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
        d["ips"] = self._com_latencia(self._ips_fmt(ips, t))
        d["lista_janelas"] = janelas
        return d

    def _com_latencia(self, ips, n=10):
        """Quem olha os IPs de um programa/processo vê a latência: pinga os n com mais tráfego (da internet)."""
        alvo = [r["ip"] for r in ips if r["tipo"] == "internet"][:n]
        self.sondas.vigiar(alvo)
        pt = self.trafego.por_ip()
        for r in ips:
            q = self.sondas.resumo_ip(r["ip"], 30)
            r["lat"] = {k: q[k] for k in ("mediana", "jitter", "perda")} if q else None
            r["geo"] = self.geo.info(r["ip"])
            r.update(pt.get(r["ip"], {}))
        return ips

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
        g["ips_vivos"] = self._com_latencia(self._ips_fmt(vivos, t))
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
        pt = self.trafego.por_ip()
        for r in rem[:500]:
            r["geo"] = self.geo.info(r["ip"])
            r.update(pt.get(r["ip"], {}))
        return {"protocolos": self.trafego.protocolos(), "geoip": self.geo.disponivel, "ativo": self.rede.ok, "erro": self.rede.erro, "env": e.get("env"), "rec": e.get("rec"),
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
        return {"versao": config.VERSAO, "sistema": "windows" if WIN else "linux", "admin": eh_admin(), "modo_coleta": self.leitor.modo,
                "tempo_amostra_ms": round(sum(tm) / len(tm) * 1000, 1) if tm else None,
                "tempo_amostra_max_ms": round(max(tm) * 1000, 1) if tm else None,
                "rede_ok": self.rede.ok, "rede_erro": self.rede.erro, "eventos_rede": self.rede.eventos,
                "etw": self.rede.estatisticas(), "sensores": self.sens.fontes, "avisos": self.sens.avisos[-5:],
                "pawnio": self.sens.pawnio, "gpu_ok": self.gpu.ok, "gpu_erro": self.gpu.erro,
                "gpus": [a["nome"] for a in self.gpu.adaptadores], "bandeja_online": time.time() - self.bandeja_ts < 40,
                "inicio": self.inicio, "banco": banco.info(), "jogo_ativo": self.jogo.ativo, "erros": list(self.erros)[-3:], "pid": os.getpid()}

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
            return {"ok": False, "msg": "O ícone do CNT Monitor (perto do relógio) precisa estar aberto para isso. "
                                        "Abra o CNT Monitor pelo menu Iniciar e tente de novo."}
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

        if isinstance(tipo, str) and tipo.startswith("rede_"):
            return self._acao_rede(tipo, a)
        return self._acao_extra(tipo, a, c)

    # ================================================================ análise de rede
    def ip_detalhe(self, ip):
        """Tudo sobre um IP remoto: tráfego (TCP×UDP, pacotes, serviços), programas, conexões com RTT TCP, ping e país."""
        import ipaddress
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            return None
        t = time.time()
        self.sondas.vigiar([ip])
        with self.lock:
            procs = self.procs
            conns = [self._conn_fmt(c, procs) for c in self.conns if c["rip"] == ip]
            progs = [{"programa": pg, "env_tot": v["env_tot"], "rec_tot": v["rec_tot"], "primeiro": v["primeiro"], "ult": v["ult"],
                      "env": round(self._taxa_ev(v, t)[0]), "rec": round(self._taxa_ev(v, t)[1])}
                     for (pg, i), v in self.vivos.items() if i == ip]
        for c in conns:
            if c["proto"] == "TCP" and c["estado"] == "Conectado":
                c["tcp"] = qualidade.tcp_estats(c["lip"], c["lport"], c["rip"], c["rport"])
        p5 = self.sondas.resumo_ip(ip, 300)
        return {"ip": ip, "host": self.dns.nome(ip), "tipo": rede.tipo_ip(ip), "geo": self.geo.info(ip),
                "trafego": self.trafego.resumo(ip, t), "programas": progs, "conexoes": conns,
                "ping": self.sondas.resumo_ip(ip, 60), "ping_5min": p5,
                "pontos": qualidade.pontuacoes(p5) if p5 and p5["perda"] < 100 else None}

    def analise_rede(self):
        """Aba Análise: captura em andamento, capturas salvas, sistema (TCP/UDP/IP/placas), Wi-Fi e teste de velocidade."""
        t = time.time()
        self._cont.append(qualidade.contadores())
        base = next((c for c in self._cont if t - c["t"] <= 60), self._cont[0])
        if base is self._cont[-1] and len(self._cont) > 1:
            base = self._cont[-2]
        cache = self._cache_rede
        if t - cache.get("t", 0) > 5:
            cache.update(t=t, wifi=qualidade.wifi())
        if t - cache.get("t_gw", 0) > 300:
            cache.update(t_gw=t, gateway=qualidade.gateway(), dns=qualidade.servidores_dns())
        cap = self.sondas.abrir("atual") if self.sondas.captura else None
        return {"captura": cap, "salvas": self.sondas.lista(), "sistema": qualidade.diferenca(base, self._cont[-1]),
                "wifi": cache["wifi"], "gateway": cache.get("gateway"), "dns": cache.get("dns", []),
                "velocidade": self.velocidade.estado, "geoip": self.geo.disponivel,
                "top_ips": [{"ip": ip, "host": self._host(ip)} for ip in self._top_ips(8)],
                "teste": self.teste.ver(), "programas": self.rprog.ao_vivo(), "programas_salvos": self.rprog.lista()}

    def conexoes_qualidade(self):
        """Conexões em andamento (tráfego nos últimos 10 s) com a qualidade de cada uma: taxa, pacotes/s, RTT e
        retransmissões do TCP (do Windows), jitter de chegada do UDP, ping ao destino e uma nota (MOS estimado)."""
        t = time.time()
        fl = sorted(self.fluxos_vivos.lista(t), key=lambda x: -x["bytes_10s"])[:60]
        with self.lock:
            procs, conns = self.procs, list(self.conns)
        ips = list(dict.fromkeys(x["ip"] for x in fl if rede.tipo_ip(x["ip"]) in ("internet", "rede local")))
        self.sondas.vigiar(ips[:qualidade.Sondas.MAX_VIGIA])
        tcp = {(c["lport"], c["rip"], c["rport"]): c for c in conns if c["proto"].startswith("TCP") and c["estado"] == "Conectado"}
        out, estats = [], 0
        for x in fl:
            udp = x["udp"]
            p = self.sondas.resumo_ip(x["ip"], 30)
            r = {**x, "proto": "UDP" if udp else "TCP", "programa": self._pid_prog.get(x["pid"], (0, None))[1] or "—",
                 "processo": (procs.get(x["pid"]) or {}).get("nome") or "", "host": self._host(x["ip"]),
                 "servico": rede.servico(x["rport"], udp) or rede.servico(x["lport"], udp) or "",
                 "ping": p["mediana"] if p else None, "ping_jitter": p["jitter"] if p else None,
                 "perda": p["perda"] if p else None, "rtt_tcp": None, "retransmissoes": None}
            c = tcp.get((x["lport"], x["ip"], x["rport"]))
            if c and estats < 20:
                estats += 1
                e = qualidade.tcp_estats(c["lip"], c["lport"], c["rip"], c["rport"])
                if e:
                    r["rtt_tcp"], r["retransmissoes"] = e.get("rtt"), e.get("retransmissoes")
            lat = r["ping"] if r["ping"] is not None else r["rtt_tcp"]
            r["mos"] = qualidade.mos(lat, x["jitter"] if x["jitter"] is not None else r["ping_jitter"], r["perda"] or 0)[0]
            out.append(r)
        return out

    def _top_ips(self, n):
        t = time.time()
        with self.lock:
            soma = defaultdict(float)
            for (pg, ip), v in self.vivos.items():
                if t - v["ult"] < 120 and rede.tipo_ip(ip) == "internet":
                    e, r = self._taxa_ev(v, t)
                    soma[ip] += v["env_tot"] + v["rec_tot"] + 100 * (e + r)
        return sorted(soma, key=lambda k: -soma[k])[:n]

    def _acao_rede(self, tipo, a):
        try:
            if tipo == "rede_traceroute":
                ip = qualidade.alvo_valido(a.get("ip"))
                r = qualidade.traceroute(ip)
                for s in r["saltos"]:
                    s["host"] = self.dns.nome(s["ip"]) if s["ip"] else None
                    s["geo"] = self.geo.info(s["ip"]) if s["ip"] else None
                return {"ok": True, "msg": f'{len(r["saltos"])} saltos' + ("" if r["chegou"] else " (o destino não respondeu)"), "dados": r}
            if tipo == "rede_mtu":
                r = qualidade.path_mtu(qualidade.alvo_valido(a.get("ip")))
                return {"ok": r["mtu"] is not None, "msg": r["msg"] or f'Path MTU: {r["mtu"]} bytes', "dados": r}
            if tipo == "rede_dns":
                return {"ok": True, "msg": "Teste de DNS concluído.", "dados": qualidade.teste_dns()}
            if tipo == "rede_http":
                return {"ok": True, "msg": "Teste HTTP concluído.", "dados": qualidade.medir_http(str(a.get("url") or ""))}
            if tipo == "rede_velocidade":
                ok = self.velocidade.iniciar(min(30, max(5, int(a.get("segundos") or 10))))
                return {"ok": ok, "msg": "Teste de velocidade começou." if ok else "Já tem um teste rodando."}
            if tipo == "rede_velocidade_cancelar":
                self.velocidade.cancelar()
                return {"ok": True, "msg": "Teste cancelado."}
            if tipo == "rede_analise_iniciar":
                bruto = a.get("alvos") if isinstance(a.get("alvos"), list) else []
                if not 1 <= len(bruto) <= 10:
                    return {"ok": False, "msg": "Escolha de 1 a 10 destinos."}
                alvos = [qualidade.alvo_valido(x) for x in bruto]
                ok, msg = self.sondas.iniciar_captura(alvos, [str(x)[:80] for x in bruto], max(0, min(1440, int(a.get("minutos") or 0))))
                return {"ok": ok, "msg": msg}
            if tipo == "rede_analise_parar":
                ident = self.sondas.parar_captura()
                return {"ok": bool(ident), "msg": "Análise salva." if ident else "Nenhuma análise gravando.", "dados": ident}
            if tipo == "rede_analise_apagar":
                ok = self.sondas.apagar(str(a.get("id") or ""))
                return {"ok": ok, "msg": "Análise apagada." if ok else "Análise não encontrada."}
            if tipo == "rede_teste":
                ok, msg = self.teste.iniciar(a.get("destino"), a.get("protocolo"), a.get("porta"), a.get("segundos"))
                return {"ok": ok, "msg": msg}
            if tipo == "rede_teste_cancelar":
                self.teste.cancelar()
                return {"ok": True, "msg": "Teste cancelado."}
            if tipo == "rede_prog_iniciar":
                ok, msg = self.rprog.adicionar(a.get("programa"))
                return {"ok": ok, "msg": msg}
            if tipo == "rede_prog_remover":
                ok, msg = self.rprog.remover(str(a.get("programa") or ""))
                return {"ok": ok, "msg": msg}
            if tipo == "rede_prog_parar":
                ident = self.rprog.parar()
                return {"ok": bool(ident), "msg": "Monitoramento salvo. Use Exportar .log para levar os dados." if ident
                        else "Nenhum programa sendo monitorado.", "dados": ident}
            if tipo == "rede_prog_apagar":
                ok = self.rprog.apagar(str(a.get("id") or ""))
                return {"ok": ok, "msg": "Monitoramento apagado." if ok else "Monitoramento não encontrado."}
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        except OSError as e:
            return {"ok": False, "msg": f"Falhou: {e}"}
        return {"ok": False, "msg": "Ação desconhecida."}

    def _acao_extra(self, tipo, a, c):
        def res(ok_msg):
            ok, msg = ok_msg
            return {"ok": ok, "msg": msg if isinstance(msg, str) else "Feito.", "dados": None if isinstance(msg, str) else msg}
        if tipo == "jogo_ligar":
            return self.jogo.ativar(c)
        if tipo == "jogo_desligar":
            return self.jogo.desativar(reabrir=c["jogo_reabrir"])
        if tipo == "limpar_cache":
            ok, d = jogo.limpar_cache_memoria()
            if not ok:
                return {"ok": False, "msg": d}
            lib = d["liberado"]
            return {"ok": True, "msg": f"Cache limpo: {lib / 2**20:.0f} MB voltaram a ficar livres.", "dados": d}
        if tipo == "energia_plano":
            return res(energia.ativar_plano(a.get("guid", "")))
        if tipo == "energia_criar":
            base = energia.DESEMPENHO_MAXIMO if a.get("base") == "maximo" else energia.ALTO_DESEMPENHO
            g, txt = energia.criar_plano(base)
            if g:
                energia.ativar_plano(g)
                return {"ok": True, "msg": "Plano criado e ativado."}
            return {"ok": False, "msg": txt or "O Windows não deixou criar esse plano."}
        if tipo == "energia_ajuste":
            return res(energia.mudar_ajuste(a.get("chave"), a.get("onde"), a.get("valor", 0)))
        if tipo == "energia_modo":
            return res(energia.mudar_modo_energia(a.get("guid", "")))
        if tipo == "windows_ajuste":
            return res(energia.mudar_windows(a.get("chave"), bool(a.get("ativo"))))
        if tipo == "brilho":
            return res(energia.mudar_brilho(a.get("nivel", 50)))
        if tipo == "servico":
            return res(servicos.acao(a.get("nome"), a.get("oque"), a.get("inicio")))
        if tipo == "atualizar_verificar":
            d = self.atualizador.verificar()
            if self.atualizador.erro:
                return {"ok": False, "msg": self.atualizador.erro}
            return {"ok": True, "msg": f'Versão {d["versao"]} disponível!' if d else "Você já está na versão mais nova."}
        if tipo == "atualizar_instalar":
            return res(self.atualizador.instalar_ao_fundo())
        if tipo == "limpeza":
            ids = [str(x) for x in (a.get("ids") or [])]
            if not ids:
                return {"ok": False, "msg": "Marque pelo menos um item."}
            r = limpeza.limpar(ids)
            tot = sum(x["bytes"] for x in r)
            return {"ok": True, "msg": f"Limpeza concluída: {tot / 2**20:,.0f} MB liberados.".replace(",", "."), "dados": r}
        if tipo == "navegadores":
            ids = [str(x) for x in (a.get("ids") or [])]
            r = [x for x in limpeza.navegadores(ids) if x["id"] in ids]
            abertos = [x["nome"] for x in r if x["aberto"]]
            tot = sum(x["bytes"] for x in r if x["limpo"])
            msg = f"Cache limpo: {tot / 2**20:,.0f} MB.".replace(",", ".")
            if abertos:
                msg += f' Não limpei {", ".join(abertos)} porque está aberto — feche e tente de novo.'
            return {"ok": tot > 0 or not abertos, "msg": msg, "dados": r}
        if tipo == "tarefa":
            return res(self.tarefas.rodar(a.get("qual", "")))
        if tipo == "vps_salvar":
            item = vps.validar([a.get("vps") or {}])
            if not item:
                return {"ok": False, "msg": "Preencha pelo menos o endereço do servidor."}
            item = item[0]
            lista = configuracao.obter("vps")
            antigo = next((v for v in lista if v["id"] == item["id"]), None)
            if antigo and antigo["host"] == item["host"] and antigo["porta"] == item["porta"]:
                item["fingerprint"] = antigo["fingerprint"]
            else:
                item["fingerprint"] = ""
            if vps.falta_segredo(item, a.get("segredo")):
                return {"ok": False, "msg": vps.MSG_SEGREDO}
            vps.guardar_segredo(item, a.get("segredo"))
            lista = [v for v in lista if v["id"] != item["id"]] + [item] if not antigo else [item if v["id"] == item["id"] else v for v in lista]
            configuracao.salvar({"vps": lista})
            self.vps.sincronizar(configuracao.obter("vps"))
            return {"ok": True, "msg": f'Servidor "{item["nome"]}" salvo.', "dados": {"id": item["id"]}}
        if tipo == "vps_remover":
            vid = a.get("id")
            configuracao.salvar({"vps": [v for v in configuracao.obter("vps") if v["id"] != vid]})
            vps.apagar_segredo(vid)
            self.vps.sincronizar(configuracao.obter("vps"))
            return {"ok": True, "msg": "Servidor removido."}
        if tipo == "vps_testar":
            item = vps.validar([a.get("vps") or {}])
            if not item:
                return {"ok": False, "msg": "Preencha o endereço do servidor."}
            item = item[0]
            antigo = next((v for v in configuracao.obter("vps") if v["id"] == item["id"]), None)
            mesmo = antigo and (antigo["host"], antigo["porta"]) == (item["host"], item["porta"])
            if vps.falta_segredo(item, a.get("segredo")):
                return {"ok": False, "msg": vps.MSG_SEGREDO}
            esperado = antigo["fingerprint"] if mesmo else ""
            return res(self.vps.testar(item, a.get("segredo"), esperado))
        if isinstance(tipo, str) and tipo.startswith("ajuda_"):
            return self._acao_ajuda(tipo, a)
        if tipo == "vps_esquecer":
            vid = a.get("id")
            configuracao.salvar({"vps": [dict(v, fingerprint="") if v["id"] == vid else v for v in configuracao.obter("vps")]})
            self.vps.sincronizar(configuracao.obter("vps"))
            return {"ok": True, "msg": "Identidade esquecida. A próxima conexão vai gravar a nova."}
        if tipo == "regra_salvar":
            item = regras.validar([a.get("regra") or {}])
            if not item:
                return {"ok": False, "msg": "Escolha o programa da regra."}
            item = item[0]
            lista = configuracao.obter("regras_prog")
            if any(r["id"] == item["id"] for r in lista):
                lista = [item if r["id"] == item["id"] else r for r in lista]
            else:
                lista = lista + [item]
            configuracao.salvar({"regras_prog": lista})
            return {"ok": True, "msg": f'Regra para "{item["programa"]}" salva: {regras.descrever(item)}.'}
        if tipo == "regra_remover":
            configuracao.salvar({"regras_prog": [r for r in configuracao.obter("regras_prog") if r["id"] != a.get("id")]})
            return {"ok": True, "msg": "Regra apagada."}
        if tipo == "captura":
            modo = a.get("modo")
            if modo not in captura.ACOES and modo not in ("parar", "ffmpeg") and not str(modo).startswith("fluxo:"):
                return {"ok": False, "msg": "Tipo de captura desconhecido."}
            return self._enfileirar({"tipo": "captura", "modo": modo})
        if tipo in ACOES_PERS:   # personalizar: registro, papel de parede e arquivos do usuário são do ícone
            return self._enfileirar({"tipo": tipo, **{k: a[k] for k in CAMPOS_PERS if k in a}})
        if tipo in ("captura_escolher_pasta", "captura_abrir_pasta"):   # quem abre o diálogo/Explorer é o ícone
            return self._enfileirar({"tipo": tipo})
        if tipo == "captura_tarefa":   # ferramentas sem arquivo: rodam no ícone, na sessão do usuário
            if a.get("nome") not in captura.TAREFAS_ICONE:
                return {"ok": False, "msg": "Tarefa desconhecida."}
            return self._enfileirar({"tipo": tipo, "nome": a["nome"]})
        if tipo in ("captura_favorito", "captura_tags"):
            try:
                d = banco.captura(int(a.get("id") or 0))
            except (TypeError, ValueError):
                d = None
            if not d:
                return {"ok": False, "msg": "Essa captura não está mais no histórico."}
            if tipo == "captura_favorito":
                banco.captura_atualizar(d["id"], {"favorito": int(bool(a.get("valor")))})
                return {"ok": True, "msg": "Marcada como favorita." if a.get("valor") else "Tirada das favoritas."}
            tags = ", ".join(t.strip() for t in str(a.get("valor") or "").split(",") if t.strip())[:200]
            banco.captura_atualizar(d["id"], {"tags": tags})
            return {"ok": True, "msg": "Etiquetas salvas." if tags else "Etiquetas apagadas."}
        if tipo in ("captura_copiar", "captura_enviar", "captura_ler", "captura_apagar", "captura_pasta", "captura_abrir",
                    "captura_ferramenta", "captura_renomear"):
            try:
                d = banco.captura(int(a.get("id") or 0))
            except (TypeError, ValueError):
                d = None
            if not d:
                return {"ok": False, "msg": "Essa captura não está mais no histórico."}
            if tipo == "captura_apagar":
                banco.captura_apagar(d["id"])
                self.captura_cache.pop(d["id"], None)
                if not a.get("arquivo"):
                    return {"ok": True, "msg": "Tirada do histórico (o arquivo continua na pasta)."}
            if tipo == "captura_pasta":
                return self._enfileirar({"tipo": "pasta", "caminho": d["caminho"]})
            if tipo == "captura_abrir":
                return self._enfileirar({"tipo": "abrir_exe", "caminho": d["caminho"]})
            if tipo == "captura_ferramenta":
                if a.get("nome") not in captura.FERRAMENTAS_ARQUIVO:
                    return {"ok": False, "msg": "Ferramenta desconhecida."}
                return self._enfileirar({"tipo": tipo, "nome": a["nome"], "cid": d["id"], "caminho": d["caminho"]})
            if tipo == "captura_renomear":
                return self._enfileirar({"tipo": tipo, "cid": d["id"], "caminho": d["caminho"],
                                         "novo": str(a.get("novo") or "")[:150]})
            return self._enfileirar({"tipo": tipo, "cid": d["id"], "caminho": d["caminho"]})  # "id" é o da fila
        if tipo == "vent_canal":
            return res(self.vent.definir(str(a.get("canal") or ""), a.get("dados") if isinstance(a.get("dados"), dict) else {}))
        if tipo == "vent_global":
            return res(self.vent.definir_global(a.get("dados") if isinstance(a.get("dados"), dict) else {}))
        if tipo == "alerta_teste":
            if time.time() - self.bandeja_ts > 40:
                return {"ok": False, "msg": "O ícone perto do relógio precisa estar aberto para mostrar notificações."}
            self.fila.put({"tipo": "notificar", "titulo": "CNT Monitor — teste", "msg": "Os alertas estão funcionando.",
                           "id": uuid.uuid4().hex[:12]})
            return {"ok": True, "msg": "Notificação de teste enviada."}
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

    def receber_janelas(self, mapa, frente=None):
        self.bandeja_ts = time.time()
        with self.lock:
            self.janelas = {int(k): v for k, v in (mapa or {}).items()}
            self.janelas_ts = time.time()
            self.frente_pid = int(frente) if frente else None
