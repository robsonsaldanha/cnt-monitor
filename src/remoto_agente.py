"""Agente do acesso remoto: existe só enquanto dura um pedido de ajuda, na sessão de quem é atendido.

Faz a conexão direta com o técnico (WebRTC), manda a tela e aplica mouse/teclado. Quem manda
nele é o serviço, por linhas JSON na entrada padrão; ele responde com o estado na saída padrão:

  entrada:  {"cmd": "resposta", "codigo": "MPCR..."} | {"cmd": "aceitar"} | {"cmd": "encerrar"}
  saída:    {"estado": "convite", "convite": "MPCC...", "senha": "ABCD-EFGH-JKLM", ...}

Se o serviço fechar a entrada padrão (parou, caiu), a sessão é encerrada na hora.
As regras (senha, uma tentativa, aceite, prazos) estão em monitorpc/remoto.py.
"""
import os
import sys
import json
import math
import time
import asyncio
import fractions
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitorpc import remoto  # noqa: E402

WIN = os.name == "nt"
STUN_PADRAO = ("stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478")
LARGURA_MAX = 1920
QUADROS_S = 15
MANTER_CAMINHO_S = 20       # roteadores esquecem um caminho UDP parado em 30 s ou mais
_MAX_MENSAGEM = 300000
_MAX_AREA = 100000

# KeyboardEvent.code do navegador -> scancode (conjunto 1). Tecla física, não letra: vale o
# layout do teclado de quem é atendido. Os de baixo (E0) são as teclas "estendidas".
SCAN = {"Escape": 0x01, "Minus": 0x0C, "Equal": 0x0D, "Backspace": 0x0E, "Tab": 0x0F, "BracketLeft": 0x1A,
        "BracketRight": 0x1B, "Enter": 0x1C, "ControlLeft": 0x1D, "Semicolon": 0x27, "Quote": 0x28, "Backquote": 0x29,
        "ShiftLeft": 0x2A, "Backslash": 0x2B, "Comma": 0x33, "Period": 0x34, "Slash": 0x35, "ShiftRight": 0x36,
        "NumpadMultiply": 0x37, "AltLeft": 0x38, "Space": 0x39, "CapsLock": 0x3A, "NumLock": 0x45, "ScrollLock": 0x46,
        "Numpad7": 0x47, "Numpad8": 0x48, "Numpad9": 0x49, "NumpadSubtract": 0x4A, "Numpad4": 0x4B, "Numpad5": 0x4C,
        "Numpad6": 0x4D, "NumpadAdd": 0x4E, "Numpad1": 0x4F, "Numpad2": 0x50, "Numpad3": 0x51, "Numpad0": 0x52,
        "NumpadDecimal": 0x53, "IntlBackslash": 0x56, "F11": 0x57, "F12": 0x58, "IntlRo": 0x73, "NumpadComma": 0x7E}
SCAN.update({"Key" + c: s for c, s in zip("QWERTYUIOP", range(0x10, 0x1A))})
SCAN.update({"Key" + c: s for c, s in zip("ASDFGHJKL", range(0x1E, 0x27))})
SCAN.update({"Key" + c: s for c, s in zip("ZXCVBNM", range(0x2C, 0x33))})
SCAN.update({"Digit" + c: s for c, s in zip("1234567890", range(0x02, 0x0C))})
SCAN.update({f"F{i}": 0x3A + i for i in range(1, 11)})
SCAN_E0 = {"NumpadEnter": 0x1C, "ControlRight": 0x1D, "NumpadDivide": 0x35, "PrintScreen": 0x37, "AltRight": 0x38,
           "Home": 0x47, "ArrowUp": 0x48, "PageUp": 0x49, "ArrowLeft": 0x4B, "ArrowRight": 0x4D, "End": 0x4F,
           "ArrowDown": 0x50, "PageDown": 0x51, "Insert": 0x52, "Delete": 0x53, "MetaLeft": 0x5B, "MetaRight": 0x5C,
           "ContextMenu": 0x5D}

M_MOVER, M_ABSOLUTO, M_RODA = 0x0001, 0x8000, 0x0800
M_BOTAO = {0: (0x0002, 0x0004), 1: (0x0020, 0x0040), 2: (0x0008, 0x0010)}     # esquerdo, meio, direito: (baixa, sobe)
T_ESTENDIDA, T_SOBE, T_SCAN = 0x0001, 0x0002, 0x0008


def _fracao(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return int(round(max(0.0, min(1.0, float(v))) * 65535))


def traduzir(m):
    """Mensagem do técnico -> eventos para o Windows: ("mouse", x, y, dado, flags) ou ("tecla", scan, flags).
    Qualquer coisa fora do formato é ignorada (lista vazia)."""
    t, a = m.get("t"), m.get("a")
    if t == "mouse":
        x, y = _fracao(m.get("x")), _fracao(m.get("y"))
        if x is None or y is None:
            return []
        flags, dado = M_MOVER | M_ABSOLUTO, 0
        if a in ("baixa", "sobe"):
            b = m.get("b")
            if isinstance(b, bool) or b not in M_BOTAO:
                return []
            flags |= M_BOTAO[b][a == "sobe"]
        elif a == "roda":
            d = m.get("d")
            if isinstance(d, bool) or not isinstance(d, (int, float)) or not math.isfinite(d):
                return []
            flags, dado = flags | M_RODA, int(max(-1200, min(1200, d)))
        elif a != "move":
            return []
        return [("mouse", x, y, dado, flags)]
    if t == "tecla" and a in ("baixa", "sobe") and isinstance(m.get("c"), str):
        c = m["c"]
        if c in SCAN:
            return [("tecla", SCAN[c], T_SCAN | (T_SOBE if a == "sobe" else 0))]
        if c in SCAN_E0:
            return [("tecla", SCAN_E0[c], T_SCAN | T_ESTENDIDA | (T_SOBE if a == "sobe" else 0))]
    return []


class Entrada:
    """Aplica os eventos no Windows e lembra o que ficou apertado, para soltar quando a sessão acaba."""

    def __init__(self, enviar=None, area=None):
        self._enviar = enviar or _send_input
        self._area = area or _por_na_area
        self._teclas = set()      # (scan, estendida)
        self._botoes = set()      # flag de "sobe" de cada botão apertado

    def aplicar(self, m):
        if m.get("t") == "area":
            if isinstance(m.get("texto"), str) and len(m["texto"]) <= _MAX_AREA:
                self._area(m["texto"])
            return
        for ev in traduzir(m):
            if ev[0] == "tecla":
                k = (ev[1], ev[2] & T_ESTENDIDA)
                (self._teclas.discard if ev[2] & T_SOBE else self._teclas.add)(k)
            else:
                for baixa, sobe in M_BOTAO.values():
                    if ev[4] & baixa:
                        self._botoes.add(sobe)
                    if ev[4] & sobe:
                        self._botoes.discard(sobe)
            self._enviar(ev)

    def soltar(self):
        """Nenhuma tecla ou botão fica preso depois que o técnico sai (um Ctrl preso atrapalha muito)."""
        for scan, est in sorted(self._teclas):
            self._enviar(("tecla", scan, T_SCAN | T_SOBE | est))
        for sobe in sorted(self._botoes):
            self._enviar(("mouse", None, None, 0, sobe))
        self._teclas.clear()
        self._botoes.clear()


_tela = threading.local()


def _na_tela_ativa():
    """Liga esta thread à área de trabalho que está aparecendo. A pergunta do UAC fica em outra área de
    trabalho: sem isto o técnico veria a tela parada e não conseguiria responder. Só a conta do serviço
    consegue abrir essa outra área; rodando como usuário comum nada muda."""
    if not WIN:
        return
    import ctypes
    u = ctypes.windll.user32
    u.OpenInputDesktop.restype = ctypes.c_void_p
    h = u.OpenInputDesktop(0, False, 0x10000000)        # GENERIC_ALL
    if not h:
        return
    h = ctypes.c_void_p(h)
    nome = ctypes.create_unicode_buffer(256)
    u.GetUserObjectInformationW(h, 2, nome, ctypes.sizeof(nome), None)      # UOI_NAME
    if nome.value and nome.value == getattr(_tela, "nome", None):
        u.CloseDesktop(h)
        return
    if u.SetThreadDesktop(h):
        antiga = getattr(_tela, "h", None)
        _tela.h, _tela.nome = h, nome.value
        if antiga:
            u.CloseDesktop(antiga)
    else:
        u.CloseDesktop(h)


def _send_input(ev):
    import ctypes
    from ctypes import wintypes
    ponteiro = ctypes.c_size_t
    _na_tela_ativa()

    class MI(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ponteiro)]

    class KI(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ponteiro)]

    class U(ctypes.Union):
        _fields_ = [("mi", MI), ("ki", KI)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", U)]

    i = INPUT()
    if ev[0] == "mouse":
        i.type = 0
        if ev[1] is None:
            i.u.mi = MI(0, 0, 0, ev[4], 0, 0)
        else:
            i.u.mi = MI(ev[1], ev[2], ctypes.c_ulong(ev[3] & 0xFFFFFFFF).value, ev[4], 0, 0)
    else:
        i.type = 1
        i.u.ki = KI(0, ev[1], ev[2], 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def _por_na_area(texto):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(texto, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def capturar_tela():
    """A tela principal, reduzida para caber em LARGURA_MAX e com lados pares (exigência do vídeo)."""
    from PIL import ImageGrab
    _na_tela_ativa()
    img = ImageGrab.grab().convert("RGB")
    if img.width > LARGURA_MAX:
        img = img.resize((LARGURA_MAX, round(img.height * LARGURA_MAX / img.width)))
    w, h = img.width - img.width % 2, img.height - img.height % 2
    return img if (w, h) == img.size else img.crop((0, 0, w, h))


def _quadro_de_espera():
    from PIL import Image
    return Image.new("RGB", (320, 180), (0, 0, 0))


class Agente:
    def __init__(self, ao_estado, stun=STUN_PADRAO, capturar=capturar_tela, entrada=None, agora=time.time):
        self.sessao = remoto.Sessao(agora)
        self.entrada = entrada or Entrada()
        self._ao_estado = ao_estado
        self._stun = list(stun or [])
        self._capturar = capturar
        # uma thread só para a tela e a entrada: os eventos saem na ordem em que chegaram
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remoto")
        self._convite = ""
        self._provando = False
        self._fechando = False
        self.fim = asyncio.Event()
        self.pc = None
        self.canal = None

    # ------------------------------------------------------------ estado para o serviço
    def _avisar(self, erro="", ordem=None):
        s = self.sessao
        d = {"estado": s.estado, "motivo": s.motivo, "conferencia": s.conferencia,
             "convite": self._convite if s.estado == "convite" else "",
             "senha": remoto.formatar_senha(s.senha) if s.estado == "convite" else ""}
        if ordem is not None:        # resposta a uma ordem do serviço: uma linha por ordem, com o número dela
            d.update(erro=erro, id=ordem)
        self._ao_estado(d)

    # ------------------------------------------------------------ conexão
    async def iniciar(self):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(urls=u) for u in self._stun]))
        self.canal = self.pc.createDataChannel("controle")
        self.canal.on("message", lambda msg: asyncio.ensure_future(self._mensagem(msg)))
        self.pc.addTrack(_trilha(self))

        @self.pc.on("connectionstatechange")
        async def _mudou():
            if self.pc.connectionState in ("failed", "closed"):
                await self.encerrar("A conexão caiu.")

        await self.pc.setLocalDescription(await self.pc.createOffer())    # já sai com os endereços (ICE completo)
        self._convite = remoto.convite(self.pc.localDescription.sdp)
        self._vigia = asyncio.ensure_future(self._vigiar())
        self._caminho = asyncio.ensure_future(self._manter_caminho())
        self._avisar()

    async def _manter_caminho(self):
        """O convite leva o endereço público que o roteador deu a este PC, mas a resposta do técnico demora
        minutos e o roteador esquece um caminho parado. Um pedido ao servidor STUN de tempos em tempos,
        pelas mesmas portas, mantém esse endereço valendo até a resposta chegar."""
        import socket
        from aioice import stun
        try:
            # antes da resposta o vídeo e o canal de controle ainda têm cada um a sua ligação
            cons = [t._connection for t in self.pc._RTCPeerConnection__iceTransports]
            servidor = next((c.stun_server for c in cons if c.stun_server), None)
            if not servidor:
                return
            destino = (await asyncio.get_running_loop().getaddrinfo(
                *servidor, family=socket.AF_INET, type=socket.SOCK_DGRAM))[0][4]
            while self.sessao.estado == "convite":
                await asyncio.sleep(MANTER_CAMINHO_S)
                for p in [p for c in cons for p in c._protocols]:
                    if ":" not in p.local_candidate.host:      # só IPv4 passa por tradução de endereço
                        p.send_stun(stun.Message(stun.Method.BINDING, stun.Class.REQUEST), destino)
        except Exception as e:  # noqa: BLE001  (é só um reforço: sem ele a sessão segue igual)
            print("remoto: não consegui manter o caminho aberto:", e, file=sys.stderr)

    async def _vigiar(self):
        while self.sessao.conferir_prazo() != "encerrada":
            await asyncio.sleep(1)
        await self.encerrar(self.sessao.motivo)

    async def comando(self, c):
        """Ordem do serviço (que só aceita ordens vindas do próprio PC). Responde sempre com uma linha."""
        if not isinstance(c, dict):
            return
        erro = ""
        try:
            await self._executar(c)
        except remoto.Erro as e:
            erro = str(e)
        self._avisar(erro, c.get("id", 0))

    async def _executar(self, c):
        from aiortc import RTCSessionDescription
        cmd = c.get("cmd")
        if cmd == "resposta":
            sdp = self.sessao.receber_resposta(c.get("codigo"))
            try:
                await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
            except Exception:  # noqa: BLE001  (SDP que o WebRTC não aceita)
                await self.encerrar("O código de resposta não serve para este convite. Gere outro convite.")
                raise remoto.Erro(self.sessao.motivo) from None
        elif cmd == "aceitar":
            self.sessao.aceitar()
            self._mandar({"t": "ativa"})
        elif cmd == "encerrar":
            await self.encerrar("Encerrado por quem está no computador.")
        else:
            raise remoto.Erro("Ordem desconhecida.")

    def _mandar(self, m):
        try:
            if self.canal and self.canal.readyState == "open":
                self.canal.send(json.dumps(m))
        except Exception:  # noqa: BLE001
            pass

    async def _mensagem(self, msg):
        if not isinstance(msg, str) or len(msg) > _MAX_MENSAGEM:
            return
        try:
            m = json.loads(msg)
        except ValueError:
            return
        if not isinstance(m, dict):
            return
        t = m.get("t")
        loop = asyncio.get_running_loop()
        if t == "prova":
            if self._provando:          # uma tentativa só, mesmo que mandem várias de uma vez
                return
            self._provando = True
            try:
                mac = await loop.run_in_executor(None, self.sessao.conferir_prova, self.pc.localDescription.sdp,
                                                 self.pc.remoteDescription.sdp, m.get("mac"))
            except remoto.Erro as e:
                await self.encerrar(str(e))
                return
            self._mandar({"t": "prova", "mac": mac})
            self._avisar()
        elif t == "fim":
            await self.encerrar("O técnico encerrou a sessão.")
        elif t in ("mouse", "tecla", "area") and self.sessao.ativa:
            self._pool.submit(self._aplicar, m)

    def _aplicar(self, m):
        try:
            if self.sessao.ativa:       # de novo: pode ter encerrado enquanto esperava na fila
                self.entrada.aplicar(m)
        except Exception as e:  # noqa: BLE001
            print("remoto: entrada recusada pelo Windows:", e, file=sys.stderr)

    async def encerrar(self, motivo=""):
        if self._fechando:
            return
        self._fechando = True
        self.sessao.encerrar(motivo)
        self._mandar({"t": "fim", "motivo": self.sessao.motivo})
        try:
            await asyncio.get_running_loop().run_in_executor(self._pool, self.entrada.soltar)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.2)        # dá tempo de o aviso de fim sair antes de fechar
        try:
            await self.pc.close()
        except Exception:  # noqa: BLE001
            pass
        self._pool.shutdown(wait=False)
        self._avisar()
        self.fim.set()


def _trilha(agente):
    import av
    from aiortc import MediaStreamTrack

    class Tela(MediaStreamTrack):
        kind = "video"

        def __init__(self):
            super().__init__()
            self._ini = None
            self._espera = _quadro_de_espera()

        async def recv(self):
            # antes do aceite (e depois do fim) só sai um quadro preto, uma vez por segundo
            ativa = agente.sessao.ativa
            if self._ini is None:
                self._ini = time.time()
            else:
                await asyncio.sleep(1 / QUADROS_S if ativa else 1)
                ativa = agente.sessao.ativa
            img = self._espera
            if ativa:
                try:
                    img = await asyncio.get_running_loop().run_in_executor(agente._pool, agente._capturar)
                except Exception:  # noqa: BLE001  (tela indisponível por um instante, fila fechada)
                    img = self._espera
                if not agente.sessao.ativa:
                    img = self._espera
            quadro = av.VideoFrame.from_image(img)
            quadro.pts = int((time.time() - self._ini) * 90000)
            quadro.time_base = fractions.Fraction(1, 90000)
            return quadro

    return Tela()


async def _principal(stun):
    loop = asyncio.get_running_loop()

    def estado(d):
        sys.stdout.write(json.dumps(d) + "\n")
        sys.stdout.flush()

    agente = Agente(estado, stun)

    def ler():
        for linha in sys.stdin:
            try:
                c = json.loads(linha)
            except ValueError:
                continue
            asyncio.run_coroutine_threadsafe(agente.comando(c), loop)
        asyncio.run_coroutine_threadsafe(agente.encerrar("O Monitor do PC foi fechado."), loop)

    await agente.iniciar()
    threading.Thread(target=ler, daemon=True).start()
    await agente.fim.wait()


def main():
    stun = STUN_PADRAO
    if "--sem-stun" in sys.argv:
        stun = ()
    elif "--stun" in sys.argv:
        stun = tuple(u for u in sys.argv[sys.argv.index("--stun") + 1].split(",") if u.startswith(("stun:", "stuns:")))
    asyncio.run(_principal(stun))


if __name__ == "__main__":
    main()
