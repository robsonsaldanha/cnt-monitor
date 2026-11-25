"""Teste de conectar o computador à empresa (licenca.parear) e do envio do uso do computador (metricas.py), com um hub falso.

    python build/teste_empresa.py
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["MONITORPC_DADOS"] = tempfile.mkdtemp(prefix="mpc-teste-emp-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from monitorpc import licenca, metricas  # noqa: E402

PRIV = Ed25519PrivateKey.generate()
licenca.PUBLICA = base64.b64encode(PRIV.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
licenca.ARQUIVO = os.path.join(os.environ["MONITORPC_DADOS"], "licenca.json")
licenca._maquina = lambda: "smbios:teste-empresa-0001"


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def permissao(**k):
    agora = int(time.time())
    p = {"v": 1, "emp": "EMP-1", "lic": ["LIC-1"], "maq": licenca._idx_maquina(), "funcoes": ["chamadas-historico", "chamadas-relatorios"], "nbf": agora, "exp": agora + 7 * 86400}
    p.update(k)
    corpo = b64u(json.dumps(p).encode())
    return corpo + "." + b64u(PRIV.sign(corpo.encode()))


def ok(c, m):
    print(("ok   " if c else "FALHA ") + m)
    if not c:
        sys.exit(1)


hub = {"posts": [], "modo": "ok", "id_cadastrado": "123456789", "chave": "cnt_" + "ab" * 32, "renovar": 200, "metricas": 200}


class H(BaseHTTPRequestHandler):
    def _resp(self, c, d):
        b = json.dumps(d).encode()
        self.send_response(c)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        hub["posts"].append((self.path, corpo, self.headers.get("Authorization")))
        if self.path.endswith("/parear"):
            if corpo.get("id_remoto") != hub["id_cadastrado"]:
                return self._resp(404, {"erro": "Este computador ainda não foi cadastrado pelo administrador da empresa. Peça para cadastrar o ID 999 888 777 no painel."})
            if hub["modo"] == "outro-pc":
                return self._resp(409, {"erro": "Este ID já está conectado a outro computador."})
            return self._resp(200, {"ok": True, "chave": hub["chave"], "empresa": "Acme Telecom", "computador": "PC Recepção"})
        if self.path.endswith("/renovar"):
            if hub["renovar"] != 200:
                return self._resp(hub["renovar"], {"erro": {401: "Conecte este computador à empresa de novo.", 403: "A empresa não tem assinatura ativa. Fale com a CNT."}[hub["renovar"]]})
            return self._resp(200, {"permissao": permissao(), "empresa": "Acme Telecom"})
        if self.path.endswith("/metricas"):
            if hub["metricas"] != 200:
                return self._resp(hub["metricas"], {"erro": "Conecte este computador à empresa de novo."})
            return self._resp(200, {"ok": True})
        self._resp(404, {"erro": "?"})

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
licenca.HUB_EMPRESA = metricas.HUB = f"http://127.0.0.1:{srv.server_port}/v1/empresa"

# ---------------- endereço
for t, esperado in [("cntoficial.com.br/acme", "acme"), ("https://cntoficial.com.br/acme/", "acme"), ("HTTPS://www.cntoficial.com.br/Acme-Telecom?x=1", "acme-telecom"),
                    ("acme", "acme"), ("a", ""), ("outro.com/acme", ""), ("acme--x", ""), ("", ""), ("cntoficial.com.br/acme/outra/coisa", "acme")]:
    ok(licenca.slug_do_endereco(t) == esperado, f"endereço {t!r} -> {esperado!r}")

# ---------------- conectar
ok(not licenca.parear("", "123456789")[0], "sem endereço não chama o hub")
ok(not licenca.parear("acme", "")[0] and not hub["posts"], "sem o ID do acesso remoto não chama o hub")
r = licenca.parear("cntoficial.com.br/acme", "999 888 777")
ok(not r[0] and "cadastrado" in r[1] and not licenca.conectado(), "ID que o administrador não cadastrou: explica o que fazer e não conecta")
hub["modo"] = "outro-pc"
ok(not licenca.parear("acme", "123 456 789")[0] and not licenca.conectado(), "ID já usado por outro computador é recusado")
hub["modo"] = "ok"
r = licenca.parear("cntoficial.com.br/acme", "123 456 789", {"sistema": "Windows 11", "cpu": "Core i5", "nucleos": 8, "ram_total": 1, "discos": []})
ok(r[0] and licenca.conectado() and licenca.tem("chamadas-historico") and licenca.tem("chamadas-relatorios") and not licenca.tem("chamadas-multi-pc"), "conectar guarda a chave e já libera as funções da assinatura")
parear = [p for p in hub["posts"] if p[0].endswith("/parear")][-1][1]
ok(parear["slug"] == "acme" and parear["id_remoto"] == "123456789" and parear["maquina"] == "smbios:teste-empresa-0001" and parear["info"]["nucleos"] == 8, "envia o endereço, o ID só com números, o computador e a ficha")
e = licenca.estado()
ok(e["conectado"] and e["empresa"] == "Acme Telecom" and e["empresa_slug"] == "acme" and e["computador"] == "PC Recepção" and "chave" not in json.dumps(e), "estado mostra a empresa e nunca a chave")
guardado = open(licenca.ARQUIVO, encoding="utf-8").read()
ok("chave" in guardado and "senha" not in guardado.lower(), "a chave fica só no arquivo local e nenhuma senha é guardada")

# ---------------- renovar com a chave
ok(licenca.renovar()[0] and hub["posts"][-1][2] == "Bearer " + hub["chave"], "renovar usa a chave do computador como Bearer")
hub["renovar"] = 403
r = licenca.renovar()
ok(not r[0] and licenca.conectado() and not licenca.tem("chamadas-historico") and "assinatura" in licenca.estado()["mensagem"], "empresa sem assinatura: continua conectado, as funções Premium se fecham e o motivo aparece")
hub["renovar"] = 200
licenca.renovar_se_preciso()
ok(licenca.tem("chamadas-historico"), "voltando a ter assinatura, a renovação automática reabre as funções sozinha")
hub["renovar"] = 401
r = licenca.renovar()
ok(not r[0] and not licenca.conectado() and not licenca.tem("chamadas-historico"), "chave recusada (conexão reiniciada): desconecta e pede para conectar de novo")
hub["renovar"] = 200

# ---------------- uso do computador
m = metricas.coletar("Core i5", True)
ok(all(isinstance(m[k], int) and 0 <= m[k] <= 100 for k in ("cpu", "ram", "disco")) and m["uptime_s"] >= 0 and m["info"]["cpu"] == "Core i5", "medida: percentuais inteiros e a ficha da máquina")
proibido = {"processos", "programas", "janelas", "usuario", "user", "hostname", "arquivos", "teclas", "senha", "titulo"}
def chaves(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k.lower()
            yield from chaves(v)
    elif isinstance(o, list):
        for v in o:
            yield from chaves(v)
ok(not (set(chaves(m)) & proibido), "a medida não leva nada pessoal (processos, janelas, usuário, arquivos, teclas)")
ok(len(json.dumps(m)) < 2000, "a medida é pequena")
ok(not metricas.Envio().enviar(), "sem conexão com a empresa, nada é enviado")
licenca.parear("acme", "123456789")
env = metricas.Envio(cpu_nome=lambda: "Core i5")
antes = len(hub["posts"])
ok(env.enviar() and hub["posts"][-1][0].endswith("/metricas") and hub["posts"][-1][2] == "Bearer " + hub["chave"] and "info" in hub["posts"][-1][1], "envia o uso com a chave; a primeira medida leva a ficha")
env.enviar()
ok("info" not in hub["posts"][-1][1], "as medidas seguintes não repetem a ficha (ela só vai a cada 10 minutos)")
hub["metricas"] = 401
ok(not env.enviar() and not licenca.conectado(), "hub recusa a chave: o programa se desconecta e para de enviar")
srv.shutdown()
print("conectar à empresa e uso do computador ok")
