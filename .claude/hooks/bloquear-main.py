"""Gancho do Claude Code: impede mexer direto na main.

A main é produção. Todo trabalho acontece numa branch tipo/descricao-curta (feature, fix, refactor, hotfix, featfix)
e entra na main só por PR. Este gancho (PreToolUse) bloqueia, com a main como branch atual:
  - Edit/Write/NotebookEdit em arquivos do repositório;
  - git commit, merge, rebase, cherry-pick, revert, reset, am, apply.
E, em qualquer branch:
  - git push que leve algo para a main (inclui HEAD:main, --force e --all/--mirror).
Não bloqueia: git fetch, git pull (só traz o que já está na main do GitHub), checkout/switch, status, log, diff, tag e push de tag, nem `gh pr merge` (o merge pela PR).
Saída 2 = bloqueado; a mensagem vai para o Claude. Fluxo completo em docs/FLUXO-GIT.md e na skill /monitor-tarefa.
"""
import json
import os
import re
import subprocess
import sys

PROTEGIDAS = {"main", "master"}
MUDAM_BRANCH = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+(commit|merge|rebase|cherry-pick|revert|reset|am|apply)\b")
SEPARADOR = re.compile(r"[;&|\n]")  # fim do comando git push numa linha com vários comandos
PUSH = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+push\b(.*)")


def branch_atual(pasta):
    try:
        r = subprocess.run(["git", "-C", pasta, "branch", "--show-current"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def raiz(pasta):
    try:
        r = subprocess.run(["git", "-C", pasta, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        return os.path.normcase(os.path.realpath(r.stdout.strip())) if r.returncode == 0 else ""
    except Exception:
        return ""


def bloquear(msg):
    sys.stderr.write("BLOQUEADO: " + msg + "\nCrie uma branch a partir da main (ex.: git switch -c fix/descricao-curta) "
                     "ou use a skill /monitor-tarefa. Regras em docs/FLUXO-GIT.md.\n")
    sys.exit(2)


def push_para_main(args):
    args = SEPARADOR.split(args, maxsplit=1)[0]   # só o que pertence ao git push, não o resto da linha
    partes = [p for p in args.split() if p]
    if any(p in ("--all", "--mirror") for p in partes):
        return True
    for p in partes:
        if p.startswith("-"):
            continue
        destino = p.split(":")[-1].lstrip("+")
        if destino in PROTEGIDAS or destino == "refs/heads/main":
            return True
    return False


def main():
    try:
        dados = json.load(sys.stdin)
    except Exception:
        return
    pasta = dados.get("cwd") or os.getcwd()
    tool = dados.get("tool_name", "")
    entrada = dados.get("tool_input") or {}
    base = raiz(pasta)
    if not base:
        return
    atual = branch_atual(pasta)

    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        alvo = os.path.normcase(os.path.realpath(entrada.get("file_path") or entrada.get("notebook_path") or ""))
        if atual in PROTEGIDAS and alvo.startswith(base + os.sep):
            bloquear(f"a branch atual é '{atual}' (produção); não edite arquivos direto nela.")
        return

    if tool == "Bash":
        cmd = entrada.get("command") or ""
        m = PUSH.search(cmd)
        if m and push_para_main(m.group(1)):
            bloquear("push para a main não é permitido; a main só recebe mudanças por PR.")
        if m and not re.search(r"\S", re.sub(r"--?\S+", "", SEPARADOR.split(m.group(1), maxsplit=1)[0])) and atual in PROTEGIDAS:
            bloquear(f"'git push' sem destino estando na '{atual}' enviaria a main.")
        if atual in PROTEGIDAS and MUDAM_BRANCH.search(cmd):
            bloquear(f"a branch atual é '{atual}' (produção); este comando git alteraria a main.")


main()
