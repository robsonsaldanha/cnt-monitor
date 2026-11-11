"""Implementações Linux das partes que, no Windows, usam APIs do sistema.

Cada módulo do pacote monitorpc que depende do Windows importa daqui as mesmas funções e classes quando roda
no Linux (no fim do próprio módulo), então o motor, o servidor e a interface não mudam."""
import os
import shutil
import subprocess


def rodar(args, timeout=20, entrada=None):
    """(código, saída em texto). Programa ausente ou que trava devolve (-1, mensagem)."""
    if not shutil.which(args[0]) and not os.path.isabs(args[0]):
        return -1, f"{args[0]} não está instalado."
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, input=entrada)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace") + (
            "" if r.returncode == 0 else (r.stderr or b"").decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def ler(caminho, padrao=None):
    try:
        with open(caminho, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return padrao
