"""Testes das estatísticas de rede (cálculos puros, série por IP, análise contínua com ping falso).
No Windows também testa o ping ICMP ao próprio PC, o HTTP local e, como administrador, o RTT TCP.

    python build/teste_rede.py
"""
import os
import sys
import json
import socket
import struct
import tempfile
import time
import threading
import http.server

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import qualidade as Q, rede  # noqa: E402


def perto(a, b, tol=0.01):
    return a is not None and abs(a - b) <= tol


def teste_resumo_latencia_jitter_perda():
    r = Q.resumo([10, 12, None, None, None, 14, 10])
    assert r["n"] == 7 and r["perdidos"] == 3 and perto(r["perda"], 42.86)
    assert r["perda_rajada"] == 3
    assert r["min"] == 10 and r["max"] == 14 and perto(r["media"], 11.5) and perto(r["mediana"], 11)
    assert perto(r["jitter"], 3)              # |12-10| e |10-14| (só pares seguidos sem perda)
    assert r["jitter_max"] == 4
    assert Q.resumo([])["perda"] is None and Q.resumo([None])["mediana"] is None


def teste_percentis():
    v = list(range(1, 101))
    assert perto(Q.percentil(v, 50), 50.5) and perto(Q.percentil(v, 95), 95.05) and perto(Q.percentil(v, 99), 99.01)
    assert Q.percentil([7], 99) == 7 and Q.percentil([], 50) is None


def teste_mos_e_pontuacoes():
    bom, r_bom = Q.mos(20, 2, 0)
    ruim, _ = Q.mos(300, 50, 10)        # R ≈ 39 -> MOS ≈ 2,4
    assert 4.3 <= bom <= 4.42 and r_bom > 90
    assert ruim < 3 and Q.mos(None, 1, 0) == (None, None)
    p = Q.pontuacoes(Q.resumo([20] * 30), bufferbloat=10, disponibilidade=100)
    assert p["tempo_real"] >= 95 and p["estabilidade"] == 100 and p["congestionamento"] == 95 and p["confiabilidade"] == 100
    assert p["saude"] == round((p["tempo_real"] + 100 + 95 + 100) / 4)
    assert Q.pontuacoes(Q.resumo([20, 200, 20, None, None, None, 200]), disponibilidade=90)["confiabilidade"] == 0


def teste_quedas_e_bufferbloat():
    fora = [False] * 10 + [True] * 5 + [False] * 10 + [True, True] + [False] * 3
    q = Q.quedas(fora)
    assert q["quedas"] == 1 and q["tempo_fora"] == 5 and perto(q["disponibilidade"], 83.333, 0.001)  # 2 s não é queda
    assert q["mttr"] == 5 and q["eventos"][0]["inicio"] == 10
    assert Q.quedas([False] * 5)["quedas"] == 0 and Q.quedas([])["disponibilidade"] is None
    linhas = [[i, 1000, 10] for i in range(20)] + [[20 + i, 10_000_000, 90] for i in range(20)]
    assert Q.bufferbloat(linhas, 2) == (10, 90, 80)
    assert Q.bufferbloat([[i, 1000, 10] for i in range(30)], 2) == (None, None, None)   # nunca encheu o link
    assert [Q.nota_bufferbloat(x) for x in (2, 20, 50, 100, 300, 900, None)] == ["A+", "A", "B", "C", "D", "F", None]


def teste_canal_wifi_e_alvos():
    assert Q.canal_wifi(2_437_000) == (6, "2,4 GHz") and Q.canal_wifi(5_180_000) == (36, "5 GHz")
    assert Q.canal_wifi(5_975_000) == (5, "6 GHz") and Q.canal_wifi(1_000_000) == (None, None)
    assert Q.alvo_valido(" 1.1.1.1 ") == "1.1.1.1" and Q.alvo_valido("::1") == "::1"
    for ruim in ("", "a b", "x" * 300, "1.1.1.1; rm"):
        try:
            Q.alvo_valido(ruim)
        except ValueError:
            continue
        raise AssertionError(ruim)


def teste_pergunta_dns():
    p = Q.pergunta_dns("www.exemplo.com", 0x1234)
    assert struct.unpack_from(">HHH", p) == (0x1234, 0x0100, 1)
    assert p[12:] == b"\x03www\x07exemplo\x03com\x00\x00\x01\x00\x01"


def _acc(local, remoto, porta_rem, porta_loc, udp, envio, n=1, tam=1000, pid=7):
    """Entrada no formato do ETW: envio vai do local para o remoto; recebimento, ao contrário."""
    a, b = socket.inet_aton(local), socket.inet_aton(remoto)
    if envio:
        chave = (pid, b, a, struct.pack(">HH", porta_rem, porta_loc), udp)
        return chave, [tam * n, 0, n, 0]
    return (pid, a, b, struct.pack(">HH", porta_loc, porta_rem), udp), [0, tam * n, 0, n]


def teste_agrupar_tcp_udp_portas():
    acc = dict([_acc("192.168.1.5", "8.8.8.8", 443, 50000, False, True, 2),
                _acc("192.168.1.5", "8.8.8.8", 443, 50000, False, False, 3),
                _acc("192.168.1.5", "8.8.8.8", 443, 50001, True, False, 1),
                _acc("192.168.1.5", "155.133.250.1", 27015, 60000, True, True, 4)])
    acc[(9, socket.inet_aton("127.0.0.1"), socket.inet_aton("127.0.0.1"), struct.pack(">HH", 1, 2), False)] = [5, 5, 1, 1]
    # fluxo UDP com o ritmo de chegada medido pelo ETW (relógio de 10 MHz): variação 40 ms em 4, maior intervalo 30 ms
    chave, v = _acc("192.168.1.5", "200.1.1.1", 10000, 40000, True, False, 5, pid=8)
    acc[chave] = v + [400_000, 4, 300_000]
    lista, det, fluxos = rede.agrupar(acc, {"192.168.1.5"})
    tot = {(p, ip): (e, r) for p, ip, e, r in lista}
    assert tot == {(7, "8.8.8.8"): (2000, 4000), (7, "155.133.250.1"): (4000, 0), (8, "200.1.1.1"): (0, 5000)}   # loopback fica de fora
    d = {(x[0], x[1], x[3]): x[4:] for x in det}
    assert d[("8.8.8.8", 443, False)] == (2000, 3000, 2, 3)
    assert d[("8.8.8.8", 443, True)] == (0, 1000, 0, 1)
    assert d[("155.133.250.1", 27015, True)] == (4000, 0, 4, 0)
    f = {(x[0], x[1], x[2], x[4]): x[5:] for x in fluxos}
    assert f[(7, "8.8.8.8", 443, False)] == (2000, 3000, 2, 3, 0, 0, 0)
    assert f[(8, "200.1.1.1", 10000, True)] == (0, 5000, 0, 5, 40.0, 4, 30.0)
    vivos = rede.FluxosVivos()
    for s in range(100, 106):
        vivos.add(s, [(8, "200.1.1.1", 10000, 40000, True, 0, 5000, 0, 5, 40.0, 4, 30.0 + s - 100)])
    x = vivos.lista(agora=105.5)[0]
    assert x["rec"] == 5000 and x["pps"] == 5 and x["jitter"] == 10.0 and x["silencio"] == 35.0
    assert vivos.lista(agora=200) == []                                          # parado há mais de 10 s: some


def teste_trafego_por_ip_e_protocolo():
    t = rede.TrafegoIP()
    for s in range(100, 110):
        t.add(s, [("8.8.8.8", 443, 50000, False, 1000, 2000, 2, 3), ("8.8.8.8", 443, 50001, True, 0, 500, 0, 1),
                  ("1.2.3.4", 53, 5353, True, 100, 100, 1, 1)])
    r = t.resumo("8.8.8.8", agora=110)
    assert r["tot"]["tcp_env"] == 10_000 and r["tot"]["udp_rec"] == 5000 and r["tot"]["pacotes_rec"] == 40
    assert r["pico"] == 3500 and r["sustentado"] == round(35_000 / 60) and r["pps"] == round(60 / 60, 1)
    assert [s["nome"] for s in r["servicos"]] == ["HTTPS", "QUIC (HTTP/3)"] and len(r["serie"]) == 10
    assert t.resumo("9.9.9.9") is None
    pr = t.protocolos()
    assert pr["nomes"][:3] == ["HTTPS", "QUIC (HTTP/3)", "DNS"] and len(pr["serie"]) == 10
    assert rede.servico(27020, True) == "Steam/Valve (jogo)" and rede.servico(3478, True) == "STUN/TURN (chamadas)"
    assert rede.servico(12345, False) is None


def teste_sondas_vigia_e_analise():
    respostas = {"10.0.0.1": [5.0], "1.1.1.1": [20.0, 22.0, None]}
    vez = {}
    trava = threading.Lock()

    def ping_falso(ip):                    # sequência fixa por IP (as sondas pingam em paralelo)
        lst = respostas.get(ip, [None])
        with trava:
            vez[ip] = vez.get(ip, -1) + 1
            return lst[vez[ip] % len(lst)]

    with tempfile.TemporaryDirectory() as pasta:
        s = Q.Sondas(ping_fn=ping_falso, carga_fn=lambda: 1000, pasta=pasta)
        base = time.time()
        s.vigiar(["1.1.1.1"], seg=5)
        for k in range(4):
            s.passo(base + k)
        assert s.resumo_ip("1.1.1.1", janela=60)["n"] == 4
        s.passo(base + 10)                             # passou do prazo: para de pingar
        assert s.resumo_ip("1.1.1.1", janela=60)["n"] == 4
        ok, _ = s.iniciar_captura(["10.0.0.1", "1.1.1.1"], ["roteador", "Cloudflare"])
        assert ok and not s.iniciar_captura(["1.1.1.1"], ["x"])[0]
        for k in range(30):
            s.passo(base + 20 + k)
        vivo = s.abrir("atual")
        assert vivo["gravando"] and vivo["segundos"] == 30 and vivo["destinos"][0]["nome"] == "roteador"
        ident = s.parar_captura()
        assert s.captura is None and [x["id"] for x in s.lista()] == [ident]
        a = s.abrir(ident)
        assert a["segundos"] == 30 and a["disponibilidade"]["quedas"] == 0
        cf = a["destinos"][1]
        assert 30 <= cf["perda"] <= 37 and cf["min"] == 20 and cf["pontos"]["confiabilidade"] == 100
        assert json.dumps(a)                           # serializável para a API
        assert s.abrir("../../etc") is None and not s.apagar("..\\x")
        assert s.apagar(ident) and s.lista() == []


def teste_analisar_quedas_e_linha_do_tempo():
    linhas = [[1000 + i, 0, 5.0, None if 10 <= i < 16 else 20.0] for i in range(1300)]
    a = Q.analisar({"id": "x", "inicio": 1000, "alvos": ["192.168.1.1", "1.1.1.1"], "nomes": ["r", "c"], "linhas": linhas})
    assert a["disponibilidade"]["quedas"] == 1 and a["disponibilidade"]["eventos"][0]["t"] == 1010
    assert a["passo"] == 3 and len(a["tempo"]) == 434 and len(a["tempo"][0]) == 2 + 2 * 2


def _servidor_http():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            corpo = b"ola" * 1000
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def teste_http_local():
    srv = _servidor_http()
    try:
        r = Q.medir_http(f"http://127.0.0.1:{srv.server_address[1]}/x?y=1")
        assert r["status"] == 200 and r["bytes"] >= 3000 and r["tls"] is None and r["total"] >= r["ttfb"] >= 0
        for ruim in ("ftp://a", "nada", "file:///c:/x"):
            try:
                Q.medir_http(ruim)
            except ValueError:
                continue
            raise AssertionError(ruim)
    finally:
        srv.shutdown()


def teste_windows_ping_contadores_e_tcp():
    if os.name != "nt":
        print("  (pulado: só no Windows)")
        return
    r = Q.ping("127.0.0.1")
    assert r["status"] == Q.IP_SUCCESS and r["rtt"] is not None and r["de"] == "127.0.0.1", r
    r6 = Q.ping("::1")
    assert r6["status"] == Q.IP_SUCCESS and r6["de"] == "::1", r6
    c = Q.contadores()
    assert c["tcp"].get("segmentos_out", 0) > 0 and "datagramas_in" in c["udp"]
    assert Q.diferenca(c, Q.contadores())["tcp"]["segmentos_out"] >= 0
    srv = _servidor_http()
    try:
        porta = srv.server_address[1]
        s = socket.create_connection(("127.0.0.1", porta))
        lport = s.getsockname()[1]
        est = Q.tcp_estats("127.0.0.1", lport, "127.0.0.1", porta)
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        s.recv(100)
        est = Q.tcp_estats("127.0.0.1", lport, "127.0.0.1", porta)
        s.close()
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            assert est is not None and "retransmissoes" in est, est
            assert est.get("rtt") is None or 0 <= est["rtt"] < 1000, est      # loopback: RTT pequeno
        else:
            print("  (RTT TCP pulado: precisa de administrador)")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes de rede passaram")
