"""Alertas: regras que o usuário liga/desliga. Quando a condição dura o tempo pedido,
aparece uma notificação do Windows (pelo ícone perto do relógio) e fica no histórico."""
import time
import uuid

TIPOS = {
    "mem": ("Memória do PC acima de", "%"),
    "cpu": ("CPU do PC acima de", "%"),
    "temp_cpu": ("Temperatura do processador acima de", "°C"),
    "temp_gpu": ("Temperatura da placa de vídeo acima de", "°C"),
    "disco_cheio": ("Disco quase cheio (uso acima de)", "%"),
    "prog_mem": ("Programa usando mais memória que", "GB"),
    "prog_cpu": ("Programa usando mais CPU que", "%"),
    "prog_aberto": ("Programa abriu", ""),
}

PADRAO = [
    {"id": "mem", "tipo": "mem", "limite": 90, "duracao_s": 30, "ativo": True},
    {"id": "temp_cpu", "tipo": "temp_cpu", "limite": 90, "duracao_s": 10, "ativo": True},
    {"id": "temp_gpu", "tipo": "temp_gpu", "limite": 85, "duracao_s": 10, "ativo": True},
    {"id": "cpu", "tipo": "cpu", "limite": 95, "duracao_s": 60, "ativo": False},
    {"id": "disco", "tipo": "disco_cheio", "limite": 95, "duracao_s": 0, "ativo": True},
]

ESPERA = 15 * 60  # não repete o mesmo alerta por 15 min


def validar(regras):
    out = []
    for r in regras or []:
        if not isinstance(r, dict) or r.get("tipo") not in TIPOS:
            continue
        try:
            item = {"id": str(r.get("id") or uuid.uuid4().hex[:8])[:20], "tipo": r["tipo"],
                    "limite": float(r.get("limite", 0)), "duracao_s": max(0, min(3600, int(r.get("duracao_s", 0)))),
                    "ativo": bool(r.get("ativo", True))}
        except (TypeError, ValueError):
            continue
        if r["tipo"].startswith("prog_"):
            item["programa"] = str(r.get("programa") or "")[:80]
            if not item["programa"]:
                continue
        out.append(item)
    return out[:50]


def descrever(r):
    t, un = TIPOS[r["tipo"]]
    lim = f'{r["limite"]:g}{(" " + un) if un else ""}'
    alvo = f' “{r["programa"]}”' if r.get("programa") else ""
    if r["tipo"] == "prog_aberto":
        return f"{t}{alvo}"
    dur = f' por {r["duracao_s"]} s' if r["duracao_s"] else ""
    if r["tipo"].startswith("prog_"):
        return f'{alvo.strip()} usando mais de {lim}{dur}'
    return f"{t} {lim}{dur}"


class Alertas:
    def __init__(self):
        self.desde = {}      # id -> desde quando a condição está verdadeira
        self.ultimo = {}     # id -> último disparo
        self.historico = []  # [{ts, id, titulo, msg}]
        self._abertos = set()

    def avaliar(self, regras, e, particoes):
        """Devolve [(id_da_regra, titulo, msg)] dos alertas que dispararam agora."""
        t = time.time()
        disparos = []
        progs = {g["nome"]: g for g in e.get("programas", []) if not g.get("outros")}
        for r in regras:
            if not r.get("ativo"):
                self.desde.pop(r["id"], None)
                continue
            tipo, lim = r["tipo"], r["limite"]
            valor, cond, alvo = None, False, ""
            if tipo == "mem":
                valor = e.get("mem_pct")
            elif tipo == "cpu":
                valor = e.get("cpu")
            elif tipo == "temp_cpu":
                valor = e.get("temp_cpu")
            elif tipo == "temp_gpu":
                valor = e.get("temp_gpu")
            elif tipo == "disco_cheio":
                cheios = [p for p in particoes if p["pct"] >= lim]
                if cheios:
                    valor, alvo = cheios[0]["pct"], cheios[0]["unidade"]
            elif tipo in ("prog_mem", "prog_cpu", "prog_aberto"):
                g = progs.get(r.get("programa"))
                if tipo == "prog_aberto":
                    aberto = g is not None
                    era = r["id"] in self._abertos
                    if aberto and not era:
                        self._abertos.add(r["id"])
                        disparos.append(self._disparar(r, t, f'{r["programa"]} foi aberto.'))
                    elif not aberto:
                        self._abertos.discard(r["id"])
                    continue
                if g:
                    valor = g["mem"] / 2**30 if tipo == "prog_mem" else g["cpu"]
            if valor is not None:
                cond = valor >= lim
            if not cond:
                self.desde.pop(r["id"], None)
                continue
            ini = self.desde.setdefault(r["id"], t)
            if t - ini < r["duracao_s"] or t - self.ultimo.get(r["id"], 0) < ESPERA:
                continue
            un = TIPOS[tipo][1]
            vtxt = f"{valor:.1f} {un}".strip() if isinstance(valor, float) else f"{valor} {un}".strip()
            if tipo == "mem":
                msg = f"A memória está em {vtxt}. Veja quem está usando mais na guia Programas."
            elif tipo == "cpu":
                msg = f"A CPU está em {vtxt} há {int(t - ini)} s."
            elif tipo in ("temp_cpu", "temp_gpu"):
                msg = f"Está em {vtxt}. Verifique a ventilação e o que está pesando."
            elif tipo == "disco_cheio":
                msg = f"A unidade {alvo} está {vtxt} cheia."
            else:
                msg = f'{r["programa"]} está usando {vtxt}.'
            disparos.append(self._disparar(r, t, msg))
        return disparos

    def _disparar(self, r, t, msg):
        self.ultimo[r["id"]] = t
        titulo = "CNT Monitor — " + descrever(r)
        self.historico.append({"ts": t, "id": r["id"], "titulo": descrever(r), "msg": msg})
        self.historico = self.historico[-200:]
        return r["id"], titulo, msg
