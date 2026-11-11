"""Controle de ventoinhas pelos canais gravaveis do LibreHardwareMonitor.

Regra de convivencia com outros programas (Gigabyte Control Center, iCUE, Afterburner...): o ultimo a mexer vale.
O Monitor so escreve num canal quando o usuario pede aqui. Se a leitura do canal passar a diferir do que o
Monitor gravou por 2 leituras seguidas, outro programa assumiu: o Monitor larga o canal sem escrever nada,
sem aviso, e passa a mostrar o valor novo. O usuario pode voltar a controlar por aqui a qualquer momento.
"""
import threading

MODOS = ("auto", "manual", "curva")
TOLERANCIA = 4.0          # pontos percentuais; PWM tem degraus e alguns controladores arredondam
LEITURAS_PARA_CEDER = 2
PASSO_MIN = 2.0           # na curva, so reescreve se o alvo mudar pelo menos isto
HISTERESE_PADRAO = 3
PADRAO = {"protecao": True, "limite_temp": 85, "minimo": 30, "canais": {}}


def _int(v, padrao, lo, hi):
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        return padrao
    return max(lo, min(hi, v))


def validar_curva(pontos):
    """Lista de [temperatura, %] ordenada por temperatura, sem temperaturas repetidas (2 a 12 pontos)."""
    out = {}
    if isinstance(pontos, list):
        for p in pontos[:24]:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                try:
                    t, v = float(p[0]), float(p[1])
                except (TypeError, ValueError):
                    continue
                if -20 <= t <= 130:
                    out[round(t)] = _int(v, 0, 0, 100)
    lista = sorted([t, v] for t, v in out.items())[:12]
    return lista if len(lista) >= 2 else [[30, 30], [50, 45], [65, 70], [80, 100]]


def validar(v):
    """Valida o bloco 'ventoinhas' da configuracao."""
    v = v if isinstance(v, dict) else {}
    out = {"protecao": bool(v.get("protecao", PADRAO["protecao"])),
           "limite_temp": _int(v.get("limite_temp"), PADRAO["limite_temp"], 60, 100),
           "minimo": _int(v.get("minimo"), PADRAO["minimo"], 0, 100), "canais": {}}
    canais = v.get("canais")
    if isinstance(canais, dict):
        for cid, c in list(canais.items())[:64]:
            if not isinstance(c, dict) or not isinstance(cid, str) or not cid.startswith("/") or len(cid) > 120:
                continue
            modo = c.get("modo") if c.get("modo") in MODOS else "auto"
            mini = c.get("minimo")
            out["canais"][cid] = {
                "modo": modo, "valor": _int(c.get("valor"), 50, 0, 100),
                "curva": validar_curva(c.get("curva")), "sensor": str(c.get("sensor") or "")[:120],
                "minimo": None if mini in (None, "") else _int(mini, out["minimo"], 0, 100),
                "histerese": _int(c.get("histerese"), HISTERESE_PADRAO, 0, 15),
                "apelido": "".join(ch for ch in str(c.get("apelido") or "") if ord(ch) >= 32)[:40]}
    return out


def alvo_curva(curva, temp):
    """Interpolacao linear; abaixo do primeiro ponto vale o primeiro, acima do ultimo vale o ultimo."""
    if temp <= curva[0][0]:
        return float(curva[0][1])
    for (t0, v0), (t1, v1) in zip(curva, curva[1:]):
        if temp <= t1:
            return v0 + (v1 - v0) * (temp - t0) / (t1 - t0)
    return float(curva[-1][1])


def alvo_com_histerese(curva, temp, ultimo_temp, ultimo_alvo, histerese):
    """Evita ficar mudando a velocidade por oscilacoes pequenas da temperatura: so recalcula
    quando a temperatura sobe ou desce pelo menos 'histerese' graus desde a ultima mudanca."""
    if ultimo_temp is not None and ultimo_alvo is not None and abs(temp - ultimo_temp) < histerese:
        return ultimo_alvo, ultimo_temp
    return alvo_curva(curva, temp), temp


def externo(lido, escrito, estrikes):
    """Devolve (novo_estrikes, cedeu): divergencia por LEITURAS_PARA_CEDER leituras seguidas = outro programa mexeu."""
    if lido is None or abs(lido - escrito) <= TOLERANCIA:
        return 0, False
    estrikes += 1
    return estrikes, estrikes >= LEITURAS_PARA_CEDER


def par_ventoinha(id_controle):
    """O canal /x/control/N tem a leitura de RPM em /x/fan/N."""
    return id_controle.replace("/control/", "/fan/") if "/control/" in id_controle else None


class Ventoinhas:
    def __init__(self, sens, ler_cfg, gravar_cfg):
        self.sens = sens
        self._ler_cfg = ler_cfg
        self._gravar_cfg = gravar_cfg
        self.lock = threading.RLock()
        self._est = {}        # id -> {"escrito", "strikes", "alvo_temp", "alvo"}
        self._emergencia = False
        self._ult_ciclo = 0

    # ---------------- acesso ao LHM (substituivel no demo e nos testes) ----------------
    def _controles(self):
        """{id: objeto IControl} dos canais que o hardware deixa escrever."""
        if hasattr(self.sens, "controles"):          # Linux: canais pwm do hwmon (mesma interface do LHM)
            return self.sens.controles()
        pc = getattr(self.sens, "_pc", None)
        out = {}
        if pc is None:
            return out

        def visitar(hw):
            for s in hw.Sensors:
                try:
                    if str(s.SensorType) == "Control" and s.Control is not None:
                        out[str(s.Identifier)] = s.Control
                except Exception:
                    continue
            for sub in hw.SubHardware:
                visitar(sub)
        for hw in pc.Hardware:
            visitar(hw)
        return out

    def _escrever(self, ctl, pct):
        ctl.SetSoftware(float(pct))

    def _soltar(self, ctl):
        ctl.SetDefault()

    # ---------------- estado ----------------
    def _lista_sens(self):
        with self.sens.lock:
            return list(self.sens.lista)

    def _temp_ref(self, lista, sensor_id):
        if sensor_id:
            s = next((x for x in lista if x["id"] == sensor_id), None)
            return s["valor"] if s else None
        temps = [x["valor"] for x in lista if x["tipo_en"] == "Temperature" and not x.get("limite")
                 and x["tipo_hw"] in ("Processador", "Placa de vídeo")]
        return max(temps) if temps else None

    def temp_maxima(self, lista=None):
        return self._temp_ref(lista if lista is not None else self._lista_sens(), "")

    def canais(self):
        """Canais para a interface: leitura atual + o que o usuario configurou."""
        lista = self._lista_sens()
        por_id = {s["id"]: s for s in lista}
        cfg = self._ler_cfg()
        ctrls = self._controles()
        out = []
        for s in lista:
            if s["tipo_en"] != "Control" or s["id"] not in ctrls:
                continue
            cid = s["id"]
            rpm = por_id.get(par_ventoinha(cid))
            c = cfg["canais"].get(cid, {"modo": "auto"})
            out.append({"id": cid, "hw": s["hw"], "sub": s.get("sub", ""), "tipo_hw": s["tipo_hw"], "nome": s["nome"],
                        "pct": s["valor"], "rpm": rpm["valor"] if rpm else None, "modo": c.get("modo", "auto"),
                        "valor": c.get("valor", 50), "curva": c.get("curva") or validar_curva(None),
                        "sensor": c.get("sensor", ""), "minimo": c.get("minimo"), "histerese": c.get("histerese", HISTERESE_PADRAO),
                        "apelido": c.get("apelido", "")})
        return {"canais": out, "protecao": cfg["protecao"], "limite_temp": cfg["limite_temp"], "minimo": cfg["minimo"],
                "emergencia": self._emergencia, "temp_max": self.temp_maxima(lista),
                "temperaturas": [{"id": x["id"], "nome": f'{x["hw"]} — {x["nome"]}', "valor": x["valor"]} for x in lista
                                 if x["tipo_en"] == "Temperature" and not x.get("limite")],
                "suportado": bool(ctrls)}

    # ---------------- acoes do usuario ----------------
    def definir(self, cid, dados):
        """Aplica o que o usuario escolheu para um canal. Devolve (ok, msg)."""
        ctrls = self._controles()
        ctl = ctrls.get(cid)
        if ctl is None:
            return False, "Esse canal não aceita controle por software (só leitura)."
        cfg = self._ler_cfg()
        antigo = cfg["canais"].get(cid, {})
        novo = validar({"canais": {cid: {**antigo, **{k: v for k, v in dados.items() if k != "id"}}}})["canais"].get(cid)
        if novo is None:
            return False, "Canal inválido."
        if not any(k in dados for k in ("modo", "valor", "curva", "sensor", "histerese", "minimo")):
            cfg["canais"][cid] = novo      # só apelido: não toca no hardware
            self._gravar_cfg(cfg)
            return True, "Salvo."
        with self.lock:
            if novo["modo"] == "auto":
                try:
                    self._soltar(ctl)
                except Exception as e:  # noqa: BLE001
                    return False, f"Não consegui devolver ao automático: {e}"
                self._est.pop(cid, None)
            elif novo["modo"] == "manual":
                alvo = max(self._minimo(cfg, novo), novo["valor"])
                try:
                    self._escrever(ctl, alvo)
                except Exception as e:  # noqa: BLE001
                    return False, f"O hardware recusou a mudança: {e}"
                self._est[cid] = {"escrito": float(alvo), "strikes": 0, "alvo_temp": None, "alvo": None}
            else:   # curva: o ciclo aplica
                self._est[cid] = {"escrito": None, "strikes": 0, "alvo_temp": None, "alvo": None}
        cfg["canais"][cid] = novo
        self._gravar_cfg(cfg)
        return True, {"auto": "Voltou para o automático.", "manual": "Velocidade aplicada.", "curva": "Curva ativada."}[novo["modo"]]

    def definir_global(self, dados):
        cfg = self._ler_cfg()
        for k in ("protecao", "limite_temp", "minimo"):
            if k in dados:
                cfg[k] = dados[k]
        cfg = validar(cfg)
        self._gravar_cfg(cfg)
        return True, "Salvo."

    def _minimo(self, cfg, canal):
        m = canal.get("minimo")
        return cfg["minimo"] if m is None else m

    # ---------------- ciclo (chamado a cada leitura de sensores) ----------------
    def ciclo(self):
        cfg = self._ler_cfg()
        donos = {cid: c for cid, c in cfg["canais"].items() if c["modo"] in ("manual", "curva")}
        if not donos:
            self._emergencia = False
            return
        ctrls = self._controles()
        lista = self._lista_sens()
        lidos = {s["id"]: s["valor"] for s in lista if s["tipo_en"] == "Control"}
        tmax = self.temp_maxima(lista)
        with self.lock:
            if cfg["protecao"] and tmax is not None:
                if tmax >= cfg["limite_temp"] and not self._emergencia:
                    self._emergencia = True
                    for cid in donos:
                        if cid in ctrls:
                            try:
                                self._soltar(ctrls[cid])
                            except Exception:
                                pass
                        self._est.pop(cid, None)
                elif self._emergencia and tmax < cfg["limite_temp"] - 10:
                    self._emergencia = False
            if self._emergencia:
                return
            mudou = False
            for cid, c in donos.items():
                ctl = ctrls.get(cid)
                if ctl is None:
                    continue
                est = self._est.setdefault(cid, {"escrito": None, "strikes": 0, "alvo_temp": None, "alvo": None})
                if est["escrito"] is not None:
                    est["strikes"], cedeu = externo(lidos.get(cid), est["escrito"], est["strikes"])
                    if cedeu:    # outro programa mexeu: larga sem escrever nada e passa a mostrar o valor dele
                        self._est.pop(cid, None)
                        cfg["canais"][cid] = {**c, "modo": "auto"}
                        mudou = True
                        continue
                if c["modo"] == "manual":
                    if est["escrito"] is None:
                        alvo = max(self._minimo(cfg, c), c["valor"])
                    else:
                        continue
                else:
                    temp = self._temp_ref(lista, c["sensor"])
                    if temp is None:
                        continue
                    alvo, est["alvo_temp"] = alvo_com_histerese(c["curva"], temp, est["alvo_temp"], est["alvo"], c["histerese"])
                    est["alvo"] = alvo
                    alvo = max(self._minimo(cfg, c), min(100.0, alvo))
                    if est["escrito"] is not None and abs(alvo - est["escrito"]) < PASSO_MIN:
                        continue
                try:
                    self._escrever(ctl, alvo)
                    est["escrito"] = float(alvo)
                    est["strikes"] = 0
                except Exception:
                    continue
            if mudou:
                self._gravar_cfg(cfg)

    def liberar(self):
        """Devolve todos os canais ao automatico (ao parar o servico)."""
        try:
            ctrls = self._controles()
        except Exception:
            return
        with self.lock:
            for cid in list(self._est):
                if cid in ctrls:
                    try:
                        self._soltar(ctrls[cid])
                    except Exception:
                        pass
            self._est.clear()
