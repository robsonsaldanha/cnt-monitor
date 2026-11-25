"""Histórico de chamadas na nuvem da empresa (função Premium "chamadas-historico").

Com a função ligada em Configurações e uma licença válida (licenca.py), as chamadas já encerradas deste computador são
enviadas ao hub (cntoficial.com.br/v1/chamadas) e a empresa as vê na aba "Nuvem" da tela Chamadas, de qualquer PC com a
mesma licença. Só sobe o que o Call flow já guardou aqui (os últimos dias do voip.db).

- desligado por padrão: nada sai do computador sem a pessoa ligar;
- sem licença ou com a licença revogada o envio para na hora (o hub confere a cada pedido);
- os dados vão cifrados no hub, isolados por empresa, com retenção e cota definidas pela licença.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, configuracao, licenca, sinalizacao

FUNCAO = "chamadas-historico"
FUNCAO_RELATORIOS = "chamadas-relatorios"
FUNCAO_VARIOS_PCS = "chamadas-multi-pc"
HUB = "https://cntoficial.com.br/v1/chamadas"
CURSOR = os.path.join(config.BASE, "nuvem_chamadas.json")
LOTE = 10                      # chamadas por envio (cada uma pode ter centenas de mensagens)
INTERVALO_S = 30               # o motor chama sincronizar() neste ritmo
INTERVALO_AO_VIVO_S = 10       # e publicar_ao_vivo() neste (Premium: vários computadores)


class NuvemErro(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def _chamar(metodo, rota, corpo=None, params=None, timeout=25, bruto=False):
    token = licenca.token()
    if not token:
        raise NuvemErro("Sem licença Premium válida neste computador.", 401)
    url = f"{HUB}/{rota}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "CNTMonitor/" + config.VERSAO})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            dados_resp = r.read(32 << 20)
            return dados_resp if bruto else json.loads(dados_resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(1 << 14)).get("erro") or ""
        except Exception:  # noqa: BLE001
            msg = ""
        raise NuvemErro(msg or f"O servidor da CNT recusou o pedido ({e.code}).", e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise NuvemErro(f"Sem conexão com o servidor da CNT: {getattr(e, 'reason', e)}", 0)


def empacotar(d):
    """Linha do voip.db (como a devolve Voip.desde) -> o que o hub guarda: resumo + detalhe."""
    extra = d.get("dados")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra or "{}")
        except ValueError:
            extra = {}
    extra = extra or {}
    fl = extra.get("rtp") or []
    mos = [f["mos"] for f in fl if f.get("mos")]
    return {"id": d["id"], "inicio": d.get("inicio"), "fim": d.get("fim"), "tipo": d.get("tipo"), "de": d.get("de"),
            "para": d.get("para"), "origem": d.get("origem"), "destino": d.get("destino"), "estado": d.get("estado"),
            "duracao": d.get("duracao"), "nmsg": d.get("nmsg"), "desligou": d.get("desligou"), "motivo": d.get("motivo"),
            "mos": min(mos) if mos else None, "perda": max((f.get("perda") or 0 for f in fl), default=None),
            "jitter_ms": max((f.get("jitter_ms") or 0 for f in fl), default=None), "agente": extra.get("agente", ""),
            "pc_nome": _nome_pc(), "dados": extra}


def _nome_pc():
    import socket
    return socket.gethostname()[:120]


class Nuvem:
    def __init__(self, voip, pasta=None):
        self.voip = voip
        self.arquivo = os.path.join(pasta, "nuvem_chamadas.json") if pasta else CURSOR
        self.lock = threading.Lock()
        self.ocupado = False
        self.erro = ""
        self.ultimo_envio = 0
        self.enviadas = 0
        self._cache = {}
        self.vivo_ocupado = False
        self.vivo_erro = ""

    def ativa(self):
        return bool(configuracao.obter("chamadas_nuvem")) and licenca.tem(FUNCAO)

    # ---- cursor: rowid da última chamada enviada no voip.db. Chamada regravada ganha rowid novo e sobe de novo (atualiza).
    def _cursor(self):
        try:
            with open(self.arquivo, encoding="utf-8") as f:
                return int(json.load(f).get("rowid", 0))
        except (OSError, ValueError, TypeError):
            return 0

    def _gravar_cursor(self, rowid):
        os.makedirs(os.path.dirname(self.arquivo), exist_ok=True)
        tmp = self.arquivo + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"rowid": int(rowid)}, f)
        os.replace(tmp, self.arquivo)

    def _maximo(self, con):
        return con.execute("SELECT MAX(rowid) FROM chamadas").fetchone()[0] or 0

    def _novas(self, limite):
        """(chamadas ainda não enviadas, rowid de cada uma). Se o cursor passou do fim (tabela esvaziada), recomeça de 0."""
        con = self.voip._con()
        try:
            cur = self._cursor()
            if cur > self._maximo(con):
                cur = 0
            cols = ", ".join(sinalizacao.CAMPOS_MON)
            linhas = con.execute(f"SELECT rowid, {cols} FROM chamadas WHERE rowid > ? AND tipo = 'chamada' ORDER BY rowid LIMIT ?",
                                 (cur, int(limite))).fetchall()
        finally:
            con.close()
        return [dict(zip(("rowid",) + sinalizacao.CAMPOS_MON, x)) for x in linhas]

    def pular_o_que_ja_existe(self):
        """Depois de apagar tudo na nuvem: o que já está no voip.db não sobe de novo; só o que for gravado daqui para a frente."""
        con = self.voip._con()
        try:
            self._gravar_cursor(self._maximo(con))
        finally:
            con.close()

    def sincronizar(self, limite_lotes=20):
        """Envia o que ainda não subiu. Só um envio por vez. Devolve quantas chamadas foram enviadas."""
        if not self.lock.acquire(blocking=False):
            return 0
        enviadas = 0
        try:
            if not self.ativa():
                return 0
            for _ in range(limite_lotes):
                lote = self._novas(LOTE)
                if not lote:
                    break
                _chamar("POST", "enviar", {"chamadas": [empacotar(c) for c in lote]})
                self._gravar_cursor(lote[-1]["rowid"])
                enviadas += len(lote)
                self.enviadas += len(lote)
                self.ultimo_envio = time.time()
            self.erro = ""
        except NuvemErro as e:
            self.erro = str(e)
        except Exception as e:  # noqa: BLE001
            self.erro = f"Falha ao enviar: {e}"
        finally:
            self.lock.release()
        return enviadas

    def sincronizar_ao_fundo(self):
        if self.ocupado:
            return
        self.ocupado = True

        def _correr():
            try:
                self.sincronizar()
            finally:
                self.ocupado = False
        threading.Thread(target=_correr, daemon=True, name="nuvem-chamadas").start()

    # ---- o que a tela mostra (a tela atualiza a cada poucos segundos: respostas do hub ficam em cache por pouco tempo)
    def _em_cache(self, chave, ttl, fn):
        v = self._cache.get(chave)
        if v and time.time() - v[0] < ttl:
            return v[1]
        r = fn()
        self._cache[chave] = (time.time(), r)
        return r

    def estado(self):
        lic = licenca.tem(FUNCAO)
        r = {"ligado": bool(configuracao.obter("chamadas_nuvem")), "licenciado": lic, "erro": self.erro or self.vivo_erro,
             "ultimo_envio": self.ultimo_envio or None, "enviadas": self.enviadas, "resumo": None,
             "relatorios": licenca.tem(FUNCAO_RELATORIOS), "varios_pcs": licenca.tem(FUNCAO_VARIOS_PCS)}
        if lic:
            try:
                r["resumo"] = self._em_cache("resumo", 20, lambda: _chamar("GET", "resumo", timeout=8))
            except NuvemErro as e:
                r["erro"] = r["erro"] or str(e)
        return r

    def chamadas(self, busca="", limite=300, pc=""):
        return _chamar("GET", "lista", params={"busca": busca or "", "limite": int(limite), **({"pc": pc} if pc else {})})["chamadas"]

    # ---- Premium: relatórios avançados (hub calcula; o programa mostra, exporta e gera o PDF)
    @staticmethod
    def _periodo(de, ate, pc):
        p = {k: int(v) for k, v in (("de", de), ("ate", ate)) if v}
        if pc:
            p["pc"] = pc
        return p

    def relatorio(self, de=0, ate=0, pc=""):
        return _chamar("GET", "relatorio", params=self._periodo(de, ate, pc), timeout=60)

    def exportar(self, de=0, ate=0, pc=""):
        return _chamar("GET", "exportar", params=self._periodo(de, ate, pc), timeout=120, bruto=True)

    # ---- Premium: vários computadores da empresa
    def computadores(self):
        return self._em_cache("pcs", 5, lambda: _chamar("GET", "computadores", timeout=10))["computadores"]

    def ao_vivo_empresa(self):
        return self._em_cache("vivo", 4, lambda: _chamar("GET", "ao-vivo", timeout=10))["computadores"]

    def publicar_ao_vivo(self):
        """Publica as chamadas em andamento DESTE computador para os outros da empresa (ficam só na memória do hub, 90 s)."""
        if not (configuracao.obter("chamadas_nuvem") and licenca.tem(FUNCAO_VARIOS_PCS)):
            return
        try:
            vivas = [c for c in self.voip.ao_vivo()["chamadas"] if c.get("ativa") and c.get("tipo") == "chamada"]
            campos = ("id", "inicio", "de", "para", "origem", "destino", "estado", "duracao", "desligou", "motivo", "tipo", "agente", "mos")
            _chamar("POST", "ao-vivo", {"chamadas": [{k: c.get(k) for k in campos} for c in vivas[:50]], "pc_nome": _nome_pc()}, timeout=8)
            self.vivo_erro = ""
        except NuvemErro as e:
            self.vivo_erro = str(e)
        except Exception as e:  # noqa: BLE001
            self.vivo_erro = f"Falha ao publicar chamadas ao vivo: {e}"

    def publicar_ao_vivo_ao_fundo(self):
        if self.vivo_ocupado:
            return
        self.vivo_ocupado = True

        def _correr():
            try:
                self.publicar_ao_vivo()
            finally:
                self.vivo_ocupado = False
        threading.Thread(target=_correr, daemon=True, name="nuvem-ao-vivo").start()

    def detalhe(self, id_):
        try:
            return _chamar("GET", "detalhe", params={"id": id_})
        except NuvemErro as e:
            if e.status == 404:
                return None
            raise

    def apagar(self, id_=None, tudo=False, confirmar=""):
        corpo = {"tudo": True, "confirmar": confirmar} if tudo else {"id": id_}
        r = _chamar("POST", "apagar", corpo)
        if tudo:
            self.pular_o_que_ja_existe()    # senão o que acabou de ser apagado subiria de novo no próximo envio
        return r
