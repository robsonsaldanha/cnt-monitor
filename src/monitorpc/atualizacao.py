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

REPO = "robsonsaldanha/cnt-monitor"
PASTA = os.path.join(config.BASE, "atualizacao")
FLAG_REABRIR = os.path.join(config.BASE, "reabrir_icone.flag")
PREFIXOS = ("CNTMonitor-Setup", "MonitorDoPC-Setup")   # nome atual primeiro; o antigo vale para releases até a 2.6.x


def _versao(t):
    return tuple(int(x) for x in re.findall(r"\d+", t or "")[:3]) or (0,)


def travar(caminho):
    """Abre o arquivo para leitura sem deixar ninguém escrever, renomear ou apagar enquanto estiver aberto
    (e falha se alguém já estiver com ele aberto para escrita)."""
    import win32file
    return win32file.CreateFile(caminho, win32file.GENERIC_READ, win32file.FILE_SHARE_READ, None,
                                win32file.OPEN_EXISTING, 0, None)


def sha256_travado(trava, ao_ler=None):
    import win32file
    h = hashlib.sha256()
    lidos = 0
    while True:
        bloco = win32file.ReadFile(trava, 1 << 16)[1]
        if not bloco:
            return h.hexdigest()
        h.update(bloco)
        lidos += len(bloco)
        if ao_ler:
            ao_ler(lidos)


EM_ANDAMENTO = ("baixando", "conferindo", "instalando")
VERIFICAR_CADA = 600      # segundos entre as procuras automáticas por versão nova
REPETIR_FALHA = 6 * 3600  # depois de uma instalação automática que falhou, espera isto para tentar a mesma versão


class Atualizador:
    def __init__(self):
        self.lock = threading.Lock()      # uma instalação por vez
        self.ultima_verificacao = None
        self.disponivel = None
        self.erro = None
        self.situacao = "parado"
        self.pct = 0                      # andamento da etapa atual (baixando / conferindo), 0 a 100
        self.alvo = None                  # versão que está sendo instalada
        # ETag da última resposta: o 304 ("nada mudou") poupa o download, mas sem login ele CONTA na cota
        # do GitHub (60 consultas por hora por IP público; cada PC faz 6 por hora)
        self._etag = None
        self._resposta = None

    def _etapa(self, situacao):
        self.situacao, self.pct = situacao, 0

    def _andou(self, feito, total):
        self.pct = min(100, int(feito * 100 / total)) if total else 0

    def _falha(self, msg):
        self.situacao, self.erro = "erro", msg
        return False, msg

    def verificar(self):
        if self.situacao in EM_ANDAMENTO:      # não mexe no andamento de uma instalação em curso
            return self.disponivel
        self.situacao = "verificando"
        try:
            cab = {"User-Agent": "MonitorDoPC/" + config.VERSAO, "Accept": "application/vnd.github+json"}
            if self._etag and self._resposta is not None:
                cab["If-None-Match"] = self._etag
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=cab)
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
                self._etag, self._resposta = r.headers.get("ETag"), d
        except urllib.error.HTTPError as e:
            if e.code == 304 and self._resposta is not None:
                return self._avaliar(self._resposta)
            if e.code == 404:
                self.erro = ("O repositório do CNT Monitor é privado — o GitHub não deixa baixar sem login. "
                             "Deixe o repositório público para a atualização automática funcionar.")
            elif e.code in (403, 429):
                self.erro = ("O GitHub limitou por enquanto as consultas vindas desta rede (60 por hora por endereço de internet). "
                             "A procura volta sozinha em até uma hora.")
            else:
                self.erro = f"GitHub: {e}"
            self.situacao = "erro"
            self.ultima_verificacao = time.time()
            return None
        except Exception as e:  # noqa: BLE001
            self.erro = f"Sem conexão com o GitHub: {e}"
            self.situacao = "erro"
            self.ultima_verificacao = time.time()
            return None
        return self._avaliar(d)

    def _avaliar(self, d):
        self.ultima_verificacao = time.time()
        self.erro = None
        exes = [a for a in d.get("assets", []) if a["name"].endswith(".exe")]
        ativo = next((a for p in PREFIXOS for a in exes if a["name"].startswith(p)), None)
        sha = re.search(r"SHA256:\s*([0-9a-fA-F]{64})", d.get("body") or "")
        info = {"versao": (d.get("tag_name") or "").lstrip("v"), "nome": d.get("name"), "notas": (d.get("body") or "")[:3000],
                "url": ativo["browser_download_url"] if ativo else None, "tamanho": ativo["size"] if ativo else 0,
                "sha256": sha.group(1).lower() if sha else None, "pagina": d.get("html_url")}
        nova = _versao(info["versao"]) > _versao(config.VERSAO)
        self.disponivel = info if nova and info["url"] else None
        self.situacao = "nova versão disponível" if self.disponivel else "atualizado"
        return self.disponivel

    def instalar_ao_fundo(self):
        """Começa a instalação e volta na hora: a tela acompanha o andamento por estado()."""
        if self.situacao in EM_ANDAMENTO:
            return True, "A atualização já está em andamento."
        info = self.disponivel or self.verificar()
        if not info:
            return False, self.erro or "Você já está na versão mais nova."
        if not info.get("sha256"):
            return self._falha("O release não traz a assinatura SHA-256; por segurança não vou instalar.")
        self.alvo, self.erro = info["versao"], None
        self._etapa("baixando")
        threading.Thread(target=self.instalar, daemon=True, name="atualizacao").start()
        return True, f"Baixando a versão {info['versao']}."

    def instalar(self):
        if not self.lock.acquire(blocking=False):
            return True, "A atualização já está em andamento."
        try:
            return self._instalar()
        finally:
            self.lock.release()

    def _instalar(self):
        info = self.disponivel or self.verificar()
        if not info:
            return False, self.erro or "Você já está na versão mais nova."
        if not info.get("sha256"):
            return self._falha("O release não traz a assinatura SHA-256; por segurança não vou instalar.")
        os.makedirs(PASTA, exist_ok=True)
        destino = os.path.join(PASTA, f"CNTMonitor-Setup-{info['versao']}.exe")
        self.alvo, self.erro = info["versao"], None
        self._etapa("baixando")
        try:
            if os.path.lexists(destino):
                os.remove(destino)   # nunca reaproveita um arquivo que já estava lá (poderia ter sido posto por outro usuário)
            req = urllib.request.Request(info["url"], headers={"User-Agent": "MonitorDoPC/" + config.VERSAO})
            with urllib.request.urlopen(req, timeout=120) as r, open(destino, "xb") as f:
                total = int(r.headers.get("Content-Length") or 0) or info.get("tamanho") or 0
                feito = 0
                while True:
                    bloco = r.read(1 << 16)
                    if not bloco:
                        break
                    f.write(bloco)
                    feito += len(bloco)
                    self._andou(feito, total)
        except Exception as e:  # noqa: BLE001
            return self._falha(f"Não consegui baixar: {e}")
        # confere o que ficou no disco e roda o instalador com o arquivo travado: entre a conferência e a
        # execução ninguém consegue trocar o conteúdo (o serviço roda como SYSTEM)
        self._etapa("conferindo")
        try:
            trava = travar(destino)
        except Exception as e:  # noqa: BLE001
            return self._falha(f"Não consegui conferir o arquivo baixado: {e}")
        try:
            tamanho = os.path.getsize(destino)
            if sha256_travado(trava, lambda lidos: self._andou(lidos, tamanho)) != info["sha256"]:
                trava.Close()
                os.remove(destino)
                return self._falha("O arquivo baixado não confere com a assinatura SHA-256. Atualização cancelada.")
            with open(FLAG_REABRIR, "w") as f:
                f.write(info["versao"])
            self._etapa("instalando")
            subprocess.Popen([destino, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/TASKS=pawnio",
                              "/LOG=" + os.path.join(PASTA, "instalacao.log")],
                             creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED | NEW_PROCESS_GROUP
        except Exception as e:  # noqa: BLE001
            return self._falha(f"Não consegui iniciar a instalação: {e}")
        finally:
            trava.Close()
        return True, f"Instalando a versão {info['versao']}. O CNT Monitor reinicia sozinho em alguns segundos."

    def estado(self):
        return {"versao_atual": config.VERSAO, "disponivel": self.disponivel, "erro": self.erro,
                "ultima_verificacao": self.ultima_verificacao, "situacao": self.situacao,
                "pct": self.pct, "alvo": self.alvo}
