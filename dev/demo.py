"""Roda o Monitor com dados de demonstração (sensores, rede e histórico falsos).
Serve para testar a interface fora do Windows:  python dev/demo.py
"""
import os
import sys
import math
import time
import random
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "src"))
os.environ.setdefault("MONITORPC_DADOS", os.path.join(tempfile.gettempdir(), "monitorpc-demo"))

import psutil  # noqa: E402
from monitorpc import config, banco, motor, servidor, temperatura, rede, gpu, inicializacao, energia, servicos, icones, ventoinhas  # noqa: E402

PROGS = ["Claude", "Google Chrome", "WSL (Linux)", "Maestri", "Vynex Forge", "Discord", "OneDrive",
         "Antivírus (Defender)", "MobaXterm", "Spotify"]
IPS = [("142.250.79.14", "gru14s25-in-f14.1e100.net"), ("160.79.104.10", "api.anthropic.com"),
       ("162.159.135.232", "discord.com"), ("13.107.42.14", "onedrive.live.com"), ("35.186.224.25", None),
       ("192.168.0.1", None), ("200.147.3.157", "uol.com.br"), ("104.18.32.47", None)]


def semear():
    agora = int(time.time())
    g, a, p, t, r = [], [], [], [], {}
    for ts in range(agora - 3 * 86400, agora - 60, 60):
        pico = 1 if (ts // 1800) % 23 == 0 else 0
        mem = 68 + 9 * math.sin(ts / 15000) + pico * 24 + random.random() * 2
        cpu = 12 + random.random() * 15 + pico * 70
        tc = 52 + cpu * 0.35 + random.random() * 3
        g.append((ts, min(cpu, 100), min(mem, 99), int(mem / 100 * 16.9e9), int(16.9e9), 12, 2e5 + random.random() * 4e6,
                  1e5 + random.random() * 2e6, 3e4 + random.random() * 2e5, 1e5 + random.random() * 3e6,
                  random.random() * 30, tc, 48 + random.random() * 10, 41 + random.random() * 3))
        for i, n in enumerate(PROGS):
            m = int((1.2e9 - i * 1.1e8) * (1 + .2 * math.sin(ts / 4000 + i)) + (pico * 1.6e9 if n in ("Maestri", "WSL (Linux)") else 0))
            a.append((ts, n, m, round(random.random() * (6 if i < 5 else 1) + pico * (25 if n == "Maestri" else 0), 2),
                      random.random() * 5e5, random.random() * 3e5, random.random() * 4e4, random.random() * 3e5, 3 + i, 10 - i))
        if ts % 300 == 0:
            for i, n in enumerate(PROGS[:6]):
                p.append((ts, 1000 + i, n.split()[0].lower() + ".exe", n, int(6e8 - i * 5e7), random.random() * 5, 0, 0, 0, 0))
        if ts % 120 == 0:
            t += [(ts, "/intelcpu/0/temperature/0", "Intel Core i7-10750H", "CPU Package", tc),
                  (ts, "/gpu-nvidia/0/temperature/0", "NVIDIA GeForce GTX 1650", "GPU Core", 50 + random.random() * 8),
                  (ts, "/nvme/0/temperature/0", "SSD NVMe 512GB", "Temperature", 41 + random.random() * 3)]
        h = (ts // 3600) * 3600
        for j, (ip, host) in enumerate(IPS):
            k = (h, PROGS[j % 6], ip)
            v = r.setdefault(k, [host, 0, 0])
            v[1] += int(random.random() * 2e5)
            v[2] += int(random.random() * 2e6)
    banco.gravar(geral=None, apps=a, procs=p, temps=t,
                 remotos=[(k[0], k[1], k[2], v[0], v[1], v[2]) for k, v in r.items()])
    con = banco.conectar()
    con.executemany("INSERT INTO geral VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", g)
    con.commit()
    con.close()


FAN_DEMO = {"pct": 45.0}     # "hardware" da ventoinha falsa; mude o valor aqui para simular outro programa


class VentoinhasFake(ventoinhas.Ventoinhas):
    def _controles(self):
        return {"/lpc/0/control/1": object()}

    def _escrever(self, ctl, pct):
        FAN_DEMO["pct"] = float(pct)

    def _soltar(self, ctl):
        FAN_DEMO["pct"] = 45.0


class SensoresFake(temperatura.Sensores):
    def iniciar(self):
        self.fontes = ["LibreHardwareMonitor (demo)"]
        self.pawnio = True

    def ler(self):
        cpu = psutil.cpu_percent()
        base = [
            ("/intelcpu/0/temperature/0", "Intel Core i7-10750H", "Processador", "CPU Package", "Temperature", 58 + cpu * .3 + random.random() * 2),
            ("/intelcpu/0/temperature/1", "Intel Core i7-10750H", "Processador", "Core Max", "Temperature", 60 + random.random() * 4),
            ("/intelcpu/0/load/0", "Intel Core i7-10750H", "Processador", "CPU Total", "Load", cpu),
            ("/intelcpu/0/power/0", "Intel Core i7-10750H", "Processador", "CPU Package", "Power", 14 + random.random() * 6),
            ("/intelcpu/0/clock/1", "Intel Core i7-10750H", "Processador", "Core #1", "Clock", 3800 + random.random() * 200),
            ("/gpu-nvidia/0/temperature/0", "NVIDIA GeForce GTX 1650", "Placa de vídeo", "GPU Core", "Temperature", 55 + random.random() * 3),
            ("/gpu-nvidia/0/load/0", "NVIDIA GeForce GTX 1650", "Placa de vídeo", "GPU Core", "Load", random.random() * 12),
            ("/gpu-nvidia/0/fan/0", "NVIDIA GeForce GTX 1650", "Placa de vídeo", "GPU Fan", "Fan", 1450 + random.random() * 50),
            ("/nvme/0/temperature/0", "SSD NVMe 512GB", "Armazenamento", "Temperature", "Temperature", 42 + random.random()),
            ("/nvme/0/load/30", "SSD NVMe 512GB", "Armazenamento", "Used Space", "Load", 71.2),
            ("/lpc/0/fan/0", "Placa-mãe", "Placa-mãe", "Fan #1", "Fan", 2200 + random.random() * 100),
        ]
        base += [("/lpc/0/control/1", "Placa-mãe", "Placa-mãe", "Fan Control #1", "Control", FAN_DEMO["pct"]),
                 ("/lpc/0/fan/1", "Placa-mãe", "Placa-mãe", "Fan #1", "Fan", FAN_DEMO["pct"] * 28 + random.random() * 20)]
        out = []
        for sid, hw, thw, nome, t, v in base:
            rot, un = temperatura.TIPOS[t]
            out.append({"id": sid, "hw": hw, "sub": "", "tipo_hw": thw, "nome": nome, "tipo": rot, "tipo_en": t,
                        "valor": round(v, 1), "un": un, "fonte": "LHM"})
        for s in out:
            mm = self._minmax.setdefault(s["id"], [s["valor"], s["valor"]])
            mm[0] = min(mm[0], s["valor"])
            mm[1] = max(mm[1], s["valor"])
            s["min"], s["max"] = mm
        with self.lock:
            self.lista = out
        return out


class RedeFake(rede.MonitorRede):
    def iniciar(self):
        self.ok = True
        return True

    def coletar(self):
        pids = [p.pid for p in psutil.process_iter()][:6]
        out = []
        for i, pid in enumerate(pids):
            for ip, _ in random.sample(IPS, 3):
                out.append((pid, ip, int(random.random() * 4e4), int(random.random() * 3e5)))
        self.eventos += len(out)
        return out


class GPUFake(gpu.MonitorGPU):
    def iniciar(self):
        self.ok = True
        self.adaptadores = [{"luid": (0, 1), "nome": "NVIDIA GeForce GTX 1650", "mem_dedicada": 4 * 2**30, "mem_compartilhada": 8 * 2**30, "fabricante": "NVIDIA"},
                            {"luid": (0, 2), "nome": "Intel(R) UHD Graphics", "mem_dedicada": 128 * 2**20, "mem_compartilhada": 8 * 2**30, "fabricante": "Intel"}]

    def ler(self):
        pids = [p.pid for p in psutil.process_iter()][:4]
        self.por_pid = {pid: {"uso": random.random() * 20, "mem": int(random.random() * 3e8), "luid": (0, 1)} for pid in pids}
        self.por_gpu = {(0, 1): {"uso": 10 + random.random() * 30, "engines": {"3D": 20 + random.random() * 10, "Copy": 2.0, "VideoDecode": 5.0}, "mem_ded": int(1.2e9), "mem_comp": int(1e8)},
                        (0, 2): {"uso": random.random() * 8, "engines": {"3D": random.random() * 8}, "mem_ded": 0, "mem_comp": int(4e8)}}


def ini_fake():
    return [{"id": "a1", "nome": "Discord", "comando": r'"C:\Users\robson\AppData\Local\Discord\Update.exe" --processStart Discord.exe', "exe": "/usr/bin/python3", "args": "",
             "existe": True, "origem": "Registro (Run)", "escopo": "robson", "ativo": True, "descricao": "Discord", "empresa": "Discord Inc.", "_alterar": (None, "Run", "Discord")},
            {"id": "a2", "nome": "OneDrive", "comando": r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background', "exe": "", "args": "",
             "existe": True, "origem": "Registro (Run)", "escopo": "robson", "ativo": False, "descricao": "Microsoft OneDrive", "empresa": "Microsoft Corporation", "_alterar": (None, "Run", "OneDrive")},
            {"id": "a3", "nome": "Monitor do PC", "comando": r'"C:\Program Files\Monitor do PC\MonitorPC.exe" --bandeja', "exe": "", "args": "",
             "existe": True, "origem": "Registro (Run)", "escopo": "Todos os usuários", "ativo": True, "descricao": "Monitor do PC", "empresa": "", "_alterar": (None, "Run", "MonitorDoPC")}]


def energia_fake():
    return {"planos": [{"guid": "381b4222-f694-41f0-9685-ff5bb260df2e", "nome": "Equilibrado", "ativo": True, "conhecido": "Equilibrado"},
                       {"guid": "a1841308-3541-4fab-bc81-f71556f20b4a", "nome": "Economia de energia", "ativo": False, "conhecido": None}],
            "ativo": {"nome": "Equilibrado"}, "modo": {"guid": "00000000-0000-0000-0000-000000000000", "nome": "Equilibrado"},
            "modos": [{"guid": k, "nome": v} for k, v in energia.MODOS.items()],
            "ajustes": [{"chave": "tela", "titulo": "Desligar a tela após", "tipo": "min", "valor": {"tomada": 10, "bateria": 5}, "opcoes": energia.MIN},
                        {"chave": "cpu_max", "titulo": "Desempenho máximo do processador", "tipo": "pct", "valor": {"tomada": 100, "bateria": 99}, "opcoes": [50, 60, 70, 80, 90, 99, 100]},
                        {"chave": "tampa", "titulo": "Ao fechar a tampa", "tipo": "enum", "valor": {"tomada": 1, "bateria": 1}, "opcoes": [{"v": 0, "t": "Não fazer nada"}, {"v": 1, "t": "Suspender"}]}],
            "windows": [{"chave": k, "titulo": t, "descricao": d, "ativo": True} for k, t, d in energia.AJUSTES_WINDOWS], "brilho": 70,
            "tem_alto": False, "tem_maximo": False}


def servicos_fake(forcar=False):
    return [{"nome": "Docker", "exibicao": "Docker Desktop Service", "status": "running", "status_txt": "Em execução", "inicio": "automatic",
             "inicio_txt": "Automático", "pid": 1, "usuario": "LocalSystem", "descricao": "Serviço do Docker Desktop", "binpath": "", "exe": "", "protegido": None},
            {"nome": "Spooler", "exibicao": "Spooler de Impressão", "status": "running", "status_txt": "Em execução", "inicio": "automatic",
             "inicio_txt": "Automático", "pid": 0, "usuario": "LocalSystem", "descricao": "Gerencia impressão", "binpath": "", "exe": "", "protegido": "Serviço do Windows"}]


if __name__ == "__main__":
    banco.iniciar()
    if banco.info()["registros"] < 100:
        semear()
    m = motor.Motor()
    m.sens = SensoresFake()
    m.vent = VentoinhasFake(m.sens, m.vent._ler_cfg, m.vent._gravar_cfg)
    m.rede = RedeFake()
    m.gpu = GPUFake()
    inicializacao.listar = ini_fake
    energia.estado = energia_fake
    energia.planos = lambda: energia_fake()["planos"]
    servicos.listar = servicos_fake
    inicializacao.alterar = lambda item, ativo: True
    m.iniciar()
    m.bandeja_ts = time.time() + 10 ** 9
    servidor.criar(m)
    print("Demo em", config.URL)
    while True:
        time.sleep(3600)
