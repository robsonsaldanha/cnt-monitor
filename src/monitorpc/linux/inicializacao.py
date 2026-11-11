"""Programas que abrem ao entrar na sessão: autostart do XDG (vale para GNOME, KDE, Cinnamon, XFCE e MATE).
/etc/xdg/autostart vale para todos os usuários; ~/.config/autostart, para cada um."""
import os
import pwd
import shlex
import shutil
import hashlib

SISTEMA = "/etc/xdg/autostart"


def ler_desktop(texto):
    d, secao = {}, None
    for linha in texto.splitlines():
        s = linha.strip()
        if s.startswith("["):
            secao = s
            continue
        if secao == "[Desktop Entry]" and "=" in s and not s.startswith("#"):
            k, _, v = s.partition("=")
            d.setdefault(k.strip(), v.strip())
    return d


def ativo(d):
    return not (d.get("Hidden", "").lower() == "true" or d.get("X-GNOME-Autostart-enabled", "").lower() == "false")


def _usuarios():
    out = []
    for p in pwd.getpwall():
        if p.pw_uid >= 1000 and p.pw_uid < 65534 and os.path.isdir(p.pw_dir):
            out.append((p.pw_name, os.path.join(p.pw_dir, ".config", "autostart"), p.pw_uid, p.pw_gid))
    return out


def _id(*partes):
    return hashlib.sha1("|".join(partes).encode("utf-8", "replace")).hexdigest()[:16]


def listar():
    itens = []
    pastas = [("Todos os usuários", SISTEMA, None, None)] + [(u, p, uid, gid) for u, p, uid, gid in _usuarios()]
    for escopo, pasta, uid, gid in pastas:
        try:
            nomes = sorted(f for f in os.listdir(pasta) if f.endswith(".desktop"))
        except OSError:
            continue
        for f in nomes:
            arq = os.path.join(pasta, f)
            try:
                with open(arq, encoding="utf-8", errors="replace") as a:
                    d = ler_desktop(a.read())
            except OSError:
                continue
            cmd = d.get("Exec", "")
            try:
                partes = [x for x in shlex.split(cmd) if not x.startswith("%")]
            except ValueError:
                partes = cmd.split()
            exe = shutil.which(partes[0]) if partes else ""
            itens.append({"id": _id(arq), "nome": d.get("Name") or f[:-8], "comando": cmd, "exe": exe or (partes[0] if partes else ""),
                          "args": " ".join(partes[1:]), "existe": bool(exe), "origem": "Autostart (XDG)", "escopo": escopo,
                          "ativo": ativo(d), "descricao": d.get("Comment", ""), "empresa": "",
                          "_alterar": (arq, uid, gid)})
    return itens


def alterar(item, ligado):
    """Liga/desliga como as Configurações do GNOME/KDE fazem: Hidden e X-GNOME-Autostart-enabled no .desktop."""
    arq, uid, gid = item["_alterar"]
    with open(arq, encoding="utf-8", errors="replace") as f:
        linhas = f.read().splitlines()
    fora = ("Hidden=", "X-GNOME-Autostart-enabled=")
    novas, i, secao = [], None, None
    for l in linhas:
        if l.strip().startswith("["):
            secao = l.strip()
        elif secao == "[Desktop Entry]" and l.strip().startswith(fora):
            continue        # só na seção principal: as ações ([Desktop Action ...]) ficam como estão
        novas.append(l)
        if l.strip() == "[Desktop Entry]":
            i = len(novas)
    extra = [f"Hidden={'false' if ligado else 'true'}", f"X-GNOME-Autostart-enabled={'true' if ligado else 'false'}"]
    novas[i if i is not None else len(novas):i if i is not None else len(novas)] = extra
    tmp = arq + ".cnt"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(novas) + "\n")
    if uid is not None and os.geteuid() == 0:
        os.chown(tmp, uid, gid)
    os.replace(tmp, arq)
    return True
