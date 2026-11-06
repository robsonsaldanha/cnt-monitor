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
    out = dict(config.PADRAO)
    for k, padrao in config.PADRAO.items():
        if k not in c:
            continue
        v = c[k]
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
            _atual = dict(config.PADRAO)
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
