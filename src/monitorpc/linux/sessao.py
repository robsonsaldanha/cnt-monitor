"""Sessão gráfica de quem está usando o PC, vista pelo serviço (root, sem tela): quem é, se é X11 ou Wayland
e as variáveis que um programa precisa para abrir janelas nela."""
import os
import pwd
import shlex
import subprocess

from . import rodar

VARIAVEIS = ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
             "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION", "LANG")


def _propriedades(sid):
    _c, txt = rodar(["loginctl", "show-session", sid, "-p", "Name", "-p", "User", "-p", "Type", "-p", "Active",
                     "-p", "State", "-p", "Class", "-p", "Display", "-p", "Remote", "-p", "Leader"], timeout=5)
    return dict(l.split("=", 1) for l in txt.splitlines() if "=" in l)


def sessao_grafica():
    """{"id", "usuario", "uid", "tipo" (x11|wayland), "display", "lider"} da sessão gráfica ativa, ou None."""
    c, txt = rodar(["loginctl", "list-sessions", "--no-legend"], timeout=5)
    if c != 0:
        return None
    cand = []
    for linha in txt.splitlines():
        partes = linha.split()
        if not partes:
            continue
        p = _propriedades(partes[0])
        if p.get("Class") != "user" or p.get("Type") not in ("x11", "wayland") or p.get("Remote") == "yes":
            continue
        p["_id"] = partes[0]
        cand.append((p.get("Active") == "yes", p.get("State") == "active", p))
    if not cand:
        return None
    p = max(cand, key=lambda x: (x[0], x[1]))[2]
    try:
        uid = int(p.get("User"))
    except (TypeError, ValueError):
        return None
    return {"id": p["_id"], "usuario": p.get("Name"), "uid": uid, "tipo": p.get("Type"),
            "display": p.get("Display") or "", "lider": int(p.get("Leader") or 0)}


def _ambiente_de(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            itens = f.read().split(b"\0")
    except OSError:
        return {}
    out = {}
    for i in itens:
        k, _, v = i.partition(b"=")
        k = k.decode("utf-8", "replace")
        if k in VARIAVEIS:
            out[k] = v.decode("utf-8", "replace")
    return out


def ambiente(s):
    """Variáveis da sessão gráfica copiadas de um processo do próprio usuário que já está nela (o shell gráfico,
    o painel...). É o mesmo que o ambiente que um programa aberto pelo menu receberia."""
    melhor = {}
    for nome in os.listdir("/proc"):
        if not nome.isdigit():
            continue
        try:
            if os.stat(f"/proc/{nome}").st_uid != s["uid"]:
                continue
        except OSError:
            continue
        a = _ambiente_de(nome)
        pontos = ("DISPLAY" in a) + ("WAYLAND_DISPLAY" in a) * 2 + ("DBUS_SESSION_BUS_ADDRESS" in a)
        if pontos > melhor.get("_pontos", 0):
            melhor = dict(a, _pontos=pontos)
            if pontos >= 3 + (s["tipo"] == "wayland"):
                break
    melhor.pop("_pontos", None)
    if s["tipo"] == "x11" and "DISPLAY" not in melhor:
        melhor["DISPLAY"] = s["display"] or ":0"
    melhor.setdefault("XDG_RUNTIME_DIR", f"/run/user/{s['uid']}")
    melhor.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{s['uid']}/bus")
    melhor.setdefault("XDG_SESSION_TYPE", s["tipo"])
    return melhor


def _popen_como(s, args, **kw):
    pw = pwd.getpwuid(s["uid"])
    amb = {"HOME": pw.pw_dir, "USER": pw.pw_name, "LOGNAME": pw.pw_name, "SHELL": pw.pw_shell,
           "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", **ambiente(s)}
    if os.geteuid() != 0:          # rodando como o próprio usuário (testes, demonstração): sem trocar de conta
        return subprocess.Popen(args, env={**os.environ, **amb}, **kw)
    grupos = os.getgrouplist(pw.pw_name, pw.pw_gid)
    return subprocess.Popen(args, env=amb, cwd=pw.pw_dir, user=pw.pw_uid, group=pw.pw_gid, extra_groups=grupos, **kw)


def abrir_na_tela_do_usuario(comando):
    """Abre um programa na sessão gráfica de quem está usando o PC (o serviço não tem tela própria)."""
    s = sessao_grafica()
    if not s:
        return False
    try:
        _popen_como(s, shlex.split(comando), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError as e:
        print("Não consegui abrir na sessão do usuário:", e)
        return False


def em_servico():
    """Verdadeiro quando este processo é o serviço do systemd (root, sem sessão gráfica)."""
    return os.geteuid() == 0 and bool(os.environ.get("INVOCATION_ID")) and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def abrir_como_sistema(comando):
    """Abre `comando` (lista) na tela de quem está usando o PC, com dois canos para conversar com ele.
    No Linux o agente roda como a própria pessoa: é quem pode ver e controlar a tela (X11 e portal do Wayland).
    Levanta OSError com a mensagem pronta."""
    s = sessao_grafica()
    if not s:
        raise OSError("Ninguém está usando este computador (nenhuma sessão gráfica aberta).")
    try:
        return _popen_como(s, list(comando), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, encoding="utf-8", bufsize=1, start_new_session=True)
    except OSError as e:
        raise OSError(f"Não consegui abrir o acesso remoto na tela do usuário: {e}") from None
