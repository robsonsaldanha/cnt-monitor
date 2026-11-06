"""Configurações compartilhadas entre o serviço e o ícone da bandeja."""
import os
import sys

VERSAO = "2.0.0"
WIN = os.name == "nt"

if os.environ.get("MONITORPC_DADOS"):
    BASE = os.environ["MONITORPC_DADOS"]
elif WIN:
    BASE = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "MonitorPC")
else:
    BASE = os.path.expanduser("~/.local/share/MonitorPC")

DB = os.path.join(BASE, "historico.db")
LOG = os.path.join(BASE, "servico.log")

PORTA = 48731
URL = f"http://127.0.0.1:{PORTA}/"

INTERVALO = 5              # leitura ao vivo (s)
GRAVAR_CADA = 15           # histórico geral e por programa (s)
GRAVAR_PROCS_CADA = 60     # histórico por processo (s)
GRAVAR_TEMPS_CADA = 30     # histórico de temperatura (s)
RETENCAO = 7 * 24 * 3600   # 7 dias
LIMPAR_CADA = 3600

LIMIAR_MEM = 88
LIMIAR_CPU = 85


def pasta_recursos():
    """Pasta onde ficam ui/ e lhm/ (dentro do executável ou no código-fonte)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
