"""Teste de servidor-vps/baixar-instalador.py: só publica o que vem assinado (pasta de recebimento do CI).

    python build/teste_publicar.py
"""
import base64
import importlib.util
import json
import os
import sys
import tempfile

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PASTA = tempfile.mkdtemp(prefix="mpc-teste-pub-")
FONTE = os.path.join(tempfile.mkdtemp(prefix="mpc-teste-chave-"), "atualizacao.py")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

PRIV, OUTRA = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
PUB = base64.b64encode(PRIV.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
with open(FONTE, "w", encoding="utf-8") as f:
    f.write(f'PUBLICA = "{PUB}"\n')
os.environ["DOWNLOADS"], os.environ["CNT_FONTE_CHAVE"] = PASTA, FONTE
os.environ["CNT_GITHUB"] = "0"          # o teste não chama a internet

spec = importlib.util.spec_from_file_location("baixar_instalador", os.path.join(RAIZ, "servidor-vps", "baixar-instalador.py"))
bi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bi)


def ok(c, m):
    print(("ok   " if c else "FALHA ") + m)
    if not c:
        sys.exit(1)


def receber(nome, conteudo, chave=PRIV, sig=True):
    os.makedirs(bi.RECEBIDOS, exist_ok=True)
    with open(os.path.join(bi.RECEBIDOS, nome), "wb") as f:
        f.write(conteudo)
    if sig:
        import hashlib
        sha = hashlib.sha256(conteudo).hexdigest()
        ass = base64.b64encode(chave.sign(f"cnt-monitor-release-v1\n{nome}\n{sha}\n".encode())).decode()
        with open(os.path.join(bi.RECEBIDOS, nome + ".sig"), "w", encoding="utf-8") as f:
            f.write(ass)


def manifesto():
    with open(os.path.join(PASTA, "ultima.json"), encoding="utf-8") as f:
        return json.load(f)


# ---- assinatura válida: publica
receber("CNTMonitor-Setup-9.0.0.exe", b"exe-nove")
receber("cnt-monitor_9.0.0_amd64.deb", b"deb-nove")
bi.main()
m = manifesto()
ok(m["versao"] == "9.0.0" and m["arquivo"] == "CNTMonitor-Setup-9.0.0.exe" and m["assinatura"].endswith(".sig") and m["linux"]["arquivo"] == "cnt-monitor_9.0.0_amd64.deb", "instalador assinado é publicado, com o .deb e as assinaturas no manifesto")
ok(open(os.path.join(PASTA, "CNTMonitor-Setup-9.0.0.exe"), "rb").read() == b"exe-nove" and os.path.exists(os.path.join(PASTA, "CNTMonitor-Setup-9.0.0.exe.sig")), "o arquivo publicado é o recebido, com a .sig ao lado")
ok(os.listdir(bi.RECEBIDOS) == [], "a pasta de recebimento fica vazia")
import hashlib
ok(m["sha256"] == hashlib.sha256(b"exe-nove").hexdigest() and m["tamanho"] == 8, "sha256 e tamanho do manifesto são os do arquivo")

# ---- assinatura de outra chave: recusado, nada muda
receber("CNTMonitor-Setup-9.1.0.exe", b"exe-falso", chave=OUTRA)
bi.main()
ok(manifesto()["versao"] == "9.0.0" and not os.path.exists(os.path.join(PASTA, "CNTMonitor-Setup-9.1.0.exe")), "assinado por outra chave não é publicado")
ok(os.listdir(bi.RECEBIDOS) == [], "e o arquivo recusado é apagado do recebimento")

# ---- arquivo adulterado depois de assinado: recusado
receber("CNTMonitor-Setup-9.2.0.exe", b"exe-bom")
with open(os.path.join(bi.RECEBIDOS, "CNTMonitor-Setup-9.2.0.exe"), "wb") as f:
    f.write(b"exe-trocado")
bi.main()
ok(manifesto()["versao"] == "9.0.0", "arquivo trocado depois de assinado não é publicado")

# ---- sem assinatura: espera (a .sig pode estar chegando) e não publica
receber("CNTMonitor-Setup-9.3.0.exe", b"exe-sem-sig", sig=False)
bi.main()
ok(manifesto()["versao"] == "9.0.0" and os.path.exists(os.path.join(bi.RECEBIDOS, "CNTMonitor-Setup-9.3.0.exe")), "sem .sig nada é publicado e o arquivo espera a assinatura chegar")
with open(os.path.join(bi.RECEBIDOS, "CNTMonitor-Setup-9.3.0.exe.sig"), "w") as f:
    f.write(base64.b64encode(PRIV.sign(f"cnt-monitor-release-v1\nCNTMonitor-Setup-9.3.0.exe\n{hashlib.sha256(b'exe-sem-sig').hexdigest()}\n".encode())).decode())
bi.main()
ok(manifesto()["versao"] == "9.3.0", "quando a .sig chega, publica")

# ---- nomes que não são instalador são ignorados; versões antigas são podadas (3 mais novas)
receber("qualquer-coisa.exe", b"x")
receber("../fora.exe", b"x") if False else None
for v in ("9.4.0", "9.5.0", "9.6.0"):
    receber(f"CNTMonitor-Setup-{v}.exe", b"exe-" + v.encode())
    bi.main()
ok(manifesto()["versao"] == "9.6.0", "sempre publica a versão mais nova")
exes = sorted(x for x in os.listdir(PASTA) if x.endswith(".exe"))
ok(len(exes) == 3 and exes[-1] == "CNTMonitor-Setup-9.6.0.exe", f"mantém só as 3 versões mais novas ({exes})")
ok(all(os.path.exists(os.path.join(PASTA, x + ".sig")) for x in exes) and not any(x.endswith(".sig") and x[:-4] not in exes and x.startswith("CNTMonitor") for x in os.listdir(PASTA)), "as .sig das versões podadas também saem")
ok(os.path.exists(os.path.join(bi.RECEBIDOS, "qualquer-coisa.exe")), "arquivo fora do padrão de nome não é tocado nem publicado")
receber("MonitorDoPC-Setup-9.9.9.exe", b"nome-antigo")
bi.main()
ok(not any(x.startswith("MonitorDoPC-Setup-") for x in os.listdir(bi.RECEBIDOS)) and not any(x.startswith("MonitorDoPC-Setup-") for x in os.listdir(PASTA)), "o instalador de nome antigo é descartado do recebimento e não é publicado")
print("publicação assinada na VPS ok")
