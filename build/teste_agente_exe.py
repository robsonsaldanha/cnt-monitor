"""Confere que o agente do acesso remoto empacotado abre e gera um convite válido.

    python build/teste_agente_exe.py dist/MonitorPCRemoto/MonitorPCRemoto.exe
"""
import os
import sys
import tempfile

os.environ["MONITORPC_DADOS"] = tempfile.mkdtemp(prefix="mpc-teste-agente-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import remoto  # noqa: E402

g = remoto.Gerente([os.path.abspath(sys.argv[1])])
try:
    r = g.pedir(stun=False)
    assert r == (True, ""), r
    e = g.estado()
    assert e["estado"] == "convite" and len(e["senha"]) == 14, e
    assert "a=fingerprint:sha-256" in remoto.ler_convite(e["convite"])
    ok, msg = g.ordem("resposta", codigo="MPC2-lixo")
    assert not ok and "não é válido" in msg, (ok, msg)
finally:
    g.parar()
assert g.estado()["estado"] == "encerrada", g.estado()
print("agente empacotado ok")
