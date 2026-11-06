"""Ícone do Monitor do PC perto do relógio. Abre a janela do programa.

  MonitorPC.exe            -> abre a janela (e o ícone, se ainda não estiver aberto)
  MonitorPC.exe --bandeja  -> só o ícone (usado ao ligar o Windows)
"""
import os
import sys
import json
import time
import shutil
import threading
import subprocess
import webbrowser
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitorpc import config  # noqa: E402

WIN = os.name == "nt"
PASTA_USUARIO = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "MonitorPC")
ICONE = None


def _log():
    os.makedirs(PASTA_USUARIO, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        f = open(os.path.join(PASTA_USUARIO, "bandeja.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def navegador():
    if not WIN:
        return None
    for c in (
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ):
        if c and os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def abrir_janela(aba=""):
    url = config.URL + (f"#{aba}" if aba else "")
    nav = navegador()
    if nav:
        perfil = os.path.join(PASTA_USUARIO, "janela")
        subprocess.Popen([nav, f"--app={url}", f"--user-data-dir={perfil}", "--window-size=1440,900",
                          "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                          "--disable-sync"], close_fds=True)
    else:
        webbrowser.open(url)


def ler_agora():
    try:
        with urllib.request.urlopen(config.URL + "api/agora", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def desenhar(mem, cpu, ok=True):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(28, 30, 38, 255))
    if not ok:
        d.line([18, 18, 46, 46], fill=(239, 83, 80, 255), width=7)
        d.line([46, 18, 18, 46], fill=(239, 83, 80, 255), width=7)
        return img

    def cor(v, alto, medio):
        if v >= alto:
            return (239, 83, 80, 255)
        if v >= medio:
            return (255, 183, 77, 255)
        return (102, 187, 106, 255)

    for x0, x1, v, c in ((10, 38, mem, cor(mem, 88, 75)), (44, 54, cpu, cor(cpu, 85, 60))):
        d.rounded_rectangle([x0, 10, x1, 54], radius=4, fill=(55, 58, 70, 255))
        h = max(3, int(44 * min(v, 100) / 100))
        d.rounded_rectangle([x0, 54 - h, x1, 54], radius=4, fill=c)
    return img


def atualizar():
    ultimo = None
    while True:
        e = ler_agora()
        try:
            if ICONE is not None:
                if e:
                    chave = (int(e["mem_pct"] // 4), int(e["cpu"] // 10))
                    if chave != ultimo:
                        ICONE.icon = desenhar(e["mem_pct"], e["cpu"])
                        ultimo = chave
                    partes = [f"Memória {e['mem_pct']:.0f}%", f"CPU {e['cpu']:.0f}%"]
                    if e.get("temp_cpu"):
                        partes.append(f"{e['temp_cpu']:.0f}°C")
                    ICONE.title = "Monitor do PC — " + " · ".join(partes)
                else:
                    if ultimo != "erro":
                        ICONE.icon = desenhar(0, 0, ok=False)
                        ultimo = "erro"
                    ICONE.title = "Monitor do PC — serviço não está respondendo"
        except Exception:
            pass
        time.sleep(5)


def instancia_unica():
    if not WIN:
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MonitorPC-Bandeja")
    return ctypes.windll.kernel32.GetLastError() != 183


def main():
    global ICONE
    _log()
    so_icone = "--bandeja" in sys.argv
    if not instancia_unica():
        if not so_icone:
            abrir_janela()
        return
    if not so_icone:
        threading.Timer(0.5, abrir_janela).start()
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem("Abrir Monitor do PC", lambda *_: abrir_janela(), default=True),
        pystray.MenuItem("Relatório", lambda *_: abrir_janela("relatorio")),
        pystray.MenuItem("Rede e conexões", lambda *_: abrir_janela("rede")),
        pystray.MenuItem("Temperatura", lambda *_: abrir_janela("hardware")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Fechar ícone (o monitoramento continua)", lambda *_: ICONE.stop()),
    )
    ICONE = pystray.Icon("monitor-pc", desenhar(0, 0), "Monitor do PC", menu)
    threading.Thread(target=atualizar, daemon=True).start()
    ICONE.run()


if __name__ == "__main__":
    main()
