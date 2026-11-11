"""Discos físicos no Linux pelo /sys/block: modelo, tamanho, SSD/HDD, barramento e onde estão montados."""
import os

import psutil

from . import ler

IGNORAR = ("loop", "ram", "zram", "sr", "fd", "md", "dm-", "nbd")


def mapa_discos(raiz="/sys/block"):
    """{ 'sda': {nome, tamanho, tipo, barramento, letras:[pontos de montagem]} } só dos discos de verdade."""
    out = {}
    try:
        nomes = sorted(os.listdir(raiz))
    except OSError:
        return out
    montagens = {}
    try:
        for p in psutil.disk_partitions(all=False):
            montagens.setdefault(os.path.realpath(p.device), []).append(p.mountpoint)
    except Exception:  # noqa: BLE001
        pass
    for n in nomes:
        if n.startswith(IGNORAR):
            continue
        d = os.path.join(raiz, n)
        caminho = os.path.realpath(d)
        if "/virtual/" in caminho and not n.startswith(("vd", "xvd")):
            continue
        modelo = " ".join(filter(None, (ler(os.path.join(d, "device", "vendor")), ler(os.path.join(d, "device", "model"))))) or n
        rot = ler(os.path.join(d, "queue", "rotational"))
        barramento = "NVMe" if n.startswith("nvme") else "USB" if "/usb" in caminho else "Virtual" if n.startswith(("vd", "xvd")) \
            else "SATA" if n.startswith("sd") else ""
        pontos = list(montagens.get(f"/dev/{n}", []))
        for part in sorted(os.listdir(d)):
            if part.startswith(n):
                pontos += montagens.get(f"/dev/{part}", [])
        try:
            tamanho = int(ler(os.path.join(d, "size"), "0")) * 512
        except ValueError:
            tamanho = 0
        out[n] = {"nome": modelo.strip(), "tamanho": tamanho, "tipo": "HDD" if rot == "1" else "SSD" if rot == "0" else "",
                  "barramento": barramento, "letras": pontos}
    return out
