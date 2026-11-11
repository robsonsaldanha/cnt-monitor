"""Testes do monitoramento de rede por programa (RTP, sondas TCP/UDP, teste até um destino e o .log).

    python build/teste_rede_programa.py
"""
import os
import sys
import json
import shutil
import socket
import struct
import tempfile
import threading
import time

DADOS = tempfile.mkdtemp(prefix="mpc-teste-rprog-")
os.environ["MONITORPC_DADOS"] = DADOS   # antes de importar: nada toca na pasta de dados de verdade
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import qualidade as Q, rede_programa as R  # noqa: E402


def rtp(pt, seq, ts, ssrc=0x1234ABCD):
    return struct.pack(">BBHII", 0x80, pt, seq, ts, ssrc)


def teste_ler_rtp_separa_stun_dtls_rtcp():
    assert R.ler_rtp(rtp(0, 7, 160)) == (0, 7, 160, 0x1234ABCD)
    assert R.ler_rtp(rtp(111 | 0x80, 1, 2))[0] == 111                  # marcador ligado não muda o payload type
    assert R.ler_rtp(b"\x00\x01" + b"\x00" * 18) is None                # STUN
    assert R.ler_rtp(b"\x16\xfe\xfd" + b"\x00" * 20) is None            # DTLS
    assert R.ler_rtp(struct.pack(">BBHII", 0x80, 200, 1, 2, 3)) is None  # RTCP (sender report)
    assert R.ler_rtp(b"\x80\x00\x00") is None


def teste_rtp_perda_ordem_e_jitter_rfc3550():
    # G.711 (8000 Hz): um pacote a cada 20 ms com 160 de timestamp; chegada alternando +5 ms e -5 ms
    f = None
    chegou = 0
    for i, seq in enumerate([s for s in range(100, 200) if s not in (110, 111, 150)] + [111]):
        ts = (seq - 100) * 160
        chegada = 10 + (seq - 100) * 0.020 + (0.005 if i % 2 else -0.005)
        if f is None:
            f = R.FluxoRtp(0, seq, ts, chegada)
        else:
            f.add(seq, ts, chegada)
        chegou += 1
    r = f.resumo()
    assert r["codec"].startswith("G.711") and r["relogio"] == 8000
    assert r["esperados"] == 100 and r["recebidos"] == chegou and r["perdidos"] == 2   # 110 e 150; o 111 veio atrasado
    assert r["fora_de_ordem"] == 1 and f.valido()
    assert 7 <= r["jitter_ms"] <= 10.5, r          # |D| = 10 ms em quase todo par: J converge para ~10 ms
    f.add(199, 99 * 160, 99)                        # repetido
    assert f.resumo()["duplicados"] == 1


def teste_rtp_volta_da_numeracao_e_relogio_estimado():
    f = R.FluxoRtp(111, 65530, 0, 0.0)              # Opus: payload dinâmico, relógio de 48 kHz
    for k in range(1, 120):
        f.add((65530 + k) & 0xFFFF, (k * 960) & 0xFFFFFFFF, k * 0.020)
    r = f.resumo()
    assert r["relogio"] == 48000 and "Opus" in r["codec"]
    assert r["esperados"] == 120 and r["perdidos"] == 0 and r["fora_de_ordem"] == 0
    assert r["jitter_ms"] is not None and r["jitter_ms"] < 0.5
    lixo = R.FluxoRtp(96, 1, 0, 0.0)                # "RTP" que não anda em ordem: não é RTP de verdade
    for k in range(40):
        lixo.add((k * 7919) & 0xFFFF, k, k * 0.01)
    assert not lixo.valido()


def teste_ipv4_bruto():
    origem, destino = socket.inet_aton("200.1.2.3"), socket.inet_aton("192.168.0.10")
    cab = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 48, 1, 0, 64, 17, 0, origem, destino)
    udp = struct.pack(">HHHH", 3478, 50000, 28, 0)
    p = cab + udp + rtp(0, 5, 800)
    assert R.analisar_ipv4(p) == (origem, destino, 3478, 50000, rtp(0, 5, 800))
    assert R.analisar_ipv4(cab[:9] + b"\x06" + cab[10:] + udp + b"x" * 12) is None           # TCP
    frag = cab[:6] + struct.pack(">H", 0x0010) + cab[8:]
    assert R.analisar_ipv4(frag + udp + b"x" * 12) is None                                    # pedaço de fragmento
    assert R.analisar_ipv4(b"\x60" + b"\x00" * 60) is None                                    # IPv6


def teste_sondas_tcp_e_udp_de_verdade():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    r = Q.sonda_tcp("127.0.0.1", srv.getsockname()[1])
    assert r["tipo"] == "aberta" and r["rtt"] is not None
    fechada = srv.getsockname()[1]
    srv.close()
    assert Q.sonda_tcp("127.0.0.1", fechada, 1)["tipo"] in ("recusada", "silencio")

    eco = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    eco.bind(("127.0.0.1", 0))

    def responder():
        dados, de = eco.recvfrom(100)
        eco.sendto(dados, de)
    threading.Thread(target=responder, daemon=True).start()
    r = Q.sonda_udp("127.0.0.1", eco.getsockname()[1], 2, b"CNTE12345678", confere=b"CNTE12345678")
    assert r["tipo"] == "resposta" and r["rtt"] is not None, r
    mudo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)       # existe mas nunca responde: silêncio, não perda
    mudo.bind(("127.0.0.1", 0))
    assert Q.sonda_udp("127.0.0.1", mudo.getsockname()[1], 0.4) == {"tipo": "silencio", "rtt": None}
    livre = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    livre.bind(("127.0.0.1", 0))
    porta_livre = livre.getsockname()[1]
    livre.close()
    r = Q.sonda_udp("127.0.0.1", porta_livre, 1)
    if os.name == "nt":                                            # o "porta inacessível" chega como resposta do destino
        assert r["tipo"] == "porta_inacessivel" and r["rtt"] is not None, r
    eco.close()
    mudo.close()


def teste_jitter_movel_e_resumo_udp():
    assert Q.jitter_movel([10, 12, None, 16, 10], 10) == [None, 2.0, 2.0, 3.0, 4.0]
    r = Q.resumo_udp([None, None, None], ["silencio"] * 3)
    assert r["sem_devolucao"] and r["perda"] is None                 # nunca respondeu: não é perda
    r = Q.resumo_udp([10, None, 12, None], ["resposta", "silencio", "porta_inacessivel", "silencio"])
    assert not r["sem_devolucao"] and r["perda"] == 50.0             # responde às vezes: o resto é perda


def teste_teste_ate_um_destino_com_referencia_da_vps():
    ecos = []
    t = Q.TesteDestino(udp=lambda ip, porta: {"tipo": "silencio", "rtt": None},
                       eco=lambda seq: ecos.append(seq) or {"tipo": "resposta", "rtt": 30.0 + seq % 2})
    t.INTERVALO = 0.05
    for ruim in ((["x"], "udp", 5060, 5), ("1.1.1.1", "sctp", 1, 5), ("1.1.1.1", "udp", 0, 5), ("1.1.1.1", "tcp", 70000, 5)):
        try:
            t.iniciar(*ruim)
        except ValueError:
            continue
        raise AssertionError(ruim)
    ok, _ = t.iniciar("127.0.0.1", "udp", 5060, 5)
    assert ok and not t.iniciar("127.0.0.1", "udp", 5060, 5)[0]          # um de cada vez
    time.sleep(0.4)
    t.cancelar()
    for _ in range(100):
        if t.ver()["fase"] != "medindo":
            break
        time.sleep(0.05)
    v = t.ver()
    assert v["fase"] == "cancelado" and v["resultado"]["sem_devolucao"] and v["resultado"]["perda"] is None
    assert v["serie"] and all(len(x) == 4 for x in v["serie"])
    t2 = Q.TesteDestino(udp=lambda ip, porta: {"tipo": "silencio", "rtt": None},
                        eco=lambda seq: {"tipo": "resposta", "rtt": 30.0})
    t2.INTERVALO = 0.01
    t2.iniciar("127.0.0.1", "udp", 5060, 5)
    t2.estado["inicio"] -= 4.9                                            # acaba logo
    for _ in range(200):
        if t2.ver()["fase"] == "pronto":
            break
        time.sleep(0.05)
    v = t2.ver()
    assert v["fase"] == "pronto" and v["vps"]["respondeu"] and v["vps"]["mediana"] == 30.0   # referência separada


class Relogio:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def _monitor(pings, tcp=None, udp=None):
    rel = Relogio()
    ecos = []
    conns = [{"pid": 10, "proto": "TCP", "lip": "192.168.0.10", "lport": 50000, "rip": "8.8.8.8", "rport": 443, "estado": "Conectado"},
             {"pid": 11, "proto": "UDP", "lip": "0.0.0.0", "lport": 40000, "rip": "", "rport": 0, "estado": "—"}]
    retr = {"n": 0}

    def estats(lip, lport, rip, rport):
        retr["n"] += 2
        return {"rtt": 25, "retransmissoes": retr["n"]}
    m = R.Monitor(lambda nome: {"Navegador": [10], "Softphone": [11]}.get(nome, []),
                  lambda: [("Navegador", 1000, 2000), ("Softphone", 50, 50), ("Steam", 5_000_000, 100), ("Parado", 0, 0)],
                  lambda: conns, ping=lambda ip: pings.get(ip), tcp=tcp, udp=udp,
                  eco=lambda seq: ecos.append(seq) or {"tipo": "resposta", "rtt": 40.0},
                  estats=estats, captura=False, pasta=os.path.join(DADOS, "progs"), maquina=lambda: {"maquina": "PC-TESTE"},
                  agora=rel, automatico=False)
    return m, rel, ecos


def _fluxos(pid, ip, porta, udp, env, rec, var=0.0, n=0, gap=0.0):
    return (pid, ip, porta, 50000 if not udp else 40000, udp, env, rec, env // 100, rec // 100, var, n, gap)


def teste_monitor_dois_programas_com_concorrencia_e_log():
    usados = []
    m, rel, ecos = _monitor({"8.8.8.8": 12.0}, udp=lambda ip, porta: usados.append((ip, porta)) or {"tipo": "silencio", "rtt": None})
    assert m.adicionar("Navegador")[0] and m.adicionar("Softphone")[0]
    assert m.adicionar("Navegador") == (True, "Navegador já está sendo monitorado.")
    for x in ("A", "B"):
        m.adicionar(x)
    assert not m.adicionar("C")[0]                                    # até 4 ao mesmo tempo
    m.remover("A")
    m.remover("B")
    m.passo(rel.t)                                                    # primeiro segundo: monta pid -> programa
    for s in range(30):
        rel.t += 1
        m.add_fluxos(rel.t, [_fluxos(10, "8.8.8.8", 443, False, 10_000, 50_000),
                             _fluxos(11, "200.1.1.1", 10000, True, 8000, 8000, var=40.0, n=4, gap=25.0),
                             _fluxos(99, "1.1.1.1", 443, False, 999, 999)])          # outro programa: não entra
        porta = 40000
        for k in range(50):                                           # chamada chegando: RTP pela porta do softphone
            seq = s * 50 + k
            m._pacote_udp(rel.t + k * 0.02, socket.inet_aton("200.1.1.1"), b"", 10000, porta, rtp(8, seq, seq * 160))
        m.passo(rel.t)
    v = m.ao_vivo()
    nav = next(p for p in v["programas"] if p["nome"] == "Navegador")
    sp = next(p for p in v["programas"] if p["nome"] == "Softphone")
    rn, rs = nav["resumo"], sp["resumo"]
    assert rn["segundos"] == 31 and rn["rec_pico"] == 50_000 and rn["tcp_total"] > 0 and rn["udp_total"] == 0
    assert rn["latencia"]["mediana"] == 12.0 and rn["latencia"]["perda"] == 0 and rn["tcp_rtt"] == 25
    assert rn["tcp_retransmissoes"] == 2 * 30                         # só as novas: o 1º segundo marca a base
    assert rs["udp_total"] > 0 and rs["jitter_chegada"] == 10.0 and rs["maior_silencio"] == 25.0
    assert sp["rtp"] and sp["rtp"][0]["codec"].startswith("G.711 A-law") and sp["rtp"][0]["perdidos"] == 0
    assert nav["resumo"]["mos"] > 4
    assert rs["mos"] is None          # sem nenhuma latência medida (não responde a ping, UDP mudo, sem TCP): não inventa nota
    assert v["concorrencia"][-1][1] == {"Steam": 5_000_100}            # quem competia (e só quem usou a rede)
    # destino UDP que não responde a ping: passa a UDP e, sem nenhuma devolução, vira \"sem devolução\" e usa a VPS
    d = {x["ip"]: x for x in sp["destinos"]}["200.1.1.1"]
    assert d["modo"] == "sem_devolucao" and usados and usados[0] == ("200.1.1.1", 10000)
    assert ecos and v["vps"]["mediana"] == 40.0
    assert len(usados) <= 30 // R.SONDA_LENTA_S + 1                    # UDP/TCP só a cada 5 s, não a cada segundo
    nome, log = m.exportar("atual")
    assert nome.endswith(".log") and "# == Softphone ==" in log and "RTP 1234abcd" in log and "PC-TESTE" in log
    ident = m.parar()
    assert ident and m.ao_vivo()["ativo"] is False and [x["id"] for x in m.lista()] == [ident]
    nome, log = m.exportar(ident)
    linhas = [l for l in log.splitlines() if not l.startswith("#")]
    cab = linhas[0].split("\t")
    assert cab[0] == "programa" and cab[1:-1] == list(R.COLUNAS) and cab[-1] == "outros_programas_Bps"
    assert len(linhas) == 1 + 31 * 2 and all(len(l.split("\t")) == len(cab) for l in linhas[1:])
    assert any("Steam=5000100" in l for l in linhas[1:])
    a = m.abrir(ident)
    assert [p["nome"] for p in a["programas"]] == ["Navegador", "Softphone"] and a["programas"][1]["rtp"]
    for ruim in ("../../x", "", "atual", "20261111-000000"):
        assert m.abrir(ruim) is None and not m.apagar(ruim)
    assert m.apagar(ident) and m.lista() == []


def teste_monitor_troca_para_tcp_quando_nao_responde_ping():
    tcps = []
    m, rel, _ = _monitor({}, tcp=lambda ip, porta: tcps.append(porta) or {"tipo": "aberta", "rtt": 33.0})
    m.adicionar("Navegador")
    m.passo(rel.t)
    for s in range(12):
        rel.t += 1
        m.add_fluxos(rel.t, [_fluxos(10, "8.8.8.8", 443, False, 1000, 1000)])
        m.passo(rel.t)
    p = m.ao_vivo()["programas"][0]
    assert {x["ip"]: x for x in p["destinos"]}["8.8.8.8"]["modo"] == "tcp" and tcps and tcps[0] == 443
    serie = p["serie"]
    assert serie[1][R.C["sonda_sem_resposta"]] == 1                                # ping sem resposta conta como falha
    assert any(l[R.C["sonda"]] == "tcp" and l[R.C["latencia_ms"]] == 33.0 for l in serie)
    m.parar()


def teste_reduzir_e_mos_janela():
    C = R.C
    base = [0] * len(R.COLUNAS)
    ls = []
    for i in range(10):
        l = list(base)
        l[0], l[C["env_Bps"]], l[C["latencia_ms"]], l[C["sonda"]] = 100 + i, 1000 * i, 20.0, "icmp"
        l[C["sonda_sem_resposta"]], l[C["maior_silencio_ms"]], l[C["rtp_jitter_ms"]], l[C["rtp_perdidos"]] = 0, i, None, None
        l[C["jitter_chegada_ms"]] = 2.0
        ls.append(l)
    r = R.reduzir(ls, 5)
    assert len(r) == 2 and r[0][0] == 100 and r[0][C["env_Bps"]] == 2000 and r[0][C["maior_silencio_ms"]] == 4
    assert r[1][C["sonda"]] == "icmp"
    bom = R.mos_janela(ls)
    ls[3][C["sonda_sem_resposta"]], ls[3][C["latencia_ms"]] = 1, None
    assert bom > 4.3 and R.mos_janela(ls) < bom                      # perda nas sondas derruba a nota


if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("teste_"):
                f()
                print("ok", nome)
        print("todos os testes da rede por programa passaram")
    finally:
        shutil.rmtree(DADOS, ignore_errors=True)
