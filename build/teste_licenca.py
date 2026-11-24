"""Teste do cliente da licença Premium (src/monitorpc/licenca.py) contra um "hub" falso que assina como o de verdade.

    python build/teste_licenca.py
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["MONITORPC_DADOS"] = tempfile.mkdtemp(prefix="mpc-teste-lic-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from monitorpc import licenca  # noqa: E402

PRIV = Ed25519PrivateKey.generate()
PUB = base64.b64encode(PRIV.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
licenca.PUBLICA = PUB
licenca.ARQUIVO = os.path.join(os.environ["MONITORPC_DADOS"], "licenca.json")
licenca._maquina = lambda: "smbios:teste-0001"


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def assinar(p, priv=PRIV):
    corpo = b64u(json.dumps(p).encode())
    return corpo + "." + b64u(priv.sign(corpo.encode()))


def payload(**k):
    agora = int(time.time())
    p = {"v": 1, "emp": "EMP-1", "lic": ["LIC-1"], "maq": licenca._idx_maquina(), "funcoes": ["chamadas-historico"],
         "nbf": agora, "exp": agora + 7 * 86400}
    p.update(k)
    return p


def ok(c, m):
    print(("ok   " if c else "FALHA ") + m)
    if not c:
        sys.exit(1)


# ---------------- permissão
bom = assinar(payload())
ok(licenca.ler_permissao(bom) is not None, "permissão assinada, deste computador e no prazo vale")
corpo, sig = bom.split(".")
falso = b64u(json.dumps(payload(funcoes=["chamadas-historico", "chamadas-multi-pc"])).encode()) + "." + sig
ok(licenca.ler_permissao(falso) is None, "permissão editada (funções a mais) não vale")
ok(licenca.ler_permissao(assinar(payload(), Ed25519PrivateKey.generate())) is None, "assinada por outra chave não vale")
ok(licenca.ler_permissao(assinar(payload(exp=int(time.time()) - 1))) is None, "vencida não vale")
ok(licenca.ler_permissao(assinar(payload(maq="0" * 64))) is None, "de outro computador não vale")
ok(licenca.ler_permissao(assinar(payload(v=2))) is None, "versão de formato desconhecida não vale")
ok(licenca.ler_permissao(assinar(payload(nbf=int(time.time()) + 3600))) is None, "que ainda não começou (relógio muito diferente) não vale")
ok(licenca.ler_permissao(assinar(payload(funcoes=["chamadas-historico", "inventada"])))["funcoes"] == ["chamadas-historico"], "função desconhecida é ignorada")
for lixo in ("", "abc", "a.b", "a.b.c", None):
    ok(licenca.ler_permissao(lixo) is None, f"lixo {lixo!r} não vale nem derruba")

# ---------------- hub falso
estado_hub = {"senha": "Senha-da-Ana-7", "modo": "ok", "chamadas": []}


class Hub(BaseHTTPRequestHandler):
    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        estado_hub["chamadas"].append((self.path, corpo))
        if estado_hub["modo"] == "revogada":
            return self._resp(403, {"erro": "A licença não está mais ativa neste computador."})
        if self.path.endswith("/entrar"):
            if corpo.get("senha") != estado_hub["senha"]:
                return self._resp(401, {"erro": "E-mail ou senha incorretos."})
            return self._resp(200, {"permissao": assinar(payload()), "empresa": "Empresa Teste", "funcoes": ["chamadas-historico"]})
        if self.path.endswith("/renovar"):
            return self._resp(200, {"permissao": assinar(payload(funcoes=["chamadas-historico", "chamadas-relatorios"])), "empresa": "Empresa Teste"})
        self._resp(404, {"erro": "?"})

    def _resp(self, c, d):
        b = json.dumps(d).encode()
        self.send_response(c)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Hub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
licenca.HUB = f"http://127.0.0.1:{srv.server_port}/v1/licenca"

# ---------------- entrar / usar / renovar / revogar / sair
ok(not licenca.tem("chamadas-historico") and licenca.estado()["entrou"] is False, "antes de entrar não há função Premium")
try:
    licenca.exigir("chamadas-historico")
    ok(False, "exigir sem licença deveria falhar")
except licenca.PremiumNecessario:
    ok(True, "exigir sem licença levanta PremiumNecessario")
ok(licenca.entrar("", "x")[0] is False, "sem e-mail não chama o hub")
ok(licenca.entrar("ana@exemplo.com", "errada")[0] is False and not licenca.tem("chamadas-historico"), "senha errada não libera nada")
r = licenca.entrar("Ana@Exemplo.com", "Senha-da-Ana-7")
ok(r[0] and licenca.tem("chamadas-historico") and not licenca.tem("chamadas-multi-pc"), "entrar libera só o que a licença tem")
ok(estado_hub["chamadas"][-1][1]["maquina"] == "smbios:teste-0001" and estado_hub["chamadas"][-1][1]["email"] == "ana@exemplo.com", "envia o computador e o e-mail em minúsculas")
guardado = open(licenca.ARQUIVO, encoding="utf-8").read()
ok("Senha-da-Ana-7" not in guardado and "permissao" in guardado, "a senha nunca é guardada")
e = licenca.estado()
ok(e["valida"] and e["empresa"] == "Empresa Teste" and e["email"] == "ana@exemplo.com" and e["funcoes"][0]["id"] == "chamadas-historico" and "permissao" not in e, "estado() resume sem expor a permissão")
ok(licenca.renovar()[0] and licenca.tem("chamadas-relatorios"), "renovar troca a permissão (agora com mais uma função)")

antes = len(estado_hub["chamadas"])
licenca.renovar_se_preciso()
ok(len(estado_hub["chamadas"]) == antes, "com a permissão nova, renovar_se_preciso não chama o hub")
d = json.load(open(licenca.ARQUIVO, encoding="utf-8"))
d["permissao"] = assinar(payload(exp=int(time.time()) + 3600))
json.dump(d, open(licenca.ARQUIVO, "w", encoding="utf-8"))
licenca.renovar_se_preciso()
ok(len(estado_hub["chamadas"]) == antes + 1, "perto do vencimento, renovar_se_preciso renova")

hub_url = licenca.HUB
licenca.HUB = "http://127.0.0.1:9/v1/licenca"          # hub fora do ar
r = licenca.renovar()
ok(not r[0] and licenca.tem("chamadas-historico"), "sem conexão a permissão continua valendo até vencer")
licenca.HUB = hub_url

estado_hub["modo"] = "revogada"
r = licenca.renovar()
ok(not r[0] and not licenca.tem("chamadas-historico") and licenca.estado()["entrou"] is False and "licença" in licenca.estado()["mensagem"].lower(),
   "licença revogada no hub: a renovação recusa e as funções Premium se fecham, com o motivo na tela")
estado_hub["modo"] = "ok"
ok(licenca.entrar("ana@exemplo.com", "Senha-da-Ana-7")[0] and licenca.tem("chamadas-historico"), "entrar de novo depois funciona")
ok(licenca.sair()[0] and not licenca.tem("chamadas-historico") and not os.path.exists(licenca.ARQUIVO), "sair apaga a licença deste computador")

# o hub devolvendo uma permissão falsa não é aceita
class Falso(Hub):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._resp(200, {"permissao": assinar(payload(), Ed25519PrivateKey.generate()), "empresa": "X"})


srv2 = HTTPServer(("127.0.0.1", 0), Falso)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
licenca.HUB = f"http://127.0.0.1:{srv2.server_port}/v1/licenca"
ok(not licenca.entrar("ana@exemplo.com", "x")[0] and not licenca.tem("chamadas-historico"), "permissão de servidor falso é recusada")
srv.shutdown(); srv2.shutdown()
print("licença Premium (cliente) ok")
