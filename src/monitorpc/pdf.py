"""PDF pela impressora de PDF do Chrome (modo headless, sem abrir janela).

O Chrome guarda os links internos do HTML no PDF; a impressora padrão do Windows não. Se não houver Chrome, usa o Edge
(mesmo motor). Sem nenhum dos dois, `renderizar` devolve None e a tela cai na janela de impressão normal.
"""
import os
import shutil
import subprocess
import sys
import tempfile

LIMITE_HTML = 60_000_000
ESPERA = 90          # segundos para o navegador gerar o PDF


def _candidatos():
    if sys.platform == "win32":
        pastas = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for sub in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            for p in pastas:
                if p:
                    yield os.path.join(p, sub)
    else:
        for nome in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            achado = shutil.which(nome)
            if achado:
                yield achado


def navegador():
    """Caminho do Chrome (ou do Edge, se faltar) para imprimir em PDF; None se não houver."""
    for c in _candidatos():
        if os.path.isfile(c):
            return c
    return None


def _como_root():
    if sys.platform == "win32":
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def renderizar(html):
    """HTML (str) -> bytes do PDF, ou None se não deu (sem navegador, erro ou tempo esgotado)."""
    exe = navegador()
    if not exe or not isinstance(html, str) or not html or len(html) > LIMITE_HTML:
        return None
    pasta = tempfile.mkdtemp(prefix="cntpdf-")
    try:
        entrada = os.path.join(pasta, "relatorio.html")
        saida = os.path.join(pasta, "relatorio.pdf")
        with open(entrada, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--user-data-dir=" + os.path.join(pasta, "perfil"), "--no-pdf-header-footer",
               "--virtual-time-budget=4000", "--print-to-pdf=" + saida]
        if _como_root():
            cmd.append("--no-sandbox")
        cmd.append("file:///" + entrada.replace("\\", "/").lstrip("/"))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=ESPERA, creationflags=flags)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if not os.path.isfile(saida):
            return None
        with open(saida, "rb") as f:
            dados = f.read()
        return dados if dados.startswith(b"%PDF") else None
    finally:
        shutil.rmtree(pasta, ignore_errors=True)
