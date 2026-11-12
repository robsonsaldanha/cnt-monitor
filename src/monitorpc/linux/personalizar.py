"""Personalizar no Linux: tema claro/escuro e papel de parede em GNOME/Ubuntu, Cinnamon/Mint, KDE e XFCE.

Roda no ícone, com o usuário da sessão (gsettings, plasma-apply-* e xfconf-query falam com a sessão dele).
Barra, relógio e ícones perto do relógio são do Windows: aqui a lista fica vazia e a tela esconde essa parte.
"""
import os
import re

from . import rodar

PASTAS_TEMA = ("/usr/share/themes", os.path.expanduser("~/.themes"), os.path.expanduser("~/.local/share/themes"))


def ambiente():
    d = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    if "kde" in d or "plasma" in d:
        return "kde"
    if "cinnamon" in d:
        return "cinnamon"
    if "xfce" in d:
        return "xfce"
    if "mate" in d:
        return "mate"
    return "gnome"      # GNOME, Ubuntu, Pop, Budgie, Unity: usam org.gnome.desktop.*


def temas_instalados():
    out = set()
    for p in PASTAS_TEMA:
        try:
            out.update(os.listdir(p))
        except OSError:
            pass
    return out


def par_do_tema(nome, escuro, existentes):
    """O mesmo tema na versão clara ou escura (Yaru <-> Yaru-dark, Mint-Y-Aqua <-> Mint-Y-Dark-Aqua,
    Adwaita <-> Adwaita-dark). None se não houver o par instalado."""
    base = re.sub(r"-[Dd]ark(?=-|$)", "", nome or "")
    if not base:
        return None
    if not escuro:
        return base if base in existentes else None
    cands = [base + "-dark", base + "-Dark"]
    m = re.match(r"(Mint-[A-Z])(-.*)?$", base)
    if m:
        cands.insert(0, m.group(1) + "-Dark" + (m.group(2) or ""))
    return next((c for c in cands if c in existentes), None)


def _gs(*args):
    return rodar(["gsettings", *args])


def _gs_ler(esquema, chave):
    c, s = _gs("get", esquema, chave)
    return s.strip().strip("'") if c == 0 else None


def _gtk(esquema, escuro):
    atual = _gs_ler(esquema, "gtk-theme")
    novo = par_do_tema(atual, escuro, temas_instalados()) if atual else None
    if novo and novo != atual:
        _gs("set", esquema, "gtk-theme", novo)


def tema_atual():
    amb = ambiente()
    if amb == "kde":
        c, s = rodar(["kreadconfig5", "--group", "General", "--key", "ColorScheme"])
        return "escuro" if c == 0 and "dark" in s.lower() else "claro"
    v = _gs_ler("org.gnome.desktop.interface", "color-scheme") or ""
    return "escuro" if "dark" in v else "claro"


def aplicar_tema(modo):
    escuro = modo == "escuro"
    amb = ambiente()
    if amb == "kde":
        c, s = rodar(["plasma-apply-colorscheme", "BreezeDark" if escuro else "BreezeLight"])
        return (c == 0), ("Tema " + modo + ".") if c == 0 else s.strip()[:200]
    if amb == "xfce":
        c, s = rodar(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"])
        novo = par_do_tema(s.strip(), escuro, temas_instalados()) if c == 0 else None
        if not novo:
            return False, "Não achei a versão " + modo + " do tema atual."
        c, s = rodar(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName", "-s", novo])
        return c == 0, ("Tema " + modo + ".") if c == 0 else s.strip()[:200]
    c, s = _gs("set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark" if escuro else "default")
    if amb == "cinnamon":
        _gs("set", "org.x.apps.portal", "color-scheme", "prefer-dark" if escuro else "prefer-light")
        _gtk("org.cinnamon.desktop.interface", escuro)
    elif amb == "mate":
        _gtk("org.mate.interface", escuro)
    else:
        _gtk("org.gnome.desktop.interface", escuro)
    return c == 0, ("Tema " + modo + ".") if c == 0 else s.strip()[:200]


def definir_papel(caminho):
    if not os.path.isfile(caminho):
        return False, "Imagem não encontrada."
    uri = "file://" + os.path.abspath(caminho)
    amb = ambiente()
    if amb == "kde":
        c, s = rodar(["plasma-apply-wallpaperimage", caminho])
    elif amb == "xfce":
        c, s = rodar(["xfconf-query", "-c", "xfce4-desktop", "-l"])
        props = [p for p in s.split() if p.endswith("/last-image")] if c == 0 else []
        for p in props:
            c, s = rodar(["xfconf-query", "-c", "xfce4-desktop", "-p", p, "-s", caminho])
        if not props:
            c, s = 1, "Não achei os monitores no xfconf."
    elif amb == "cinnamon":
        c, s = _gs("set", "org.cinnamon.desktop.background", "picture-uri", uri)
    elif amb == "mate":
        c, s = _gs("set", "org.mate.background", "picture-filename", caminho)
    else:
        c, s = _gs("set", "org.gnome.desktop.background", "picture-uri", uri)
        _gs("set", "org.gnome.desktop.background", "picture-uri-dark", uri)
    return c == 0, "Papel de parede trocado." if c == 0 else s.strip()[:200]


def ler_sistema():
    return []


def aplicar_sistema(_ident, _valor):
    return False, "Essa opção é do Windows."


def icones_bandeja():
    return []


def definir_icone(_ident, _visivel):
    return False, "Essa opção é do Windows."


def reiniciar_explorer():
    return False, "Essa opção é do Windows."


def mostrar_icones_area(_sim):
    return False, "Essa opção é do Windows."


def ocultar_barra(_sim):
    return False, "Essa opção é do Windows."
