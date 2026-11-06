"""Configurações do Monitor do PC (gravadas em ProgramData\\MonitorPC\\config.json)."""
import os
import json
import threading

from . import config

WIN = os.name == "nt"
_lock = threading.Lock()
_atual = None

CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME_RUN = "MonitorDoPC"


def _validar(c):
    from . import alertas
    out = dict(config.PADRAO)
    out["alertas"] = [dict(x) for x in alertas.PADRAO]
    out["jogo_fechar"] = []
    out["mini_itens"] = list(config.PADRAO["mini_itens"])
    out["regras_prog"] = []
    out["vps"] = []
    for k, padrao in config.PADRAO.items():
        if k not in c:
            continue
        v = c[k]
        if k == "alertas":
            if isinstance(v, list):
                out[k] = alertas.validar(v)
            continue
        if k in ("jogo_fechar", "mini_itens"):
            if isinstance(v, list):
                out[k] = [str(x)[:80] for x in v if isinstance(x, str)][:50]
            continue
        if k == "regras_prog":
            if isinstance(v, list):
                from . import regras
                out[k] = regras.validar(v)
            continue
        if k == "vps":
            if isinstance(v, list):
                from . import vps
                out[k] = vps.validar(v)
            continue
        if isinstance(padrao, bool):
            out[k] = bool(v)
        elif isinstance(padrao, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if k in config.OPCOES and v not in config.OPCOES[k]:
                continue
            if k.startswith("limiar_"):
                v = max(30, min(110, v))
            if k == "mini_opacidade":
                v = max(30, min(100, v))
            if k == "mini_escala":
                v = max(50, min(300, v))
            if k == "mini_monitor":
                v = max(1, min(8, v))
            if k in ("mini_margem_x", "mini_margem_y"):
                v = max(0, min(4000, v))
            if k == "acesso_porta":
                v = max(1024, min(65535, v))
            out[k] = v
        elif isinstance(padrao, str):
            if k in config.OPCOES and v not in config.OPCOES[k]:
                continue
            out[k] = str(v)
    return out


def carregar():
    global _atual
    with _lock:
        try:
            with open(config.CONFIG, encoding="utf-8") as f:
                _atual = _validar(json.load(f))
        except (OSError, ValueError):
            _atual = _validar({})
        return dict(_atual)


def obter(chave=None):
    if _atual is None:
        carregar()
    return _atual[chave] if chave else dict(_atual)


def salvar(novos):
    """Aplica e grava. Devolve a configuração final."""
    global _atual
    atual = obter()
    atual.update(novos or {})
    final = _validar(atual)
    os.makedirs(config.BASE, exist_ok=True)
    tmp = config.CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=1)
    os.replace(tmp, config.CONFIG)
    with _lock:
        _atual = final
    aplicar_inicializacao(final)
    return dict(final)


def _exe_bandeja():
    return os.path.join(config.pasta_programa(), "MonitorPC.exe")


def aplicar_inicializacao(c=None):
    """Liga/desliga o ícone ao iniciar o Windows (HKLM\\...\\Run, vale para todos os usuários)."""
    if not WIN:
        return
    c = c or obter()
    exe = _exe_bandeja()
    if not os.path.exists(exe):
        return
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CHAVE_RUN, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            if c["iniciar_com_windows"]:
                args = "" if c["abrir_janela_ao_iniciar"] else " --bandeja"
                winreg.SetValueEx(k, NOME_RUN, 0, winreg.REG_SZ, f'"{exe}"{args}')
            else:
                try:
                    winreg.DeleteValue(k, NOME_RUN)
                except OSError:
                    pass
    except OSError as e:
        print("Não consegui alterar a inicialização:", e)


PROTEGIDAS = {"acesso_senha", "api_chave"}   # só mudam pelas funções próprias, nunca pelo formulário


def salvar_publico(novos):
    return salvar({k: v for k, v in (novos or {}).items() if k not in PROTEGIDAS})


def publico():
    c = obter()
    c["acesso_tem_senha"] = bool(c.pop("acesso_senha", ""))
    c["api_tem_chave"] = bool(c.get("api_chave"))
    c.pop("api_chave", None)
    return c


# ---------------------------------------------------------------- backup das configurações
def backup():
    """Tudo menos senhas/chaves (essas ficam só neste PC)."""
    import time
    from . import config as _c
    c = obter()
    for k in PROTEGIDAS:
        c.pop(k, None)
    return {"app": "monitor-pc", "tipo": "backup-configuracoes", "versao": _c.VERSAO, "criado_em": time.time(),
            "aviso": "Senhas (acesso pelo celular, VPS) e a chave da API não vão no backup.", "config": c}


def restaurar(dados):
    if not isinstance(dados, dict) or dados.get("app") != "monitor-pc" or not isinstance(dados.get("config"), dict):
        return False, "Esse arquivo não é um backup do Monitor do PC."
    novos = {k: v for k, v in dados["config"].items() if k in config.PADRAO and k not in PROTEGIDAS}
    salvar(novos)
    return True, f"{len(novos)} configurações restauradas. Senhas não fazem parte do backup: cadastre de novo se precisar."
