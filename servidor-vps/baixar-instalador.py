#!/usr/bin/env python3
"""Publica na VPS os instaladores da release mais nova do CNT Monitor, para o site entregar o download daqui.

DUAS ORIGENS, nesta ordem:
  1. PASTA DE RECEBIMENTO (`.recebidos`, dentro de DOWNLOADS): o CI do GitHub envia ali, por uma chave que só escreve nessa pasta
     (rrsync), os .exe/.deb e as .sig da release. Cada arquivo só é publicado se a assinatura Ed25519 CONFERE com a chave pública
     do programa (a mesma que os computadores usam para aceitar atualizações): quem tiver a chave de envio não consegue publicar
     um instalador falso. É o caminho do repositório privado.
  2. GitHub (só enquanto o repositório for público): o que o script sempre fez, descrito abaixo.

Roda pelo cnt-monitor-baixar.timer (e pelo implantar.sh logo depois de cada release). No GitHub: baixa da última release o CNTMonitor-Setup-<versão>.exe (Windows) e,
se existir, o cnt-monitor_<versão>_amd64.deb (Linux); confere o SHA-256 que o GitHub publica para cada arquivo e
só então o põe na pasta (troca atômica). Depois grava ultima.json ({"arquivo": .exe, "linux": {"arquivo": .deb}}),
que o encontro.js lê para redirecionar /baixar. Mantém as 3 versões mais novas de cada um.

Variáveis: DOWNLOADS (padrão /var/www/cntmonitor_downloads); CNT_GITHUB=0 desliga o plano B do GitHub (use depois que o repositório for privado).
"""
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

REPO = "robsonsaldanha/cnt-monitor"
PASTA = os.environ.get("DOWNLOADS", "/var/www/cntmonitor_downloads")
MANTER = 3
RECEBIDOS = os.path.join(PASTA, ".recebidos")
# a chave pública que confere as assinaturas é a embutida no programa; o checkout do repositório na VPS a traz
FONTE_CHAVE = os.environ.get("CNT_FONTE_CHAVE", "/var/www/html_cntmonitor/src/monitorpc/atualizacao.py")
RE_NOME = re.compile(r"^CNTMonitor-Setup-(\d+(?:\.\d+)*)\.exe$")
RE_DEB = re.compile(r"^cnt-monitor_(\d+(?:\.\d+)*)_amd64\.deb$")
CABECALHO = {"User-Agent": "cnt-monitor-site", "Accept": "application/vnd.github+json"}


def _arquivo(rel, padrao):
    for a in rel.get("assets", []):
        m = padrao.match(a.get("name", ""))
        if m:
            digest = a.get("digest") or ""
            if not digest.startswith("sha256:"):
                raise SystemExit(f"{a['name']}: o GitHub não informou o SHA-256; não dá para conferir")
            sig = next((x for x in rel.get("assets", []) if x.get("name") == a["name"] + ".sig"), None)
            return (m.group(1), a["name"], a["browser_download_url"], int(a["size"]), digest[7:].lower(),
                    sig["browser_download_url"] if sig else None)
    return None


def ultima_release():
    """(dados do .exe, dados do .deb ou None) da última release."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=CABECALHO)
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.load(r)
    exe = _arquivo(rel, RE_NOME)
    if not exe:
        raise SystemExit(f"release {rel.get('tag_name')} sem CNTMonitor-Setup-*.exe")
    return exe, _arquivo(rel, RE_DEB)


def chave_publica():
    with open(FONTE_CHAVE, encoding="utf-8") as f:
        m = re.search(r'^PUBLICA = "([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit(f"não achei a chave pública em {FONTE_CHAVE}")
    return m.group(1)


def assinatura_confere(nome, sha, assinatura_b64, publica):
    """Mesma conta do programa (src/monitorpc/atualizacao.py): Ed25519 sobre 'cnt-monitor-release-v1\\n<nome>\\n<sha256>\\n'."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(publica)).verify(
            base64.b64decode(assinatura_b64.strip()), f"cnt-monitor-release-v1\n{nome}\n{sha.lower()}\n".encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def _descartar(*caminhos):
    for c in caminhos:
        try:
            os.remove(c)
        except FileNotFoundError:
            pass


def recebidos():
    """(exe, deb) da pasta de recebimento: a versão mais nova do .exe e o .deb dela, só com assinatura válida. Cada item é
    (versão, nome, caminho, tamanho, sha, caminho da .sig). Arquivo sem assinatura válida é apagado e nunca publicado."""
    if not os.path.isdir(RECEBIDOS):
        return None, None
    publica = chave_publica()
    validos = {}
    for nome in sorted(os.listdir(RECEBIDOS)):
        for padrao in (RE_NOME, RE_DEB):
            m = padrao.match(nome)
            if not m:
                continue
            arq, sig = os.path.join(RECEBIDOS, nome), os.path.join(RECEBIDOS, nome + ".sig")
            if not os.path.isfile(sig):
                break                                  # a .sig pode ainda estar chegando
            sha = sha256_de(arq)
            with open(sig, encoding="utf-8", errors="replace") as f:
                ass = f.read(4096)
            if assinatura_confere(nome, sha, ass, publica):
                validos[nome] = (m.group(1), nome, arq, os.path.getsize(arq), sha, sig)
            else:
                print(f"recusado {nome}: assinatura não confere")
                _descartar(arq, sig)
            break
    exes = sorted((v for n, v in validos.items() if RE_NOME.match(n)), key=lambda v: versao(v[1]), reverse=True)
    if not exes:
        return None, None
    ver = exes[0][0]
    deb = next((v for n, v in validos.items() if RE_DEB.match(n) and v[0] == ver), None)
    return exes[0], deb


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
    m = RE_NOME.match(nome) or RE_DEB.match(nome)
    return tuple(int(x) for x in m.group(1).split("."))


def garantir(item):
    """Baixa (se ainda não está aqui, inteiro e conferido) e devolve as informações para o ultima.json."""
    ver, nome, url, tamanho, sha, url_sig = item
    destino = os.path.join(PASTA, nome)
    if os.path.isabs(url):             # veio da pasta de recebimento (já conferido, com assinatura válida): só põe no lugar
        shutil.copyfile(url, destino + ".parcial")
        os.chmod(destino + ".parcial", 0o644)
        os.replace(destino + ".parcial", destino)
        shutil.copyfile(url_sig, destino + ".sig.parcial")
        os.chmod(destino + ".sig.parcial", 0o644)
        os.replace(destino + ".sig.parcial", destino + ".sig")
        _descartar(url, url_sig)
        print(f"publicado {nome} ({tamanho} bytes, assinatura conferida)")
        return {"versao": ver, "arquivo": nome, "tamanho": tamanho, "sha256": sha, "assinatura": nome + ".sig"}
    if not (os.path.exists(destino) and os.path.getsize(destino) == tamanho and sha256_de(destino) == sha):
        baixar(url, destino, tamanho, sha)
        print(f"baixado {nome} ({tamanho} bytes, sha256 conferido)")
    info = {"versao": ver, "arquivo": nome, "tamanho": tamanho, "sha256": sha}
    if url_sig:  # assinatura Ed25519: o programa confere; é pequena e o GitHub não a lista com SHA-256
        sig = os.path.join(PASTA, nome + ".sig")
        with urllib.request.urlopen(urllib.request.Request(url_sig, headers={"User-Agent": "cnt-monitor-site"}), timeout=30) as r:
            dados = r.read(4096)
        with open(sig + ".parcial", "wb") as f:
            f.write(dados)
        os.chmod(sig + ".parcial", 0o644)
        os.replace(sig + ".parcial", sig)
        info["assinatura"] = nome + ".sig"
    return info


def main():
    os.makedirs(PASTA, exist_ok=True)
    exe, deb = recebidos()
    if not exe and os.environ.get("CNT_GITHUB", "1") == "0":
        print("nada novo no recebimento (GitHub desligado)")
        return
    if not exe:                       # nada novo no recebimento: o GitHub só serve enquanto o repositório for público
        try:
            exe, deb = ultima_release()
        except (urllib.error.URLError, OSError) as e:
            print(f"nada novo no recebimento e o GitHub não respondeu ({e}); fica como está")
            return
    info = garantir(exe)
    atuais = {info["arquivo"], info.get("assinatura")}
    if deb:
        info["linux"] = garantir(deb)
        atuais |= {info["linux"]["arquivo"], info["linux"].get("assinatura")}
    tmp = os.path.join(PASTA, "ultima.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(PASTA, "ultima.json"))
    for padrao in (RE_NOME, RE_DEB):
        antigos = sorted((x for x in os.listdir(PASTA) if padrao.match(x)), key=versao, reverse=True)[MANTER:]
        for x in antigos:
            if x not in atuais:
                for y in (x, x + ".sig"):
                    if os.path.exists(os.path.join(PASTA, y)):
                        os.remove(os.path.join(PASTA, y))
                print(f"removido {x}")
    print("atual: " + ", ".join(sorted(atuais)))


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        sys.exit(f"falhou: {e}")
