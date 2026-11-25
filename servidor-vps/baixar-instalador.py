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
import time
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
    """Todos os arquivos da pasta de recebimento com assinatura válida: lista de (versão, nome, caminho, tamanho, sha, caminho da .sig).
    O .exe e o .deb chegam em momentos diferentes e são tratados cada um por conta própria. Arquivo cuja assinatura não confere é
    apagado (nunca é publicado); arquivo sem .sig espera (a .sig pode estar chegando)."""
    if not os.path.isdir(RECEBIDOS):
        return []
    publica = chave_publica()
    for velho in os.listdir(RECEBIDOS):           # nome antigo do instalador (para instalações até a 2.6): a VPS não o serve
        if velho.startswith("MonitorDoPC-Setup-"):
            _descartar(os.path.join(RECEBIDOS, velho))
    validos = []
    for nome in sorted(os.listdir(RECEBIDOS)):
        m = RE_NOME.match(nome) or RE_DEB.match(nome)
        if not m:
            continue
        arq, sig = os.path.join(RECEBIDOS, nome), os.path.join(RECEBIDOS, nome + ".sig")
        if not os.path.isfile(sig):
            continue
        sha = sha256_de(arq)
        with open(sig, encoding="utf-8", errors="replace") as f:
            ass = f.read(4096)
        if assinatura_confere(nome, sha, ass, publica):
            validos.append((m.group(1), nome, arq, os.path.getsize(arq), sha, sig))
        else:
            print(f"recusado {nome}: assinatura não confere")
            _descartar(arq, sig)
    return validos


def limpar_recebidos(agora=None):
    """Não deixa sobra no recebimento: o que já foi publicado (ou é de versão mais velha que a publicada), .sig sem arquivo há mais de
    1 dia e arquivo que nunca recebeu a .sig em 2 dias. O que acabou de chegar e ainda espera a .sig fica."""
    if not os.path.isdir(RECEBIDOS):
        return
    agora = agora or time.time()
    publicado = {}
    for padrao in (RE_NOME, RE_DEB):
        vs = [versao(x) for x in os.listdir(PASTA) if padrao.match(x) and os.path.exists(os.path.join(PASTA, x + ".sig"))]
        publicado[padrao] = max(vs) if vs else None
    for nome in os.listdir(RECEBIDOS):
        caminho = os.path.join(RECEBIDOS, nome)
        if not os.path.isfile(caminho):
            continue
        idade = agora - os.path.getmtime(caminho)
        base = nome[:-4] if nome.endswith(".sig") else nome
        padrao = RE_NOME if RE_NOME.match(base) else RE_DEB if RE_DEB.match(base) else None
        if padrao is None:
            continue                                   # fora do padrão de nome (ex.: o instalador legado já foi tratado em recebidos())
        velho = publicado[padrao] is not None and versao(base) <= publicado[padrao]
        sem_par = (nome.endswith(".sig") and not os.path.exists(os.path.join(RECEBIDOS, base)) and idade > 86400) or \
                  (not nome.endswith(".sig") and not os.path.exists(caminho + ".sig") and idade > 2 * 86400)
        if velho or sem_par:
            _descartar(caminho)
            print(f"limpo do recebimento: {nome}")


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


def _info(nome, antigo):
    """Linha do ultima.json para um arquivo já publicado (a .sig precisa estar ao lado). Reaproveita o sha256 do manifesto
    anterior quando é o mesmo arquivo, para não reler 100 MB a cada 30 minutos."""
    caminho = os.path.join(PASTA, nome)
    tamanho = os.path.getsize(caminho)
    sha = antigo.get("sha256") if antigo.get("arquivo") == nome and antigo.get("tamanho") == tamanho else sha256_de(caminho)
    m = RE_NOME.match(nome) or RE_DEB.match(nome)
    return {"versao": m.group(1), "arquivo": nome, "tamanho": tamanho, "sha256": sha, "assinatura": nome + ".sig"}


def montar_manifesto():
    """ultima.json = o .exe mais novo publicado e, em "linux", o .deb mais novo (cada um com a sua versão)."""
    arq = os.path.join(PASTA, "ultima.json")
    try:
        with open(arq, encoding="utf-8") as f:
            antigo = json.load(f)
    except (OSError, ValueError):
        antigo = {}

    def mais_novo(padrao):
        nomes = sorted((x for x in os.listdir(PASTA) if padrao.match(x) and os.path.exists(os.path.join(PASTA, x + ".sig"))), key=versao, reverse=True)
        return nomes[0] if nomes else None
    exe, deb = mais_novo(RE_NOME), mais_novo(RE_DEB)
    if not exe:
        return None
    info = _info(exe, antigo)
    if deb:
        info["linux"] = _info(deb, antigo.get("linux") or {})
    tmp = arq + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, arq)
    return info


def podar(info):
    atuais = {info["arquivo"], info["assinatura"]}
    if info.get("linux"):
        atuais |= {info["linux"]["arquivo"], info["linux"]["assinatura"]}
    for padrao in (RE_NOME, RE_DEB):
        antigos = sorted((x for x in os.listdir(PASTA) if padrao.match(x)), key=versao, reverse=True)[MANTER:]
        for x in antigos:
            if x not in atuais:
                _descartar(os.path.join(PASTA, x), os.path.join(PASTA, x + ".sig"))
                print(f"removido {x}")


def main():
    os.makedirs(PASTA, exist_ok=True)
    novos = recebidos()
    for item in sorted(novos, key=lambda v: versao(v[1])):
        garantir(item)                # move para a pasta publicada (a assinatura já foi conferida)
    if not novos and os.environ.get("CNT_GITHUB", "1") != "0":
        try:                          # nada novo no recebimento: o GitHub só serve enquanto o repositório for público
            exe, deb = ultima_release()
            garantir(exe)
            if deb:
                garantir(deb)
        except (urllib.error.URLError, OSError) as e:
            print(f"nada novo no recebimento e o GitHub não respondeu ({e}); fica como está")
    info = montar_manifesto()
    if info:
        podar(info)
        limpar_recebidos()
        print("atual: " + ", ".join(sorted({info["arquivo"], info["assinatura"]} | ({info["linux"]["arquivo"], info["linux"]["assinatura"]} if info.get("linux") else set()))))
    else:
        print("nada publicado ainda")


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        sys.exit(f"falhou: {e}")
