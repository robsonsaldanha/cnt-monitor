"""Testes de quando o medidor de FPS (PresentMon) liga e desliga, com um FPS falso.

    python build/teste_jogo.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import jogo  # noqa: E402


class FPSFalso:
    def __init__(self):
        self.rodando = False
        self.ult_inicio = 0.0
        self.inicios = 0

    def iniciar(self):
        self.inicios += 1
        self.ult_inicio = jogo.time.time()
        self.rodando = True
        return True

    def parar(self):
        self.rodando = False


def _modo(ativo=False):
    m = jogo.ModoJogo.__new__(jogo.ModoJogo)   # sem __init__: não lê o estado salvo do PC
    m.fps, m.ativo = FPSFalso(), ativo
    return m


def teste_mini_painel_liga_fps_sem_modo_jogo():
    m = _modo()
    m.ajustar_fps({"mini_ativo": True, "mini_itens": ["cpu", "fps"]})
    assert m.fps.rodando


def teste_desliga_quando_ninguem_precisa():
    m = _modo()
    m.ajustar_fps({"mini_ativo": True, "mini_itens": ["fps"]})
    m.ajustar_fps({"mini_ativo": True, "mini_itens": ["cpu"]})
    assert not m.fps.rodando
    m.ajustar_fps({"mini_ativo": False, "mini_itens": ["fps"]})
    assert not m.fps.rodando


def teste_modo_jogo_respeita_opcao_de_fps():
    m = _modo(ativo=True)
    m.ajustar_fps({"jogo_fps": False, "mini_ativo": False})
    assert not m.fps.rodando
    m.ajustar_fps({"jogo_fps": True, "mini_ativo": False})
    assert m.fps.rodando


def teste_nao_relanca_logo_depois_de_cair():
    m = _modo()
    c = {"mini_ativo": True, "mini_itens": ["fps"]}
    m.ajustar_fps(c)
    m.fps.rodando = False          # o PresentMon caiu
    m.ajustar_fps(c)
    assert m.fps.inicios == 1
    m.fps.ult_inicio -= 31
    m.ajustar_fps(c)
    assert m.fps.inicios == 2 and m.fps.rodando


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes do FPS passaram")
