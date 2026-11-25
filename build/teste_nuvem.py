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
hub = {"recebidas": [], "pedidos": 0, "auth": [], "modo": "ok", "gets": [], "vivo": []}


class H(BaseHTTPRequestHandler):
    def _resp(self, c, d):
        b = json.dumps(d).encode()
        self.send_response(c)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        hub["pedidos"] += 1
        hub["gets"].append(self.path)
        hub["auth"].append(self.headers.get("Authorization"))
        if self.path.startswith("/v1/chamadas/exportar"):
            b = "\ufeff\"Computador\";\"Call-ID\"\r\n".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if "/relatorio" in self.path:
            return self._resp(200, {"totais": {"chamadas": 2}, "por_dia": [], "por_hora": [0] * 24})
        if "/computadores" in self.path:
            return self._resp(200, {"computadores": [{"id": "abc", "nome": "PC1"}]})
        if "/ao-vivo" in self.path:
            return self._resp(200, {"computadores": []})
        if "/resumo" in self.path:
            return self._resp(200, {"total": 7, "bytes": 10, "cota_bytes": 100, "retencao_dias": 90})
        if "/lista" in self.path:
            return self._resp(200, {"chamadas": []})
        self._resp(404, {"erro": "?"})

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        hub["pedidos"] += 1
        hub["auth"].append(self.headers.get("Authorization"))
        if hub["modo"] == "403":
            return self._resp(403, {"erro": "A licença não está ativa para esta função."})
        if self.path.endswith("/enviar"):
            hub["recebidas"] += corpo["chamadas"]
            return self._resp(200, {"ok": True, "recebidas": len(corpo["chamadas"])})
        if self.path.endswith("/ao-vivo"):
            hub["vivo"].append(corpo)
            return self._resp(200, {"ok": True})
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


# ---------------- Premium: relatórios e vários computadores (cada função pede a sua licença)
licenca.tem = lambda f: f == "chamadas-historico"
e = n.estado()
ok(e["licenciado"] and not e["relatorios"] and not e["varios_pcs"], "estado mostra quais funções Premium a licença tem")
licenca.tem = lambda f: True
e = n.estado()
ok(e["relatorios"] and e["varios_pcs"] and e["resumo"]["total"] == 7, "licença completa libera relatórios e vários PCs")
antes = hub["pedidos"]
n.estado(); n.estado()
ok(hub["pedidos"] == antes, "a tela atualiza toda hora: o resumo fica em cache e não sobrecarrega o hub")

r = n.relatorio(1_800_000_000, 1_800_086_400, "abcd1234")
ok(r["totais"]["chamadas"] == 2 and "de=1800000000" in hub["gets"][-1] and "ate=1800086400" in hub["gets"][-1] and "pc=abcd1234" in hub["gets"][-1], "relatório pede o período e o computador")
csv = n.exportar(1_800_000_000, 1_800_086_400)
ok(csv.startswith("\ufeff".encode("utf-8")) and b"Call-ID" in csv, "exportar devolve a planilha como bytes")
ok(n.computadores()[0]["nome"] == "PC1" and n.ao_vivo_empresa() == [], "lista os computadores da empresa e o ao vivo")
n.chamadas("busca", 50, "abcd1234")
ok("pc=abcd1234" in hub["gets"][-1], "o histórico pode ser filtrado por computador")

# ao vivo: só chamadas em andamento deste PC; sem a função, não publica
viva = sinalizacao._nova("viva-1", 1_800_000_500, "chamada")
viva.update(de="11999990001", para="1133334444", estado="Em andamento", inicio_conv=1_800_000_505)
fim = sinalizacao._nova("fim-1", 1_800_000_100, "chamada")
fim.update(final=True, estado="Encerrada")
voip.chamadas["viva-1"], voip.chamadas["fim-1"] = viva, fim
n.publicar_ao_vivo()
ok(len(hub["vivo"]) == 1 and [c["id"] for c in hub["vivo"][-1]["chamadas"]] == ["viva-1"] and hub["vivo"][-1]["pc_nome"], "publica só a chamada em andamento, com o nome do computador")
licenca.tem = lambda f: f != "chamadas-multi-pc"
n.publicar_ao_vivo()
ok(len(hub["vivo"]) == 1, "sem a função de vários PCs nada é publicado")
licenca.tem = lambda f: True
configuracao.salvar({"chamadas_nuvem": False})
n.publicar_ao_vivo()
ok(len(hub["vivo"]) == 1, "com a opção desligada nada é publicado")
configuracao.salvar({"chamadas_nuvem": True})
hub["modo"] = "403"
n.publicar_ao_vivo()
ok("licença" in n.vivo_erro.lower() and "licença" in n.estado()["erro"].lower(), "recusa do hub aparece como erro na tela")
hub["modo"] = "ok"

# ---------------- sem licença não chama nem para listar
licenca.token = lambda: ""
try:
    n.chamadas()
    ok(False, "listar sem licença deveria falhar")
except nuvem.NuvemErro as e:
    ok(e.status == 401, "listar sem licença é recusado antes de sair do computador")
srv.shutdown()
print("histórico na nuvem (cliente) ok")
