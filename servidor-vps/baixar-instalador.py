#!/usr/bin/env python3
"""Copia para a VPS o instalador da release mais nova do CNT Monitor, para o site entregar o download daqui.

Roda pelo cnt-monitor-baixar.timer. Baixa CNTMonitor-Setup-<versão>.exe da última release do GitHub, confere o
SHA-256 que o GitHub publica para o arquivo e só então o põe na pasta (troca atômica). Depois grava ultima.json,
que o encontro.js lê para redirecionar /baixar. Mantém as 3 versões mais novas.

Variável: DOWNLOADS (padrão /var/www/cntmonitor_downloads).
"""
import hashlib
import json
import os
import re
import sys
import urllib.request

REPO = "robsonsaldanha/cnt-monitor"
PASTA = os.environ.get("DOWNLOADS", "/var/www/cntmonitor_downloads")
MANTER = 3
RE_NOME = re.compile(r"^CNTMonitor-Setup-(\d+(?:\.\d+)*)\.exe$")
CABECALHO = {"User-Agent": "cnt-monitor-site", "Accept": "application/vnd.github+json"}


def ultima_release():
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=CABECALHO)
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        m = RE_NOME.match(a.get("name", ""))
        if m:
            digest = a.get("digest") or ""
            if not digest.startswith("sha256:"):
                raise SystemExit(f"{a['name']}: o GitHub não informou o SHA-256; não dá para conferir")
            return m.group(1), a["name"], a["browser_download_url"], int(a["size"]), digest[7:].lower()
    raise SystemExit(f"release {rel.get('tag_name')} sem CNTMonitor-Setup-*.exe")


def baixar(url, destino, tamanho, sha):
    h = hashlib.sha256()
    n = 0
    tmp = destino + ".parcial"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnt-monitor-site"}), timeout=60) as r, \
            open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            n += len(b)
            if n > tamanho:
                break
            h.update(b)
            f.write(b)
    if n != tamanho or h.hexdigest() != sha:
        os.remove(tmp)
        raise SystemExit(f"download não confere (bytes {n}/{tamanho}, sha256 {h.hexdigest()})")
    os.chmod(tmp, 0o644)
    os.replace(tmp, destino)


def sha256_de(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def versao(nome):
    return tuple(int(x) for x in RE_NOME.match(nome).group(1).split("."))


def main():
    os.makedirs(PASTA, exist_ok=True)
    ver, nome, url, tamanho, sha = ultima_release()
    destino = os.path.join(PASTA, nome)
    if not (os.path.exists(destino) and os.path.getsize(destino) == tamanho and sha256_de(destino) == sha):
        baixar(url, destino, tamanho, sha)
        print(f"baixado {nome} ({tamanho} bytes, sha256 conferido)")
    info = {"versao": ver, "arquivo": nome, "tamanho": tamanho, "sha256": sha}
    tmp = os.path.join(PASTA, "ultima.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(PASTA, "ultima.json"))
    antigos = sorted((x for x in os.listdir(PASTA) if RE_NOME.match(x)), key=versao, reverse=True)[MANTER:]
    for x in antigos:
        if x != nome:
            os.remove(os.path.join(PASTA, x))
            print(f"removido {x}")
    print(f"atual: {nome}")


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        sys.exit(f"falhou: {e}")
