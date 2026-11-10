"""Monitoramento de servidores (VPS) Linux por SSH — nada precisa ser instalado no servidor.

Lê /proc (CPU, memória, carga, rede), df (discos), ps (processos), systemctl (serviços que
você escolher), docker ps (containers) e testa URLs (sites/APIs) a partir do seu PC.
Senhas e chaves ficam criptografadas com a proteção de dados do Windows (DPAPI).
A identidade do servidor (impressão digital da chave SSH) é guardada na primeira conexão
e conferida nas próximas — se mudar, o Monitor não conecta e avisa.
"""
import io
import os
import re
import json
import time
import uuid
import base64
import hashlib
import threading
import urllib.error
import urllib.request
from collections import deque

from . import config

WIN = os.name == "nt"
RE_UNIDADE = re.compile(r"^[A-Za-z0-9@._:-]{1,80}$")


# ---------------------------------------------------------------- segredos (DPAPI)
def _cifrar(b):
    if WIN:
        import win32crypt
        return win32crypt.CryptProtectData(b, "MonitorPC", None, None, None, 0)
    return base64.b64encode(b)


def _decifrar(b):
    if WIN:
        import win32crypt
        return win32crypt.CryptUnprotectData(b, None, None, None, 0)[1]
    return base64.b64decode(b)


_seg_lock = threading.Lock()


def _ler_segredos():
    try:
        with open(config.SEGREDOS, "rb") as f:
            return json.loads(_decifrar(f.read()).decode("utf-8"))
    except Exception:
        return {}


def _gravar_segredos(d):
    os.makedirs(config.BASE, exist_ok=True)
    with open(config.SEGREDOS + ".tmp", "wb") as f:
        f.write(_cifrar(json.dumps(d).encode("utf-8")))
    os.replace(config.SEGREDOS + ".tmp", config.SEGREDOS)


def segredo(vid):
    with _seg_lock:
        return _ler_segredos().get(vid, {})


def _destino(v):
    return f'{str(v["host"]).strip().lower()}:{int(v["porta"])}'


def segredo_de(v):
    """A senha/chave guardada para o servidor `v` — só se foi salva para este mesmo endereço e porta.
    É por aqui que toda conexão pega a credencial: trocar o endereço (pelo formulário, pelas
    configurações ou por um backup) nunca leva a senha antiga para o servidor novo."""
    s = segredo(v["id"])
    return s if s.get("destino") == _destino(v) else {}


def guardar_segredo(v, dados):
    dados = dados if isinstance(dados, dict) else {}
    with _seg_lock:
        d = _ler_segredos()
        atual = d.get(v["id"], {})
        if atual.get("destino") != _destino(v):
            atual = {}          # o que estava guardado era de outro endereço: não acompanha
        for k in ("senha", "chave", "frase"):
            if dados.get(k):
                atual[k] = str(dados[k])
        if dados.get("limpar_chave"):
            atual.pop("chave", None)
            atual.pop("frase", None)
        atual["destino"] = _destino(v)
        d[v["id"]] = atual
        _gravar_segredos(d)


def vincular(lista):
    """Versões antigas guardavam a senha sem o endereço: prende cada uma ao servidor cadastrado agora.
    Roda uma vez, quando o serviço inicia."""
    with _seg_lock:
        d = _ler_segredos()
        mudou = False
        for v in lista:
            s = d.get(v["id"])
            if s and "destino" not in s:
                s["destino"] = _destino(v)
                mudou = True
        if mudou:
            _gravar_segredos(d)


MSG_SEGREDO = ("O endereço ou a porta mudou: digite a senha (ou a chave) de novo. "
               "Por segurança, a que está guardada só é enviada ao servidor para o qual foi salva.")


def falta_segredo(v, novo):
    """Há uma senha/chave guardada para este cadastro, mas para outro endereço, e não veio digitada
    a credencial do tipo de login escolhido."""
    novo = novo if isinstance(novo, dict) else {}
    return bool(segredo(v["id"]) and not segredo_de(v) and not novo.get("chave" if v["auth"] == "chave" else "senha"))


def apagar_segredo(vid):
    with _seg_lock:
        d = _ler_segredos()
        d.pop(vid, None)
        _gravar_segredos(d)


# ---------------------------------------------------------------- configuração
def validar(lista):
    out = []
    for v in lista or []:
        if not isinstance(v, dict) or not str(v.get("host") or "").strip():
            continue
        try:
            item = {
                "id": str(v.get("id") or uuid.uuid4().hex[:10])[:20],
                "nome": str(v.get("nome") or v["host"]).strip()[:60],
                "host": str(v["host"]).strip()[:120],
                "porta": max(1, min(65535, int(v.get("porta") or 22))),
                "usuario": str(v.get("usuario") or "root").strip()[:60],
                "auth": "chave" if v.get("auth") == "chave" else "senha",
                "intervalo_s": max(10, min(600, int(v.get("intervalo_s") or 30))),
                "servicos": [s for s in (v.get("servicos") or []) if isinstance(s, str) and RE_UNIDADE.match(s)][:30],
                "urls": [u for u in (v.get("urls") or []) if isinstance(u, str) and re.match(r"^https?://", u)][:20],
                "docker": bool(v.get("docker", False)),
                "ativo": bool(v.get("ativo", True)),
                "fingerprint": str(v.get("fingerprint") or "")[:120],
                "alerta_offline": bool(v.get("alerta_offline", True)),
                "alerta_disco": max(0, min(100, int(90 if v.get("alerta_disco") is None else v["alerta_disco"]))),
                "alerta_cpu": max(0, min(100, int(v.get("alerta_cpu") or 0))),
                "alerta_mem": max(0, min(100, int(v.get("alerta_mem") or 0))),
                "alerta_servicos": bool(v.get("alerta_servicos", True)),
                "alerta_urls": bool(v.get("alerta_urls", True)),
            }
        except (TypeError, ValueError):
            continue
        out.append(item)
    return out[:20]


# ---------------------------------------------------------------- coleta
def _script(v):
    s = ("cat /proc/loadavg; echo @@@; head -1 /proc/stat; echo @@@; "
         "grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo; echo @@@; "
         "df -kP -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null; echo @@@; "
         "cat /proc/net/dev; echo @@@; cat /proc/uptime; echo @@@; nproc; echo @@@; "
         "hostname; (. /etc/os-release 2>/dev/null; echo \"$PRETTY_NAME\"); uname -r; echo @@@; "
         "ps -eo pid,comm,%cpu,%mem,rss --sort=-%cpu 2>/dev/null | head -16; echo @@@; ")
    if v["servicos"]:
        s += "for u in " + " ".join(v["servicos"]) + "; do echo \"$u $(systemctl is-active $u 2>/dev/null)\"; done; "
    s += "echo @@@; "
    if v["docker"]:
        s += "docker ps -a --format '{{.Names}}|{{.Status}}|{{.Image}}' 2>/dev/null || sudo -n docker ps -a --format '{{.Names}}|{{.Status}}|{{.Image}}' 2>/dev/null; "
    s += "echo @@@"
    return s


def _parse(txt):
    sec = [x.strip("\n") for x in txt.split("@@@")]
    while len(sec) < 12:
        sec.append("")
    d = {}
    la = sec[0].split()
    d["load"] = [float(x) for x in la[:3]] if len(la) >= 3 else [0, 0, 0]
    st = sec[1].split()
    d["cpu_tempos"] = [int(x) for x in st[1:9]] if len(st) > 4 else None
    mem = {}
    for l in sec[2].splitlines():
        p = l.replace(":", "").split()
        if len(p) >= 2:
            mem[p[0]] = int(p[1]) * 1024
    d["mem_total"] = mem.get("MemTotal", 0)
    d["mem_usada"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
    d["swap_total"] = mem.get("SwapTotal", 0)
    d["swap_usada"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
    discos = []
    for l in sec[3].splitlines()[1:]:
        p = l.split()
        if len(p) >= 6 and p[1].isdigit():
            total, usado = int(p[1]) * 1024, int(p[2]) * 1024
            discos.append({"dispositivo": p[0], "ponto": p[5], "total": total, "usado": usado,
                           "pct": round(usado / total * 100, 1) if total else 0})
    d["discos"] = discos
    rx = tx = 0
    for l in sec[4].splitlines()[2:]:
        if ":" not in l:
            continue
        nome, resto = l.split(":", 1)
        if nome.strip() == "lo":
            continue
        p = resto.split()
        if len(p) >= 9:
            rx += int(p[0])
            tx += int(p[8])
    d["net"] = (rx, tx)
    try:
        d["uptime"] = float(sec[5].split()[0])
    except (ValueError, IndexError):
        d["uptime"] = None
    try:
        d["nucleos"] = int(sec[6].strip())
    except ValueError:
        d["nucleos"] = None
    info = sec[7].splitlines()
    d["hostname"] = info[0] if info else ""
    d["sistema"] = info[1] if len(info) > 1 else ""
    d["kernel"] = info[2] if len(info) > 2 else ""
    procs = []
    for l in sec[8].splitlines()[1:]:
        p = l.split(None, 4)
        if len(p) == 5:
            try:
                procs.append({"pid": int(p[0]), "nome": p[1], "cpu": float(p[2]), "mem_pct": float(p[3]),
                              "mem": int(p[4]) * 1024})
            except ValueError:
                pass
    d["processos"] = procs
    servs = []
    for l in sec[9].splitlines():
        p = l.split()
        if p:
            servs.append({"nome": p[0], "estado": p[1] if len(p) > 1 else "desconhecido"})
    d["servicos"] = servs
    conts = []
    for l in sec[10].splitlines():
        p = l.split("|")
        if len(p) >= 3:
            conts.append({"nome": p[0], "status": p[1], "imagem": p[2], "rodando": p[1].lower().startswith("up")})
    d["containers"] = conts
    return d


class Servidor:
    def __init__(self, cfg, ger):
        self.cfg = cfg
        self.ger = ger
        self.cliente = None
        self.online = False
        self.erro = None
        self.ultimo = None
        self.dados = {}
        self.urls = []
        self.serie = deque(maxlen=720)
        self._cpu_ant = None
        self._net_ant = None
        self._parar = threading.Event()
        self._avisos = {}
        self._offline_desde = None
        self.th = threading.Thread(target=self._loop, daemon=True, name=f"vps-{cfg['id']}")
        self.th.start()

    def parar(self):
        self._parar.set()
        self._fechar()

    def _fechar(self):
        try:
            if self.cliente:
                self.cliente.close()
        except Exception:
            pass
        self.cliente = None

    def _conectar(self):
        import paramiko
        v = self.cfg
        seg = segredo_de(v)
        if not seg and segredo(v["id"]):
            raise ValueError(MSG_SEGREDO)
        srv = self

        class Politica(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = _impressao(key)
                if not srv.cfg.get("fingerprint"):
                    srv.ger.salvar_fingerprint(srv.cfg["id"], fp)
                    srv.cfg["fingerprint"] = fp
                elif srv.cfg["fingerprint"] != fp:
                    raise paramiko.SSHException(_msg_identidade(fp))

        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(Politica())
        kw = {"hostname": v["host"], "port": v["porta"], "username": v["usuario"], "timeout": 12, "banner_timeout": 12,
              "auth_timeout": 12, "look_for_keys": False, "allow_agent": False}
        if v["auth"] == "chave" and seg.get("chave"):
            kw["pkey"] = carregar_chave(seg["chave"], seg.get("frase"))
        else:
            kw["password"] = seg.get("senha", "")
        c.connect(**kw)
        c.get_transport().set_keepalive(30)
        self.cliente = c

    def coletar(self):
        if self.cliente is None or not self.cliente.get_transport() or not self.cliente.get_transport().is_active():
            self._fechar()
            self._conectar()
        _i, o, _e = self.cliente.exec_command(_script(self.cfg), timeout=25)
        txt = o.read().decode("utf-8", "replace")
        d = _parse(txt)
        t = time.time()
        cpu = None
        if d["cpu_tempos"]:
            tot, ocioso = sum(d["cpu_tempos"]), d["cpu_tempos"][3] + d["cpu_tempos"][4]
            if self._cpu_ant and tot > self._cpu_ant[0]:
                cpu = round(100 * (1 - (ocioso - self._cpu_ant[1]) / (tot - self._cpu_ant[0])), 1)
            self._cpu_ant = (tot, ocioso)
        rx_s = tx_s = None
        if self._net_ant and t > self._net_ant[0]:
            rx_s = max(0.0, (d["net"][0] - self._net_ant[1]) / (t - self._net_ant[0]))
            tx_s = max(0.0, (d["net"][1] - self._net_ant[2]) / (t - self._net_ant[0]))
        self._net_ant = (t, d["net"][0], d["net"][1])
        d["cpu"] = cpu
        d["rx"] = rx_s
        d["tx"] = tx_s
        d["mem_pct"] = round(d["mem_usada"] / d["mem_total"] * 100, 1) if d["mem_total"] else None
        d["disco_pct"] = max((x["pct"] for x in d["discos"] if x["ponto"] == "/"), default=max((x["pct"] for x in d["discos"]), default=None))
        d.pop("cpu_tempos", None)
        return d

    def _testar_urls(self):
        out = []
        for u in self.cfg["urls"]:
            ini = time.time()
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "MonitorDoPC/" + config.VERSAO}, method="GET")
                with urllib.request.urlopen(req, timeout=10) as r:
                    cod = r.status
            except urllib.error.HTTPError as e:
                cod = e.code
            except Exception as e:  # noqa: BLE001
                out.append({"url": u, "ok": False, "codigo": None, "ms": None, "erro": str(e)[:120]})
                continue
            out.append({"url": u, "ok": 200 <= cod < 400, "codigo": cod, "ms": round((time.time() - ini) * 1000), "erro": None})
        return out

    def _avisar(self, chave, titulo, msg, espera=1800):
        if time.time() - self._avisos.get(chave, 0) < espera:
            return
        self._avisos[chave] = time.time()
        self.ger.eventos.append((f'VPS {self.cfg["nome"]}: {titulo}', msg))

    def _verificar_alertas(self):
        v, d = self.cfg, self.dados
        if v["alerta_disco"] and d.get("disco_pct") is not None and d["disco_pct"] >= v["alerta_disco"]:
            self._avisar("disco", "disco quase cheio", f'O disco está {d["disco_pct"]:.0f}% cheio.')
        if v["alerta_cpu"] and d.get("cpu") is not None and d["cpu"] >= v["alerta_cpu"]:
            self._avisar("cpu", "CPU alta", f'A CPU está em {d["cpu"]:.0f}%.', 900)
        if v["alerta_mem"] and d.get("mem_pct") is not None and d["mem_pct"] >= v["alerta_mem"]:
            self._avisar("mem", "memória alta", f'A memória está em {d["mem_pct"]:.0f}%.', 900)
        if v["alerta_servicos"]:
            for s in d.get("servicos", []):
                if s["estado"] != "active":
                    self._avisar("svc:" + s["nome"], f'serviço {s["nome"]} parado', f'O serviço {s["nome"]} está “{s["estado"]}”.')
        if v["alerta_urls"]:
            for u in self.urls:
                if not u["ok"]:
                    self._avisar("url:" + u["url"], "site fora do ar", f'{u["url"]} respondeu {u["codigo"] or u["erro"]}.')

    def _loop(self):
        while not self._parar.is_set():
            ini = time.time()
            try:
                self.dados = self.coletar()
                self.online = True
                self.erro = None
                self._offline_desde = None
                self.ultimo = time.time()
            except Exception as e:  # noqa: BLE001
                self.online = False
                self.erro = _msg_erro(e)
                self._fechar()
                if self._offline_desde is None:
                    self._offline_desde = time.time()
                if self.cfg["alerta_offline"] and time.time() - self._offline_desde > 90:
                    self._avisar("offline", "sem resposta", f"Não consegui conectar: {self.erro}")
            try:
                self.urls = self._testar_urls()
            except Exception:
                pass
            if self.online:
                self._verificar_alertas()
            d = self.dados if self.online else {}
            t = int(time.time())
            self.serie.append([t, d.get("cpu"), d.get("mem_pct"), d.get("disco_pct"), (d.get("load") or [None])[0],
                               d.get("rx"), d.get("tx"), 1 if self.online else 0])
            self.ger.para_gravar.append((t, self.cfg["id"], d.get("cpu"), d.get("mem_pct"), d.get("disco_pct"),
                                         (d.get("load") or [None])[0], d.get("rx"), d.get("tx"), 1 if self.online else 0))
            self._parar.wait(max(3, self.cfg["intervalo_s"] - (time.time() - ini)))

    def estado(self):
        v = {k: x for k, x in self.cfg.items()}
        return {**v, "online": self.online, "erro": self.erro, "ultimo": self.ultimo, "dados": self.dados,
                "urls_status": self.urls, "serie": list(self.serie)[-240:], "tem_senha": _tem_credencial(v)}


def _tem_credencial(v):
    s = segredo_de(v)
    return bool(s.get("senha") or s.get("chave"))


def _impressao(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _msg_identidade(fp):
    return (f"A identidade do servidor mudou ({fp}). Pode ser reinstalação da VPS ou um ataque. "
            "Se foi você que reinstalou, use “Esquecer identidade” e conecte de novo.")


def carregar_chave(texto, frase=None):
    import paramiko
    erros = []
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(texto), password=frase or None)
        except Exception as e:  # noqa: BLE001
            erros.append(str(e))
    raise ValueError("Chave privada inválida ou frase-senha errada.")


def _msg_erro(e):
    s = str(e) or e.__class__.__name__
    if "Authentication failed" in s:
        return "usuário ou senha/chave incorretos"
    if "timed out" in s.lower():
        return "o servidor não respondeu (tempo esgotado)"
    if "Unable to connect" in s or "refused" in s.lower():
        return "conexão recusada (porta SSH fechada?)"
    if "getaddrinfo" in s or "Name or service" in s:
        return "endereço não encontrado"
    return s[:200]


class Gerenciador:
    def __init__(self):
        self.servidores = {}
        self.eventos = []
        self.para_gravar = []
        self.lock = threading.Lock()
        self._fp_pendentes = {}

    def salvar_fingerprint(self, vid, fp):
        self._fp_pendentes[vid] = fp

    def sincronizar(self, lista):
        """Liga/desliga coletores conforme a configuração."""
        ids = {v["id"] for v in lista if v["ativo"]}
        with self.lock:
            for vid in list(self.servidores):
                s = self.servidores[vid]
                novo = next((v for v in lista if v["id"] == vid), None)
                if vid not in ids or (novo and {k: novo[k] for k in novo if k != "fingerprint"} != {k: s.cfg[k] for k in s.cfg if k != "fingerprint"}):
                    s.parar()
                    del self.servidores[vid]
            for v in lista:
                if v["ativo"] and v["id"] not in self.servidores:
                    self.servidores[v["id"]] = Servidor(dict(v), self)

    def estado(self, lista):
        with self.lock:
            out = []
            for v in lista:
                s = self.servidores.get(v["id"])
                if s:
                    out.append(s.estado())
                else:
                    out.append({**v, "online": False, "erro": "desligado" if not v["ativo"] else "iniciando",
                                "dados": {}, "serie": [], "urls_status": [], "tem_senha": _tem_credencial(v)})
            return out

    def testar(self, v, seg, esperado=""):
        """Testa a conexão sem salvar (usa os dados digitados; o que falta vem do que está guardado
        para este mesmo endereço). Com `esperado` (identidade já gravada), recusa um servidor
        diferente antes de enviar a senha/chave."""
        seg = seg if isinstance(seg, dict) else {}
        guardado = segredo_de(v)
        import paramiko

        class Politica(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if esperado and _impressao(key) != esperado:
                    raise paramiko.SSHException(_msg_identidade(_impressao(key)))

        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(Politica())
        kw = {"hostname": v["host"], "port": v["porta"], "username": v["usuario"], "timeout": 12, "banner_timeout": 12,
              "auth_timeout": 12, "look_for_keys": False, "allow_agent": False}
        try:
            if v["auth"] == "chave":
                kw["pkey"] = carregar_chave(seg.get("chave") or guardado.get("chave", ""), seg.get("frase") or guardado.get("frase"))
            else:
                kw["password"] = seg.get("senha") or guardado.get("senha", "")
            c.connect(**kw)
            fp = _impressao(c.get_transport().get_remote_server_key())
            _i, o, _e = c.exec_command("hostname; (. /etc/os-release 2>/dev/null; echo \"$PRETTY_NAME\"); nproc", timeout=15)
            info = o.read().decode("utf-8", "replace").split("\n")
            return True, f"Conectou em {info[0]} ({info[1] if len(info) > 1 else ''}, {info[2] if len(info) > 2 else '?'} núcleos). Identidade: {fp}"
        except Exception as e:  # noqa: BLE001
            return False, "Não conectou: " + _msg_erro(e)
        finally:
            c.close()
