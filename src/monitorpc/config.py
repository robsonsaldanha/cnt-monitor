"""Constantes e caminhos compartilhados entre o serviço e o ícone da bandeja."""
import os
import sys

VERSAO = "2.2.0"
WIN = os.name == "nt"

if os.environ.get("MONITORPC_DADOS"):
    BASE = os.environ["MONITORPC_DADOS"]
elif WIN:
    BASE = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "MonitorPC")
else:
    BASE = os.path.expanduser("~/.local/share/MonitorPC")

DB = os.path.join(BASE, "historico.db")
LOG = os.path.join(BASE, "servico.log")
CONFIG = os.path.join(BASE, "config.json")
TOKEN = os.path.join(BASE, "token.txt")

PORTA = 48731
URL = f"http://127.0.0.1:{PORTA}/"

GRAVAR_CADA = 15           # histórico geral e por programa (s)
GRAVAR_PROCS_CADA = 60     # histórico por processo (s)
GRAVAR_TEMPS_CADA = 30     # histórico de temperatura (s)
LIMPAR_CADA = 3600
JANELA_TAXA = 1.0          # CPU/disco/rede são a média do último segundo

# valores padrão das configurações (o usuário muda na tela Configurações)
PADRAO = {
    "intervalo_ms": 250,
    "iniciar_com_windows": True,
    "abrir_janela_ao_iniciar": False,
    "tema": "auto",
    "rede_por_programa": True,
    "dns_reverso": True,
    "rede_em_bits": False,
    "sensores": True,
    "sensores_intervalo_s": 2,
    "gpu_por_processo": True,
    "limiar_mem": 88,
    "limiar_cpu": 85,
    "limiar_temp": 90,
    "retencao_dias": 7,
    "permitir_encerrar": True,
    "confirmar_encerrar": True,
    "alertas_ativos": True,
    "alertas": None,            # preenchido com alertas.PADRAO
    "jogo_fechar": [],          # programas que o Modo Jogo fecha
    "jogo_reabrir": True,
    "jogo_plano": True,
    "jogo_prioridade": True,
    "jogo_modo_windows": True,
    "jogo_silenciar": True,
    "jogo_fps": True,
    "atualizacao_auto": True,
}

OPCOES = {
    "intervalo_ms": [250, 500, 1000, 2000, 5000],
    "tema": ["auto", "escuro", "claro"],
    "sensores_intervalo_s": [1, 2, 5, 10],
    "retencao_dias": [1, 3, 7, 15, 30],
}


def pasta_recursos():
    """Pasta onde ficam ui/ e lhm/ (dentro do executável ou no código-fonte)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pasta_programa():
    """Pasta de instalação (onde fica MonitorPC.exe)."""
    if getattr(sys, "frozen", False):
        d = os.path.dirname(sys.executable)
        return os.path.dirname(d) if os.path.basename(d).lower() == "servico" else d
    return os.path.dirname(pasta_recursos())
