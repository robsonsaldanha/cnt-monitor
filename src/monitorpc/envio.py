"""Envio (upload) de capturas para hosts de imagem e para um uploader personalizado.

Roda no ícone da bandeja. Os hosts sem conta foram conferidos nas páginas de API deles:
Catbox (catbox.moe/tools.php), Litterbox (litterbox.catbox.moe/tools.php), Uguu (uguu.se/api)
e tmpfiles (tmpfiles.org/api). O Imgur exige um Client-ID registrado pelo usuário.
"""
import os
import re
import json
import uuid
import shutil
import tempfile
import mimetypes
import urllib.parse
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
            "resposta": str(d.get("resposta") or "").strip()[:200],
            "modelo": "".join(ch for ch in str(d.get("modelo") or "") if ch not in "\r\n").strip()[:500],
            "nome": "".join(ch for ch in str(d.get("nome") or "") if ord(ch) >= 32).strip()[:60]}


_SXCU_JSON = re.compile(r"\{json:([^}]+)\}|\$json:([^$]+)\$")


def _caminho_sxcu(p):
    """files[0].url / files.0.url -> files.0.url"""
    return re.sub(r"\[(\d+)\]", r".\1", p.strip()).strip(".")


def de_sxcu(d):
    """Converte um uploader personalizado do ShareX (.sxcu) para o formato do Monitor.
    Aceita POST multipart; o link pode ser {json:caminho}, {response} ou um modelo com eles. Erro: ValueError."""
    if not isinstance(d, dict) or not d.get("RequestURL"):
        raise ValueError("esse arquivo não é um uploader do ShareX (.sxcu)")
    if str(d.get("RequestMethod") or "POST").upper() != "POST":
        raise ValueError("só dá para importar uploaders que usam POST")
    if str(d.get("Body") or "MultipartFormData") != "MultipartFormData":
        raise ValueError("só dá para importar uploaders que enviam o arquivo como formulário (MultipartFormData)")
    tipos = str(d.get("DestinationType") or "ImageUploader")
    if not any(t in tipos for t in ("ImageUploader", "FileUploader")):
        raise ValueError("esse uploader não é de imagens nem de arquivos")
    url = str(d["RequestURL"]).strip()
    params = d.get("Parameters") if isinstance(d.get("Parameters"), dict) else {}
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({str(k): str(v) for k, v in params.items()})
    modelo = str(d.get("URL") or "").strip()
    resposta = ""
    m = _SXCU_JSON.fullmatch(modelo)
    if m:
        resposta, modelo = _caminho_sxcu(m.group(1) or m.group(2)), ""
    elif modelo in ("{response}", "$response$"):
        modelo = ""
    elif modelo:
        modelo = _SXCU_JSON.sub(lambda x: "{json:" + _caminho_sxcu(x.group(1) or x.group(2)) + "}", modelo)
        modelo = modelo.replace("$response$", "{response}")
        if re.search(r"\{(?!json:|response\})[^}]*\}", modelo):
            raise ValueError("o link desse uploader usa recursos que o Monitor ainda não entende (só {json:…} e {response})")
    u = validar_uploader({"url": url, "campo": d.get("FileFormName") or "file", "cabecalhos": d.get("Headers"),
                          "campos": d.get("Arguments"), "resposta": resposta, "modelo": modelo, "nome": d.get("Name")})
    if not u["url"]:
        raise ValueError("o endereço do uploader precisa começar com https:// ou http://")
    return u


def montar_link(modelo, bruto):
    """Preenche {json:caminho} e {response} do modelo com a resposta do site."""
    txt = bruto.decode("utf-8", "replace").strip()
    obj = None

    def valor(m):
        nonlocal obj
        if obj is None:
            obj = _json(bruto)
        v = caminho_json(obj, m.group(1))
        return "" if v is None else str(v)
    return re.sub(r"\{json:([^}]+)\}", valor, modelo).replace("{response}", txt)


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


FORMATOS = {"url": "Só o link", "markdown": "Markdown", "html": "HTML", "bbcode": "BBCode (fóruns)"}


def formatar_link(url, formato, caminho=""):
    """Texto copiado depois do envio. Imagens viram imagem embutida; vídeos e outros, um link com o nome do arquivo."""
    import html
    nome = os.path.basename(caminho or "") or "captura"
    img = nome.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))
    if formato == "markdown":
        rot = nome.replace("[", "(").replace("]", ")")
        return f"![{rot}]({url})" if img else f"[{rot}]({url})"
    if formato == "html":
        u, n = html.escape(url, quote=True), html.escape(nome, quote=True)
        return f'<img src="{u}" alt="{n}">' if img else f'<a href="{u}">{n}</a>'
    if formato == "bbcode":
        return f"[img]{url}[/img]" if img else f"[url={url}]{nome}[/url]"
    return url


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
        if u["modelo"]:
            return _link(montar_link(u["modelo"], bruto))
        if not u["resposta"]:
            return _link(bruto.decode("utf-8", "replace"))
        return _link(caminho_json(_json(bruto), u["resposta"]))
    raise RuntimeError("destino de envio desconhecido")


def enviar_limpo(caminho, c):
    """Envia tirando antes os metadados das imagens (se a opção estiver ligada); o arquivo original não muda."""
    from . import efeitos
    alvo = caminho
    if c.get("captura_sem_metadados", True):
        try:
            alvo = efeitos.sem_metadados(caminho)
        except (OSError, ValueError):
            alvo = caminho
    try:
        return enviar(alvo, c)
    finally:
        if alvo != caminho:
            shutil.rmtree(os.path.dirname(alvo), ignore_errors=True)


def enviar_texto(texto, c):
    """Envia um texto como arquivo .txt."""
    pasta = tempfile.mkdtemp(prefix="mpc-texto-")
    arq = os.path.join(pasta, "texto.txt")
    try:
        with open(arq, "w", encoding="utf-8") as f:
            f.write(texto)
        return enviar(arq, c)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


# ---------------------------------------------------------------- depois do envio
def encurtar(url, timeout=20):
    """Link curto pelo is.gd (sem conta; API em is.gd/apishorteningreference.php)."""
    req = urllib.request.Request("https://is.gd/create.php?format=simple&url=" + urllib.parse.quote(url, safe=""),
                                 headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _link(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("o is.gd recusou: " + e.read()[:200].decode("utf-8", "replace").strip()) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"sem conexão com o is.gd ({e.reason})") from None


COMPARTILHAR = {
    "whatsapp": ("WhatsApp", "https://wa.me/?text={u}"),
    "telegram": ("Telegram", "https://t.me/share/url?url={u}"),
    "x": ("X (Twitter)", "https://twitter.com/intent/tweet?url={u}"),
    "reddit": ("Reddit", "https://www.reddit.com/submit?url={u}"),
    "email": ("E-mail", "mailto:?subject=Captura%20de%20tela&body={u}"),
}


def link_compartilhar(servico, url):
    if servico not in COMPARTILHAR:
        raise ValueError("serviço desconhecido")
    return COMPARTILHAR[servico][1].format(u=urllib.parse.quote(url, safe=""))
