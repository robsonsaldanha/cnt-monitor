"""Testes do canal direto das chamadas pelo ID do compartilhamento online (src/monitorpc/p2p.py).
Sobe o ponto de encontro de verdade (servidor-vps/encontro.js, precisa do Node) e liga o PC B (Encontro + p2p.Servidor)
ao PC A (ClienteRemoto) só pelo ID, na mesma máquina.

    python build/teste_p2p.py
"""
import os
import sys
import time
import socket
import tempfile
import subprocess

DADOS = tempfile.mkdtemp(prefix="mpc-teste-p2p-")
os.environ["MONITORPC_DADOS"] = DADOS
RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(RAIZ, "src"))

from monitorpc import encontro, p2p, sinalizacao  # noqa: E402

CHAVE = "cnt_" + "k" * 32


class GerenteFalso:
    def estado(self):
        return {"estado": "parada"}

    def pedir(self, **_k):
        raise AssertionError("pedido de chamadas não pode abrir o acesso remoto")


def servidor():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    p = subprocess.Popen(["node", os.path.join(RAIZ, "servidor-vps", "encontro.js")], env=dict(os.environ, PORTA=str(porta), ECO_PORTA="0"),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "ouvindo" in p.stdout.readline()
    return p, f"http://127.0.0.1:{porta}/api"


def esperar(cond, prazo=10):
    fim = time.time() + prazo
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.1)
    return False


def main():
    proc, url = servidor()
    try:
        p2p.URL = url
        habilitado = [True]
        captura = {"ativo": False}
        grandes = [{"id": f"c{i}", "x": "y" * 3000} for i in range(60)]          # ~180 KB: passa pelos pedaços

        def atender(rota, metodo, q, corpo):
            if not habilitado[0]:
                return 403, {"erro": "O Call flow está desabilitado nesse computador."}
            if rota == "estado":
                return 200, {"ativo": captura["ativo"], "q": q}
            if rota == "grande":
                return 200, {"lista": grandes}
            if rota == "captura" and metodo == "POST":
                captura["ativo"] = bool(corpo.get("ligar"))
                return 200, {"ok": True, "ativo": captura["ativo"]}
            return 404, {"erro": "recurso não encontrado"}

        e = encontro.Encontro(GerenteFalso(), url=url, arquivo=os.path.join(DADOS, "id.json"))
        e.chamadas = p2p.Servidor(atender, lambda: CHAVE if habilitado[0] else "", e._enviar)
        e.iniciar()
        assert esperar(lambda: e.situacao == "online"), e.msg

        assert p2p.id_de("123 456 789") == "123456789" and p2p.id_de("192.168.0.20") == "" and p2p.id_de("12345") == ""

        a = sinalizacao.ClienteRemoto()
        ok, msg = a.conectar("999 999 999", CHAVE)
        assert not ok and "Não encontrei" in msg, msg                               # ID que não existe
        ok, msg = a.conectar(e.id, "cnt_errada_" + "x" * 20)
        assert not ok and "Chave inválida" in msg, msg                               # chave errada: B recusa e não prova nada
        assert not a.url, "tentativa falha não pode deixar o alvo ligado"

        ok, msg = a.conectar(" ".join([e.id[:3], e.id[3:6], e.id[6:]]), CHAVE)       # só o ID, sem IP
        assert ok, msg
        assert a.url == "id:" + e.id and a.rotulo() == "ID " + encontro.formatar_id(e.id)
        assert a.get("estado", busca="x")["q"] == {"busca": "x"}
        assert len(a.get("grande")["lista"]) == 60                                  # mensagem grande remontada
        assert a.post("captura", {"ligar": True})["ativo"] and captura["ativo"]     # A liga a captura de B
        try:
            a.get("nada")
            raise AssertionError("rota inexistente devia falhar")
        except RuntimeError as err:
            assert "não encontrado" in str(err)

        t0 = time.time()
        for _ in range(3):
            a.get("estado")
        assert time.time() - t0 < 3, "o canal aberto deve ser reaproveitado"
        assert len(p2p._canais) == 1

        habilitado[0] = False                                                       # Call flow desligado em B
        try:
            a.get("estado")
            raise AssertionError("com o Call flow desligado B deve recusar")
        except RuntimeError as err:
            assert "desabilitado" in str(err)

        # monitoramento: sincroniza pelo ID como pelo endereço
        habilitado[0] = True
        m = sinalizacao.Monitoramento(pasta=DADOS)
        ok, msg = m.adicionar("", e.id, CHAVE)
        assert ok, msg
        assert m.listar()[0]["nome"] == "ID " + encontro.formatar_id(e.id) and m.listar()[0]["url"] == "id:" + e.id
        assert m.adicionar("", e.id, CHAVE) == (False, "Esse computador já está cadastrado.")
        print("OK", flush=True)
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
    os._exit(0)
