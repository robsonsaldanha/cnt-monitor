"""Teste do envio do histórico de chamadas à nuvem (src/monitorpc/nuvem.py) contra um hub falso.

    python build/teste_nuvem.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DADOS = tempfile.mkdtemp(prefix="mpc-teste-nuvem-")
os.environ["MONITORPC_DADOS"] = DADOS
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from monitorpc import configuracao, licenca, nuvem, sinalizacao  # noqa: E402


def ok(c, m):
    print(("ok   " if c else "FALHA ") + m)
    if not c:
        sys.exit(1)


# ---------------- hub falso
hub = {"recebidas": [], "pedidos": 0, "auth": [], "modo": "ok"}


class H(BaseHTTPRequestHandler):
    def _resp(self, c, d):
        b = json.dumps(d).encode()
        self.send_response(c)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        hub["pedidos"] += 1
        hub["auth"].append(self.headers.get("Authorization"))
        if hub["modo"] == "403":
            return self._resp(403, {"erro": "A licença não está ativa para esta função."})
        if self.path.endswith("/enviar"):
            hub["recebidas"] += corpo["chamadas"]
            return self._resp(200, {"ok": True, "recebidas": len(corpo["chamadas"])})
        if self.path.endswith("/apagar"):
            return self._resp(200, {"ok": True, "apagadas": 3})
        self._resp(404, {"erro": "?"})

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
nuvem.HUB = f"http://127.0.0.1:{srv.server_port}/v1/chamadas"

# ---------------- chamadas locais (voip.db de verdade, sem captura)
voip = sinalizacao.Voip(pasta=DADOS, captura=False)


def gravar(i, tipo="chamada", rtp=None):
    con = voip._con()
    con.execute("INSERT OR REPLACE INTO chamadas(id, inicio, fim, tipo, de, para, origem, destino, estado, duracao, nmsg, dados, desligou, motivo)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"call-{i}", 1_800_000_000 + i, 1_800_000_050 + i, tipo, "11999990001", "1133334444", "10.0.0.1:5060", "10.0.0.2:5060",
                 "Encerrada", 40.0, 6, json.dumps({"msgs": [{"ts": 1, "metodo": "INVITE"}], "rtp": rtp or [], "agente": "Asterisk"}), "Origem", "BYE"))
    con.commit()
    con.close()


for i in range(25):
    gravar(i)
gravar(99, tipo="registro")
n = nuvem.Nuvem(voip, pasta=DADOS)

# ---------------- desligado ou sem licença: nada sai
licenca.tem = lambda f: True
licenca.token = lambda: "TOKEN-DE-TESTE"
ok(n.sincronizar() == 0 and hub["pedidos"] == 0, "desligado por padrão: nada é enviado")
configuracao.salvar({"chamadas_nuvem": True})
licenca.tem = lambda f: False
ok(n.sincronizar() == 0 and hub["pedidos"] == 0, "ligado mas sem licença: nada é enviado")
licenca.tem = lambda f: True

# ---------------- envio em lotes
ok(n.sincronizar() == 25 and hub["pedidos"] == 3, "envia as 25 chamadas em 3 lotes (10+10+5)")
ok(all(c["tipo"] == "chamada" for c in hub["recebidas"]) and len(hub["recebidas"]) == 25, "registros SIP (REGISTER) não sobem, só chamadas")
ok(set(hub["auth"]) == {"Bearer TOKEN-DE-TESTE"}, "usa a permissão da licença como Bearer")
ok(n.sincronizar() == 0 and hub["pedidos"] == 3, "sem novidade, não chama o hub")
gravar(100)
ok(n.sincronizar() == 1 and hub["recebidas"][-1]["id"] == "call-100", "chamada nova sobe sozinha")
gravar(100)   # regravada (INSERT OR REPLACE): ganha rowid novo e sobe de novo, atualizando
ok(n.sincronizar() == 1, "chamada atualizada sobe de novo")

# ---------------- conteúdo
rtp = [{"mos": 4.2, "perda": 0.5, "jitter_ms": 4, "recebidos": 99}, {"mos": 3.9, "perda": 1.5, "jitter_ms": 9, "recebidos": 80}]
gravar(101, rtp=rtp)
n.sincronizar()
u = hub["recebidas"][-1]
ok(u["mos"] == 3.9 and u["perda"] == 1.5 and u["jitter_ms"] == 9 and u["agente"] == "Asterisk" and u["dados"]["msgs"][0]["metodo"] == "INVITE" and u["pc_nome"],
   "resumo (pior MOS, maior perda e jitter) e detalhe vão junto")

# ---------------- hub recusa: cursor não anda e tenta de novo depois
gravar(102)
hub["modo"] = "403"
ok(n.sincronizar() == 0 and "licença" in n.erro.lower(), "licença revogada: nada sobe e o motivo aparece")
hub["modo"] = "ok"
ok(n.sincronizar() == 1 and n.erro == "", "voltando a valer, a chamada que ficou para trás sobe")

# ---------------- apagar tudo na nuvem não faz o histórico local subir de novo
antes = len(hub["recebidas"])
r = n.apagar(tudo=True, confirmar="APAGAR")
ok(r.get("apagadas") == 3 and n.sincronizar() == 0 and len(hub["recebidas"]) == antes, "apagar tudo na nuvem não reenvia o que já existe localmente")
gravar(103)
ok(n.sincronizar() == 1, "mas o que for gravado depois sobe")

# ---------------- limpeza local esvazia a tabela: o cursor recomeça
con = voip._con()
con.execute("DELETE FROM chamadas")
con.commit()
con.close()
gravar(200)
ok(n.sincronizar() == 1 and hub["recebidas"][-1]["id"] == "call-200", "tabela esvaziada e preenchida de novo: o cursor não fica à frente")

# ---------------- sem licença não chama nem para listar
licenca.token = lambda: ""
try:
    n.chamadas()
    ok(False, "listar sem licença deveria falhar")
except nuvem.NuvemErro as e:
    ok(e.status == 401, "listar sem licença é recusado antes de sair do computador")
srv.shutdown()
print("histórico na nuvem (cliente) ok")
