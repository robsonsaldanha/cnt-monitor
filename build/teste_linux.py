"""Testes das partes Linux do CNT Monitor (monitorpc/linux): leitores de /proc, /sys, ss, iw, systemctl e
autostart, captura de pacotes por programa, ping ICMP e sensores num /sys/class/hwmon de mentira.
Os leitores de texto rodam em qualquer sistema; o resto só no Linux (pulado no Windows).

    python build/teste_linux.py          (no Linux, como root, roda também a captura e o ping)
"""
import os
import sys
import time
import socket
import struct
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

LINUX = sys.platform.startswith("linux")
ROOT = LINUX and os.geteuid() == 0

from monitorpc.linux import rede as LR, servicos as LS  # noqa: E402


def _ipv4(org, dst, proto, carga, sport=5000, dport=6000):
    if proto == 17:
        l4 = struct.pack(">HHHH", sport, dport, 8 + len(carga), 0)
    else:
        l4 = struct.pack(">HHIIBBHHH", sport, dport, 1, 0, 5 << 4, 0x18, 65535, 0, 0)
    total = 20 + len(l4) + len(carga)
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, total, 1, 0, 64, proto, 0, socket.inet_aton(org), socket.inet_aton(dst))
    return ip + l4 + carga


def teste_ler_pacote_udp_tcp_e_cortado():
    p = LR.ler_pacote(_ipv4("10.0.0.2", "8.8.8.8", 17, b"\x80" + b"x" * 199))
    assert p[2:6] == (5000, 6000, True, 200) and p[6][:1] == b"\x80", p
    t = _ipv4("10.0.0.2", "1.1.1.1", 6, b"y" * 1000)
    assert LR.ler_pacote(t)[4:6] == (False, 1000)
    assert LR.ler_pacote(t[:LR.CAB])[5] == 1000, "pacote cortado: o tamanho vem do cabeçalho IP"
    assert LR.ler_pacote(_ipv4("10.0.0.2", "1.1.1.1", 6, b""))[5] == 0       # só ACK
    frag = bytearray(_ipv4("10.0.0.2", "1.1.1.1", 17, b"z" * 20))
    frag[6:8] = struct.pack(">H", 100)
    assert LR.ler_pacote(bytes(frag)) is None
    assert LR.ler_pacote(b"\x45" + b"\0" * 10) is None


def teste_ler_ss():
    txt = ("ESTAB 0 0 192.168.0.10:51234 142.250.1.1:443\n"
           "\t cubic wscale:7,7 rto:216 rtt:15.25/3.5 ato:40 mss:1448 minrtt:12.1 retrans:0/7 bytes_retrans:9000\n"
           "ESTAB 0 0 [::ffff:10.0.0.5]:22 [::ffff:10.0.0.9]:60000\n\t cubic rtt:0.5/0.25 mss:65483\n")
    m = LR.ler_ss(txt)
    a = m[("192.168.0.10", 51234, "142.250.1.1", 443)]
    assert a["rtt"] == 15.25 and a["rtt_var"] == 3.5 and a["retransmissoes"] == 7 and a["rto"] == 216 and a["rtt_min"] == 12.1, a
    assert m[("10.0.0.5", 22, "10.0.0.9", 60000)]["rtt"] == 0.5


def teste_ler_snmp():
    txt = ("Ip: Forwarding DefaultTTL InReceives InDiscards OutDiscards FragOKs\nIp: 2 64 1000 3 4 5\n"
           "Tcp: RtoAlgorithm ActiveOpens OutSegs RetransSegs CurrEstab InErrs OutRsts\nTcp: 1 10 500 7 3 1 2\n"
           "Udp: InDatagrams NoPorts InErrors OutDatagrams\nUdp: 100 2 1 90\n")
    s = LR.ler_snmp(txt, "Udp6InDatagrams 10\nIp6InDiscards 1\n")
    assert s["tcp"]["segmentos_out"] == 500 and s["tcp"]["retransmitidos"] == 7 and s["tcp"]["estabelecidas"] == 3
    assert s["udp"]["datagramas_in"] == 110 and s["udp"]["sem_porta"] == 2
    assert s["ip"]["descartados_in"] == 4 and s["ip"]["fragmentados_ok"] == 5
    from monitorpc import qualidade as Q
    a = {"t": 0, "tcp": s["tcp"], "udp": s["udp"], "ip": s["ip"], "placas": {}}
    b = {"t": 10, "tcp": dict(s["tcp"], segmentos_out=1500, retransmitidos=17), "udp": s["udp"], "ip": s["ip"], "placas": {}}
    assert Q.diferenca(a, b)["tcp"]["taxa_retransmissao"] == 1.0


def teste_ler_iw():
    link = ("Connected to aa:bb:cc:dd:ee:ff (on wlp2s0)\n\tSSID: Casa\n\tfreq: 5180.0\n\tsignal: -55 dBm\n"
            "\trx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2\n\ttx bitrate: 780.0 MBit/s VHT-MCS 8 80MHz VHT-NSS 2\n")
    d = LR.ler_iw_link(link)
    assert d["ssid"] == "Casa" and d["rssi"] == "-55" and d["rx"][0] == "866.7" and LR._padrao(d["tx"][1]) == "802.11ac"
    assert LR._qualidade(-55) == 90
    scan = "BSS 11:22:33:44:55:66(on wlp2s0)\n\tfreq: 2437\n\tsignal: -70.00 dBm\n\tSSID: Vizinho\n"
    r = LR.ler_iw_scan(scan)
    assert r[0]["canal"] == 6 and r[0]["rssi"] == -70 and r[0]["ssid"] == "Vizinho", r


def teste_ler_units_systemd():
    units = ("cnt-monitor.service loaded active running CNT Monitor\n"
             "cups.service loaded active running CUPS Scheduler\n"
             "teamviewerd.service loaded inactive dead TeamViewer remote control daemon\n"
             "foo.service loaded failed failed Foo\n")
    arqs = "cnt-monitor.service enabled enabled\nteamviewerd.service disabled enabled\nfoo.service static -\n"
    l = {x["nome"]: x for x in LS.ler_units(units, arqs)}
    assert l["cnt-monitor"]["protegido"] and l["cnt-monitor"]["inicio"] == "automatic"
    assert l["teamviewerd"]["status"] == "stopped" and l["teamviewerd"]["inicio"] == "disabled" and not l["teamviewerd"]["protegido"]
    assert l["foo"]["status_txt"] == "Falhou" and l["foo"]["inicio"] == "manual"
    assert l["teamviewerd"]["exibicao"] == "TeamViewer remote control daemon"


def teste_autostart_listar_e_alterar():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc.linux import inicializacao as LI
    d = tempfile.mkdtemp()
    arq = os.path.join(d, "app.desktop")
    with open(arq, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Meu App\nExec=sh -c true %U\nComment=teste\n[Desktop Action x]\nHidden=true\n")
    antigo = LI.SISTEMA
    LI.SISTEMA = d
    try:
        it = next(i for i in LI.listar() if i["nome"] == "Meu App")
        assert it["ativo"] and it["exe"].endswith("/sh") and it["args"] == "-c true", it
        LI.alterar(it, False)
        assert not next(i for i in LI.listar() if i["nome"] == "Meu App")["ativo"]
        LI.alterar(it, True)
        txt = open(arq).read()
        assert next(i for i in LI.listar() if i["nome"] == "Meu App")["ativo"] and txt.count("Hidden=") == 2, txt
    finally:
        LI.SISTEMA = antigo


def teste_sensores_hwmon_de_mentira_e_ventoinha():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc import temperatura, ventoinhas
    raiz = tempfile.mkdtemp()

    def chip(n, nome, arqs):
        p = os.path.join(raiz, f"hwmon{n}")
        os.makedirs(p)
        for k, v in {"name": nome, **arqs}.items():
            with open(os.path.join(p, k), "w") as f:
                f.write(v + "\n")
        return p
    chip(0, "coretemp", {"temp1_input": "61000", "temp1_label": "Package id 0", "temp1_crit": "100000",
                         "temp2_input": "58000", "temp2_label": "Core 0"})
    p = chip(1, "nct6798", {"fan2_input": "1200", "pwm2": "128", "pwm2_enable": "5", "temp1_input": "0"})
    s = temperatura.Sensores(raiz)
    s.iniciar()
    lst = s.ler()
    nomes = {x["nome"]: x for x in lst}
    assert nomes["CPU Package"]["valor"] == 61.0 and nomes["CPU Package"]["tipo_hw"] == "Processador"
    assert nomes["CPU Package Critical"]["limite"] and "in0" not in nomes
    assert s.resumo()["temp_cpu"] == 61.0
    assert not any(x["id"] == "/hwmon/nct6798/temp/1" for x in lst), "0 °C é sensor sem leitura"
    assert ventoinhas.par_ventoinha("/hwmon/nct6798/control/2") == "/hwmon/nct6798/fan/2"
    v = ventoinhas.Ventoinhas(s, lambda: ventoinhas.validar({}), lambda c: None)
    canais = v.canais()["canais"]
    assert canais and canais[0]["rpm"] == 1200.0 and canais[0]["pct"] == 50.2, canais
    ok, msg = v.definir("/hwmon/nct6798/control/2", {"modo": "manual", "valor": 80})
    assert ok, msg
    assert open(os.path.join(p, "pwm2")).read() == "204" and open(os.path.join(p, "pwm2_enable")).read() == "1"
    v.definir("/hwmon/nct6798/control/2", {"modo": "auto"})
    assert open(os.path.join(p, "pwm2_enable")).read() == "5", "volta ao modo automático original do chip"


def teste_pacote_conta_uma_vez_so_na_placa_com_ip():
    """Bridge, bonding e a placa acelerada das nuvens mostram o mesmo pacote de novo na interface sem IP."""
    import collections
    import psutil
    E = collections.namedtuple("E", "family address")
    S = collections.namedtuple("S", "flags")
    placas = {"eth0": [E(socket.AF_INET, "10.1.0.4")], "enP30832s1": [E(getattr(socket, "AF_PACKET", 17), "00:0d:3a")],
              "lo": [E(socket.AF_INET, "127.0.0.1")], "wg0": [E(socket.AF_INET6, "fd00::2")]}
    antigos = psutil.net_if_addrs, psutil.net_if_stats
    psutil.net_if_addrs = lambda: placas
    psutil.net_if_stats = lambda: {"lo": S("up,loopback")}
    try:
        p = LR.Placas()
        assert p.conta("eth0") and p.conta("wg0") and not p.conta("enP30832s1") and not p.conta("lo")
        placas["wlan0"] = [E(socket.AF_INET, "192.168.0.9")]
        p._ult -= 3                               # placa nova (Wi-Fi conectou) entra em segundos
        assert p.conta("wlan0")
    finally:
        psutil.net_if_addrs, psutil.net_if_stats = antigos


def teste_portas_pid_e_captura_por_programa():
    if not ROOT:
        return print("  (pulado: precisa de Linux e root)")
    from monitorpc import rede
    m = rede.MonitorRede()
    assert m.iniciar(), m.erro
    try:
        rec = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rec.bind(("0.0.0.0", 0))
        env = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        env.connect(("192.0.2.1", 9))          # TEST-NET: não chega a lugar nenhum, mas sai pela placa
        time.sleep(0.3)
        m.coletar()
        for _ in range(20):
            env.send(b"x" * 300)
            time.sleep(0.01)
        time.sleep(0.5)
        lista = m.coletar()
        meu = [x for x in lista if x[0] == os.getpid() and x[1] == "192.0.2.1"]
        assert meu and meu[0][2] == 6000, (meu, lista[:5])
        assert any(f[0] == os.getpid() and f[4] for f in m.fluxos), "fluxo UDP do próprio processo"
    finally:
        m.parar()


def teste_ping_icmp_proprio():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc import qualidade as Q
    r = Q.ping("127.0.0.1", 1000)
    if r["status"] == -1:
        return print("  (ping pulado: sem permissão para ICMP)", r)
    assert r["status"] == Q.IP_SUCCESS and r["rtt"] is not None and r["de"] == "127.0.0.1", r
    rs = []
    th = [threading.Thread(target=lambda: rs.append(Q.ping("127.0.0.1", 1000))) for _ in range(8)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert all(x["status"] == Q.IP_SUCCESS for x in rs), "pings em paralelo não se confundem"
    if ROOT:
        assert Q.ping("127.0.0.1", 500, tam=1472, nao_fragmentar=True)["status"] == Q.IP_SUCCESS     # loopback: MTU 65536
    assert Q.ping("192.0.2.1", 300)["status"] in (Q.IP_REQ_TIMED_OUT, Q.IP_DEST_NET_UNREACHABLE, Q.IP_DEST_HOST_UNREACHABLE)


def teste_tcp_estats_por_ss():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc import qualidade as Q
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(srv.getsockname())
    a, _ = srv.accept()
    c.sendall(b"oi")
    a.recv(10)
    time.sleep(0.1)
    LR._ss["t"] = 0
    lip, lport = c.getsockname()
    e = Q.tcp_estats(lip, lport, *srv.getsockname())
    for s in (c, a, srv):
        s.close()
    assert e is not None and e["rtt"] is not None and e["rtt"] < 100 and "retransmissoes" in e, e


def teste_contadores_e_dns_do_sistema():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc import qualidade as Q
    c = Q.contadores()
    assert c["tcp"].get("segmentos_out", 0) > 0 and "datagramas_in" in c["udp"], c
    assert isinstance(Q.servidores_dns(), list)


def teste_discos_energia_servicos_limpeza():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc import discos, energia, servicos, limpeza, inicializacao
    m = discos.mapa_discos()
    assert m and not any(k.startswith(("loop", "ram", "zram")) for k in m), m
    e = energia.estado()
    assert e["sistema"] == "linux" and isinstance(e["planos"], list)
    assert isinstance(servicos.listar(True), list)
    ids = {c["id"] for c in limpeza.analisar()}
    assert {"temp_sistema", "lixeira", "apt"} <= ids, ids
    assert isinstance(inicializacao.listar(), list)


def teste_x11_tela_mouse_teclado_e_area_no_xvfb():
    """Agente do acesso remoto numa tela X virtual: monitores, captura, mouse (posição exata), teclado e
    área de transferência (texto, imagem e arquivos), do jeito que o técnico usa."""
    import shutil
    import subprocess
    if not LINUX or not shutil.which("Xvfb") or not shutil.which("xclip"):
        return print("  (pulado: precisa de Linux com Xvfb e xclip)")
    from monitorpc.linux import tela as T, area as A
    xvfb = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1600x900x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    antigo = {k: os.environ.get(k) for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")}
    os.environ.update(DISPLAY=":97", XDG_SESSION_TYPE="x11")
    os.environ.pop("WAYLAND_DISPLAY", None)
    try:
        time.sleep(1.0)
        T._x11 = None
        assert not T.wayland()
        mons = T.listar_monitores()
        assert mons[0]["w"] == 1600 and mons[0]["h"] == 900 and mons[0]["principal"], mons
        d = T.plataforma()._dpy()
        tela = d.screen()
        janela = tela.root.create_window(100, 100, 400, 300, 0, tela.root_depth, background_pixel=tela.white_pixel)
        janela.map()
        d.sync()
        time.sleep(0.5)
        img = T.Telas(mons).quadro(0)
        assert img.size == (1600, 900)
        from PIL import ImageStat
        assert ImageStat.Stat(img.crop((150, 150, 450, 350)).convert("L")).mean[0] > 240, "a captura mostra a janela branca"
        assert ImageStat.Stat(img.crop((900, 500, 1500, 850)).convert("L")).mean[0] < 15, "e o fundo preto do Xvfb"
        import remoto_agente as ra
        for (x, y), msg in (((0.5, 0.25), "meio"), ((1.0, 1.0), "canto")):
            for ev in ra.traduzir({"t": "mouse", "a": "move", "x": x, "y": y, "m": 0}, mons):
                T.enviar_evento(ev, mons)
            p = T.plataforma()._dpy().screen().root.query_pointer()
            assert (p.root_x, p.root_y) == (round(x * 1599), round(y * 899)), (msg, p.root_x, p.root_y)
        for a in ("baixa", "sobe"):
            for ev in ra.traduzir({"t": "tecla", "a": a, "c": "KeyA"}, mons) + ra.traduzir({"t": "mouse", "a": a, "b": 0, "x": 0.1, "y": 0.1}, mons):
                T.enviar_evento(ev, mons)
        assert T.evdev(0x1E, 0) == 30 and T.evdev(0x5B, 1) == 125 and T.evdev(0x48, 1) == 103 and T.evdev(0x57, 0) == 87
        area = A.AreaLinux()
        area.texto("olá do técnico")
        time.sleep(0.3)
        assert area.ler() == ("texto", "olá do técnico")
        s1 = area.seq()
        arq = tempfile.mktemp(suffix=" com espaço.txt")
        open(arq, "w").close()
        area.arquivos([arq])
        time.sleep(0.3)
        assert area.ler() == ("arquivos", [arq]) and area.seq() != s1
        from PIL import Image
        import io
        b = io.BytesIO()
        Image.new("RGB", (8, 8), (255, 0, 0)).save(b, "PNG")
        area.imagem(b.getvalue())
        time.sleep(0.3)
        tipo, png = area.ler()
        assert tipo == "imagem" and Image.open(io.BytesIO(png)).size == (8, 8)
    finally:
        subprocess.run(["pkill", "-f", "xclip -selection clipboard"], capture_output=True)
        xvfb.kill()
        for k, v in antigo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        T._x11 = None


def teste_sessao_sem_tela_nao_quebra():
    if not LINUX:
        return print("  (pulado: só no Linux)")
    from monitorpc import sessao
    s = sessao.sessao_grafica()
    assert s is None or s["tipo"] in ("x11", "wayland"), s
    assert sessao.em_servico() in (True, False)


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("teste_"):
            f()
            print("ok", nome)
    print("todos os testes do Linux passaram")
