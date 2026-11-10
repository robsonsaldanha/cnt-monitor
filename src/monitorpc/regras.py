"""Regras por programa: o que fazer automaticamente com um programa sempre que ele estiver aberto.

Cada regra (todas opcionais):
- prioridade: baixa / abaixo do normal / normal / acima do normal / alta
- nucleos: em quais núcleos do processador ele pode rodar
- bloquear: encerra o programa assim que ele abrir
- avisar_ao_abrir: notificação quando ele abrir
- limite de memória (GB): avisar ou encerrar quando passar
Processos do Windows (protegidos) nunca são alterados.
"""
import time
import uuid

import psutil

PRIORIDADES = {
    "baixa": getattr(psutil, "IDLE_PRIORITY_CLASS", 19),
    "abaixo": getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 10),
    "normal": getattr(psutil, "NORMAL_PRIORITY_CLASS", 0),
    "acima": getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", -5),
    "alta": getattr(psutil, "HIGH_PRIORITY_CLASS", -10),
}
NOMES_PRIORIDADE = {"": "Não mudar", "baixa": "Baixa", "abaixo": "Abaixo do normal", "normal": "Normal",
                    "acima": "Acima do normal", "alta": "Alta"}
ESPERA_AVISO = 15 * 60


def validar(lista):
    out = []
    n = psutil.cpu_count() or 64
    for r in lista or []:
        if not isinstance(r, dict) or not str(r.get("programa") or "").strip():
            continue
        try:
            item = {
                "id": str(r.get("id") or uuid.uuid4().hex[:8])[:20],
                "programa": str(r["programa"]).strip()[:80],
                "ativo": bool(r.get("ativo", True)),
                "prioridade": r.get("prioridade") if r.get("prioridade") in PRIORIDADES else "",
                "nucleos": sorted({int(x) for x in (r.get("nucleos") or []) if 0 <= int(x) < n}),
                "bloquear": bool(r.get("bloquear", False)),
                "avisar_ao_abrir": bool(r.get("avisar_ao_abrir", False)),
                "limite_mem_gb": max(0.0, float(r.get("limite_mem_gb") or 0)),
                "acao_limite": r.get("acao_limite") if r.get("acao_limite") in ("avisar", "encerrar") else "avisar",
            }
        except (TypeError, ValueError):
            continue
        out.append(item)
    return out[:100]


def descrever(r):
    partes = []
    if r["bloquear"]:
        partes.append("não deixar abrir")
    if r["prioridade"]:
        partes.append(f'prioridade {NOMES_PRIORIDADE[r["prioridade"]].lower()}')
    if r["nucleos"]:
        partes.append(f'só nos núcleos {", ".join(map(str, r["nucleos"]))}')
    if r["avisar_ao_abrir"]:
        partes.append("avisar quando abrir")
    if r["limite_mem_gb"]:
        partes.append(f'{"encerrar" if r["acao_limite"] == "encerrar" else "avisar"} se passar de {r["limite_mem_gb"]:g} GB')
    return ", ".join(partes) or "nenhuma ação marcada"


class MotorRegras:
    def __init__(self):
        self.aplicados = {}     # (regra, pid, inicio) -> True
        self.presentes = set()
        self.avisos = {}
        self.log = []

    def _registrar(self, msg):
        self.log.append({"ts": time.time(), "msg": msg})
        self.log = self.log[-100:]

    def aplicar(self, regras, estado, procs):
        """Devolve [(titulo, msg)] de notificações. Chamado a cada 2 s."""
        avisos = []
        progs = {g["nome"]: g for g in (estado or {}).get("programas", []) if not g.get("outros")}
        vivos = set()
        for r in regras:
            if not r["ativo"]:
                continue
            g = progs.get(r["programa"])
            if not g:
                self.presentes.discard(r["id"])
                continue
            pids = [p for p in g["pids"] if p in procs and not procs[p]["protegido"]]
            if r["id"] not in self.presentes:
                self.presentes.add(r["id"])
                if r["avisar_ao_abrir"] and not r["bloquear"]:
                    avisos.append((f'{r["programa"]} abriu', "Regra do CNT Monitor."))
            if r["bloquear"]:
                n = 0
                for p in pids:
                    try:
                        psutil.Process(p).kill()
                        n += 1
                    except (psutil.Error, OSError):
                        pass
                if n:
                    self._registrar(f'{r["programa"]}: bloqueado ({n} processo(s) encerrados)')
                    avisos.append((f'{r["programa"]} foi bloqueado', "Uma regra do CNT Monitor não deixa esse programa abrir."))
                continue
            for p in pids:
                d = procs[p]
                chave = (r["id"], r["prioridade"], tuple(r["nucleos"]), p, round(d["inicio"]))
                vivos.add(chave)
                if chave in self.aplicados:
                    continue
                self.aplicados[chave] = True
                try:
                    pr = psutil.Process(p)
                    if r["prioridade"]:
                        pr.nice(PRIORIDADES[r["prioridade"]])
                    if r["nucleos"]:
                        pr.cpu_affinity(r["nucleos"])
                except (psutil.Error, OSError, ValueError):
                    pass
            if r["limite_mem_gb"] and g["mem"] / 2**30 > r["limite_mem_gb"]:
                if r["acao_limite"] == "encerrar":
                    n = 0
                    for p in pids:
                        try:
                            psutil.Process(p).kill()
                            n += 1
                        except (psutil.Error, OSError):
                            pass
                    self._registrar(f'{r["programa"]}: passou de {r["limite_mem_gb"]:g} GB e foi encerrado')
                    avisos.append((f'{r["programa"]} foi encerrado', f'Passou de {r["limite_mem_gb"]:g} GB de memória (regra).'))
                elif time.time() - self.avisos.get(r["id"], 0) > ESPERA_AVISO:
                    self.avisos[r["id"]] = time.time()
                    self._registrar(f'{r["programa"]}: passou de {r["limite_mem_gb"]:g} GB')
                    avisos.append((f'{r["programa"]} passou de {r["limite_mem_gb"]:g} GB',
                                   f'Está usando {g["mem"] / 2**30:.1f} GB de memória.'))
        for k in [k for k in self.aplicados if k not in vivos]:
            self.aplicados.pop(k, None)
        return avisos
