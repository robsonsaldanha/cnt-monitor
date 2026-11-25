"""Uso do computador enviado à empresa (só para o administrador saber se o computador precisa de upgrade).

Vai o MÍNIMO: processador, memória e disco em %, tempo ligado e a ficha da máquina (sistema, modelo do processador, memória e
discos). NÃO vai nada pessoal: nem tela, arquivos, senhas, teclas, nomes de janelas ou de programas, nem a lista de processos.
Só é enviado depois que a pessoa conecta este computador a uma empresa (licenca.parear).
"""
import os
import platform
import threading
import time

import psutil

from . import config, licenca

HUB = "https://cntoficial.com.br/v1/empresa"
INTERVALO_S = 60
INFO_CADA_S = 600          # a ficha da máquina muda pouco: vai a cada 10 minutos, não a cada medida


def _pct(v):
    return max(0, min(100, int(round(float(v)))))


def _disco_principal():
    raiz = (os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else "/"
    return psutil.disk_usage(raiz).percent


def _discos():
    out = []
    for p in psutil.disk_partitions(all=False):
        if os.name == "nt" and "cdrom" in (p.opts or ""):
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        out.append({"nome": p.device.rstrip("\\") if os.name == "nt" else p.mountpoint, "total": u.total, "usado": u.used})
        if len(out) >= 8:
            break
    return out


def ficha(cpu_nome=""):
    """Dados da máquina (sem nome do usuário nem do computador)."""
    return {"sistema": platform.platform()[:100], "cpu": str(cpu_nome or platform.processor() or "")[:120], "nucleos": psutil.cpu_count(logical=True) or 0,
            "ram_total": psutil.virtual_memory().total, "discos": _discos()}


def coletar(cpu_nome="", com_ficha=True):
    """O que vai ao hub numa medida. Todos os valores são números inteiros ou texto curto."""
    m = {"cpu": _pct(psutil.cpu_percent(interval=None)), "ram": _pct(psutil.virtual_memory().percent), "disco": _pct(_disco_principal()),
         "uptime_s": max(0, int(time.time() - psutil.boot_time())), "versao": config.VERSAO}
    if com_ficha:
        m["info"] = ficha(cpu_nome)
    return m


class Envio:
    def __init__(self, cpu_nome=lambda: ""):
        self.cpu_nome = cpu_nome
        self.ocupado = False
        self.erro = ""
        self.ultimo = 0
        self._ultima_ficha = 0
        psutil.cpu_percent(interval=None)       # a primeira leitura é sempre 0: descarta

    def enviar(self):
        chave = licenca.chave_computador()
        if not chave:
            return False
        agora = time.time()
        com_ficha = agora - self._ultima_ficha >= INFO_CADA_S
        try:
            corpo = coletar(self.cpu_nome() if com_ficha else "", com_ficha)
            licenca.pedir_com_chave(HUB, "metricas", corpo, chave)
            if com_ficha:
                self._ultima_ficha = agora
            self.ultimo, self.erro = agora, ""
            return True
        except licenca.LicencaErro as e:
            self.erro = str(e)
            if e.status == 401:                      # a empresa reiniciou a conexão ou removeu o computador
                licenca.chave_invalida(str(e))
            return False
        except Exception as e:  # noqa: BLE001
            self.erro = f"Falha ao enviar o uso do computador: {e}"
            return False

    def enviar_ao_fundo(self):
        if self.ocupado:
            return
        self.ocupado = True

        def _correr():
            try:
                self.enviar()
            finally:
                self.ocupado = False
        threading.Thread(target=_correr, daemon=True, name="metricas-empresa").start()
