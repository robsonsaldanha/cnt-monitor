"""Área de transferência no Linux para o acesso remoto: texto, imagem e arquivos.
X11: xclip. Wayland: wl-clipboard (wl-copy / wl-paste). Mesma interface da AreaWindows do agente."""
import os
import shutil
import hashlib
import subprocess
import urllib.parse

TEXTOS = ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "STRING", "TEXT")
ARQS_GNOME, ARQS_URI = "x-special/gnome-copied-files", "text/uri-list"


def _wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def caminhos_de_uris(txt):
    """Lista de file:// (uma por linha; a do GNOME começa com "copy" ou "cut") -> caminhos locais."""
    out = []
    for l in txt.splitlines():
        l = l.strip()
        if l.startswith("file://"):
            u = urllib.parse.urlsplit(l)
            if u.netloc in ("", "localhost"):
                out.append(urllib.parse.unquote(u.path))
    return out


def uris_de_caminhos(caminhos):
    return [urllib.parse.urlunsplit(("file", "", urllib.parse.quote(os.path.abspath(c)), "", "")) for c in caminhos]


def _kde():
    return "KDE" in (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()


class AreaLinux:
    def __init__(self):
        self._w = _wayland()
        # sem o programa a área de transferência só não funciona (o resto da sessão segue): o erro aparece no uso
        self.disponivel = bool(shutil.which("wl-paste" if self._w else "xclip"))

    # --- baixo nível
    def _tipos(self):
        cmd = ["wl-paste", "--list-types"] if self._w else ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return []
        return r.stdout.decode("utf-8", "replace").split() if r.returncode == 0 else []

    def _ler(self, tipo):
        cmd = ["wl-paste", "--no-newline", "--type", tipo] if self._w else ["xclip", "-selection", "clipboard", "-t", tipo, "-o"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def _gravar(self, tipo, dados):
        cmd = ["wl-copy", "--type", tipo] if self._w else ["xclip", "-selection", "clipboard", "-t", tipo, "-i"]
        # o xclip/wl-copy fica rodando para "segurar" o conteúdo: sem canos de saída, senão o run() esperaria por ele
        subprocess.run(cmd, input=dados, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)

    # --- interface do agente
    def ler(self):
        tipos = self._tipos()
        for t in (ARQS_GNOME, ARQS_URI):
            if t in tipos:
                cs = caminhos_de_uris((self._ler(t) or b"").decode("utf-8", "replace"))
                if cs:
                    return "arquivos", cs
        t = next((t for t in TEXTOS if t in tipos), None)
        if t:
            b = self._ler(t)
            if b is not None:
                return "texto", b.decode("utf-8", "replace")
        if "image/png" in tipos:
            b = self._ler("image/png")
            if b:
                return "imagem", b
        return None

    def seq(self):
        """Muda quando o conteúdo muda (o X11 e o Wayland não têm um contador como o do Windows)."""
        c = self.ler()
        if c is None:
            return ""
        dado = c[1] if isinstance(c[1], bytes) else repr(c[1]).encode("utf-8", "replace")
        return c[0] + hashlib.sha1(dado).hexdigest()

    def texto(self, texto):
        self._gravar("text/plain;charset=utf-8" if self._w else "UTF8_STRING", texto.encode("utf-8"))

    def imagem(self, png):
        self._gravar("image/png", png)

    def arquivos(self, caminhos):
        """Arquivos prontos para o Ctrl+V no gerenciador de arquivos (Nautilus/Nemo/Thunar/Caja ou Dolphin)."""
        uris = uris_de_caminhos(caminhos)
        if _kde():
            self._gravar(ARQS_URI, ("\r\n".join(uris) + "\r\n").encode("utf-8"))
        else:
            self._gravar(ARQS_GNOME, ("copy\n" + "\n".join(uris)).encode("utf-8"))
