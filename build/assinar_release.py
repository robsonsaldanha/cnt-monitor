"""Assina os arquivos de uma release com Ed25519 (uma assinatura <arquivo>.sig por arquivo).

Uso no CI: RELEASE_SIGNING_KEY=<semente em base64> python build/assinar_release.py saida/*.exe saida/*.deb
A chave pública correspondente fica em src/monitorpc/atualizacao.py (PUBLICA). A mensagem assinada é a mesma
que o atualizador remonta: nome do arquivo + SHA-256 do conteúdo.
"""
import base64
import hashlib
import os
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def mensagem(nome, sha256):
    return f"cnt-monitor-release-v1\n{nome}\n{sha256.lower()}\n".encode()


def assinar(chave, caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    sig = chave.sign(mensagem(os.path.basename(caminho), h.hexdigest()))
    with open(caminho + ".sig", "wb") as f:
        f.write(base64.b64encode(sig))


def main(arquivos):
    semente = os.environ.get("RELEASE_SIGNING_KEY", "").strip()
    if not semente:
        sys.exit("RELEASE_SIGNING_KEY não definida: sem a chave de assinatura a release não pode ser publicada.")
    if not arquivos:
        sys.exit("Nenhum arquivo para assinar.")
    chave = Ed25519PrivateKey.from_private_bytes(base64.b64decode(semente))
    for caminho in arquivos:
        assinar(chave, caminho)
        print("assinado:", caminho)


if __name__ == "__main__":
    main(sys.argv[1:])
