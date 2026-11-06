"""Discos físicos: nome/modelo, unidades (C:, D:...), leitura/gravação e tempo ativo de cada um."""
import os
import json
import time
import subprocess

import psutil

WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0


def mapa_discos():
    """{ 'PhysicalDrive0': {nome, tamanho, tipo, barramento, letras:[...]} } via PowerShell (Storage)."""
    if not WIN:
        out = {}
        for nome in (psutil.disk_io_counters(perdisk=True) or {}):
            out[nome] = {"nome": nome, "tamanho": 0, "tipo": "", "barramento": "", "letras": []}
        return out
    cmd = ("$d = Get-Disk | Select-Object Number, FriendlyName, Size, BusType, "
           "@{n='Midia';e={(Get-PhysicalDisk | Where-Object DeviceId -eq $_.Number).MediaType}}; "
           "$p = Get-Partition | Where-Object DriveLetter | Select-Object DiskNumber, DriveLetter; "
           "@{discos=@($d); particoes=@($p)} | ConvertTo-Json -Depth 4 -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                           text=True, timeout=30, creationflags=SEM_JANELA)
        dados = json.loads(r.stdout or "{}")
    except Exception:
        dados = {}
    out = {}
    for d in dados.get("discos") or []:
        n = d.get("Number")
        if n is None:
            continue
        midia = str(d.get("Midia") or "")
        midia = {"4": "HDD", "3": "SSD", "HDD": "HDD", "SSD": "SSD"}.get(midia, midia if midia not in ("0", "Unspecified") else "")
        out[f"PhysicalDrive{n}"] = {"nome": (d.get("FriendlyName") or f"Disco {n}").strip(), "tamanho": d.get("Size") or 0,
                                    "tipo": midia, "barramento": str(d.get("BusType") or ""), "letras": [], "numero": n}
    for p in dados.get("particoes") or []:
        k = f"PhysicalDrive{p.get('DiskNumber')}"
        letra = str(p.get("DriveLetter") or "").strip()
        if k in out and letra and len(letra) == 1:
            out[k]["letras"].append(letra + ":")
    return out


class Discos:
    def __init__(self):
        self.mapa = {}
        self._ult_mapa = 0
        self._ant = {}

    def atualizar_mapa(self, forcar=False):
        if forcar or time.time() - self._ult_mapa > 600:
            try:
                m = mapa_discos()
                if m:
                    self.mapa = m
            except Exception:
                pass
            self._ult_mapa = time.time()

    def ler(self, t, janela):
        """Taxas por disco físico, média da última janela de tempo."""
        try:
            io = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            io = {}
        out = []
        for nome, c in io.items():
            hist = self._ant.setdefault(nome, [])
            atual = (t, c.read_bytes, c.write_bytes, (c.read_time or 0) + (c.write_time or 0))
            while len(hist) > 1 and hist[1][0] <= t - janela:
                hist.pop(0)
            ref = hist[0] if hist else None
            hist.append(atual)
            ler = gravar = ativo = 0.0
            if ref and t > ref[0]:
                dt = t - ref[0]
                ler = max(0.0, (atual[1] - ref[1]) / dt)
                gravar = max(0.0, (atual[2] - ref[2]) / dt)
                ativo = max(0.0, min(100.0, (atual[3] - ref[3]) / (dt * 1000) * 100))
            m = self.mapa.get(nome, {})
            out.append({"id": nome, "nome": m.get("nome") or nome, "letras": m.get("letras", []), "tipo": m.get("tipo", ""),
                        "barramento": m.get("barramento", ""), "tamanho": m.get("tamanho", 0),
                        "ler": round(ler), "gravar": round(gravar), "ativo": round(ativo, 1)})
        out.sort(key=lambda d: d["id"])
        return out
