"""Servidor web do Monitor do PC.

- Local (127.0.0.1:48731): a janela do programa. Leitura livre só neste PC; alterações exigem
  o token secreto embutido na página (outro site aberto no navegador não consegue lê-lo).
- Rede de casa (0.0.0.0:48732, opcional): acesso pelo celular com senha (cookie de sessão).
  Pode ser "somente ver". A API de consulta /api/v1 aceita também uma chave (X-API-Key).
- /api/v1/*: API somente leitura e documentada (docs/API.md) para outros programas.
"""
import os
import json
import math
import base64
import time
import hashlib
import secrets
import threading
import subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

from . import config, configuracao, banco, icones, energia, servicos, alertas, regras, limpeza, captura, envio

HOSTS_OK = {f"127.0.0.1:{config.PORTA}", f"localhost:{config.PORTA}"}
ORIGENS_OK = {f"http://127.0.0.1:{config.PORTA}", f"http://localhost:{config.PORTA}"}
WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0
TIPOS_ARQ = {".woff2": "font/woff2", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".png": "image/png", ".svg": "image/svg+xml"}
POST_GRANDE = {"/api/capturas/conteudo", "/api/capturas/editada"}   # imagens inteiras (até 64 MB)


# ---------------------------------------------------------------- senha e sessões (acesso pelo celular)
def hash_senha(senha):
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, 200_000)
    return f"pbkdf2${sal.hex()}${h.hex()}"


def confere_senha(senha, guardado):
    try:
        _, sal, h = guardado.split("$")
        return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(sal), 200_000).hex(), h)
    except (ValueError, AttributeError):
        return False


SESSOES = {}          # token -> expira
TENTATIVAS = {}       # ip -> [ts...]


def _limpar_sessoes(t):
    """Descarta sessões vencidas e IPs sem tentativa recente (senão os dicionários só crescem)."""
    for tok, expira in list(SESSOES.items()):
        if expira <= t:
            SESSOES.pop(tok, None)
    for ip, ts in list(TENTATIVAS.items()):
        if not any(t - x < 300 for x in ts):
            TENTATIVAS.pop(ip, None)


class ParamInvalido(ValueError):
    """Parâmetro da URL inválido: vira resposta 400, não 500."""


def _num(q, nome, padrao, tipo=float):
    bruto = q.get(nome)
    if bruto is None or bruto == "":
        return padrao
    try:
        v = float(bruto)
    except (TypeError, ValueError):
        raise ParamInvalido(f"parâmetro '{nome}' inválido: use um número") from None
    if not math.isfinite(v):
        raise ParamInvalido(f"parâmetro '{nome}' inválido: use um número")
    return int(v) if tipo is int else v


def banco_captura(c):
    """Valida o registro de uma captura vindo do ícone da bandeja."""
    if not isinstance(c, dict):
        raise ParamInvalido("registro inválido")
    tipo = c.get("tipo")
    caminho = str(c.get("caminho") or "")[:1000]
    if tipo not in ("imagem", "video", "gif", "arquivo") or not caminho:
        raise ParamInvalido("registro inválido")
    dur = c.get("duracao")
    return {"tipo": tipo, "modo": str(c.get("modo") or "")[:20], "caminho": caminho,
            "largura": _num(c, "largura", 0, int), "altura": _num(c, "altura", 0, int), "bytes": _num(c, "bytes", 0, int),
            "duracao": _num(c, "duracao", 0) if dur is not None else None}


def mini_jpeg(b64):
    """Miniatura JPEG (base64) -> bytes; ignora o que não for um JPEG pequeno."""
    if not b64 or not isinstance(b64, str):
        return None
    try:
        dados = base64.b64decode(b64, validate=True)
    except ValueError:
        return None
    return dados if dados[:3] == b"\xff\xd8\xff" and len(dados) <= 300_000 else None
LOGIN_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monitor do PC — entrar</title><style>
:root{--bg:#101418;--p:#171c22;--b:#29313b;--t:#e6ebf0;--t2:#9aa6b2;--a:#5aa9e6;--r:#ef5f5f}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--t);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:16px}
form{background:var(--p);border:1px solid var(--b);border-radius:14px;padding:26px 24px;width:min(360px,100%)}
h1{font-size:20px;margin:0 0 4px}p{color:var(--t2);margin:0 0 18px;font-size:13.5px}
input{width:100%;background:#1e252d;border:1px solid var(--b);color:var(--t);border-radius:9px;padding:11px 12px;font:inherit;margin-bottom:12px}
button{width:100%;background:var(--a);border:0;color:#081018;font-weight:600;border-radius:9px;padding:11px;font:inherit;cursor:pointer}
.e{color:var(--r);font-size:13px;margin-bottom:10px}</style></head><body>
<form method="post" action="/login"><h1>Monitor do PC</h1><p>Digite a senha de acesso configurada no computador.</p>__ERRO__
<input type="password" name="senha" placeholder="Senha" autofocus autocomplete="current-password"><button>Entrar</button></form></body></html>"""


# ---------------------------------------------------------------- firewall (só rede privada)
REGRA_FW = "Monitor do PC (acesso pelo celular)"


def firewall(ligar, porta):
    if not WIN:
        return
    subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={REGRA_FW}"],
                   capture_output=True, timeout=20, creationflags=SEM_JANELA)
    if ligar:
        subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={REGRA_FW}", "dir=in", "action=allow",
                        "protocol=TCP", f"localport={porta}", "profile=private"],
                       capture_output=True, timeout=20, creationflags=SEM_JANELA)


def ips_rede():
    import psutil
    import ipaddress
    out = []
    try:
        st = psutil.net_if_stats()
        for nome, addrs in psutil.net_if_addrs().items():
            if nome in st and not st[nome].isup:
                continue
            for a in addrs:
                if a.family == 2:
                    ip = ipaddress.ip_address(a.address)
                    if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                        out.append({"placa": nome, "ip": a.address})
    except Exception:
        pass
    return out


def qr_svg(texto):
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        return img.to_string(encoding="unicode")
    except Exception:
        return None


# ---------------------------------------------------------------- API v1 (somente leitura)
def api_v1(motor, caminho, q):
    """Devolve (dados, status). Tudo aqui é só consulta."""
    p = caminho.strip("/").split("/")
    rec = p[0] if p else ""
    arg = unquote("/".join(p[1:])) if len(p) > 1 else ""
    e = motor.agora() or {}
    if rec == "":
        return {"versao": config.VERSAO, "recursos": [
            "resumo", "programas", "programas/{nome}", "processos", "processos/{pid}", "rede", "conexoes", "ips",
            "hardware", "sensores", "temperaturas", "discos", "gpus", "historico", "lentidao", "alertas",
            "vazamentos", "inicializacao", "servicos", "energia", "jogo", "vps", "status"]}, 200
    if rec == "resumo":
        return {k: e.get(k) for k in ("ts", "cpu", "nucleos", "freq", "mem_pct", "mem_usada", "mem_total", "mem_disp",
                                      "swap_pct", "ler", "gravar", "disco_ativo", "env", "rec", "temp_cpu", "temp_gpu",
                                      "temp_disco", "uso_gpu", "nproc", "nthreads", "nconn", "ligado_desde")}, 200
    if rec == "programas":
        if arg:
            g = motor.programa(arg)
            return (g, 200) if g and not g.get("ausente") else ({"erro": "programa não está aberto"}, 404)
        return [{k: v for k, v in g.items() if k not in ("pids", "ips")} for g in e.get("programas", [])], 200
    if rec == "processos":
        if arg:
            try:
                d = motor.processo(int(arg))
            except ValueError:
                d = None
            return (d, 200) if d else ({"erro": "processo não encontrado"}, 404)
        return motor.processos(), 200
    if rec in ("rede", "conexoes", "ips"):
        r = motor.rede_estado()
        if rec == "conexoes":
            return r["conexoes"], 200
        if rec == "ips":
            return r["remotos"], 200
        return {k: r[k] for k in ("ativo", "env", "rec", "programas", "interfaces", "desde")}, 200
    if rec in ("hardware", "sensores", "temperaturas", "discos", "gpus"):
        h = motor.hardware()
        if rec == "sensores":
            return h["sensores"], 200
        if rec == "temperaturas":
            return {"resumo": h["resumo"], "sensores": [s for s in h["sensores"] if s["tipo_en"] == "Temperature"]}, 200
        if rec == "discos":
            return {"fisicos": h["discos"], "unidades": h["particoes"]}, 200
        if rec == "gpus":
            return h["gpus"], 200
        return h, 200
    if rec == "historico":
        return banco.historico(min(720.0, _num(q, "horas", 1)), q.get("programa") or None), 200
    if rec == "lentidao":
        return banco.lentidao(int(time.time() - _num(q, "dias", 7) * 86400)), 200
    if rec == "alertas":
        return banco.listar_alertas(), 200
    if rec == "vazamentos":
        return motor.vazamentos, 200
    if rec == "inicializacao":
        return motor.inicializacao(), 200
    if rec == "servicos":
        return servicos.listar(), 200
    if rec == "energia":
        return energia.estado(), 200
    if rec == "jogo":
        return motor.jogo.estado(), 200
    if rec == "vps":
        return [{k: v for k, v in s.items() if k not in ("serie",)} for s in motor.vps.estado(configuracao.obter("vps"))], 200
    if rec == "status":
        return motor.status(), 200
    return {"erro": "recurso não encontrado", "veja": "/api/v1"}, 404


# ---------------------------------------------------------------- handler
def fabrica(motor, TOKEN, remoto=False):
    ui = os.path.join(config.pasta_recursos(), "ui")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _env(self, code, corpo, tipo, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, obj, code=200, extra=None):
            self._env(code, json.dumps(obj, ensure_ascii=False, default=list, separators=(",", ":")).encode("utf-8"),
                      "application/json; charset=utf-8", extra)

        # ---- autorização
        def _sessao(self):
            ck = self.headers.get("Cookie", "")
            for parte in ck.split(";"):
                k, _, v = parte.strip().partition("=")
                if k == "mpc_sessao" and SESSOES.get(v, 0) > time.time():
                    return True
            return False

        def _chave_api(self, q):
            chave = configuracao.obter("api_chave")
            dada = self.headers.get("X-API-Key") or q.get("chave") or ""
            return bool(chave) and secrets.compare_digest(dada.encode("utf-8"), chave.encode("utf-8"))

        def _pode_ler(self, q, v1=False):
            if not remoto:
                return self.headers.get("Host", "") in HOSTS_OK
            return self._sessao() or (v1 and self._chave_api(q))

        def _pode_alterar(self):
            if remoto:
                if not self._sessao() or configuracao.obter("acesso_somente_ver"):
                    return False
                org = self.headers.get("Origin")
                if org and org.split("//")[-1] != self.headers.get("Host", ""):
                    return False
            else:
                if self.headers.get("Host", "") not in HOSTS_OK:
                    return False
                org = self.headers.get("Origin")
                if org and org not in ORIGENS_OK:
                    return False
            return secrets.compare_digest(self.headers.get("X-Token", "").encode("utf-8"), TOKEN.encode("utf-8"))

        # ---- GET
        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            r = u.path
            try:
                if remoto and r == "/login":
                    return self._env(200, LOGIN_HTML.replace("__ERRO__", "").encode(), "text/html; charset=utf-8")
                if r.startswith("/api/v1"):
                    if not self._pode_ler(q, v1=True):
                        return self._json({"erro": "não autorizado — use a chave da API (X-API-Key)"}, 401)
                    dados, cod = api_v1(motor, r[len("/api/v1"):], q)
                    extra = {"Access-Control-Allow-Origin": "*"} if configuracao.obter("api_cors") and not remoto else None
                    return self._json({"ok": cod == 200, "versao_api": 1, "gerado_em": time.time(), "dados": dados}, cod, extra)
                if not self._pode_ler(q):
                    if remoto:
                        return self._env(302, b"", "text/plain", {"Location": "/login"})
                    return self._json({"erro": "host não permitido"}, 403)
                if r in ("/", "/index.html"):
                    with open(os.path.join(ui, "index.html"), "rb") as f:
                        tok = b"" if remoto and configuracao.obter("acesso_somente_ver") else TOKEN.encode()
                        html = f.read().replace(b"__TOKEN__", tok).replace(b"__REMOTO__", b"1" if remoto else b"0")
                    return self._env(200, html, "text/html; charset=utf-8")
                if r.startswith("/fontes/") or r.startswith("/estatico/"):
                    nome = os.path.basename(r)
                    arq = os.path.join(ui, r.strip("/").split("/")[0], nome)
                    if os.path.isfile(arq) and os.path.splitext(nome)[1] in TIPOS_ARQ:
                        with open(arq, "rb") as f:
                            dados = f.read()
                        return self._env(200, dados, TIPOS_ARQ[os.path.splitext(nome)[1]], {"Cache-Control": "max-age=604800"})
                    return self._json({"erro": "não encontrado"}, 404)
                return self._get_api(r, q)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except ParamInvalido as e:
                return self._json({"erro": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                try:
                    return self._json({"erro": str(e)}, 500)
                except Exception:
                    pass

        def _get_api(self, r, q):
            m = motor
            if r == "/api/ping":
                return self._json({"ok": True, "app": "monitor-pc", "versao": config.VERSAO})
            if r == "/api/agora":
                e = m.agora()
                if e is not None:
                    e["navegar"] = m.navegar
                return self._json(e)
            if r == "/api/processos":
                return self._json(m.processos())
            if r == "/api/serie":
                return self._json(m.serie_curta(_num(q, "seg", 300)))
            if r == "/api/processo":
                return self._json(m.processo(_num(q, "pid", 0, int)))
            if r == "/api/programa":
                return self._json(m.programa(q.get("nome", "")))
            if r == "/api/rede":
                return self._json(m.rede_estado())
            if r == "/api/hardware":
                return self._json(m.hardware())
            if r == "/api/ventoinhas":
                return self._json(m.vent.canais())
            if r == "/api/inicializacao":
                return self._json(m.inicializacao())
            if r == "/api/historico":
                return self._json(banco.historico(_num(q, "horas", 1), q.get("programa") or None))
            if r == "/api/temps_hist":
                return self._json(banco.historico_temps(_num(q, "horas", 1)))
            if r == "/api/momento":
                return self._json(banco.momento(_num(q, "ts", 0, int)))
            if r == "/api/lentidao":
                dias = _num(q, "dias", 7)
                c = configuracao.obter()
                return self._json({"episodios": banco.lentidao(int(time.time() - dias * 86400)),
                                   "limiar_mem": c["limiar_mem"], "limiar_cpu": c["limiar_cpu"],
                                   "limiar_temp": c["limiar_temp"], "banco": banco.info()})
            if r == "/api/relatorio":
                ate = _num(q, "ate", int(time.time()), int)
                desde = _num(q, "desde", ate - 86400, int)
                rel = banco.relatorio(desde, ate)
                rel["alertas"] = [a for a in banco.listar_alertas(500) if desde <= a["ts"] <= ate]
                rel["vazamentos"] = m.vazamentos
                rel["maquina"] = m.info_maquina()
                return self._json(rel)
            if r == "/api/status":
                return self._json(m.status())
            if r == "/api/config":
                revelar = not remoto and self._pode_alterar()   # só o ícone pede com o token
                return self._json({"valores": configuracao.publico(revelar), "padrao": config.PADRAO, "opcoes": config.OPCOES,
                                   "remoto": remoto, "somente_ver": remoto and configuracao.obter("acesso_somente_ver")})
            if r == "/api/icone":
                dados = icones.png(q.get("exe", ""))
                if not dados:
                    return self._json({"erro": "sem ícone"}, 404, {"Cache-Control": "max-age=3600"})
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)
                return
            if r == "/api/jogo":
                return self._json(m.jogo.estado())
            if r == "/api/energia/planos":
                return self._json(energia.planos())
            if r == "/api/energia":
                return self._json(energia.estado())
            if r == "/api/servicos":
                with m.lock:
                    procs = {p: (d["mem"], d["cpu"]) for p, d in m.procs.items()}
                lst = []
                for sv in servicos.listar():
                    mc = procs.get(sv["pid"])
                    lst.append({**sv, "mem": mc[0] if mc else 0, "cpu": mc[1] if mc else 0})
                return self._json(lst)
            if r == "/api/alertas":
                c = configuracao.obter()
                return self._json({"ativos": c["alertas_ativos"], "regras": [{**x, "descricao": alertas.descrever(x)} for x in c["alertas"]],
                                   "tipos": {k: {"titulo": v[0], "unidade": v[1]} for k, v in alertas.TIPOS.items()},
                                   "historico": banco.listar_alertas(), "vazamentos": m.vazamentos,
                                   "vazamento_ativo": c["vazamento_ativo"]})
            if r == "/api/regras":
                c = configuracao.obter()
                return self._json({"regras": [{**x, "descricao": regras.descrever(x)} for x in c["regras_prog"]],
                                   "log": m.regras.log[::-1], "prioridades": regras.NOMES_PRIORIDADE,
                                   "nucleos": m.n_nucleos()})
            if r == "/api/limpeza":
                return self._json({"categorias": limpeza.analisar(), "tarefas": m.tarefas.estado()})
            if r == "/api/navegadores":
                return self._json({"navegadores": limpeza.navegadores(), "aviso": limpeza.AVISO_NAVEGADOR})
            if r == "/api/tarefas":
                return self._json(m.tarefas.estado())
            if r == "/api/vps":
                return self._json(m.vps.estado(configuracao.obter("vps")))
            if r == "/api/vps/hist":
                return self._json(banco.vps_historico(q.get("id", ""), _num(q, "horas", 24)))
            if r == "/api/acesso":
                c = configuracao.obter()
                ips = ips_rede()
                urls = [f'http://{x["ip"]}:{c["acesso_porta"]}/' for x in ips]
                return self._json({"ativo": c["acesso_remoto"], "porta": c["acesso_porta"], "somente_ver": c["acesso_somente_ver"],
                                   "tem_senha": bool(c["acesso_senha"]), "ips": ips, "urls": urls,
                                   "qr": qr_svg(urls[0]) if urls and c["acesso_remoto"] else None,
                                   "rodando": m.remoto_rodando(), "api_chave": None if remoto else (c["api_chave"] or None),
                                   "api_cors": c["api_cors"]})
            if r == "/api/backup":
                if remoto:
                    return self._json({"erro": "faça o backup no próprio computador"}, 403)
                return self._json(configuracao.backup())
            if r == "/api/atualizacao":
                return self._json(m.atualizador.estado())
            if r == "/api/acao/resultado":
                return self._json({"resultado": m.resultado(q.get("id", ""))})
            if r.startswith("/api/capturas"):
                if remoto:
                    return self._json({"erro": "as capturas de tela só aparecem no próprio computador"}, 403)
                return self._get_capturas(r, q)
            if r == "/api/fila":
                if remoto or not self._pode_alterar():
                    return self._json({"erro": "não autorizado"}, 403)
                return self._json({"acoes": m.pegar_fila(min(25.0, _num(q, "espera", 20)))})
            return self._json({"erro": "não encontrado"}, 404)

        def _bin(self, dados, tipo, cache):
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def _get_capturas(self, r, q):
            if r == "/api/capturas":
                return self._json({"itens": banco.capturas_listar(_num(q, "limite", 300, int)), "estado": motor.captura_estado,
                                   "acoes": {k: v[0] for k, v in captura.ACOES.items()}, "destinos": envio.DESTINOS,
                                   "compartilhar": {k: v[0] for k, v in envio.COMPARTILHAR.items()},
                                   "acoes_imagem": captura.ACOES_IMAGEM, "tarefas": captura.TAREFAS})
            if r == "/api/capturas/stats":
                return self._json(banco.capturas_stats(_num(q, "dias", 30, int)))
            cid = _num(q, "id", 0, int)
            if r == "/api/capturas/mini":
                dados = banco.captura_mini(cid)
                if not dados:
                    return self._json({"erro": "sem miniatura"}, 404)
                return self._bin(dados, "image/jpeg", "max-age=86400")   # a página muda o endereço quando a imagem muda
            if r == "/api/capturas/arquivo":
                # o conteúdo vem do ícone da bandeja (que lê o arquivo com as permissões do usuário), nunca do disco aqui
                item = motor.captura_cache.get(cid)
                if not item:
                    return self._json({"erro": "peça o arquivo antes (ação captura_ler)"}, 404)
                return self._bin(item[0], item[1], "no-store")
            return self._json({"erro": "não encontrado"}, 404)

        def _post_capturas(self, r, corpo):
            m = motor
            if r == "/api/capturas/estado":
                m.captura_estado = {**(corpo if isinstance(corpo, dict) else {}), "ts": time.time()}
                return self._json({"ok": True})
            if r == "/api/capturas/registrar":
                d = banco_captura(corpo)
                return self._json({"ok": True, "id": banco.captura_registrar(d, mini_jpeg(corpo.get("mini")))})
            if r == "/api/capturas/atualizar":
                campos = {k: corpo[k] for k in ("largura", "altura", "bytes") if isinstance(corpo.get(k), int)}
                if isinstance(corpo.get("url"), str):
                    campos["url"] = corpo["url"][:2000]
                if isinstance(corpo.get("favorito"), bool):
                    campos["favorito"] = int(corpo["favorito"])
                if isinstance(corpo.get("tags"), str):
                    campos["tags"] = ", ".join(t.strip() for t in corpo["tags"].split(",") if t.strip())[:200]
                if isinstance(corpo.get("caminho"), str) and corpo["caminho"].strip():
                    campos["caminho"] = corpo["caminho"][:1000]
                banco.captura_atualizar(_num(corpo, "id", 0, int), campos, mini_jpeg(corpo.get("mini")))
                return self._json({"ok": True})
            if r == "/api/capturas/conteudo":
                cid = _num(corpo, "id", 0, int)
                tipo = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                        ".webp": "image/webp", ".bmp": "image/bmp"}.get(
                    str(corpo.get("ext", "")).lower())
                try:
                    dados = base64.b64decode(str(corpo.get("dados", "")), validate=True)
                except ValueError:
                    raise ParamInvalido("conteúdo inválido") from None
                if not tipo or not dados:
                    raise ParamInvalido("conteúdo inválido")
                m.captura_cache[cid] = (dados, tipo)
                while len(m.captura_cache) > 3:
                    m.captura_cache.pop(next(iter(m.captura_cache)))
                return self._json({"ok": True})
            if r == "/api/capturas/sxcu":
                try:
                    u = envio.de_sxcu(corpo.get("sxcu"))
                except ValueError as e:
                    msg = str(e)
                    return self._json({"ok": False, "msg": msg[:1].upper() + msg[1:] + "."})
                configuracao.salvar({"captura_uploader": u, "captura_destino": "personalizado"})
                m.config_mudou()
                return self._json({"ok": True, "msg": f'Uploader “{u["nome"] or u["url"]}” importado e escolhido como destino.',
                                   "valores": configuracao.publico()})
            if r == "/api/capturas/editada":
                d = banco.captura(_num(corpo, "id", 0, int))
                if not d:
                    return self._json({"ok": False, "msg": "Essa captura não está mais no histórico."})
                png = str(corpo.get("png", ""))
                if not png:
                    raise ParamInvalido("imagem vazia")
                return self._json(m._enfileirar({"tipo": "captura_editada", "cid": d["id"], "caminho": d["caminho"], "png": png,
                                                 "nova": bool(corpo.get("nova")), "copiar": bool(corpo.get("copiar"))}))
            return self._json({"erro": "não encontrado"}, 404)

        # ---- POST
        def do_POST(self):
            r = urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                return self._json({"erro": "pedido inválido"}, 400)
            if n < 0:
                return self._json({"erro": "pedido inválido"}, 400)
            if n > (64_000_000 if r in POST_GRANDE else 2_000_000):
                return self._json({"erro": "pedido grande demais"}, 413)
            bruto = self.rfile.read(n) if n else b""
            if remoto and r == "/login":
                return self._login(bruto)
            if not self._pode_alterar():
                return self._json({"erro": "não autorizado" + (" (acesso pela rede está como “somente ver”)" if remoto else "")}, 403)
            try:
                corpo = json.loads(bruto or b"{}")
            except ValueError:
                return self._json({"erro": "pedido inválido"}, 400)
            m = motor
            try:
                if r == "/api/acao":
                    if not isinstance(corpo, dict):
                        raise ParamInvalido("pedido inválido")
                    if remoto:
                        # pela rede: nada de ventoinhas nem de capturas (tela e arquivos do usuário), e "abrir"
                        # só vale para programa que o Monitor já conhece, nunca para um caminho mandado de fora
                        if str(corpo.get("tipo", "")).startswith(("vent_", "captura", "ajuda_")):
                            return self._json({"erro": "só no próprio computador"}, 403)
                        corpo.pop("caminho", None)
                    return self._json(m.acao(corpo))
                if r == "/api/config":
                    final = configuracao.salvar_publico(corpo, remoto=remoto)
                    m.config_mudou()
                    return self._json({"valores": configuracao.publico(), "ok": bool(final)})
                if r == "/api/historico/apagar":
                    banco.apagar_tudo()
                    return self._json({"ok": True})
                if r == "/api/ui/navegar":
                    m.navegar = {"aba": str(corpo.get("aba", ""))[:40], "ts": time.time()}
                    return self._json({"ok": True})
                if remoto:
                    return self._json({"erro": "só no próprio computador"}, 403)
                if r == "/api/fila/resultado":
                    m.resultado(corpo.get("id", ""), {"ok": bool(corpo.get("ok")), "msg": corpo.get("msg", ""),
                                                     "dados": corpo.get("dados")})
                    return self._json({"ok": True})
                if r.startswith("/api/capturas/"):
                    return self._post_capturas(r, corpo)
                if r == "/api/janelas":
                    m.receber_janelas(corpo.get("janelas"), corpo.get("frente"))
                    return self._json({"ok": True})
                if r == "/api/acesso/senha":
                    senha = str(corpo.get("senha") or "")
                    if len(senha) < 6:
                        return self._json({"ok": False, "msg": "Use uma senha com pelo menos 6 caracteres."})
                    configuracao.salvar({"acesso_senha": hash_senha(senha)})
                    SESSOES.clear()
                    m.config_mudou()
                    return self._json({"ok": True, "msg": "Senha salva. Quem estava conectado pelo celular precisa entrar de novo."})
                if r == "/api/acesso/chave":
                    chave = "" if corpo.get("apagar") else "mpc_" + secrets.token_urlsafe(24)
                    configuracao.salvar({"api_chave": chave})
                    return self._json({"ok": True, "chave": chave or None})
                if r == "/api/backup/restaurar":
                    ok, msg = configuracao.restaurar(corpo)
                    m.config_mudou()
                    return self._json({"ok": ok, "msg": msg})
                return self._json({"erro": "não encontrado"}, 404)
            except ParamInvalido as e:
                return self._json({"erro": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                return self._json({"erro": str(e)}, 500)

        def _login(self, bruto):
            ip = self.client_address[0]
            t = time.time()
            _limpar_sessoes(t)
            ts = [x for x in TENTATIVAS.get(ip, []) if t - x < 300]
            TENTATIVAS[ip] = ts
            if len(ts) >= 5:
                html = LOGIN_HTML.replace("__ERRO__", '<div class="e">Muitas tentativas. Espere 5 minutos.</div>')
                return self._env(429, html.encode(), "text/html; charset=utf-8")
            senha = parse_qs(bruto.decode("utf-8", "replace")).get("senha", [""])[0]
            guardado = configuracao.obter("acesso_senha")
            if guardado and confere_senha(senha, guardado):
                tok = secrets.token_urlsafe(32)
                SESSOES[tok] = t + 7 * 86400
                return self._env(302, b"", "text/plain", {"Location": "/", "Set-Cookie":
                                 f"mpc_sessao={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age={7 * 86400}"})
            ts.append(t)
            html = LOGIN_HTML.replace("__ERRO__", '<div class="e">Senha incorreta.</div>')
            return self._env(401, html.encode(), "text/html; charset=utf-8")

    return H


def _token():
    os.makedirs(config.BASE, exist_ok=True)
    tok = secrets.token_hex(24)
    with open(config.TOKEN, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


class Servidores:
    def __init__(self, motor):
        self.motor = motor
        self.token = _token()
        self.local = ThreadingHTTPServer(("127.0.0.1", config.PORTA), fabrica(motor, self.token))
        self.local.daemon_threads = True
        threading.Thread(target=self.local.serve_forever, name="http", daemon=True).start()
        self.remoto = None
        self.porta_remota = None
        self.erro_remoto = None
        motor.servidores = self
        self.aplicar()

    def aplicar(self):
        """Liga/desliga o acesso pela rede conforme as Configurações."""
        c = configuracao.obter()
        quer = c["acesso_remoto"] and bool(c["acesso_senha"])
        if self.remoto and (not quer or self.porta_remota != c["acesso_porta"]):
            try:
                self.remoto.shutdown()
                self.remoto.server_close()
            except Exception:
                pass
            self.remoto = None
            firewall(False, self.porta_remota)
        if quer and not self.remoto:
            try:
                self.remoto = ThreadingHTTPServer(("0.0.0.0", c["acesso_porta"]), fabrica(self.motor, self.token, remoto=True))
                self.remoto.daemon_threads = True
                self.porta_remota = c["acesso_porta"]
                threading.Thread(target=self.remoto.serve_forever, name="http-rede", daemon=True).start()
                firewall(True, c["acesso_porta"])
                self.erro_remoto = None
            except OSError as e:
                self.remoto = None
                self.erro_remoto = f"Não consegui abrir a porta {c['acesso_porta']}: {e}"

    def shutdown(self):
        for s in (self.local, self.remoto):
            try:
                if s:
                    s.shutdown()
            except Exception:
                pass


def criar(motor):
    return Servidores(motor)
