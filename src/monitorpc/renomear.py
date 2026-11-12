"""Renomear em massa (no estilo do PowerRename): prévia com conflitos, aplicação e desfazer.

Roda no ícone, com as permissões do usuário (o serviço nunca mexe nos arquivos dele). A prévia e a aplicação
usam a mesma função (`planejar`), então o que a tela mostra é exatamente o que será feito.
"""
import os
import re
import json
import uuid
import fnmatch

WIN = os.name == "nt"
LIMITE = 5000
PROIBIDOS = '<>:"/\\|?*'
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
CAIXAS = ("", "minusculas", "maiusculas", "titulo", "frase")
ALVOS = ("nome", "extensao", "tudo")


def regras_validas(r):
    """Normaliza as regras vindas da tela (o que não vier fica com o padrão)."""
    r = r if isinstance(r, dict) else {}

    def inteiro(k, padrao, a, b):
        try:
            return max(a, min(b, int(r.get(k, padrao))))
        except (TypeError, ValueError):
            return padrao
    return {"buscar": str(r.get("buscar") or "")[:300], "substituir": str(r.get("substituir") or "")[:300],
            "regex": bool(r.get("regex")), "diferenciar": bool(r.get("diferenciar")), "todas": r.get("todas", True) is not False,
            "caixa": r.get("caixa") if r.get("caixa") in CAIXAS else "",
            "prefixo": str(r.get("prefixo") or "")[:100], "sufixo": str(r.get("sufixo") or "")[:100],
            "numerar": bool(r.get("numerar")), "inicio": inteiro("inicio", 1, 0, 10**6), "passo": inteiro("passo", 1, 1, 1000),
            "digitos": inteiro("digitos", 2, 1, 8), "num_posicao": "inicio" if r.get("num_posicao") == "inicio" else "fim",
            "num_sep": str(r.get("num_sep") if r.get("num_sep") is not None else " ")[:5],
            "alvo": r.get("alvo") if r.get("alvo") in ALVOS else "nome",
            "filtro": str(r.get("filtro") or "*")[:100] or "*", "incluir_pastas": bool(r.get("incluir_pastas"))}


def _caixa(t, modo):
    if modo == "minusculas":
        return t.lower()
    if modo == "maiusculas":
        return t.upper()
    if modo == "titulo":
        return re.sub(r"[^\W_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), t)
    if modo == "frase":
        return t[:1].upper() + t[1:].lower()
    return t


def _partes(nome, eh_pasta):
    if eh_pasta:
        return nome, ""
    base, ext = os.path.splitext(nome)
    return base, ext


def novo_nome(nome, r, indice, eh_pasta=False, padrao=None):
    """Nome novo para um item. `indice` é a posição dele entre os itens que passaram no filtro (0, 1, 2…)."""
    base, ext = _partes(nome, eh_pasta)
    alvo = r["alvo"]
    texto = nome if alvo == "tudo" else ext[1:] if alvo == "extensao" else base
    if r["buscar"]:
        if padrao is None:
            padrao = compilar(r)
        texto = padrao.sub(_repl(r), texto, count=0 if r["todas"] else 1)
    texto = _caixa(texto, r["caixa"])
    if alvo == "tudo":
        base, ext = _partes(texto, eh_pasta)
    elif alvo == "extensao":
        ext = "." + texto if texto else ""
    else:
        base = texto
    if alvo != "extensao":
        base = r["prefixo"] + base + r["sufixo"]
        if r["numerar"]:
            n = str(r["inicio"] + indice * r["passo"]).zfill(r["digitos"])
            base = n + r["num_sep"] + base if r["num_posicao"] == "inicio" else base + r["num_sep"] + n
    return base + ext


def compilar(r):
    """Regex da busca (texto comum vira literal). ValueError com a mensagem se a expressão estiver errada."""
    flags = 0 if r["diferenciar"] else re.IGNORECASE
    try:
        return re.compile(r["buscar"] if r["regex"] else re.escape(r["buscar"]), flags)
    except re.error as e:
        raise ValueError(f"Expressão regular inválida: {e}") from None


def _repl(r):
    if r["regex"]:
        modelo = r["substituir"]
        return lambda m: m.expand(modelo)
    return lambda _m: r["substituir"]


def erro_nome(nome):
    if not nome or nome in (".", ".."):
        return "nome vazio"
    if any(ch in PROIBIDOS or ord(ch) < 32 for ch in nome):
        return "caractere não permitido (< > : \" / \\ | ? *)"
    if WIN:
        if nome[-1] in " .":
            return "termina com ponto ou espaço"
        if nome.split(".")[0].upper() in RESERVADOS:
            return "nome reservado do Windows"
    if len(nome) > 255:
        return "nome longo demais"
    return None


def _chave(n):
    return n.lower() if WIN else n


def planejar(itens, regras):
    """itens: [(nome, eh_pasta)] da pasta. Devolve [{antigo, novo, pasta, erro, muda}] dos itens que passaram no
    filtro, na ordem dos nomes. Conflito com outro nome novo ou com um arquivo que fica é marcado como erro."""
    r = regras_validas(regras)
    padrao = compilar(r) if r["buscar"] else None
    filtros = [f.strip() for f in r["filtro"].split(";") if f.strip()] or ["*"]
    escolhidos = [(n, p) for n, p in sorted(itens, key=lambda x: x[0].lower())
                  if (r["incluir_pastas"] or not p) and any(fnmatch.fnmatch(n.lower(), f.lower()) for f in filtros)]
    plano = []
    for i, (nome, eh_pasta) in enumerate(escolhidos[:LIMITE]):
        novo = novo_nome(nome, r, i, eh_pasta, padrao)
        muda = novo != nome
        plano.append({"antigo": nome, "novo": novo, "pasta": eh_pasta, "erro": erro_nome(novo) if muda else None,
                      "muda": muda})
    mudam = {_chave(x["antigo"]) for x in plano if x["muda"]}
    ficam = {_chave(n) for n, _p in itens} - mudam
    vistos = {}
    for x in plano:
        if not x["muda"] or x["erro"]:
            continue
        k = _chave(x["novo"])
        if k in ficam and k != _chave(x["antigo"]):
            x["erro"] = "já existe um arquivo com esse nome"
        elif k in vistos:
            x["erro"] = "dois arquivos ficariam com o mesmo nome"
            vistos[k]["erro"] = vistos[k]["erro"] or "dois arquivos ficariam com o mesmo nome"
        else:
            vistos[k] = x
    return plano


def listar(pasta):
    if not os.path.isdir(pasta):
        raise ValueError("Pasta não encontrada.")
    with os.scandir(pasta) as it:
        return [(e.name, e.is_dir(follow_symlinks=False)) for e in it]


def resumo(plano):
    return {"total": len(plano), "mudam": sum(1 for x in plano if x["muda"] and not x["erro"]),
            "erros": sum(1 for x in plano if x["erro"])}


def _mover_em_duas_etapas(pasta, pares):
    """[(antigo, novo)]: primeiro tudo para nomes temporários, depois para o final. Assim a↔b e mudar só
    maiúsculas funcionam. Se algo falhar, devolve o que já tinha andado."""
    marca = uuid.uuid4().hex[:8]
    temp, feitos, erros = [], [], []
    for i, (a, n) in enumerate(pares):
        t = f".cnt-ren-{marca}-{i}"
        try:
            os.rename(os.path.join(pasta, a), os.path.join(pasta, t))
            temp.append((a, n, t))
        except OSError as e:
            erros.append(f"{a}: {e.strerror or e}")
    for a, n, t in temp:
        try:
            if os.path.exists(os.path.join(pasta, n)):
                raise FileExistsError(17, "já existe um arquivo com esse nome")
            os.rename(os.path.join(pasta, t), os.path.join(pasta, n))
            feitos.append((a, n))
        except OSError as e:
            erros.append(f"{a}: {e.strerror or e}")
            try:
                os.rename(os.path.join(pasta, t), os.path.join(pasta, a))
            except OSError:
                erros.append(f"{a}: ficou como {t}")
    return feitos, erros


def aplicar(pasta, regras, arq_desfazer):
    """Refaz a prévia agora (a pasta pode ter mudado) e renomeia só os itens sem erro."""
    plano = planejar(listar(pasta), regras)
    pares = [(x["antigo"], x["novo"]) for x in plano if x["muda"] and not x["erro"]]
    if not pares:
        return False, "Nada para renomear.", {"feitos": 0, "erros": []}
    feitos, erros = _mover_em_duas_etapas(pasta, pares)
    if feitos:
        try:
            with open(arq_desfazer, "w", encoding="utf-8") as f:
                json.dump({"pasta": pasta, "pares": feitos}, f, ensure_ascii=False)
        except OSError:
            pass
    msg = f"{len(feitos)} item(ns) renomeado(s)."
    if erros:
        msg += f" {len(erros)} não deram certo."
    return bool(feitos), msg, {"feitos": len(feitos), "erros": erros[:50]}


def desfazer(arq_desfazer):
    try:
        with open(arq_desfazer, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False, "Não há o que desfazer.", None
    pasta = d.get("pasta", "")
    pares = [(n, a) for a, n in d.get("pares", []) if os.path.exists(os.path.join(pasta, n))]
    if not pares:
        return False, "Os arquivos da última renomeação não estão mais lá.", None
    feitos, erros = _mover_em_duas_etapas(pasta, pares)
    try:
        os.remove(arq_desfazer)
    except OSError:
        pass
    msg = f"{len(feitos)} item(ns) voltaram ao nome anterior."
    if erros:
        msg += f" {len(erros)} não deram certo."
    return bool(feitos), msg, {"feitos": len(feitos), "erros": erros[:50]}


def ha_desfazer(arq_desfazer):
    try:
        with open(arq_desfazer, encoding="utf-8") as f:
            d = json.load(f)
        return {"pasta": d.get("pasta"), "n": len(d.get("pares", []))}
    except (OSError, ValueError):
        return None
