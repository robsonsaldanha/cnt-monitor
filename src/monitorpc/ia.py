"""Analisar imagem com IA (opcional, desligado por padrão). Roda no ícone da bandeja.

A chave de API é do usuário e fica cifrada com o DPAPI do Windows no perfil dele (só esse usuário, neste PC,
consegue ler). Ela nunca vai para a configuração, para o serviço, para o backup nem para o celular.
"""
import io
import os
import json
import base64
import urllib.error
import urllib.request

from . import config

PROVEDORES = {
    "openai": ("OpenAI (ChatGPT)", "gpt-4o-mini"),
    "gemini": ("Google Gemini", "gemini-2.5-flash"),
    "openrouter": ("OpenRouter", "openai/gpt-4o-mini"),
}
AGENTE = f"MonitorDoPC/{config.VERSAO}"


# ---------------------------------------------------------------- chave (DPAPI do usuário)
def _arq_chave(pasta, provedor):
    return os.path.join(pasta, f"ia-{provedor}.bin")


def _dpapi(dados, cifrar):
    if os.name != "nt":
        return dados        # Linux: sem DPAPI, a proteção é o arquivo só para o dono (0600, na pasta do usuário)
    import ctypes
    from ctypes import wintypes as wt

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(dados, len(dados))
    entrada, saida = BLOB(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    c = ctypes.windll.crypt32
    f = c.CryptProtectData if cifrar else c.CryptUnprotectData
    if not f(ctypes.byref(entrada), None, None, None, None, 0x1, ctypes.byref(saida)):  # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("o Windows não conseguiu " + ("cifrar" if cifrar else "decifrar") + " a chave")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(saida.pbData)


def guardar_chave(pasta, provedor, chave):
    os.makedirs(pasta, exist_ok=True)
    arq = _arq_chave(pasta, provedor)
    if not chave:
        try:
            os.remove(arq)
        except OSError:
            pass
        return
    with open(os.open(arq, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(_dpapi(chave.strip().encode("utf-8"), True))


def ler_chave(pasta, provedor):
    try:
        with open(_arq_chave(pasta, provedor), "rb") as f:
            return _dpapi(f.read(), False).decode("utf-8")
    except OSError:
        return ""


def tem_chave(pasta, provedor):
    return os.path.isfile(_arq_chave(pasta, provedor))


# ---------------------------------------------------------------- pedido
def _jpeg_b64(img):
    m = img.convert("RGB")
    m.thumbnail((1600, 1600))
    b = io.BytesIO()
    m.save(b, "JPEG", quality=85)
    return base64.b64encode(b.getvalue()).decode()


def montar_pedido(provedor, modelo, prompt, img_b64, chave):
    """(url, cabeçalhos, corpo) do pedido. Separado do envio para dar para testar sem rede."""
    modelo = modelo or PROVEDORES[provedor][1]
    if provedor == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        corpo = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]}]}
        return url, {"x-goog-api-key": chave}, corpo
    url = ("https://openrouter.ai/api/v1/chat/completions" if provedor == "openrouter"
           else "https://api.openai.com/v1/chat/completions")
    corpo = {"model": modelo, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img_b64}}]}]}
    return url, {"Authorization": f"Bearer {chave}"}, corpo


def ler_resposta(provedor, d):
    try:
        if provedor == "gemini":
            return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]).strip()
        return (d["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("resposta inesperada da IA") from None


def analisar(img, c, pasta):
    provedor = c.get("captura_ia_provedor") or "openai"
    if provedor not in PROVEDORES:
        raise RuntimeError("provedor de IA desconhecido")
    chave = ler_chave(pasta, provedor)
    if not chave:
        raise RuntimeError(f"cadastre a sua chave do {PROVEDORES[provedor][0]} em Captura de tela › Opções › Analisar com IA")
    url, cab, corpo = montar_pedido(provedor, c.get("captura_ia_modelo"), c.get("captura_ia_prompt") or "Descreva a imagem.",
                                    _jpeg_b64(img), chave)
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": AGENTE, **cab})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return ler_resposta(provedor, json.loads(r.read()))
    except urllib.error.HTTPError as e:
        detalhe = e.read()[:300].decode("utf-8", "replace").strip()
        raise RuntimeError(f"a IA respondeu {e.code}" + (f": {detalhe}" if detalhe else "")) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"sem conexão com a IA ({e.reason})") from None
