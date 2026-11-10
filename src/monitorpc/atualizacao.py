"""Atualização automática pelo GitHub Releases.

Procura a versão mais nova, baixa o instalador, confere o SHA-256 publicado no release
(se não bater, não instala) e roda o instalador em modo silencioso. Depois que o serviço
volta, ele reabre o ícone perto do relógio na tela do usuário.
"""
import os
import re
import json
import time
import hashlib
import threading
import subprocess
import urllib.error
import urllib.request

from . import config

REPO = "robsonsaldanha/monitor-pc"
PASTA = os.path.join(config.BASE, "atualizacao")
FLAG_REABRIR = os.path.join(config.BASE, "reabrir_icone.flag")


def _versao(t):
    return tuple(int(x) for x in re.findall(r"\d+", t or "")[:3]) or (0,)


def travar(caminho):
    """Abre o arquivo para leitura sem deixar ninguém escrever, renomear ou apagar enquanto estiver aberto
    (e falha se alguém já estiver com ele aberto para escrita)."""
    import win32file
    return win32file.CreateFile(caminho, win32file.GENERIC_READ, win32file.FILE_SHARE_READ, None,
                                win32file.OPEN_EXISTING, 0, None)


def sha256_travado(trava):
    import win32file
    h = hashlib.sha256()
    while True:
        bloco = win32file.ReadFile(trava, 1 << 16)[1]
        if not bloco:
            return h.hexdigest()
        h.update(bloco)


class Atualizador:
    def __init__(self):
        self.lock = threading.Lock()
        self.ultima_verificacao = None
        self.disponivel = None
        self.erro = None
        self.situacao = "parado"

    def verificar(self):
        self.situacao = "verificando"
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"User-Agent": "MonitorDoPC/" + config.VERSAO,
                                                  "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            self.erro = ("O repositório do Monitor do PC é privado — o GitHub não deixa baixar sem login. "
                         "Deixe o repositório público para a atualização automática funcionar.") if e.code == 404 else f"GitHub: {e}"
            self.situacao = "erro"
            self.ultima_verificacao = time.time()
            return None
        except Exception as e:  # noqa: BLE001
            self.erro = f"Sem conexão com o GitHub: {e}"
            self.situacao = "erro"
            self.ultima_verificacao = time.time()
            return None
        self.ultima_verificacao = time.time()
        self.erro = None
        ativo = next((a for a in d.get("assets", []) if a["name"].startswith("MonitorDoPC-Setup") and a["name"].endswith(".exe")), None)
        sha = re.search(r"SHA256:\s*([0-9a-fA-F]{64})", d.get("body") or "")
        info = {"versao": (d.get("tag_name") or "").lstrip("v"), "nome": d.get("name"), "notas": (d.get("body") or "")[:3000],
                "url": ativo["browser_download_url"] if ativo else None, "tamanho": ativo["size"] if ativo else 0,
                "sha256": sha.group(1).lower() if sha else None, "pagina": d.get("html_url")}
        nova = _versao(info["versao"]) > _versao(config.VERSAO)
        self.disponivel = info if nova and info["url"] else None
        self.situacao = "nova versão disponível" if self.disponivel else "atualizado"
        return self.disponivel

    def instalar(self):
        info = self.disponivel or self.verificar()
        if not info:
            return False, self.erro or "Você já está na versão mais nova."
        if not info.get("sha256"):
            return False, "O release não traz a assinatura SHA-256; por segurança não vou instalar."
        os.makedirs(PASTA, exist_ok=True)
        destino = os.path.join(PASTA, f"MonitorDoPC-Setup-{info['versao']}.exe")
        self.situacao = "baixando"
        try:
            if os.path.lexists(destino):
                os.remove(destino)   # nunca reaproveita um arquivo que já estava lá (poderia ter sido posto por outro usuário)
            req = urllib.request.Request(info["url"], headers={"User-Agent": "MonitorDoPC/" + config.VERSAO})
            with urllib.request.urlopen(req, timeout=120) as r, open(destino, "xb") as f:
                while True:
                    bloco = r.read(1 << 16)
                    if not bloco:
                        break
                    f.write(bloco)
        except Exception as e:  # noqa: BLE001
            self.situacao = "erro"
            return False, f"Não consegui baixar: {e}"
        # confere o que ficou no disco e roda o instalador com o arquivo travado: entre a conferência e a
        # execução ninguém consegue trocar o conteúdo (o serviço roda como SYSTEM)
        try:
            trava = travar(destino)
        except Exception as e:  # noqa: BLE001
            self.situacao = "erro"
            return False, f"Não consegui conferir o arquivo baixado: {e}"
        try:
            if sha256_travado(trava) != info["sha256"]:
                trava.Close()
                os.remove(destino)
                self.situacao = "erro"
                return False, "O arquivo baixado não confere com a assinatura SHA-256. Atualização cancelada."
            with open(FLAG_REABRIR, "w") as f:
                f.write(info["versao"])
            self.situacao = "instalando"
            subprocess.Popen([destino, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/TASKS=pawnio",
                              "/LOG=" + os.path.join(PASTA, "instalacao.log")],
                             creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED | NEW_PROCESS_GROUP
        finally:
            trava.Close()
        return True, f"Instalando a versão {info['versao']}. O Monitor do PC reinicia sozinho em alguns segundos."

    def estado(self):
        return {"versao_atual": config.VERSAO, "disponivel": self.disponivel, "erro": self.erro,
                "ultima_verificacao": self.ultima_verificacao, "situacao": self.situacao}
