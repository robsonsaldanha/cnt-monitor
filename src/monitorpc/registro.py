"""Registros (logs) do programa: data e hora em cada linha e leitura do fim dos arquivos para a tela Logs."""
import glob
import os
import time

from . import config

MAX_LINHAS = 2000
_BYTES_POR_LINHA = 400      # folga para achar as últimas linhas sem ler o arquivo inteiro


class ComHora:
    """Arquivo de log que põe data e hora no começo de cada linha (as linhas em branco ficam como estão)."""

    def __init__(self, arquivo):
        self._f = arquivo
        self._inicio = True

    def write(self, texto):
        hora = time.strftime("%Y-%m-%d %H:%M:%S ")
        out = []
        for parte in texto.splitlines(True):
            if self._inicio and parte.strip():
                out.append(hora)
            out.append(parte)
            self._inicio = parte.endswith("\n")
        self._f.write("".join(out))
        return len(texto)

    def flush(self):
        self._f.flush()

    def __getattr__(self, nome):
        return getattr(self._f, nome)


def _bandeja():
    """O log do ícone fica na pasta de cada usuário; o serviço roda como outra conta, então vale o mais recente."""
    sub = os.path.join("MonitorPC", "bandeja.log")
    if os.name == "nt":
        candidatos = glob.glob(os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Users", "*", "AppData", "Local", sub))
        candidatos.append(os.path.join(os.environ.get("LOCALAPPDATA") or "", sub))
    else:
        candidatos = glob.glob(os.path.join("/home", "*", ".local", "share", sub))
        candidatos.append(os.path.join(os.path.expanduser("~/.local/share"), sub))
    existentes = [c for c in candidatos if os.path.isfile(c)]
    return max(existentes, key=os.path.getmtime) if existentes else ""


def arquivos():
    from . import atualizacao
    return {"servico": config.LOG, "bandeja": _bandeja(), "instalacao": os.path.join(atualizacao.PASTA, "instalacao.log")}


def ler(nome, linhas=400):
    """As últimas linhas de um dos logs conhecidos (o nome nunca vira caminho: só vale o que está em arquivos())."""
    caminho = arquivos().get(nome)
    if not caminho or not os.path.isfile(caminho):
        return {"arquivo": nome, "existe": False, "linhas": [], "bytes": 0}
    n = max(1, min(int(linhas), MAX_LINHAS))
    tamanho = os.path.getsize(caminho)
    comeco = max(0, tamanho - n * _BYTES_POR_LINHA)
    with open(caminho, "rb") as f:
        f.seek(comeco)
        partes = f.read().decode("utf-8", "replace").splitlines()
    if comeco:
        partes = partes[1:]         # a primeira veio cortada no meio
    return {"arquivo": nome, "existe": True, "linhas": partes[-n:], "bytes": tamanho}
