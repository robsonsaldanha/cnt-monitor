"""Áudio: volume de cada saída e entrada, mixer por programa e ajustes avançados.

O mixer e o dispositivo padrão pertencem a quem está usando o PC: o serviço (SYSTEM na sessão 0, ou root no
Linux) não enxerga os programas que tocam som na sessão do usuário. Por isso o trabalho é feito por um ajudante
(o próprio executável do serviço com `--audio`) aberto como o usuário logado, na sessão dele. Os dois conversam
por JSON, uma linha por pedido e uma por resposta.

Windows: Core Audio (COM via ctypes, sem dependência nova). Linux: pactl (PulseAudio ou PipeWire).
"""
import os
import re
import sys
import json
import time
import queue
import shutil
import struct
import threading
import subprocess

from . import config, sessao

WIN = os.name == "nt"
TEMPO = 6                    # segundos para o ajudante responder
PAPEIS = ("tudo", "padrao", "comunicacao")
REDUCOES = ("Silenciar os outros sons", "Reduzir os outros sons em 80%", "Reduzir os outros sons em 50%", "Não fazer nada")
_ID_MAX = 512
_APP = re.compile(r"^(sis|exe:[^\r\n\0]{1,80}|in:\d{1,10})$")


def validar(a):
    """Confere um pedido de ação da tela. Devolve (pedido para o ajudante, None) ou (None, mensagem de erro)."""
    tipo = a.get("tipo")
    ped = {"op": str(tipo)[6:] if isinstance(tipo, str) and tipo.startswith("audio_") else ""}
    op = ped["op"]
    if op not in ("volume", "mudo", "canal", "db", "padrao", "ativar", "app_volume", "app_mudo", "reducao"):
        return None, "Ação de áudio desconhecida."
    if op != "reducao":
        i = a.get("id")
        if not isinstance(i, str) or not 0 < len(i) <= _ID_MAX or any(ch in i for ch in "\r\n\0"):
            return None, "Dispositivo inválido."
        ped["id"] = i
    if op in ("app_volume", "app_mudo"):
        app = a.get("app")
        if not isinstance(app, str) or not _APP.match(app):
            return None, "Programa inválido."
        ped["app"] = app
    if op in ("volume", "canal", "app_volume"):
        v = _numero(a.get("valor"))
        if v is None or not 0 <= v <= 100:
            return None, "O volume vai de 0 a 100."
        ped["valor"] = round(v)
    if op == "canal":
        c = a.get("canal")
        if not isinstance(c, int) or isinstance(c, bool) or not 0 <= c < 32:
            return None, "Canal inválido."
        ped["canal"] = c
    if op == "db":
        v = _numero(a.get("valor"))
        if v is None or not -200 <= v <= 50:
            return None, "Valor em dB inválido."
        ped["valor"] = round(v, 1)
    if op in ("mudo", "app_mudo"):
        ped["mudo"] = bool(a.get("mudo"))
    if op == "ativar":
        ped["ativo"] = bool(a.get("ativo"))
    if op == "padrao":
        if a.get("papel", "tudo") not in PAPEIS:
            return None, "Papel inválido."
        ped["papel"] = a.get("papel", "tudo")
    if op == "reducao":
        v = a.get("valor")
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < len(REDUCOES):
            return None, "Opção inválida."
        ped["valor"] = v
    return ped, None


def _numero(v):
    if isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v else None      # NaN não passa


# ---------------------------------------------------------------- lado do serviço

class Audio:
    """Mantém o ajudante aberto na sessão de quem usa o PC e repassa os pedidos a ele."""

    def __init__(self, comando=None):
        self._comando = comando          # testes: troca o ajudante por outro programa
        self._lock = threading.Lock()
        self._proc = None
        self._fila = None
        self._dono = None
        self._conferido = 0
        self._exes = {}                  # (pid, nome) -> caminho do .exe, para o ícone do programa no mixer

    def _cmd(self):
        if self._comando:
            return list(self._comando)
        if getattr(sys, "frozen", False):
            return [sys.executable, "--audio"]
        return [sys.executable, os.path.join(config.pasta_recursos(), "servico.py"), "--audio"]

    def _quem(self):
        """Quem está usando o PC agora (troca de usuário reabre o ajudante na sessão certa)."""
        if self._comando is not None or not sessao.em_servico():
            return None
        if WIN:
            import win32ts
            return win32ts.WTSGetActiveConsoleSessionId()
        s = sessao.sessao_grafica()
        return s and s["uid"]

    def _iniciar(self):
        self._dono = self._quem()
        cmd = self._cmd()
        if self._comando is None and sessao.em_servico():
            p = sessao.abrir_como_usuario(cmd)
        else:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, encoding="utf-8", bufsize=1,
                                 creationflags=0x08000000 if WIN else 0)
        fila = queue.Queue()

        def ler():
            try:
                for linha in p.stdout:
                    fila.put(linha)
            except (OSError, ValueError):
                pass
            fila.put(None)
        threading.Thread(target=ler, daemon=True, name="audio-ajudante").start()
        self._proc, self._fila, self._conferido = p, fila, time.time()

    def _vivo(self):
        if self._proc is None or self._proc.poll() is not None:
            return False
        if time.time() - self._conferido > 10:
            self._conferido = time.time()
            if self._quem() != self._dono:
                self._matar()
                return False
        return True

    def _matar(self):
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def pedir(self, ped):
        with self._lock:
            for tentativa in (1, 2):
                if not self._vivo():
                    self._iniciar()
                try:
                    self._proc.stdin.write(json.dumps(ped) + "\n")
                    self._proc.stdin.flush()
                    linha = self._fila.get(timeout=TEMPO)
                except (OSError, ValueError, queue.Empty):
                    linha = None
                if linha:
                    try:
                        return json.loads(linha)
                    except ValueError:
                        pass
                self._matar()
            raise OSError("O controle de áudio não respondeu. Tente de novo em alguns segundos.")

    def estado(self):
        try:
            r = self.pedir({"op": "listar"})
        except OSError as e:
            r = {"erro": str(e)}
        self._com_exe(r.get("programas") or [])
        r["sistema"] = "windows" if WIN else "linux"
        r["reducoes"] = list(REDUCOES)
        return r

    def _com_exe(self, programas):
        """Acrescenta o caminho do executável (o serviço enxerga todos os processos; o ajudante, só os da pessoa)."""
        import psutil
        for p in programas:
            pid = p.get("pid")
            if not pid or p.get("app") == "sis":
                continue
            chave = (pid, p.get("nome"))
            if chave not in self._exes:
                try:
                    exe = psutil.Process(pid).exe()
                except (psutil.Error, OSError):
                    exe = ""
                if len(self._exes) > 500:
                    self._exes.clear()
                self._exes[chave] = exe
            if self._exes[chave]:
                p["exe"] = self._exes[chave]

    def acao(self, a):
        ped, erro = validar(a)
        if erro:
            return {"ok": False, "msg": erro}
        try:
            r = self.pedir(ped)
        except OSError as e:
            return {"ok": False, "msg": str(e)}
        return {"ok": bool(r.get("ok")), "msg": r.get("msg") or "Feito."}

    def parar(self):
        with self._lock:
            self._matar()


# ---------------------------------------------------------------- ajudante (sessão do usuário)

def ajudante():
    """Laço do `--audio`: lê um pedido JSON por linha e responde outro."""
    try:
        imp, erro = (_CoreAudio() if WIN else _Pulse()), None
    except Exception as e:  # noqa: BLE001
        imp, erro = None, f"Não consegui abrir o controle de áudio: {e}"
    for linha in sys.stdin:
        try:
            ped = json.loads(linha)
            if imp is None:
                r = {"ok": False, "msg": erro, "erro": erro}
            elif ped.get("op") == "listar":
                r = imp.listar()
            else:
                r = imp.executar(ped)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "msg": f"Não consegui: {e}"}
        sys.stdout.write(json.dumps(r) + "\n")     # ASCII puro: não depende da página de código do console
        sys.stdout.flush()


def _formato_texto(f):
    if not f:
        return ""
    return f"{f['hz'] / 1000:g} kHz, {f['bits']} bits{' (ponto flutuante)' if f.get('float') else ''}, {f['canais']} {'canal' if f['canais'] == 1 else 'canais'}"


# ----- Windows: Core Audio

if WIN:
    import ctypes
    from ctypes import wintypes as wt
    from .captura import _vt, _soltar, _guid

    _CLSID_ENUM = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    _IID_ENUM = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    _IID_VOL = "{5CDF2C82-841E-4546-9722-0CF74078229A}"           # IAudioEndpointVolume
    _IID_MEDIDOR = "{C02216F6-8C67-4B5B-9D00-D008E73E0064}"       # IAudioMeterInformation
    _IID_SESSOES = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"       # IAudioSessionManager2
    _IID_CONTROLE2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"     # IAudioSessionControl2
    _IID_SIMPLES = "{87CE5498-68D6-44E5-9215-6DA47EF883D8}"       # ISimpleAudioVolume
    _CLSID_POLITICA = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"    # IPolicyConfig (o mesmo do painel de Som)
    _IID_POLITICA = "{F8679F50-850A-41CF-9C72-430F290290C8}"
    _ESTADOS = {1: "ativo", 2: "desativado", 8: "desconectado"}
    _CHAVE_REDUCAO = r"Software\Microsoft\Multimedia\Audio"

    class _PK(ctypes.Structure):
        _fields_ = [("fmtid", ctypes.c_byte * 16), ("pid", wt.DWORD)]

    class _PV(ctypes.Structure):            # PROPVARIANT (24 bytes no x64)
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]

    def _exe(pid):
        """Chave do programa ("exe:discord"): os vários processos do mesmo programa viram uma linha só no mixer."""
        import psutil
        try:
            nome = psutil.Process(pid).name()
        except psutil.Error:
            return None
        return "exe:" + (nome[:-4] if nome.lower().endswith(".exe") else nome)[:80]

    def _pk(guid, pid):
        k = _PK()
        k.fmtid = _guid(guid)
        k.pid = pid
        return k

    _PK_NOME = _pk("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 14)       # PKEY_Device_FriendlyName
    _PK_PLACA = _pk("{026E516E-B814-414B-83CD-856D6FEF4822}", 2)       # PKEY_DeviceInterface_FriendlyName
    _PK_FORMATO = _pk("{F19F064D-082C-4E27-BC73-6882A1BB8E4C}", 0)     # PKEY_AudioEngine_DeviceFormat

    def _qi(obj, iid):
        p = ctypes.c_void_p()
        _vt(obj, 0, ctypes.byref(_guid(iid)), ctypes.byref(p))
        return p

    def _ativar(dev, iid):
        p = ctypes.c_void_p()
        _vt(dev, 3, ctypes.byref(_guid(iid)), ctypes.c_uint(23), None, ctypes.byref(p))   # CLSCTX_ALL
        return p

    def _texto_com(p):
        t = ctypes.wstring_at(p) if p else ""
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(p))
        return t

    def _ler_formato(raw):
        """WAVEFORMATEX(TENSIBLE) -> {hz, bits, canais, float}."""
        if len(raw) < 16:
            return None
        tag, canais, hz = struct.unpack_from("<HHI", raw)
        bits = struct.unpack_from("<H", raw, 14)[0]
        flutuante = tag == 3
        if tag == 0xFFFE and len(raw) >= 28:
            bits = struct.unpack_from("<H", raw, 18)[0] or bits
            flutuante = struct.unpack_from("<I", raw, 24)[0] == 3      # SubFormat = IEEE_FLOAT
        return {"hz": hz, "bits": bits, "canais": canais, "float": flutuante}

    class _CoreAudio:
        def __init__(self):
            ctypes.oledll.ole32.CoInitializeEx(None, 0)
            self.en = ctypes.c_void_p()
            ctypes.oledll.ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_ENUM)), None, 1,
                                                 ctypes.byref(_guid(_IID_ENUM)), ctypes.byref(self.en))

        # --- leitura

        def _dispositivos(self, fluxo, mascara=0xB):            # ativos, desativados e desconectados
            col, n = ctypes.c_void_p(), ctypes.c_uint()
            _vt(self.en, 3, ctypes.c_int(fluxo), ctypes.c_uint(mascara), ctypes.byref(col))
            try:
                _vt(col, 3, ctypes.byref(n))
                devs = []
                for i in range(n.value):
                    d = ctypes.c_void_p()
                    _vt(col, 4, ctypes.c_uint(i), ctypes.byref(d))
                    devs.append(d)
                return devs
            finally:
                _soltar(col)

        def _id(self, dev):
            s = ctypes.c_void_p()
            _vt(dev, 5, ctypes.byref(s))
            return _texto_com(s.value)

        def _padrao(self, fluxo, papel):
            d = ctypes.c_void_p()
            try:
                _vt(self.en, 4, ctypes.c_int(fluxo), ctypes.c_int(papel), ctypes.byref(d))
            except OSError:
                return None
            try:
                return self._id(d)
            finally:
                _soltar(d)

        def _dispositivo(self, ident):
            d = ctypes.c_void_p()
            try:
                _vt(self.en, 5, ctypes.c_wchar_p(ident), ctypes.byref(d))
            except OSError:
                raise OSError("Esse dispositivo não existe mais.") from None
            return d

        def _propriedades(self, dev):
            st = ctypes.c_void_p()
            _vt(dev, 4, ctypes.c_uint(0), ctypes.byref(st))         # STGM_READ
            out = {}
            try:
                for nome, chave in (("nome", _PK_NOME), ("placa", _PK_PLACA), ("formato", _PK_FORMATO)):
                    pv = _PV()
                    try:
                        _vt(st, 5, ctypes.byref(chave), ctypes.byref(pv))
                    except OSError:
                        continue
                    if pv.vt == 31 and pv.p:                            # VT_LPWSTR
                        out[nome] = ctypes.wstring_at(pv.p)
                    elif pv.vt == 65 and pv.p2:                         # VT_BLOB
                        out[nome] = _ler_formato(ctypes.string_at(pv.p2, (pv.p or 0) & 0xFFFFFFFF))
                    ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv))
            finally:
                _soltar(st)
            return out

        def _pico(self, obj, ativar=True):
            try:
                m = _ativar(obj, _IID_MEDIDOR) if ativar else _qi(obj, _IID_MEDIDOR)
            except OSError:
                return None
            try:
                v = ctypes.c_float()
                _vt(m, 3, ctypes.byref(v))
                return round(v.value * 100)
            finally:
                _soltar(m)

        def _volume(self, dev):
            v = _ativar(dev, _IID_VOL)
            try:
                esc, mudo, n = ctypes.c_float(), ctypes.c_int(), ctypes.c_uint()
                _vt(v, 9, ctypes.byref(esc))
                _vt(v, 15, ctypes.byref(mudo))
                _vt(v, 5, ctypes.byref(n))
                canais = []
                for i in range(min(n.value, 32)):
                    c = ctypes.c_float()
                    _vt(v, 13, ctypes.c_uint(i), ctypes.byref(c))
                    canais.append(round(c.value * 100))
                db, mn, mx, inc = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                _vt(v, 8, ctypes.byref(db))
                _vt(v, 20, ctypes.byref(mn), ctypes.byref(mx), ctypes.byref(inc))
                return {"volume": round(esc.value * 100), "mudo": bool(mudo.value), "canais": canais,
                        "db": round(db.value, 1), "db_min": round(mn.value, 1), "db_max": round(mx.value, 1)}
            finally:
                _soltar(v)

        def _sessoes(self, dev):
            """[(chave, pid, ativa, IAudioSessionControl2)] do dispositivo; quem chama solta os controles."""
            ger, en, n = _ativar(dev, _IID_SESSOES), ctypes.c_void_p(), ctypes.c_int()
            try:
                _vt(ger, 5, ctypes.byref(en))
            finally:
                _soltar(ger)
            out = []
            try:
                _vt(en, 3, ctypes.byref(n))
                for i in range(n.value):
                    s = ctypes.c_void_p()
                    _vt(en, 4, ctypes.c_int(i), ctypes.byref(s))
                    try:
                        c2 = _qi(s, _IID_CONTROLE2)
                    finally:
                        _soltar(s)
                    est, pid = ctypes.c_int(), wt.DWORD()
                    _vt(c2, 3, ctypes.byref(est))
                    if est.value == 2:                                   # expirada
                        _soltar(c2)
                        continue
                    _vt(c2, 14, ctypes.byref(pid))
                    chave = "sis" if _vt(c2, 15) == 0 else _exe(pid.value)     # S_OK: sons do sistema
                    if chave in (None, "exe:audiodg"):                   # processo já fechou / motor de áudio do Windows
                        _soltar(c2)
                        continue
                    out.append((chave, pid.value, est.value == 1, c2))
            finally:
                _soltar(en)
            return out

        def _programas(self, dev, ident):
            grupos = {}
            for chave, pid, ativa, c2 in self._sessoes(dev):
                try:
                    g = grupos.get(chave)
                    if g is None:
                        nome = "Sons do sistema"
                        if chave != "sis":
                            s = ctypes.c_void_p()
                            _vt(c2, 4, ctypes.byref(s))
                            nome = _texto_com(s.value)
                            if not nome or nome.startswith("@"):
                                nome = chave[4:]
                        sv = _qi(c2, _IID_SIMPLES)
                        try:
                            vol, mudo = ctypes.c_float(), ctypes.c_int()
                            _vt(sv, 4, ctypes.byref(vol))
                            _vt(sv, 6, ctypes.byref(mudo))
                        finally:
                            _soltar(sv)
                        g = grupos[chave] = {"app": chave, "nome": nome, "pid": pid, "dispositivo": ident,
                                             "volume": round(vol.value * 100), "mudo": bool(mudo.value),
                                             "ativo": False, "nivel": 0}
                    g["ativo"] = g["ativo"] or ativa
                    g["nivel"] = max(g["nivel"], self._pico(c2, ativar=False) or 0)
                finally:
                    _soltar(c2)
            return sorted(grupos.values(), key=lambda g: (g["app"] != "sis", g["nome"].lower()))

        def listar(self):
            padrao = {f: (self._padrao(f, 1), self._padrao(f, 2)) for f in (0, 1)}
            saidas, entradas, programas = [], [], []
            for fluxo, lista in ((0, saidas), (1, entradas)):
                for dev in self._dispositivos(fluxo):
                    try:
                        ident = self._id(dev)
                        est = ctypes.c_uint()
                        _vt(dev, 6, ctypes.byref(est))
                        d = {"id": ident, "estado": _ESTADOS.get(est.value, "ausente"),
                             "padrao": ident == padrao[fluxo][0], "comunicacao": ident == padrao[fluxo][1]}
                        p = self._propriedades(dev)
                        d["nome"] = p.get("nome") or "Dispositivo sem nome"
                        d["placa"] = p.get("placa") or ""
                        d["formato"] = p.get("formato")
                        d["formato_texto"] = _formato_texto(d["formato"])
                        if est.value == 1:
                            try:
                                d.update(self._volume(dev))
                            except OSError:
                                pass
                            d["nivel"] = self._pico(dev)
                            if fluxo == 0:
                                try:
                                    programas += self._programas(dev, ident)
                                except OSError:
                                    pass
                        lista.append(d)
                    finally:
                        _soltar(dev)
            for lista in (saidas, entradas):
                lista.sort(key=lambda d: (d["estado"] != "ativo", not d["padrao"], d["nome"].lower()))
            return {"saidas": saidas, "entradas": entradas, "programas": programas, "reducao": self._reducao(),
                    "recursos": {"comunicacao": True, "ativar": True, "reducao": True, "nivel": True, "db": True}}

        def _reducao(self):
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CHAVE_REDUCAO) as k:
                    v = winreg.QueryValueEx(k, "UserDuckingPreference")[0]
            except OSError:
                v = 1                                   # padrão do Windows: reduzir em 80%
            return v if v in range(len(REDUCOES)) else 1

        # --- ações

        def executar(self, p):
            op = p["op"]
            if op == "reducao":
                import winreg
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CHAVE_REDUCAO) as k:
                    winreg.SetValueEx(k, "UserDuckingPreference", 0, winreg.REG_DWORD, p["valor"])
                return {"ok": True, "msg": "Salvo. Vale a partir da próxima chamada."}
            if op in ("padrao", "ativar"):
                pol = ctypes.c_void_p()
                ctypes.oledll.ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_POLITICA)), None, 1,
                                                     ctypes.byref(_guid(_IID_POLITICA)), ctypes.byref(pol))
                try:
                    if op == "ativar":
                        _vt(pol, 14, ctypes.c_wchar_p(p["id"]), ctypes.c_int(int(p["ativo"])))
                        return {"ok": True, "msg": "Dispositivo ativado." if p["ativo"] else "Dispositivo desativado."}
                    papeis = {"tudo": (0, 1, 2), "padrao": (0, 1), "comunicacao": (2,)}[p["papel"]]
                    for papel in papeis:
                        _vt(pol, 13, ctypes.c_wchar_p(p["id"]), ctypes.c_int(papel))
                    return {"ok": True, "msg": "Dispositivo padrão trocado."}
                finally:
                    _soltar(pol)
            dev = self._dispositivo(p["id"])
            try:
                if op in ("app_volume", "app_mudo"):
                    achou = False
                    for chave, _pid, _ativa, c2 in self._sessoes(dev):
                        try:
                            if chave != p["app"]:
                                continue
                            sv = _qi(c2, _IID_SIMPLES)
                            try:
                                if op == "app_volume":
                                    _vt(sv, 3, ctypes.c_float(p["valor"] / 100), None)
                                else:
                                    _vt(sv, 5, ctypes.c_int(int(p["mudo"])), None)
                                achou = True
                            finally:
                                _soltar(sv)
                        finally:
                            _soltar(c2)
                    return {"ok": True} if achou else {"ok": False, "msg": "Esse programa não está mais tocando som."}
                v = _ativar(dev, _IID_VOL)
                try:
                    if op == "volume":
                        _vt(v, 7, ctypes.c_float(p["valor"] / 100), None)
                    elif op == "mudo":
                        _vt(v, 14, ctypes.c_int(int(p["mudo"])), None)
                    elif op == "canal":
                        n = ctypes.c_uint()
                        _vt(v, 5, ctypes.byref(n))
                        if p["canal"] >= n.value:
                            return {"ok": False, "msg": "Esse dispositivo não tem esse canal."}
                        _vt(v, 11, ctypes.c_uint(p["canal"]), ctypes.c_float(p["valor"] / 100), None)
                    elif op == "db":
                        mn, mx, inc = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                        _vt(v, 20, ctypes.byref(mn), ctypes.byref(mx), ctypes.byref(inc))
                        _vt(v, 6, ctypes.c_float(min(max(p["valor"], mn.value), mx.value)), None)
                finally:
                    _soltar(v)
                return {"ok": True}
            finally:
                _soltar(dev)


# ----- Linux: pactl (PulseAudio e PipeWire)

_BITS = {"u8": 8, "s16": 16, "s24": 24, "s24-32": 24, "s32": 32, "float32": 32}


def _ler_spec(txt):
    """'s16le 2ch 48000Hz' -> {hz, bits, canais, float}."""
    m = re.match(r"^(\S+?)(?:le|be|ne|re)?\s+(\d+)ch\s+(\d+)Hz", txt or "")
    if not m:
        return None
    return {"hz": int(m.group(3)), "bits": _BITS.get(m.group(1), 0), "canais": int(m.group(2)),
            "float": m.group(1).startswith("float")}


def _pct(v):
    try:
        return int(str(v.get("value_percent", "0")).rstrip("%"))
    except (AttributeError, ValueError):
        return 0


def _db(v):
    try:
        return round(float(str(v.get("db", "")).split()[0]), 1)
    except (AttributeError, ValueError, IndexError):
        return None


class _Pulse:
    def __init__(self):
        self.pactl = shutil.which("pactl")
        if not self.pactl:
            raise OSError("falta o pactl (pacote pulseaudio-utils)")
        self._tipo = {}

    def _rodar(self, *args, em_json=False):
        # "--": valores negativos como "-6dB" não podem ser lidos como opção
        r = subprocess.run([self.pactl] + (["-f", "json"] if em_json else []) + ["--"] + list(args), capture_output=True,
                           text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
        if r.returncode:
            raise OSError((r.stderr or r.stdout).strip() or "o pactl falhou")
        return json.loads(r.stdout or "[]") if em_json else r.stdout

    def _dispositivo(self, x, padrao):
        vol = x.get("volume") or {}
        canais = [_pct(vol[c]) for c in vol]
        primeiro = next(iter(vol.values()), {})
        f = _ler_spec(x.get("sample_specification"))
        return {"id": x.get("name", ""), "nome": x.get("description") or x.get("name", ""),
                "placa": (x.get("properties") or {}).get("alsa.card_name", ""), "estado": "ativo",
                "padrao": x.get("name") == padrao, "comunicacao": False, "volume": max(canais or [0]),
                "mudo": bool(x.get("mute")), "canais": canais, "nomes_canais": list(vol),
                "db": _db(primeiro), "db_min": None, "db_max": None, "nivel": None,
                "formato": f, "formato_texto": _formato_texto(f)}

    def listar(self):
        info = self._rodar("info")
        padrao = dict(re.findall(r"^Default (Sink|Source): (.+)$", info, re.M))
        sinks = self._rodar("list", "sinks", em_json=True)
        sources = [s for s in self._rodar("list", "sources", em_json=True)
                   if s.get("monitor_of_sink") in (None, "n/a") and not str(s.get("name", "")).endswith(".monitor")]
        saidas = [self._dispositivo(s, padrao.get("Sink")) for s in sinks]
        entradas = [self._dispositivo(s, padrao.get("Source")) for s in sources]
        self._tipo = {d["id"]: "sink" for d in saidas} | {d["id"]: "source" for d in entradas}
        nome_sink = {s.get("index"): s.get("name") for s in sinks}
        programas = []
        for e in self._rodar("list", "sink-inputs", em_json=True):
            pr = e.get("properties") or {}
            vol = e.get("volume") or {}
            programas.append({"app": f"in:{e.get('index')}", "nome": pr.get("application.name") or
                              pr.get("application.process.binary") or "Programa",
                              "pid": int(pr.get("application.process.id") or 0),
                              "dispositivo": nome_sink.get(e.get("sink"), ""),
                              "volume": max([_pct(v) for v in vol.values()] or [0]), "mudo": bool(e.get("mute")),
                              "ativo": not e.get("corked", False), "nivel": None})
        programas.sort(key=lambda g: g["nome"].lower())
        return {"saidas": saidas, "entradas": entradas, "programas": programas, "reducao": None,
                "recursos": {"comunicacao": False, "ativar": False, "reducao": False, "nivel": False, "db": True}}

    def executar(self, p):
        op = p["op"]
        if op in ("ativar", "reducao"):
            return {"ok": False, "msg": "Isso só existe no Windows."}
        if op in ("app_volume", "app_mudo"):
            idx = p["app"].split(":", 1)[1] if p["app"].startswith("in:") else None
            if idx is None:
                return {"ok": False, "msg": "Programa inválido."}
            if op == "app_volume":
                self._rodar("set-sink-input-volume", idx, f"{p['valor']}%")
            else:
                self._rodar("set-sink-input-mute", idx, "1" if p["mudo"] else "0")
            return {"ok": True}
        if p["id"] not in self._tipo:
            self.listar()
        tipo = self._tipo.get(p["id"])
        if not tipo:
            return {"ok": False, "msg": "Esse dispositivo não existe mais."}
        if op == "padrao":
            self._rodar(f"set-default-{tipo}", p["id"])
            return {"ok": True, "msg": "Dispositivo padrão trocado."}
        if op == "volume":
            self._rodar(f"set-{tipo}-volume", p["id"], f"{p['valor']}%")
        elif op == "mudo":
            self._rodar(f"set-{tipo}-mute", p["id"], "1" if p["mudo"] else "0")
        elif op == "db":
            self._rodar(f"set-{tipo}-volume", p["id"], f"{p['valor']}dB")
        elif op == "canal":
            lista = {d["id"]: d for d in self.listar()["saidas" if tipo == "sink" else "entradas"]}
            canais = list(lista[p["id"]]["canais"]) if p["id"] in lista else []
            if p["canal"] >= len(canais):
                return {"ok": False, "msg": "Esse dispositivo não tem esse canal."}
            canais[p["canal"]] = p["valor"]
            self._rodar(f"set-{tipo}-volume", p["id"], *[f"{c}%" for c in canais])
        return {"ok": True}
