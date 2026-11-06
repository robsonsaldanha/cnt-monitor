"""API local (só 127.0.0.1) que alimenta a janela do Monitor do PC."""
import os
import json
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from . import config, banco

HOSTS_OK = {f"127.0.0.1:{config.PORTA}", f"localhost:{config.PORTA}"}


def criar(motor):
    ui = os.path.join(config.pasta_recursos(), "ui", "index.html")

    class H(BaseHTTPRequestHandler):
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
            self._env(code, json.dumps(obj, ensure_ascii=False, default=list).encode("utf-8"),
                      "application/json; charset=utf-8")

        def do_GET(self):
            if self.headers.get("Host", "") not in HOSTS_OK:
                return self._json({"erro": "host não permitido"}, 403)
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            r = u.path
            try:
                if r in ("/", "/index.html"):
                    with open(ui, "rb") as f:
                        return self._env(200, f.read(), "text/html; charset=utf-8")
                if r == "/api/ping":
                    return self._json({"ok": True, "app": "monitor-pc", "versao": config.VERSAO})
                if r == "/api/agora":
                    return self._json(motor.agora())
                if r == "/api/processos":
                    return self._json(motor.processos())
                if r == "/api/processo":
                    return self._json(motor.processo(int(q.get("pid", 0))))
                if r == "/api/programa":
                    return self._json(motor.programa(q.get("nome", "")))
                if r == "/api/rede":
                    return self._json(motor.rede_estado())
                if r == "/api/hardware":
                    return self._json(motor.hardware())
                if r == "/api/historico":
                    return self._json(banco.historico(float(q.get("horas", 1)), q.get("programa") or None))
                if r == "/api/temps_hist":
                    return self._json(banco.historico_temps(float(q.get("horas", 1))))
                if r == "/api/momento":
                    return self._json(banco.momento(int(float(q.get("ts", 0)))))
                if r == "/api/lentidao":
                    dias = float(q.get("dias", 7))
                    return self._json({"episodios": banco.lentidao(int(time.time() - dias * 86400)),
                                       "limiar_mem": config.LIMIAR_MEM, "limiar_cpu": config.LIMIAR_CPU,
                                       "banco": banco.info()})
                if r == "/api/relatorio":
                    ate = int(float(q.get("ate", time.time())))
                    desde = int(float(q.get("desde", ate - 86400)))
                    return self._json(banco.relatorio(desde, ate))
                if r == "/api/status":
                    return self._json(motor.status())
                return self._json({"erro": "não encontrado"}, 404)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                return self._json({"erro": str(e)}, 500)

    srv = ThreadingHTTPServer(("127.0.0.1", config.PORTA), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
    return srv
