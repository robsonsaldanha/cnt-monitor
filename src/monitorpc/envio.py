"""Envio (upload) de capturas para hosts de imagem e para um uploader personalizado.

Roda no ícone da bandeja. Os hosts sem conta foram conferidos nas páginas de API deles:
Catbox (catbox.moe/tools.php), Litterbox (litterbox.catbox.moe/tools.php), Uguu (uguu.se/api)
e tmpfiles (tmpfiles.org/api). O Imgur exige um Client-ID registrado pelo usuário.
"""
import os
import json
import uuid
import mimetypes
import urllib.error
import urllib.request

from . import config

DESTINOS = {
    "catbox": "Catbox (permanente, até 200 MB)",
    "litterbox": "Litterbox (temporário: 1 h a 72 h)",
    "uguu": "Uguu (temporário)",
    "tmpfiles": "tmpfiles.org (temporário, até 100 MB)",
    "imgur": "Imgur (precisa de Client-ID)",
    "personalizado": "Uploader personalizado",
}
LIMITE = {"catbox": 200 * 2**20, "tmpfiles": 100 * 2**20}
AGENTE = f"MonitorDoPC/{config.VERSAO}"


def validar_uploader(d):
    """{url, campo, cabecalhos{}, campos{}, resposta}: POST multipart; `resposta` é o caminho da URL no JSON
    (ex.: data.link ou files.0.url). Vazio = a resposta inteira é o link."""
    d = d if isinstance(d, dict) else {}
    url = str(d.get("url") or "").strip()[:500]
    if url and not url.lower().startswith(("https://", "http://")):
        url = ""

    def pares(x):
        if not isinstance(x, dict):
            return {}
        out = {}
        for k, v in list(x.items())[:20]:
            k = "".join(ch for ch in str(k) if 32 < ord(ch) < 127 and ch != ":")[:80]
            v = "".join(ch for ch in str(v) if ch not in "\r\n")[:2000]
            if k:
                out[k] = v
        return out

    campo = "".join(ch for ch in str(d.get("campo") or "file") if ch.isalnum() or ch in "_-[]")[:60] or "file"
    return {"url": url, "campo": campo, "cabecalhos": pares(d.get("cabecalhos")), "campos": pares(d.get("campos")),
            "resposta": str(d.get("resposta") or "").strip()[:200]}


def _multipart(campos, campo_arq, caminho):
    limite = "----MonitorDoPC" + uuid.uuid4().hex
    nome = os.path.basename(caminho)
    tipo = mimetypes.guess_type(nome)[0] or "application/octet-stream"
    partes = []
    for k, v in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    with open(caminho, "rb") as f:
        dados = f.read()
    partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{campo_arq}"; filename="{nome}"\r\n'
                  f'Content-Type: {tipo}\r\n\r\n'.encode("utf-8") + dados + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def _post(url, campos, campo_arq, caminho, cabecalhos=None, timeout=180):
    corpo, tipo = _multipart(campos, campo_arq, caminho)
    h = {"User-Agent": AGENTE, "Content-Type": tipo, **(cabecalhos or {})}
    req = urllib.request.Request(url, data=corpo, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detalhe = e.read()[:300].decode("utf-8", "replace").strip()
        raise RuntimeError(f"o site respondeu {e.code}" + (f": {detalhe}" if detalhe else "")) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"sem conexão com o site ({e.reason})") from None


def caminho_json(obj, caminho):
    """Pega um valor de um JSON por caminho com pontos: "data.link", "files.0.url"."""
    for parte in [p for p in caminho.split(".") if p]:
        if isinstance(obj, list):
            try:
                obj = obj[int(parte)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(parte)
        else:
            return None
    return obj


def _link(txt):
    txt = (txt or "").strip() if isinstance(txt, str) else ""
    if not txt.lower().startswith(("https://", "http://")) or len(txt) > 2000 or any(ch.isspace() for ch in txt):
        raise RuntimeError("o site não devolveu um link" + (f" (respondeu: {txt[:120]})" if txt else ""))
    return txt


def _json(bruto):
    try:
        return json.loads(bruto)
    except ValueError:
        raise RuntimeError("resposta inesperada do site: " + bruto[:120].decode("utf-8", "replace")) from None


def enviar(caminho, c):
    """Envia o arquivo para o destino escolhido nas Configurações e devolve o link. Erro: RuntimeError."""
    destino = c.get("captura_destino", "catbox")
    tam = os.path.getsize(caminho)
    if destino in LIMITE and tam > LIMITE[destino]:
        raise RuntimeError(f"o arquivo tem {tam / 2**20:.0f} MB, acima do limite de {LIMITE[destino] // 2**20} MB desse site")
    if destino == "catbox":
        return _link(_post("https://catbox.moe/user/api.php", {"reqtype": "fileupload"}, "fileToUpload", caminho)
                     .decode("utf-8", "replace"))
    if destino == "litterbox":
        return _link(_post("https://litterbox.catbox.moe/resources/internals/api.php",
                           {"reqtype": "fileupload", "time": c.get("captura_litter_tempo", "24h")}, "fileToUpload", caminho)
                     .decode("utf-8", "replace"))
    if destino == "uguu":
        return _link(caminho_json(_json(_post("https://uguu.se/upload", {}, "files[]", caminho)), "files.0.url"))
    if destino == "tmpfiles":
        url = _link(caminho_json(_json(_post("https://tmpfiles.org/api/v1/upload", {}, "file", caminho)), "data.url"))
        # o link da API abre uma página; com /dl/ ele aponta direto para o arquivo
        return url.replace("://tmpfiles.org/", "://tmpfiles.org/dl/", 1) if "/dl/" not in url else url
    if destino == "imgur":
        cid = c.get("captura_imgur_id") or ""
        if not cid:
            raise RuntimeError("cadastre o Client-ID do Imgur nas Configurações (Captura de tela)")
        campo = "video" if caminho.lower().endswith(".mp4") else "image"
        return _link(caminho_json(_json(_post("https://api.imgur.com/3/image", {}, campo, caminho,
                                              {"Authorization": f"Client-ID {cid}"})), "data.link"))
    if destino == "personalizado":
        u = validar_uploader(c.get("captura_uploader"))
        if not u["url"]:
            raise RuntimeError("configure o endereço do uploader personalizado nas Configurações")
        bruto = _post(u["url"], u["campos"], u["campo"], caminho, u["cabecalhos"])
        if not u["resposta"]:
            return _link(bruto.decode("utf-8", "replace"))
        return _link(caminho_json(_json(bruto), u["resposta"]))
    raise RuntimeError("destino de envio desconhecido")
