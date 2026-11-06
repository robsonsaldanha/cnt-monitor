"""API local (só 127.0.0.1) que alimenta a janela do Monitor do PC.

Leitura: GET livre (só neste computador). Alterações (encerrar, configurações...):
POST com o token secreto que vem embutido na página — outro site aberto no navegador
não consegue ler esse token, então não consegue mandar comandos.
"""
import os
import json
import time
import secrets
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from . import config, configuracao, banco

HOSTS_OK = {f"127.0.0.1:{config.PORTA}", f"localhost:{config.PORTA}"}
ORIGENS_OK = {f"http://127.0.0.1:{config.PORTA}", f"http://localhost:{config.PORTA}"}


def _token():
    os.makedirs(config.BASE, exist_ok=True)
    tok = secrets.token_hex(24)
    with open(config.TOKEN, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


def criar(motor):
    ui = os.path.join(config.pasta_recursos(), "ui", "index.html")
    TOKEN = _token()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _env(self, code, corpo, tipo):
            self.send_response(code)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, obj, code=200):
            self._env(code, json.dumps(obj, ensure_ascii=False, default=list, separators=(",", ":")).encode("utf-8"),
                      "application/json; charset=utf-8")

        def _host_ok(self):
            return self.headers.get("Host", "") in HOSTS_OK

        def _autorizado(self):
            org = self.headers.get("Origin")
            if org and org not in ORIGENS_OK:
                return False
            return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

        def do_GET(self):
            if not self._host_ok():
                return self._json({"erro": "host não permitido"}, 403)
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            r = u.path
            try:
                if r in ("/", "/index.html"):
                    with open(ui, "rb") as f:
                        html = f.read().replace(b"__TOKEN__", TOKEN.encode())
                    return self._env(200, html, "text/html; charset=utf-8")
                if r == "/api/ping":
                    return self._json({"ok": True, "app": "monitor-pc", "versao": config.VERSAO})
                if r == "/api/agora":
                    return self._json(motor.agora())
                if r == "/api/processos":
                    return self._json(motor.processos())
                if r == "/api/serie":
                    return self._json(motor.serie_curta(float(q.get("seg", 300))))
                if r == "/api/processo":
                    return self._json(motor.processo(int(q.get("pid", 0))))
                if r == "/api/programa":
                    return self._json(motor.programa(q.get("nome", "")))
                if r == "/api/rede":
                    return self._json(motor.rede_estado())
                if r == "/api/hardware":
                    return self._json(motor.hardware())
                if r == "/api/inicializacao":
                    return self._json(motor.inicializacao())
                if r == "/api/historico":
                    return self._json(banco.historico(float(q.get("horas", 1)), q.get("programa") or None))
                if r == "/api/temps_hist":
                    return self._json(banco.historico_temps(float(q.get("horas", 1))))
                if r == "/api/momento":
                    return self._json(banco.momento(int(float(q.get("ts", 0)))))
                if r == "/api/lentidao":
                    dias = float(q.get("dias", 7))
                    c = configuracao.obter()
                    return self._json({"episodios": banco.lentidao(int(time.time() - dias * 86400)),
                                       "limiar_mem": c["limiar_mem"], "limiar_cpu": c["limiar_cpu"],
                                       "limiar_temp": c["limiar_temp"], "banco": banco.info()})
                if r == "/api/relatorio":
                    ate = int(float(q.get("ate", time.time())))
                    desde = int(float(q.get("desde", ate - 86400)))
                    return self._json(banco.relatorio(desde, ate))
                if r == "/api/status":
                    return self._json(motor.status())
                if r == "/api/config":
                    return self._json({"valores": configuracao.obter(), "padrao": config.PADRAO, "opcoes": config.OPCOES})
                if r == "/api/acao/resultado":
                    return self._json({"resultado": motor.resultado(q.get("id", ""))})
                if r == "/api/fila":
                    if not self._autorizado():
                        return self._json({"erro": "não autorizado"}, 403)
                    return self._json({"acoes": motor.pegar_fila(min(25.0, float(q.get("espera", 20))))})
                return self._json({"erro": "não encontrado"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                try:
                    return self._json({"erro": str(e)}, 500)
                except Exception:
                    pass

        def do_POST(self):
            if not self._host_ok() or not self._autorizado():
                return self._json({"erro": "não autorizado"}, 403)
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                corpo = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except (ValueError, OSError):
                return self._json({"erro": "pedido inválido"}, 400)
            r = urlparse(self.path).path
            try:
                if r == "/api/acao":
                    return self._json(motor.acao(corpo))
                if r == "/api/config":
                    return self._json({"valores": configuracao.salvar(corpo)})
                if r == "/api/historico/apagar":
                    banco.apagar_tudo()
                    return self._json({"ok": True})
                if r == "/api/fila/resultado":
                    motor.resultado(corpo.get("id", ""), {"ok": bool(corpo.get("ok")), "msg": corpo.get("msg", "")})
                    return self._json({"ok": True})
                if r == "/api/janelas":
                    motor.receber_janelas(corpo.get("janelas"))
                    return self._json({"ok": True})
                return self._json({"erro": "não encontrado"}, 404)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                return self._json({"erro": str(e)}, 500)

    srv = ThreadingHTTPServer(("127.0.0.1", config.PORTA), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
    return srv
