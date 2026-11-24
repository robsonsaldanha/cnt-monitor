"""Teste da atualização pela cópia da VPS (manifesto ultima.json), sem rede de verdade.

    python build/teste_atualizacao_vps.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["MONITORPC_DADOS"] = tempfile.mkdtemp(prefix="mpc-teste-atu-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from monitorpc import atualizacao, config  # noqa: E402

SHA = "ab" * 32
corpo = {"versao": "99.0.0", "arquivo": "CNTMonitor-Setup-99.0.0.exe", "tamanho": 1234, "sha256": SHA,
         "assinatura": "CNTMonitor-Setup-99.0.0.exe.sig",
         "linux": {"versao": "99.0.0", "arquivo": "cnt-monitor_99.0.0_amd64.deb", "tamanho": 99, "sha256": SHA,
                   "assinatura": "cnt-monitor_99.0.0_amd64.deb.sig"}}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        d = json.dumps(corpo).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(d)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
atualizacao.MANIFESTO = f"http://127.0.0.1:{srv.server_port}/download/ultima.json"
atualizacao.SITE = "https://exemplo.invalido"


def ok(c, m):
    print(("ok   " if c else "FALHA ") + m)
    if not c:
        sys.exit(1)


a = atualizacao.Atualizador() if hasattr(atualizacao, "Atualizador") else None
if a is None:
    cls = [v for v in vars(atualizacao).values() if isinstance(v, type) and hasattr(v, "verificar")][0]
    a = cls()
info = a._da_vps()
ok(info and info["versao"] == "99.0.0" and info["sha256"] == SHA, "lê o manifesto da VPS")
arq = corpo["arquivo"] if atualizacao.WIN else corpo["linux"]["arquivo"]
ok(info["url"].endswith("/download/" + arq) and info["assinatura"].endswith(arq + ".sig"), "monta os links de download e assinatura")
corpo.pop("assinatura"); corpo["linux"].pop("assinatura")
ok(a._da_vps() is None, "sem assinatura no manifesto, não usa a VPS")
corpo["assinatura"] = "../x.sig"; corpo["linux"]["assinatura"] = "../x.sig"
ok(a._da_vps() is None, "nome de arquivo com / é recusado")
srv.shutdown()
print("atualização pela VPS ok (versão atual", config.VERSAO + ")")
