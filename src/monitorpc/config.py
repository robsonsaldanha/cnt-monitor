"""Constantes e caminhos compartilhados entre o serviço e o ícone da bandeja."""
import os
import sys

VERSAO = "2.4.1"
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
    "captura_qualidade": 90,      # JPG e WebP
    "captura_webp_sem_perda": False,
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
    "captura_tarefas_rapidas": False,  # menu perto do mouse para escolher o que fazer com a captura
    "captura_janela_pos": False,  # janela "Pós-captura" com a prévia e os botões
    "captura_embelezar": False,   # margem, fundo, cantos arredondados e sombra
    "captura_efeitos": False,     # borda e marca d'água
    "captura_editor_fotos": False,  # abrir no editor de imagens do Windows (verbo "editar")
    "captura_fixar": False,       # fixar na tela
    "captura_imprimir": False,
    "captura_miniatura": False,   # salvar também uma miniatura (_mini)
    "captura_miniatura_largura": 300,
    "captura_rodar_acoes": False,  # rodar os programas de captura_acoes com o arquivo
    "captura_copiar_arquivo": False,  # o arquivo em si (para colar no Explorer, WhatsApp, Discord)
    "captura_copiar_pasta": False,
    "captura_qr": False,          # ler código QR da imagem e copiar o conteúdo
    "captura_ia": False,          # analisar a imagem com IA (chave do usuário)
    "captura_pre_envio": False,   # confirmar (com a prévia) antes de enviar
    "captura_apagar_local": False,  # depois de enviar, mandar o arquivo para a Lixeira
    "captura_sem_metadados": True,  # tirar EXIF/textos das imagens antes de enviar
    # aviso próprio (no lugar da notificação do Windows)
    "captura_aviso_clique": "abrir",
    "captura_aviso_canto": "inf_dir",
    "captura_aviso_segundos": 6,
    "captura_aviso_botoes": True,
    "captura_som": False,         # som ao concluir
    # embelezar e efeitos
    "captura_emb_margem": 40,
    "captura_emb_fundo": "azul",
    "captura_emb_cantos": 12,
    "captura_emb_sombra": True,
    "captura_marca_texto": "",
    "captura_marca_canto": "inf_dir",
    "captura_marca_opacidade": 60,
    "captura_borda": 0,
    "captura_borda_cor": "#000000",
    "captura_acoes": [],          # [{nome, programa, args}] — só configurável no próprio PC
    "captura_fluxos": [],         # fluxos de trabalho: atalho próprio + ação + tarefas
    # análise por IA (a chave fica cifrada no perfil do usuário, nunca na configuração)
    "captura_ia_provedor": "openai",
    "captura_ia_modelo": "",
    "captura_ia_prompt": "Descreva esta imagem em português, de forma objetiva. Se houver texto, transcreva.",
    # tarefas pós-envio
    "captura_envio_copiar": True,
    "captura_envio_formato": "url",
    "captura_envio_abrir": False,  # abrir o link no navegador
    "captura_envio_notificar": True,
    "captura_envio_janela": False,  # janela "Pós-envio" com link, QR e botões
    "captura_envio_encurtar": False,  # is.gd
    "captura_envio_compartilhar": "",  # "" ou um serviço de envio.COMPARTILHAR
    "captura_envio_qr": False,      # janela com o código QR do link
    # seleção de área
    "captura_anotar": True,       # barra de anotação (setas, texto, desfocar…) antes de confirmar a região
    "captura_lupa": True,
    "captura_escurecer": True,    # escurecer fora da área escolhida
    "captura_cruz": True,         # linhas que seguem o mouse e cruz no centro da área
    "captura_destino": "catbox",
    "captura_litter_tempo": "24h",
    "captura_imgur_id": "",       # Client-ID do Imgur (registrado pelo usuário)
    "captura_uploader": None,     # preenchido com envio.UPLOADER_PADRAO
    "captura_atraso_s": 3,
    "captura_afastar_mouse": "",  # canto para onde o mouse vai antes de fotografar ("" = não mexe)
    "captura_afastar_voltar": True,
    "captura_gravar_fps": 30,
    "captura_gravar_cursor": True,
    "captura_gravar_audio": "",   # nome do dispositivo de áudio (DirectShow): microfone ou "Mixagem estéreo"
    "captura_gravar_atraso": 0,
    "captura_gravar_cronometro": True,
}

OPCOES = {
    "intervalo_ms": [250, 500, 1000, 2000, 5000],
    "tema": ["auto", "escuro", "claro"],
    "sensores_intervalo_s": [1, 2, 5, 10],
    "retencao_dias": [1, 3, 7, 15, 30],
    "mini_canto": ["sup_dir", "sup_esq", "inf_dir", "inf_esq", "sup_centro", "inf_centro"],
    "mini_tamanho": ["pequeno", "normal", "grande"],
    "mini_layout": ["horizontal", "vertical"],
    "captura_formato": ["png", "jpg", "webp"],
    "captura_aviso_clique": ["abrir", "pasta", "editor", "link", "copiar", "fixar", "nada"],
    "captura_aviso_canto": ["inf_dir", "inf_esq", "sup_dir", "sup_esq"],
    "captura_aviso_segundos": [3, 4, 6, 8, 10, 15, 30],
    "captura_miniatura_largura": [100, 150, 200, 300, 400, 600],
    "captura_emb_margem": [0, 16, 24, 40, 64, 96],
    "captura_emb_fundo": ["azul", "roxo", "por_do_sol", "verde", "escuro", "claro", "transparente"],
    "captura_emb_cantos": [0, 6, 12, 20, 32],
    "captura_marca_canto": ["inf_dir", "inf_esq", "sup_dir", "sup_esq", "centro"],
    "captura_borda": [0, 1, 2, 4, 8],
    "captura_envio_compartilhar": ["", "whatsapp", "telegram", "x", "reddit", "email"],
    "captura_ia_provedor": ["openai", "gemini", "openrouter"],
    "captura_gravar_atraso": [0, 3, 5, 10],
    "captura_destino": ["catbox", "litterbox", "uguu", "tmpfiles", "imgur", "personalizado"],
    "captura_litter_tempo": ["1h", "12h", "24h", "72h"],
    "captura_envio_formato": ["url", "markdown", "html", "bbcode"],
    "captura_atraso_s": [1, 2, 3, 5, 10],
    "captura_afastar_mouse": ["", "sup_esq", "sup_dir", "inf_esq", "inf_dir"],
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
