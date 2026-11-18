"""Testes do controle de som: validação dos pedidos da tela, leitura de formato e a conversa com o ajudante
(com um ajudante falso, sem mexer no som deste PC).

    python build/teste_audio.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import audio  # noqa: E402

# Ajudante falso: responde a "listar" e ecoa as ações; "morrer" derruba o processo; "calar" não responde.
FALSO = r"""
import sys, json, os
for linha in sys.stdin:
    p = json.loads(linha)
    if p["op"] == "volume" and p["valor"] == 13:
        os._exit(1)
    if p["op"] == "volume" and p["valor"] == 14:
        continue
    r = {"saidas": [], "entradas": [], "programas": [], "pid": os.getpid()} if p["op"] == "listar" else {"ok": True, "eco": p}
    sys.stdout.write(json.dumps(r) + "\n"); sys.stdout.flush()
"""


def teste_validar_aceita():
    ped, erro = audio.validar({"tipo": "audio_volume", "id": "{0.0.0}.{abc}", "valor": "37.6"})
    assert erro is None and ped == {"op": "volume", "id": "{0.0.0}.{abc}", "valor": 38}
    ped, _ = audio.validar({"tipo": "audio_app_mudo", "id": "x", "app": "exe:Discord", "mudo": 1})
    assert ped == {"op": "app_mudo", "id": "x", "app": "exe:Discord", "mudo": True}
    ped, _ = audio.validar({"tipo": "audio_canal", "id": "x", "canal": 1, "valor": 0})
    assert ped["canal"] == 1 and ped["valor"] == 0
    ped, _ = audio.validar({"tipo": "audio_db", "id": "x", "valor": -6.25})
    assert ped["valor"] == -6.2 or ped["valor"] == -6.3
    assert audio.validar({"tipo": "audio_padrao", "id": "x"})[0]["papel"] == "tudo"
    assert audio.validar({"tipo": "audio_reducao", "valor": 3})[0] == {"op": "reducao", "valor": 3}
    assert audio.validar({"tipo": "audio_app_volume", "id": "x", "app": "in:12", "valor": 50})[1] is None


def teste_validar_recusa():
    ruins = [
        {"tipo": "audio_formatar", "id": "x"},
        {"tipo": "audio_volume", "id": "x", "valor": 101},
        {"tipo": "audio_volume", "id": "x", "valor": -1},
        {"tipo": "audio_volume", "id": "x", "valor": "nan"},
        {"tipo": "audio_volume", "id": "x", "valor": True},
        {"tipo": "audio_volume", "id": "", "valor": 5},
        {"tipo": "audio_volume", "id": "a\nb", "valor": 5},
        {"tipo": "audio_volume", "id": "x" * 600, "valor": 5},
        {"tipo": "audio_volume", "id": 7, "valor": 5},
        {"tipo": "audio_canal", "id": "x", "canal": 32, "valor": 5},
        {"tipo": "audio_canal", "id": "x", "canal": "1", "valor": 5},
        {"tipo": "audio_db", "id": "x", "valor": 999},
        {"tipo": "audio_padrao", "id": "x", "papel": "todos"},
        {"tipo": "audio_app_volume", "id": "x", "app": "pid:1; rm -rf", "valor": 5},
        {"tipo": "audio_app_volume", "id": "x", "app": "exe:a\nb", "valor": 5},
        {"tipo": "audio_reducao", "valor": 4},
        {"tipo": "audio_reducao", "valor": False},
    ]
    for a in ruins:
        ped, erro = audio.validar(a)
        assert ped is None and erro, a


def teste_formato():
    assert audio._ler_spec("s16le 2ch 48000Hz") == {"hz": 48000, "bits": 16, "canais": 2, "float": False}
    assert audio._ler_spec("float32le 6ch 44100Hz") == {"hz": 44100, "bits": 32, "canais": 6, "float": True}
    assert audio._ler_spec("") is None
    assert audio._formato_texto({"hz": 48000, "bits": 24, "canais": 2}) == "48 kHz, 24 bits, 2 canais"
    if audio.WIN:
        import struct
        ext = struct.pack("<HHIIHHHHI", 0xFFFE, 2, 48000, 384000, 8, 32, 22, 24, 3) + struct.pack("<I", 3) + bytes(12)
        assert audio._ler_formato(ext) == {"hz": 48000, "bits": 24, "canais": 2, "float": True}
        pcm = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
        assert audio._ler_formato(pcm) == {"hz": 44100, "bits": 16, "canais": 2, "float": False}


def teste_conversa_com_o_ajudante():
    g = audio.Audio([sys.executable, "-c", FALSO])
    try:
        e = g.estado()
        assert e["saidas"] == [] and "erro" not in e and e["sistema"] in ("windows", "linux") and len(e["reducoes"]) == 4
        pid = e["pid"]
        r = g.acao({"tipo": "audio_mudo", "id": "dev", "mudo": True})
        assert r["ok"] is True
        assert g.acao({"tipo": "audio_volume", "id": "dev", "valor": 500}) == {"ok": False, "msg": "O volume vai de 0 a 100."}
        # o ajudante morre no meio do pedido: abre outro e repete (a ação é idempotente)
        assert g.acao({"tipo": "audio_volume", "id": "dev", "valor": 13})["ok"] is False
        assert g.estado()["pid"] != pid
    finally:
        g.parar()


def teste_ajudante_mudo_nao_trava():
    audio.TEMPO, antes = 0.5, audio.TEMPO
    g = audio.Audio([sys.executable, "-c", FALSO])
    try:
        assert "erro" not in g.estado()
        r = g.acao({"tipo": "audio_volume", "id": "dev", "valor": 14})
        assert r["ok"] is False and "não respondeu" in r["msg"]
        assert "erro" not in g.estado()             # e volta a funcionar depois
    finally:
        audio.TEMPO = antes
        g.parar()


def teste_ajudante_de_verdade_responde():
    """O `servico.py --audio` sobe, entende o protocolo e devolve a lista (ou o erro claro, sem placa de som)."""
    g = audio.Audio([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "servico.py"), "--audio"])
    try:
        e = g.estado()
        assert isinstance(e.get("saidas", []), list) and (("saidas" in e) or e.get("erro")), e
        r = g.acao({"tipo": "audio_volume", "id": "{nao-existe}", "valor": 10})
        assert r["ok"] is False and r["msg"], r
    finally:
        g.parar()


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes do áudio passaram")
