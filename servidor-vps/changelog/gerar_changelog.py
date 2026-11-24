"""Gera servidor-vps/changelog/index.html a partir de changelog.json.

Uso:  python servidor-vps/changelog/gerar_changelog.py          (grava o index.html)
      python servidor-vps/changelog/gerar_changelog.py --conferir (falha se o index.html estiver desatualizado)

O texto do changelog é para pessoas (técnicas ou não): só o que mudou para quem usa, sem detalhes internos
nem nada de segurança sensível. A ordem do JSON é a da página: a mais nova em cima, a mais antiga embaixo.
"""
import html
import json
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
TIPOS = {"novo": "Novidade", "melhoria": "Melhoria", "correcao": "Correção"}

MODELO = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="index,follow">
<title>CNT Monitor: o que mudou em cada versão</title>
<style>
:root{--fundo:#0f1218;--cartao:#171c26;--texto:#e7eaf0;--suave:#98a1b3;--linha:#272e3d;--novo:#3fb27f;--melhoria:#4f8ff7;--correcao:#e0a03a}
@media (prefers-color-scheme:light){:root{--fundo:#f4f6fa;--cartao:#fff;--texto:#1b2230;--suave:#5b6578;--linha:#dde2ec}}
*{box-sizing:border-box}
body{margin:0;background:var(--fundo);color:var(--texto);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:40px 20px 80px}
h1{font-size:1.7rem;margin:0 0 6px}
.sub{color:var(--suave);margin:0 0 32px}
.versao{background:var(--cartao);border:1px solid var(--linha);border-radius:12px;padding:18px 20px;margin:0 0 16px}
.versao h2{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:1.15rem;margin:0 0 10px}
.versao time{color:var(--suave);font-size:.85rem;font-weight:400}
ul{list-style:none;margin:0;padding:0}
li{display:flex;gap:10px;align-items:flex-start;padding:5px 0}
.t{flex:none;min-width:84px;text-align:center;font-size:.72rem;font-weight:600;border-radius:99px;padding:2px 8px;margin-top:3px;color:#fff}
.t.novo{background:var(--novo)}.t.melhoria{background:var(--melhoria)}.t.correcao{background:var(--correcao)}
:target{outline:2px solid var(--melhoria)}
a{color:var(--melhoria)}
footer{color:var(--suave);font-size:.85rem;margin-top:28px}
</style>
</head>
<body>
<main>
<h1>CNT Monitor: o que mudou</h1>
<p class="sub">Todas as melhorias e correções de cada versão. A mais recente fica no topo.</p>
%(versoes)s
<footer><a href="/baixar">Baixar a versão mais recente</a></footer>
</main>
</body>
</html>
"""


def _id_versao(versao):
    return "v" + versao.replace(" ", "-")


def gerar():
    dados = json.loads((PASTA / "changelog.json").read_text(encoding="utf-8"))
    blocos = []
    for v in dados:
        itens = []
        for i in v["itens"]:
            tipo = i["tipo"]
            if tipo not in TIPOS:
                raise ValueError(f"tipo inválido em {v['versao']}: {tipo}")
            itens.append(f'<li><span class="t {tipo}">{TIPOS[tipo]}</span><span>{html.escape(i["texto"])}</span></li>')
        blocos.append(
            f'<section class="versao" id="{html.escape(_id_versao(v["versao"]))}">'
            f'<h2>Versão {html.escape(v["versao"])}<time datetime="{html.escape(v["data"])}">{html.escape(v["data"][8:10] + "/" + v["data"][5:7] + "/" + v["data"][:4])}</time></h2>'
            f'<ul>{"".join(itens)}</ul></section>')
    return MODELO % {"versoes": "\n".join(blocos)}


if __name__ == "__main__":
    saida = PASTA / "index.html"
    texto = gerar()
    if "--conferir" in sys.argv:
        if not saida.exists() or saida.read_text(encoding="utf-8") != texto:
            sys.exit("index.html do changelog desatualizado: rode python servidor-vps/changelog/gerar_changelog.py")
        print("changelog em dia")
    else:
        saida.write_text(texto, encoding="utf-8", newline="\n")
        print("gravado", saida)
