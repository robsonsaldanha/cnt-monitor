"""Testes do controle de ventoinhas com canais falsos (nao toca em hardware).

    python build/teste_ventoinhas.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import ventoinhas as V  # noqa: E402

CID = "/lpc/0/control/1"
RPM = "/lpc/0/fan/1"
TEMP = "/cpu/0/temperature/0"


class Ctl:
    def __init__(self, hw, cid):
        self.hw, self.cid = hw, cid


class SensFalso:
    def __init__(self):
        self.lock = threading.RLock()
        self.hw = {CID: 40.0}          # o que o "hardware" esta fazendo agora
        self.temp = 50.0
        self.lista = []
        self.atualizar()

    def atualizar(self):
        self.lista = [
            {"id": CID, "hw": "Placa-mãe", "sub": "", "tipo_hw": "Placa-mãe", "nome": "Fan #1", "tipo_en": "Control", "valor": self.hw[CID]},
            {"id": RPM, "hw": "Placa-mãe", "sub": "", "tipo_hw": "Placa-mãe", "nome": "Fan #1", "tipo_en": "Fan", "valor": 1200.0},
            {"id": TEMP, "hw": "CPU", "sub": "", "tipo_hw": "Processador", "nome": "CPU Package", "tipo_en": "Temperature", "valor": self.temp},
        ]


class VentFalsa(V.Ventoinhas):
    def __init__(self, sens):
        self.cfg = V.validar({})
        super().__init__(sens, lambda: self.cfg, self._grava)
        self.escritas, self.soltas = [], 0

    def _grava(self, c):
        self.cfg = V.validar(c)

    def _controles(self):
        return {CID: object()}

    def _escrever(self, ctl, pct):
        self.escritas.append(pct)
        self.sens.hw[CID] = pct

    def _soltar(self, ctl):
        self.soltas += 1
        self.sens.hw[CID] = 40.0


def passo(v, s):
    s.atualizar()
    v.ciclo()


def teste_validar():
    c = V.validar({"limite_temp": 500, "minimo": -3, "canais": {
        CID: {"modo": "curva", "curva": [[70, 80], [30, 20], [30, 25], ["x", 1]], "valor": 999, "minimo": ""},
        "sem-barra": {"modo": "manual"}}})
    assert c["limite_temp"] == 100 and c["minimo"] == 0
    assert list(c["canais"]) == [CID]
    k = c["canais"][CID]
    assert k["valor"] == 100 and k["minimo"] is None
    assert [p[0] for p in k["curva"]] == [30, 70]
    assert V.validar({"canais": {CID: {"modo": "x"}}})["canais"][CID]["modo"] == "auto"
    assert len(V.validar_curva([[1, 1]])) >= 2     # curva com 1 ponto volta ao padrao


def teste_curva():
    c = [[30, 20], [50, 40], [70, 100]]
    assert V.alvo_curva(c, 10) == 20 and V.alvo_curva(c, 90) == 100
    assert V.alvo_curva(c, 40) == 30 and V.alvo_curva(c, 60) == 70
    a, t = V.alvo_com_histerese(c, 51, 50, 40, 3)
    assert (a, t) == (40, 50)                       # variou menos que a histerese: mantem
    a, t = V.alvo_com_histerese(c, 54, 50, 40, 3)
    assert t == 54 and a > 40


def teste_externo():
    assert V.externo(50, 50, 0) == (0, False)
    assert V.externo(53, 50, 0) == (0, False)       # dentro da tolerancia
    e, ced = V.externo(80, 50, 0)
    assert (e, ced) == (1, False)
    assert V.externo(80, 50, e)[1] is True          # 2 leituras seguidas
    assert V.externo(50, 50, 1) == (0, False)       # voltou: zera
    assert V.par_ventoinha("/lpc/0/control/3") == "/lpc/0/fan/3" and V.par_ventoinha("/x") is None


def teste_manual_e_minimo():
    s = SensFalso(); v = VentFalsa(s)
    assert v.definir(CID, {"modo": "manual", "valor": 10})[0]
    assert v.escritas[-1] == 30                     # minimo geral 30 vale
    assert v.definir(CID, {"modo": "manual", "valor": 70, "minimo": 20})[0]
    assert v.escritas[-1] == 70
    n = len(v.escritas)
    for _ in range(4):
        passo(v, s)
    assert len(v.escritas) == n and v.soltas == 0   # estavel: nao reescreve
    assert v.canais()["canais"][0]["modo"] == "manual"


def teste_outro_programa_assume():
    s = SensFalso(); v = VentFalsa(s)
    v.definir(CID, {"modo": "manual", "valor": 70})
    passo(v, s)
    s.hw[CID] = 20.0                                # outro programa mexeu
    passo(v, s)
    assert v.cfg["canais"][CID]["modo"] == "manual"  # ainda nao: 1 leitura so
    passo(v, s)
    assert v.cfg["canais"][CID]["modo"] == "auto"    # cedeu
    assert v.soltas == 0                             # sem escrever nada em cima do outro
    n = len(v.escritas)
    s.hw[CID] = 55.0
    for _ in range(3):
        passo(v, s)
    assert len(v.escritas) == n                      # nao briga
    assert v.canais()["canais"][0]["pct"] == 55.0    # mostra o valor do outro
    assert v.definir(CID, {"modo": "manual", "valor": 60})[0] and v.escritas[-1] == 60   # usuario retoma aqui


def teste_curva_e_emergencia():
    s = SensFalso(); v = VentFalsa(s)
    v.definir(CID, {"modo": "curva", "curva": [[30, 20], [60, 60], [80, 100]], "sensor": TEMP, "minimo": 0})
    s.temp = 60
    passo(v, s)
    assert abs(v.escritas[-1] - 60) < 0.1
    s.temp = 61
    passo(v, s)
    n = len(v.escritas)
    assert n == 1                                    # histerese: 1 grau nao reescreve
    s.temp = 90
    passo(v, s)
    assert v.soltas == 1 and v.canais()["emergencia"]   # passou do limite: devolveu ao automatico
    s.temp = 80
    passo(v, s)
    assert v.canais()["emergencia"]                  # ainda nao caiu 10 graus
    s.temp = 70
    passo(v, s)
    assert not v.canais()["emergencia"]
    passo(v, s)
    assert v.escritas[-1] > 60                       # retomou a curva


def teste_apelido_nao_toca_hardware_e_liberar():
    s = SensFalso(); v = VentFalsa(s)
    assert v.definir(CID, {"apelido": "Frontal"})[0]
    assert v.cfg["canais"][CID]["apelido"] == "Frontal" and not v.escritas and v.soltas == 0
    v.definir(CID, {"modo": "manual", "valor": 50})
    v.liberar()
    assert v.soltas == 1
    assert not V.Ventoinhas(s, lambda: V.validar({}), lambda c: None).definir("/nao/existe", {"modo": "manual"})[0]


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes das ventoinhas passaram")
