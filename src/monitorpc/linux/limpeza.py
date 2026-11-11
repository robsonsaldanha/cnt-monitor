"""Limpeza no Linux, com as mesmas regras de segurança do Windows (só arquivos, nunca segue links, idade mínima,
navegador aberto é pulado): temporários, miniaturas, lixeira, registros antigos do journal e pacotes do apt."""
import os
import re
import pwd

UM_DIA = 86400


def _perfis():
    """[(usuario, pasta_pessoal)] de todos os usuários de verdade (uid 1000+)."""
    out = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 65534 and os.path.isdir(p.pw_dir):
            out.append((p.pw_name, p.pw_dir))
    if not out and os.path.isdir(os.path.expanduser("~")):
        out.append((os.environ.get("USER", "usuario"), os.path.expanduser("~")))
    return out


CATEGORIAS = [
    {"id": "temp_sistema", "nome": "Arquivos temporários do sistema",
     "desc": "/tmp e /var/tmp. Só arquivos com mais de 1 dia que nenhum programa está usando.",
     "pastas": lambda: ["/tmp", "/var/tmp"], "idade": UM_DIA, "marcado": True},
    {"id": "cache_usuario", "nome": "Cache dos programas dos usuários",
     "desc": "~/.cache de cada usuário (fora os navegadores, que têm a parte deles). Só o que tem mais de 1 dia; "
             "os programas recriam quando precisam.",
     "pastas": lambda: [os.path.join(p, ".cache") for _u, p in _perfis()], "idade": UM_DIA, "marcado": False,
     "filtro": lambda c: not re.search(r"/\.cache/(google-chrome|chromium|microsoft-edge|BraveSoftware|vivaldi|opera|mozilla)/", c)},
    {"id": "miniaturas", "nome": "Cache de miniaturas",
     "desc": "As imagens pequenas que aparecem nas pastas. O gerenciador de arquivos recria sozinho.",
     "pastas": lambda: [os.path.join(p, ".cache", "thumbnails") for _u, p in _perfis()], "idade": 0, "marcado": False},
    {"id": "lixeira", "nome": "Lixeira",
     "desc": "Esvazia a Lixeira de todos os usuários. Depois disso não dá para recuperar esses arquivos.",
     "pastas": lambda: [os.path.join(p, ".local", "share", "Trash", s) for _u, p in _perfis() for s in ("files", "info")],
     "idade": 0, "marcado": False},
    {"id": "relatorios_erro", "nome": "Relatórios de erro (apport/crash)",
     "desc": "Relatórios de programas que travaram, guardados em /var/crash. Não afetam nada.",
     "pastas": lambda: ["/var/crash"], "idade": UM_DIA, "marcado": True},
    {"id": "apt", "nome": "Pacotes baixados pelo apt",
     "desc": "Cópias dos pacotes já instalados (/var/cache/apt/archives). O apt baixa de novo se precisar.",
     "pastas": lambda: ["/var/cache/apt/archives"], "idade": 0, "marcado": True,
     "filtro": lambda c: c.endswith(".deb")},
    {"id": "journal", "nome": "Registros antigos do sistema (journal)",
     "desc": "Registros do systemd com mais de 2 semanas em /var/log/journal.",
     "pastas": lambda: ["/var/log/journal"], "idade": 14 * UM_DIA, "marcado": False,
     "filtro": lambda c: c.endswith(".journal~") or "@" in os.path.basename(c)},
]

# nome, processo, pasta de cache relativa à pasta pessoal, é Firefox?
NAVEGADORES = [
    ("Google Chrome", "chrome", ".cache/google-chrome", False),
    ("Chromium", "chromium", ".cache/chromium", False),
    ("Microsoft Edge", "msedge", ".cache/microsoft-edge", False),
    ("Brave", "brave", ".cache/BraveSoftware/Brave-Browser", False),
    ("Vivaldi", "vivaldi-bin", ".cache/vivaldi", False),
    ("Opera", "opera", ".cache/opera", False),
    ("Firefox", "firefox", ".cache/mozilla/firefox", True),
]
TAREFAS = {"otimizar": ("Otimizar unidades (TRIM nos SSDs)", ["fstrim", "--all", "--verbose"]),
           "componentes": ("Limpeza de pacotes que ninguém usa (apt autoremove)",
                           ["apt-get", "-y", "autoremove", "--purge"])}


def _abertos():
    import psutil
    nomes = set()
    for p in psutil.process_iter(["name", "cmdline"]):
        n = (p.info.get("name") or "").lower()
        if n in ("chrome", "msedge", "chromium") and "monitorpc" in " ".join(p.info.get("cmdline") or []).lower():
            continue                # a janela do próprio CNT Monitor
        nomes.add(n)
    return nomes


def navegadores(apagar_ids=None):
    from ..limpeza import _pastas_navegador, _varrer
    abertos = _abertos()
    out = []
    for nome, proc, rel, ff in NAVEGADORES:
        pastas = []
        for _u, casa in _perfis():
            pastas += _pastas_navegador(os.path.join(casa, rel), ff)
        if not pastas:
            continue
        nid = re.sub(r"[^a-z0-9]", "", nome.lower())
        aberto = proc in abertos or (proc == "chrome" and "google-chrome" in abertos)
        apagar = bool(apagar_ids and nid in apagar_ids and not aberto)
        total = n = pulados = 0
        for p in pastas:
            b, q, pu = _varrer(p, 0, apagar)
            total, n, pulados = total + b, n + q, pulados + pu
        out.append({"id": nid, "nome": nome, "bytes": total, "arquivos": n, "aberto": aberto, "limpo": apagar,
                    "pulados": pulados, "perfis": len(pastas)})
    return out
