"""Gancho do Claude Code: impede mexer direto na main (de qualquer repositório em que o comando atue).

A main é produção. Todo trabalho acontece numa branch tipo/descricao-curta (feature, fix, refactor, hotfix, featfix)
e entra na main só por PR. Este gancho (PreToolUse) bloqueia, quando a branch atual do repositório alvo é a main:
  - Edit/Write/NotebookEdit em arquivos desse repositório;
  - git commit, merge, rebase, cherry-pick, revert, reset, am, apply.
E, em qualquer branch:
  - git push que leve algo para a main (inclui HEAD:main, --force e --all/--mirror), salvo o push para o remoto `vps`
    (publicação do hub: o post-receive da VPS publica a main depois do merge da PR).
Não bloqueia: git fetch, git pull, checkout/switch, status, log, diff, tag e push de tag, nem `gh pr merge`.
O repositório alvo é o do `git -C <pasta>`, do último `cd <pasta>` do comando ou da pasta do Claude. Comandos dentro de
`ssh`/`scp` são do outro computador e não contam.
Saída 2 = bloqueado; a mensagem vai para o Claude. Fluxo completo em docs/FLUXO-GIT.md.
"""
import json
import os
import re
import subprocess
import sys

PROTEGIDAS = {"main", "master"}
MUDAM_BRANCH = {"commit", "merge", "rebase", "cherry-pick", "revert", "reset", "am", "apply"}
REMOTO_PUBLICACAO = "vps"
SEPARADOR = re.compile(r"&&|\|\||[;|\n]")  # fim de um comando numa linha com vários comandos
GIT = re.compile(r"^git\s+((?:(?:-C\s+(?:\"[^\"]+\"|\S+)|-c\s+\S+)\s+)*)([a-z][a-z-]*)\b(.*)$", re.S)


def caminho(p):
    """/d/pasta (estilo Git Bash) vira D:/pasta, que o Python do Windows entende."""
    p = p.strip().strip('"').strip("'")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m and os.name == "nt" else p


def git(pasta, *args):
    try:
        r = subprocess.run(["git", "-C", pasta, *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def raiz(pasta):
    r = git(pasta, "rev-parse", "--show-toplevel")
    return os.path.normcase(os.path.realpath(r)) if r else ""


def bloquear(msg):
    sys.stderr.write("BLOQUEADO: " + msg + "\nCrie uma branch a partir da main (ex.: git switch -c fix/descricao-curta) "
                     "ou use a skill de tarefa do projeto. Regras em docs/FLUXO-GIT.md.\n")
    sys.exit(2)


def push_para_main(args):
    partes = SEPARADOR.split(args, maxsplit=1)[0].split()
    if any(p in ("--all", "--mirror") for p in partes):
        return True
    for p in partes:
        if not p.startswith("-") and p.split(":")[-1].lstrip("+") in PROTEGIDAS | {"refs/heads/main"}:
            return True
    return False


def verificar_comando(cmd, pasta):
    atual_dir = pasta
    for seg in SEPARADOR.split(cmd):
        seg = seg.strip()
        if not seg or seg.startswith(("ssh ", "scp ", "rsync ")):
            continue
        m = re.match(r"^cd\s+(?:/d\s+)?(\"[^\"]+\"|'[^']+'|\S+)", seg)
        if m:
            novo = caminho(m.group(1))
            atual_dir = novo if os.path.isabs(novo) else os.path.join(atual_dir, novo)
            continue
        g = GIT.match(seg)
        if not g:
            continue
        opcoes, sub, resto = g.groups()
        alvo = atual_dir
        c = re.search(r"-C\s+(\"[^\"]+\"|\S+)", opcoes)
        if c:
            nova = caminho(c.group(1))
            alvo = nova if os.path.isabs(nova) else os.path.join(atual_dir, nova)
        branch = git(alvo, "branch", "--show-current")
        if sub == "push":
            tokens = [t for t in resto.split() if not t.startswith("-")]
            if tokens and tokens[0] == REMOTO_PUBLICACAO:
                continue
            if push_para_main(resto):
                bloquear("push para a main não é permitido; a main só recebe mudanças por PR.")
            if not tokens and branch in PROTEGIDAS:
                bloquear(f"'git push' sem destino estando na '{branch}' enviaria a main.")
        elif sub in MUDAM_BRANCH and branch in PROTEGIDAS:
            bloquear(f"a branch atual é '{branch}' (produção); este comando git alteraria a main.")


def main():
    try:
        dados = json.load(sys.stdin)
    except Exception:
        return
    pasta = dados.get("cwd") or os.getcwd()
    tool = dados.get("tool_name", "")
    entrada = dados.get("tool_input") or {}

    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        arq = os.path.realpath(entrada.get("file_path") or entrada.get("notebook_path") or "")
        pasta_arq = os.path.dirname(arq)
        while pasta_arq and not os.path.isdir(pasta_arq) and pasta_arq != os.path.dirname(pasta_arq):
            pasta_arq = os.path.dirname(pasta_arq)
        base = raiz(pasta_arq)
        if base and os.path.normcase(arq).startswith(base + os.sep) and git(pasta_arq, "branch", "--show-current") in PROTEGIDAS:
            bloquear("a branch atual é a main (produção); não edite arquivos direto nela.")
    elif tool == "Bash":
        verificar_comando(entrada.get("command") or "", pasta)


main()
