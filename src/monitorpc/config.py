"""Constantes e caminhos compartilhados entre o serviço e o ícone da bandeja."""
import os
import sys

VERSAO = "2.3.3"
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
    "varias_janelas": False,      # permitir mais de uma janela do Monitor aberta
    "mini_ativo": False,
    "mini_itens": ["cpu", "mem", "temp_cpu", "gpu", "fps"],
    "mini_canto": "sup_dir",
    "mini_opacidade": 85,
    "mini_tamanho": "normal",     # (antigo — substituído por mini_escala)
    "mini_layout": "horizontal",
    "mini_escala": 100,           # % do tamanho do texto (50 a 300)
    "mini_monitor": 1,            # 1 = monitor principal, 2 = segundo…
    "mini_margem_x": 16,          # distância da borda (pixels)
    "mini_margem_y": 16,
    "mini_fundo": True,           # fundo escuro atrás dos números
    "regras_prog": [],            # regras por programa (prioridade, núcleos, bloquear, limite)
    "vazamento_ativo": True,
    "acesso_remoto": False,       # acesso pelo celular (rede de casa)
    "acesso_porta": 48732,
    "acesso_somente_ver": True,
    "acesso_senha": "",           # hash (nunca a senha)
    "api_chave": "",              # chave para outros programas lerem a API pela rede
    "api_cors": False,            # deixa páginas web deste PC lerem a API (só leitura)
    "vps": [],                    # servidores cadastrados (sem senhas: ficam criptografadas à parte)
    "captura_ativa": True,        # atalhos de captura de tela ligados (quem captura é o ícone da bandeja)
    "captura_atalhos": None,      # preenchido com captura.ATALHOS_PADRAO
    "captura_pasta": "",          # vazio = Imagens\Monitor do PC
    "captura_nome": "%Y-%m-%d_%H-%M-%S",
    "captura_formato": "png",
    "captura_qualidade": 90,      # JPG
    # tarefas pós-captura (na ordem em que rodam; na área de transferência fica a última)
    "captura_copiar": True,       # copiar a imagem para a área de transferência
    "captura_salvar": True,
    "captura_perguntar": False,   # "Salvar como…": perguntar pasta e nome a cada captura
    "captura_copiar_caminho": False,
    "captura_mostrar_pasta": False,  # abrir o Explorer com o arquivo selecionado
    "captura_ocr_auto": False,    # copiar o texto da imagem (OCR)
    "captura_notificar": True,
    "captura_editor": False,      # abrir no editor depois de capturar
    "captura_enviar": False,      # enviar (upload) e copiar o link
    # tarefas pós-envio
    "captura_envio_copiar": True,
    "captura_envio_formato": "url",
    "captura_envio_abrir": False,  # abrir o link no navegador
    "captura_envio_notificar": True,
    # seleção de área
    "captura_anotar": True,       # barra de anotação (setas, texto, desfocar…) antes de confirmar a região
    "captura_lupa": True,
    "captura_destino": "catbox",
    "captura_litter_tempo": "24h",
    "captura_imgur_id": "",       # Client-ID do Imgur (registrado pelo usuário)
    "captura_uploader": None,     # preenchido com envio.UPLOADER_PADRAO
    "captura_atraso_s": 3,
    "captura_gravar_fps": 30,
    "captura_gravar_cursor": True,
}

OPCOES = {
    "intervalo_ms": [250, 500, 1000, 2000, 5000],
    "tema": ["auto", "escuro", "claro"],
    "sensores_intervalo_s": [1, 2, 5, 10],
    "retencao_dias": [1, 3, 7, 15, 30],
    "mini_canto": ["sup_dir", "sup_esq", "inf_dir", "inf_esq", "sup_centro", "inf_centro"],
    "mini_tamanho": ["pequeno", "normal", "grande"],
    "mini_layout": ["horizontal", "vertical"],
    "captura_formato": ["png", "jpg"],
    "captura_destino": ["catbox", "litterbox", "uguu", "tmpfiles", "imgur", "personalizado"],
    "captura_litter_tempo": ["1h", "12h", "24h", "72h"],
    "captura_envio_formato": ["url", "markdown", "html", "bbcode"],
    "captura_atraso_s": [1, 2, 3, 5, 10],
    "captura_gravar_fps": [10, 15, 24, 30, 60],
}
SEGREDOS = os.path.join(BASE, "segredos.bin")


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
