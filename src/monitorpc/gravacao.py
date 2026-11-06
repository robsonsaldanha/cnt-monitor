"""Gravação de tela em MP4 e GIF com o ffmpeg (roda no ícone da bandeja).

O ffmpeg não vai no instalador (cerca de 90 MB): é baixado na primeira gravação, do build do BtbN no GitHub,
conferindo o SHA-256 publicado na mesma release. Se já houver um ffmpeg no PATH, ele é usado.
"""
import io
import os
import time
import shutil
import hashlib
import zipfile
import threading
import subprocess
import urllib.request

from . import config

SEM_JANELA = 0x08000000
NOME_ZIP = "ffmpeg-master-latest-win64-gpl-shared.zip"
URL_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
AGENTE = f"MonitorDoPC/{config.VERSAO}"


def pasta_ffmpeg():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MonitorPC", "ffmpeg")


def ffmpeg():
    """Caminho do ffmpeg.exe ou None."""
    p = os.path.join(pasta_ffmpeg(), "ffmpeg.exe")
    if os.path.isfile(p):
        return p
    return shutil.which("ffmpeg")


def _baixar(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENTE}), timeout=timeout)


def baixar_ffmpeg(progresso=None, destino=None):
    """Baixa e confere o ffmpeg. progresso(baixado, total). Devolve o caminho do ffmpeg.exe."""
    destino = destino or pasta_ffmpeg()
    with _baixar(URL_BASE + "checksums.sha256") as r:
        somas = r.read().decode("utf-8", "replace")
    esperado = next((ln.split()[0].lower() for ln in somas.splitlines()
                     if ln.strip().endswith(NOME_ZIP) and len(ln.split()[0]) == 64), None)
    if not esperado:
        raise RuntimeError("não encontrei a soma de verificação do ffmpeg; tente mais tarde")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    parte = destino + ".zip.part"
    h = hashlib.sha256()
    with _baixar(URL_BASE + NOME_ZIP, timeout=120) as r, open(parte, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        feito = 0
        while True:
            bloco = r.read(1 << 20)
            if not bloco:
                break
            f.write(bloco)
            h.update(bloco)
            feito += len(bloco)
            if progresso:
                progresso(feito, total)
    try:
        if h.hexdigest() != esperado:
            raise RuntimeError("o download do ffmpeg veio corrompido (soma de verificação não confere); tente de novo")
        novo = destino + ".novo"
        shutil.rmtree(novo, ignore_errors=True)
        os.makedirs(novo)
        with zipfile.ZipFile(parte) as z:
            for nome in z.namelist():
                base = os.path.basename(nome)
                if "/bin/" in nome and base and base.lower().endswith((".exe", ".dll")) and base.lower() != "ffplay.exe":
                    with z.open(nome) as o, open(os.path.join(novo, base), "wb") as d:
                        shutil.copyfileobj(o, d)
        if not os.path.isfile(os.path.join(novo, "ffmpeg.exe")):
            raise RuntimeError("o pacote do ffmpeg não tem o ffmpeg.exe")
        shutil.rmtree(destino, ignore_errors=True)
        os.replace(novo, destino)
    finally:
        try:
            os.remove(parte)
        except OSError:
            pass
    return os.path.join(destino, "ffmpeg.exe")


def _par(n):
    return max(2, n - n % 2)  # o H.264 (yuv420p) exige largura e altura pares


class Gravador:
    def __init__(self):
        self.proc = None
        self.saida = None
        self.tipo = None
        self.inicio = 0
        self.borda = None
        self._lock = threading.Lock()

    @property
    def gravando(self):
        return self.proc is not None and self.proc.poll() is None

    def iniciar(self, ff, bbox, saida, tipo="video", fps=30, cursor=True):
        """bbox = (esq, topo, dir, baixo) em pixels da tela."""
        with self._lock:
            if self.gravando:
                raise RuntimeError("já existe uma gravação em andamento")
            l, t, r, b = bbox
            w, h = _par(r - l), _par(b - t)
            cmd = [ff, "-hide_banner", "-loglevel", "error", "-f", "gdigrab", "-framerate", str(fps),
                   "-draw_mouse", "1" if cursor else "0", "-offset_x", str(l), "-offset_y", str(t),
                   "-video_size", f"{w}x{h}", "-i", "desktop", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "18" if tipo == "gif" else "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", saida]
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         creationflags=SEM_JANELA)
            time.sleep(0.6)
            if self.proc.poll() is not None:
                erro = self.proc.stderr.read().decode("utf-8", "replace").strip()[-300:]
                self.proc = None
                raise RuntimeError("o ffmpeg não conseguiu gravar: " + (erro or "erro desconhecido"))
            self.saida, self.tipo, self.inicio = saida, tipo, time.time()
            self.borda = Borda((l, t, l + w, t + h))

    def parar(self):
        """Encerra a gravação e devolve (arquivo, tipo, segundos)."""
        with self._lock:
            p, self.proc = self.proc, None
            if self.borda:
                self.borda.fechar()
                self.borda = None
            if p is None:
                return None
            try:
                p.stdin.write(b"q")
                p.stdin.flush()
                p.wait(30)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()
                p.wait(5)
            if not os.path.isfile(self.saida) or os.path.getsize(self.saida) == 0:
                raise RuntimeError("a gravação não gerou o arquivo de vídeo")
            return self.saida, self.tipo, time.time() - self.inicio


def para_gif(ff, mp4, gif, fps=15, largura_max=960):
    filtro = (f"fps={fps},scale='min({largura_max},iw)':-2:flags=lanczos,split[a][b];"
              f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", mp4, "-vf", filtro, "-y", gif],
                       capture_output=True, timeout=600, creationflags=SEM_JANELA)
    if r.returncode != 0 or not os.path.isfile(gif):
        raise RuntimeError("não consegui converter para GIF: " + r.stderr.decode("utf-8", "replace").strip()[-200:])


def primeiro_quadro(ff, video):
    """Imagem (PIL) do primeiro quadro, para a miniatura do histórico."""
    from PIL import Image
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", video, "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"], capture_output=True, timeout=60, creationflags=SEM_JANELA)
    if r.returncode != 0 or not r.stdout:
        return None
    return Image.open(io.BytesIO(r.stdout))


class Borda:
    """Moldura vermelha em volta da área gravada (fica do lado de fora, não aparece no vídeo; o mouse passa através)."""

    def __init__(self, bbox):
        self.bbox = bbox
        self._fim = threading.Event()
        threading.Thread(target=self._tk, daemon=True).start()

    def fechar(self):
        self._fim.set()

    def _tk(self):
        from .captura import coletar_tk
        try:
            self._janelas()
        finally:
            coletar_tk()

    def _janelas(self):
        import ctypes
        import tkinter as tk
        l, t, r, b = self.bbox
        e = 3
        root = tk.Tk()
        root.withdraw()
        jan = []
        for x, y, w, h in ((l - e, t - e, r - l + 2 * e, e), (l - e, b, r - l + 2 * e, e),
                           (l - e, t, e, b - t), (r, t, e, b - t)):
            j = tk.Toplevel(root)
            j.overrideredirect(True)
            j.attributes("-topmost", True)
            j.configure(bg="#ef3b3b")
            j.geometry(f"{max(1, w)}x{max(1, h)}+{x}+{y}")
            jan.append(j)
        root.update_idletasks()
        try:
            u = ctypes.windll.user32
            for j in jan:
                hwnd = u.GetParent(j.winfo_id()) or j.winfo_id()
                u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x00080000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass

        def vigiar():
            if self._fim.is_set():
                root.destroy()
                return
            root.after(150, vigiar)

        root.after(150, vigiar)
        root.mainloop()
