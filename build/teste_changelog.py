"""Testes do changelog público: servidor-vps/changelog/changelog.json e a página gerada.

    python build/teste_changelog.py

Confere que a versão atual tem entrada no topo, que a página gerada está em dia, que a ordem é da mais nova para a
mais antiga e que o texto não traz coisas internas (caminhos, portas, chaves, tokens) que não devem ir ao público.
"""
import json
import os
import re
import sys

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PASTA = os.path.join(RAIZ, "servidor-vps", "changelog")
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, PASTA)

from monitorpc import config  # noqa: E402
import gerar_changelog  # noqa: E402

erros = []


def conferir(ok, msg):
    print(("ok   " if ok else "FALHA ") + msg)
    if not ok:
        erros.append(msg)


dados = json.load(open(os.path.join(PASTA, "changelog.json"), encoding="utf-8"))
conferir(bool(dados), "o changelog não está vazio")
conferir(dados[0]["versao"] == config.VERSAO, f"a entrada do topo é a versão atual ({config.VERSAO}); está {dados[0]['versao']}")


def chave(v):
    return tuple(int(x) for x in re.findall(r"\d+", v["versao"].split(" a ")[-1]))


conferir(all(chave(a) >= chave(b) for a, b in zip(dados, dados[1:])), "versões em ordem: mais nova em cima, mais antiga embaixo")
conferir(all(v["itens"] for v in dados), "toda versão tem pelo menos um item")
conferir(all(i["tipo"] in gerar_changelog.TIPOS and i["texto"].strip() for v in dados for i in v["itens"]), "todo item tem tipo válido e texto")

proibido = re.compile(r"[A-Za-z]:\|/etc/|/var/|\bsudo\b|\bssh\b|\btoken\b|\bsenha d[oa] (vps|servidor)\b|\bapi[_ ]?key\b|\bchave privada\b|\bsha-?256\b|\b\d{1,3}(\.\d{1,3}){3}\b|:\d{4,5}\b", re.I)
ruins = [i["texto"] for v in dados for i in v["itens"] if proibido.search(i["texto"])]
conferir(not ruins, "nenhum item traz caminho, IP, porta, chave ou termo interno" + (f": {ruins}" if ruins else ""))

pagina = os.path.join(PASTA, "index.html")
conferir(os.path.exists(pagina) and open(pagina, encoding="utf-8").read() == gerar_changelog.gerar(),
         "index.html está em dia (rode servidor-vps/changelog/gerar_changelog.py se falhar)")

if erros:
    sys.exit(f"{len(erros)} falha(s)")
print("changelog ok")
