"""Agente do acesso remoto: existe só enquanto dura um pedido de ajuda, na sessão de quem é atendido.

Faz a conexão direta com o técnico (WebRTC), manda a tela (uma trilha de vídeo por monitor) e aplica
mouse/teclado. Quem manda nele é o serviço, por linhas JSON na entrada padrão; ele responde com o estado
na saída padrão:

  entrada:  {"cmd": "resposta", "codigo": "MPCR..."} | {"cmd": "senha", "senha": "..."} | {"cmd": "aceitar"}
            | {"cmd": "area_permitir", "valor": true} | {"cmd": "encerrar"}
  saída:    {"estado": "convite", "convite": "MPCC...", "senha": "ABCD-EFGH-JKLM", ...}
            {"arquivo": {...}}   (uma transferência concluída, para o histórico)
            {"log": "..."}       (negociação da conexão, para o servico.log)
  --testar: só confere se alcança o STUN e o retransmissor (UDP e TCP), imprime uma linha JSON e sai.

Com o técnico são dois canais: "controle" (criado aqui; mouse, teclado, atalhos, texto da área de
transferência) e "dados" (criado pelo técnico depois do aceite; arquivos e imagens, em pedaços binários
com o número da transferência nos 4 primeiros bytes).

Se o serviço fechar a entrada padrão (parou, caiu), a sessão é encerrada na hora.
As regras (senha, uma tentativa, aceite, prazos, atalhos, caminhos) estão em monitorpc/remoto.py.
"""
import io
import os
import re
import sys
import json
import math
import time
import struct
import asyncio
import hashlib
import fractions
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitorpc import remoto  # noqa: E402

WIN = os.name == "nt"
STUN_PADRAO = ("stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478")
LARGURA_MAX = 1920
QUADROS_S = 30
QUADROS_ESCONDIDO = 1      # monitor que o técnico não está vendo: 1 quadro por segundo (poupa o processador)
# Limites de taxa do vídeo. Os da biblioteca (1,5 Mbps no VP8, 3 Mbps no H.264) deixam 1080p borrado; o navegador
# do técnico ainda baixa a taxa sozinho quando a rede não aguenta.
TAXAS = {"vpx": (800_000, 2_500_000, 6_000_000), "h264": (1_000_000, 3_000_000, 10_000_000)}   # mínimo, inicial, máximo
MANTER_CAMINHO_S = 20       # roteadores esquecem um caminho UDP parado em 30 s ou mais
VIGIAR_AREA_S = 0.7
LER_AREA_TENTATIVAS = 6     # ~4 s tentando ler uma cópia nova antes de desistir dela
_MAX_MENSAGEM = 300000
_MAX_AREA = remoto.MAX_TEXTO_AREA
_BUFFER_MAX = 4 * 1024 * 1024     # bytes na fila do canal antes de esperar (não enche a memória com um arquivo grande)

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

M_MOVER, M_ABSOLUTO, M_RODA, M_VIRTUAL = 0x0001, 0x8000, 0x0800, 0x4000
M_BOTAO = {0: (0x0002, 0x0004), 1: (0x0020, 0x0040), 2: (0x0008, 0x0010)}     # esquerdo, meio, direito: (baixa, sobe)
T_ESTENDIDA, T_SOBE, T_SCAN = 0x0001, 0x0002, 0x0008


def _fr(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return max(0.0, min(1.0, float(v)))


def area_virtual(monitores):
    """(x, y, largura, altura) da área de trabalho inteira: o retângulo que contém todos os monitores."""
    x0, y0 = min(m["x"] for m in monitores), min(m["y"] for m in monitores)
    x1, y1 = max(m["x"] + m["w"] for m in monitores), max(m["y"] + m["h"] for m in monitores)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _no_monitor(monitores, i, fx, fy):
    """Fração (0..1) dentro do monitor i -> coordenada 0..65535 da área de trabalho inteira, no pixel exato
    (o Windows converte de volta com pixel = n * largura / 65536, arredondando para baixo)."""
    vx, vy, vw, vh = area_virtual(monitores)
    m = monitores[i]
    px = m["x"] + round(fx * (m["w"] - 1)) - vx
    py = m["y"] + round(fy * (m["h"] - 1)) - vy
    return min(65535, -(-px * 65536 // vw)), min(65535, -(-py * 65536 // vh))


def traduzir(m, monitores=None):
    """Mensagem do técnico -> eventos para o Windows: ("mouse", x, y, dado, flags) ou ("tecla", scan, flags).
    Com a lista de monitores, a posição é a fração dentro do monitor "m" (padrão: o principal) e vale para a
    área de trabalho inteira. Qualquer coisa fora do formato é ignorada (lista vazia)."""
    t, a = m.get("t"), m.get("a")
    if t == "mouse":
        fx, fy = _fr(m.get("x")), _fr(m.get("y"))
        if fx is None or fy is None:
            return []
        if monitores:
            i = m.get("m", 0)
            if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(monitores):
                return []
            (x, y), flags = _no_monitor(monitores, i, fx, fy), M_MOVER | M_ABSOLUTO | M_VIRTUAL
        else:
            x, y, flags = int(round(fx * 65535)), int(round(fy * 65535)), M_MOVER | M_ABSOLUTO
        dado = 0
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

    def __init__(self, enviar=None, area=None, sas=None, bloquear=None):
        self._enviar = enviar or _send_input
        self._area = area or _por_na_area
        self._sas = sas or _ctrl_alt_del
        self._bloquear = bloquear or _bloquear
        self.monitores = None
        self._teclas = set()      # (scan, estendida)
        self._botoes = set()      # flag de "sobe" de cada botão apertado

    def aplicar(self, m):
        """Devolve um aviso para o técnico ("" quando deu certo ou não há o que dizer)."""
        if m.get("t") == "area":
            if isinstance(m.get("texto"), str) and len(m["texto"]) <= _MAX_AREA:
                self._area(m["texto"])
            return ""
        if m.get("t") == "atalho":
            return self.atalho(m.get("n"))
        for ev in traduzir(m, self.monitores):
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
        return ""

    def atalho(self, nome):
        """Uma combinação da lista fechada (remoto.ATALHOS): aperta tudo na ordem e solta ao contrário."""
        seq = remoto.ATALHOS.get(nome) if isinstance(nome, str) else None
        if seq == "SAS":
            return self._sas()
        if seq == "BLOQUEAR":
            return self._bloquear()
        for c in seq or ():
            self.aplicar({"t": "tecla", "a": "baixa", "c": c})
        for c in reversed(seq or ()):
            self.aplicar({"t": "tecla", "a": "sobe", "c": c})
        return ""

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


def _ctrl_alt_del():
    """Ctrl+Alt+Del simulado o Windows ignora. SendSAS funciona para a conta do serviço (este agente roda como
    SYSTEM), desde que a política SoftwareSASGeneration permita: o instalador liga essa política."""
    import ctypes
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System") as k:
            politica = winreg.QueryValueEx(k, "SoftwareSASGeneration")[0]
    except OSError:
        politica = 0
    if politica not in (1, 3):
        return ("O Windows do outro computador não deixa programas mandarem Ctrl+Alt+Del "
                "(política SoftwareSASGeneration). Reinstale o CNT Monitor nele para ligar essa opção.")
    try:
        ctypes.WinDLL("sas.dll").SendSAS(False)
    except OSError as e:
        return f"Ctrl+Alt+Del não funcionou: {e}"
    return ""


def _bloquear():
    import ctypes
    _na_tela_ativa()
    if not ctypes.windll.user32.LockWorkStation():
        return "O Windows não deixou bloquear a tela."
    return ""


def _por_na_area(texto):
    AreaWindows().texto(texto)


# ------------------------------------------------------------------ área de transferência (Windows)
class AreaWindows:
    """Ler e escrever a área de transferência de quem é atendido: texto, imagem e lista de arquivos."""

    def _abrir(self):
        import win32clipboard
        _na_tela_ativa()
        for _ in range(10):         # outro programa pode estar com ela aberta por um instante
            try:
                win32clipboard.OpenClipboard()
                return win32clipboard
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        win32clipboard.OpenClipboard()
        return win32clipboard

    def seq(self):
        import ctypes
        _na_tela_ativa()
        return ctypes.windll.user32.GetClipboardSequenceNumber()

    def ler(self):
        """("arquivos", [caminhos]) | ("texto", str) | ("imagem", png) | None. Texto antes de imagem: planilhas
        e editores põem as duas coisas, e quem copia texto quer colar texto."""
        wc = self._abrir()
        try:
            if wc.IsClipboardFormatAvailable(wc.CF_HDROP):
                return "arquivos", list(wc.GetClipboardData(wc.CF_HDROP))
            if wc.IsClipboardFormatAvailable(wc.CF_UNICODETEXT):
                return "texto", wc.GetClipboardData(wc.CF_UNICODETEXT)
        finally:
            wc.CloseClipboard()
        from PIL import Image, ImageGrab
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            b = io.BytesIO()
            img.save(b, "PNG")
            return "imagem", b.getvalue()
        return None

    def texto(self, texto):
        wc = self._abrir()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardText(texto, wc.CF_UNICODETEXT)
        finally:
            wc.CloseClipboard()

    def imagem(self, png):
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        img.load()
        b = io.BytesIO()
        img.convert("RGB").save(b, "BMP")
        wc = self._abrir()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(wc.CF_DIB, b.getvalue()[14:])            # sem o cabeçalho do arquivo .bmp
            wc.SetClipboardData(wc.RegisterClipboardFormat("PNG"), png)   # com transparência, para quem entende PNG
        finally:
            wc.CloseClipboard()

    def arquivos(self, caminhos):
        """Arquivos prontos para o Ctrl+V no Explorer (CF_HDROP, como o Explorer faz ao copiar)."""
        lista = ("\0".join(caminhos) + "\0\0").encode("utf-16-le")
        wc = self._abrir()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(wc.CF_HDROP, struct.pack("<IiiII", 20, 0, 0, 0, 1) + lista)   # DROPFILES, texto largo
            wc.SetClipboardData(wc.RegisterClipboardFormat("Preferred DropEffect"), struct.pack("<I", 1))   # copiar
        finally:
            wc.CloseClipboard()


# ------------------------------------------------------------------ arquivos com as permissões de quem é atendido
def _token_usuario():
    """Token de quem está usando o PC. Só a conta do serviço consegue pegar: rodando como o próprio usuário
    (testes, demonstração) devolve None e nada muda, porque o processo já tem as permissões dele."""
    if not WIN:
        return None
    try:
        import win32ts
        return win32ts.WTSQueryUserToken(win32ts.WTSGetActiveConsoleSessionId())
    except Exception:  # noqa: BLE001
        return None


class ComoUsuario:
    """O agente roda como SYSTEM (para operar o UAC), mas arquivo só se lê e grava como a pessoa logada:
    o técnico vê exatamente o que ela veria, e o que chega fica com ela como dona."""

    def __enter__(self):
        self.t = _token_usuario()
        if self.t:
            import win32security
            win32security.ImpersonateLoggedOnUser(self.t)
        return self

    def __exit__(self, *_):
        if self.t:
            import win32security
            win32security.RevertToSelf()
            self.t.Close()


def pastas_do_usuario():
    """Área de Trabalho, Documentos e Downloads de quem é atendido (seguem o OneDrive, se ela usa)."""
    out = {}
    t = _token_usuario()
    try:
        from win32com.shell import shell, shellcon
        for k, fid in (("Área de Trabalho", shellcon.FOLDERID_Desktop), ("Documentos", shellcon.FOLDERID_Documents),
                       ("Downloads", shellcon.FOLDERID_Downloads)):
            try:
                out[k] = shell.SHGetKnownFolderPath(fid, 0, t)
            except Exception:  # noqa: BLE001
                pass
    except ImportError:
        pass
    if "Downloads" not in out:
        try:
            import win32profile
            perfil = win32profile.GetUserProfileDirectory(t) if t else os.path.expanduser("~")
        except Exception:  # noqa: BLE001
            perfil = os.path.expanduser("~")
        out["Downloads"] = os.path.join(perfil, "Downloads")
    if t:
        t.Close()
    return out


def unidades():
    if not WIN:
        return []
    import win32api
    import win32file
    return [u for u in win32api.GetLogicalDriveStrings().split("\0") if u and win32file.GetDriveType(u) in (2, 3)]


def _listar_pasta(caminho, pastas, discos):
    """Itens de uma pasta (ou as pastas de partida, com caminho vazio), como quem é atendido os veria."""
    if not caminho:
        itens = [{"nome": k, "caminho": v, "pasta": True} for k, v in pastas.items()]
        itens += [{"nome": (f"Disco {u[:2]}" if WIN else "Sistema (/)" if u == "/" else f"Disco {os.path.basename(u)}"),
                   "caminho": u, "pasta": True} for u in discos]
        return "", itens
    caminho = remoto.validar_caminho(caminho)
    itens = []
    with ComoUsuario():
        try:
            with os.scandir(caminho) as it:
                for e in it:
                    if len(itens) >= 3000:
                        break
                    try:
                        pasta = e.is_dir()
                        st = e.stat()
                        itens.append({"nome": e.name, "caminho": e.path, "pasta": pasta,
                                      "tamanho": 0 if pasta else st.st_size, "mod": int(st.st_mtime)})
                    except OSError:
                        continue
        except OSError as e:
            raise remoto.Erro(f"Não consegui abrir essa pasta: {e.strerror or e}") from None
    itens.sort(key=lambda x: (not x["pasta"], x["nome"].lower()))
    return caminho, itens


class Arquivos:
    """Transferências pelo canal "dados": arquivos nos dois sentidos e imagens da área de transferência.
    Tudo passa pela mesma conexão criptografada da sessão: sem servidor, sem porta aberta."""

    def __init__(self, agente):
        self.ag = agente
        self.recebendo = {}         # id -> estado de um arquivo chegando do técnico
        self.cancelados = set()     # ids de envios daqui que o técnico cancelou
        self.recebidos = []         # caminhos que chegaram nesta sessão (os únicos que o técnico pode "colar")
        self._prox = 1 << 30        # ids das transferências que começam deste lado
        self._fila = asyncio.Queue()
        self._tarefa = asyncio.ensure_future(self._processar())

    def chegou(self, msg):
        self._fila.put_nowait(msg)

    async def _processar(self):
        while True:
            msg = await self._fila.get()
            try:
                await self._tratar(msg)
            except Exception as e:  # noqa: BLE001
                print("remoto: transferência:", e, file=sys.stderr)

    def _mandar(self, x):
        self.ag._mandar_dados(x)

    async def _no_disco(self, f, *a):
        return await asyncio.get_running_loop().run_in_executor(self.ag._pool_arq, f, *a)

    async def _tratar(self, msg):
        if isinstance(msg, (bytes, bytearray)):
            return await self._pedaco(bytes(msg))
        if not isinstance(msg, str) or len(msg) > _MAX_MENSAGEM:
            return
        try:
            m = json.loads(msg)
        except ValueError:
            return
        if not isinstance(m, dict):
            return
        t, n = m.get("t"), m.get("id")
        if isinstance(n, bool) or not isinstance(n, int) or not 0 < n < 1 << 30:
            return
        if t == "enviar":
            await self._receber(n, m)
        elif t == "enviado":
            await self._concluir(n, m.get("sha256"))
        elif t == "cancelar":
            self.cancelados.add(n)
            r = self.recebendo.pop(n, None)
            if r:
                await self._no_disco(self._descartar, r)
        elif t == "listar":
            try:
                caminho, itens = await self._no_disco(_listar_pasta, m.get("caminho") or "", self.ag.pastas(), unidades())
                self._mandar({"t": "lista", "id": n, "caminho": caminho, "itens": itens})
            except remoto.Erro as e:
                self._mandar({"t": "lista", "id": n, "erro": str(e)})
        elif t == "baixar":
            asyncio.ensure_future(self._enviar_arquivo(n, m.get("caminho")))
        elif t == "colar_arquivos":
            lista = [c for c in m.get("caminhos") or [] if c in self.recebidos][:100]
            if lista:
                await asyncio.get_running_loop().run_in_executor(self.ag._pool, self.ag._escrever_area, "arquivos", lista)
            self._mandar({"t": "colado", "id": n, "ok": bool(lista)})

    # --- do técnico para cá
    async def _receber(self, n, m):
        area = m.get("area") == "imagem"
        tamanho = m.get("tamanho")
        limite = remoto.MAX_IMAGEM_AREA if area else remoto.MAX_ARQUIVO
        if isinstance(tamanho, bool) or not isinstance(tamanho, int) or not 0 <= tamanho <= limite or n in self.recebendo:
            return self._mandar({"t": "recebido", "id": n, "ok": False, "erro": "Arquivo grande demais ou inválido."})
        if area and not remoto.area_permite(self.ag.modo_area, "para_atendido"):
            return self._mandar({"t": "recebido", "id": n, "ok": False, "erro": "A área de transferência está desligada."})
        nome = remoto.nome_seguro(m.get("nome"))
        r = {"n": 0, "tamanho": tamanho, "sha": hashlib.sha256(), "nome": nome, "area": area, "final": None}
        if area:
            r["f"] = io.BytesIO()
        else:
            try:
                r["final"], r["f"] = await self._no_disco(self._abrir_destino, nome)
            except OSError as e:
                return self._mandar({"t": "recebido", "id": n, "ok": False,
                                     "erro": f"Não consegui gravar em Downloads: {e.strerror or e}"})
        self.recebendo[n] = r
        self._mandar({"t": "aceito", "id": n})

    def _abrir_destino(self, nome):
        with ComoUsuario():
            pasta = os.path.join(self.ag.pastas()["Downloads"], remoto.PASTA_RECEBIDOS)
            os.makedirs(pasta, exist_ok=True)
            final = remoto.caminho_livre(pasta, nome)
            return final, open(final + ".cntparte", "xb")

    @staticmethod
    def _descartar(r):
        try:
            r["f"].close()
            if r["final"]:
                with ComoUsuario():
                    os.remove(r["final"] + ".cntparte")
        except OSError:
            pass

    async def _pedaco(self, b):
        if len(b) < 4:
            return
        n = int.from_bytes(b[:4], "big")
        r = self.recebendo.get(n)
        if not r:
            return
        dados = b[4:]
        if r["n"] + len(dados) > r["tamanho"]:
            self.recebendo.pop(n, None)
            await self._no_disco(self._descartar, r)
            return self._mandar({"t": "recebido", "id": n, "ok": False, "erro": "Chegou mais do que o tamanho anunciado."})
        r["n"] += len(dados)
        r["sha"].update(dados)
        await self._no_disco(r["f"].write, dados)

    async def _concluir(self, n, sha):
        r = self.recebendo.pop(n, None)
        if not r:
            return
        if r["n"] != r["tamanho"] or not isinstance(sha, str) or sha.lower() != r["sha"].hexdigest():
            await self._no_disco(self._descartar, r)
            return self._mandar({"t": "recebido", "id": n, "ok": False,
                                 "erro": "O arquivo chegou diferente do original (SHA-256 não confere). Mande de novo."})
        if r["area"]:
            png = r["f"].getvalue()
            try:
                await asyncio.get_running_loop().run_in_executor(self.ag._pool, self.ag._escrever_area, "imagem", png)
            except Exception as e:  # noqa: BLE001
                return self._mandar({"t": "recebido", "id": n, "ok": False, "erro": f"Imagem não aceita: {e}"})
            return self._mandar({"t": "recebido", "id": n, "ok": True, "area": True})
        try:
            final = await self._no_disco(self._finalizar, r)
        except OSError as e:
            await self._no_disco(self._descartar, r)
            return self._mandar({"t": "recebido", "id": n, "ok": False, "erro": f"Não consegui gravar: {e.strerror or e}"})
        self.recebidos.append(final)
        self.ag._historico({"sentido": "recebido", "nome": os.path.basename(final), "tamanho": r["tamanho"],
                            "sha256": r["sha"].hexdigest(), "tecnico": self.ag.tecnico, "caminho": final})
        self._mandar({"t": "recebido", "id": n, "ok": True, "nome": os.path.basename(final), "caminho": final,
                      "sha256": r["sha"].hexdigest()})

    @staticmethod
    def _finalizar(r):
        r["f"].close()
        with ComoUsuario():
            final = r["final"]
            if os.path.exists(final):      # alguém criou um arquivo com o mesmo nome enquanto chegava
                final = remoto.caminho_livre(os.path.dirname(final), os.path.basename(final))
            os.rename(r["final"] + ".cntparte", final)
        return final

    # --- daqui para o técnico
    async def _mandar_bytes(self, n, ler, tamanho):
        """Manda `tamanho` bytes lidos por ler(k) em pedaços; devolve o SHA-256 ou None se cancelado/caiu."""
        sha, enviado = hashlib.sha256(), 0
        cab = n.to_bytes(4, "big")
        while enviado < tamanho:
            if n in self.cancelados or not self.ag.sessao.ativa:
                return None
            bloco = await self._no_disco(ler, 256 * 1024)
            if not bloco:
                return None             # o arquivo encolheu enquanto ia
            sha.update(bloco)
            for i in range(0, len(bloco), remoto.TAM_PEDACO):
                while self.ag._fila_dados() > _BUFFER_MAX:
                    if n in self.cancelados or not self.ag.sessao.ativa:
                        return None
                    await asyncio.sleep(0.02)
                if not self.ag._mandar_dados(cab + bloco[i:i + remoto.TAM_PEDACO]):
                    return None
            enviado += len(bloco)
        return sha.hexdigest()

    async def _enviar_arquivo(self, n, caminho):
        try:
            caminho = remoto.validar_caminho(caminho)

            def abrir():
                with ComoUsuario():
                    f = open(caminho, "rb")
                return f, os.fstat(f.fileno()).st_size

            f, tamanho = await self._no_disco(abrir)
        except remoto.Erro as e:
            return self._mandar({"t": "erro", "id": n, "msg": str(e)})
        except OSError as e:
            return self._mandar({"t": "erro", "id": n, "msg": f"Não consegui abrir: {e.strerror or e}"})
        try:
            nome = os.path.basename(caminho)
            self._mandar({"t": "inicio", "id": n, "nome": nome, "tamanho": tamanho})
            sha = await self._mandar_bytes(n, f.read, tamanho)
            if sha is None:
                return self._mandar({"t": "erro", "id": n, "msg": "Transferência interrompida."})
            self._mandar({"t": "concluido", "id": n, "sha256": sha})
            self.ag._historico({"sentido": "enviado", "nome": nome, "tamanho": tamanho, "sha256": sha,
                                "tecnico": self.ag.tecnico, "caminho": caminho})
        finally:
            await self._no_disco(f.close)

    async def imagem_da_area(self, png):
        self._prox += 1
        n = self._prox
        b = io.BytesIO(png)
        self._mandar({"t": "inicio", "id": n, "nome": "imagem.png", "tamanho": len(png), "area": "imagem"})
        sha = await self._mandar_bytes(n, b.read, len(png))
        if sha:
            self._mandar({"t": "concluido", "id": n, "sha256": sha})

    def arquivos_da_area(self, caminhos):
        itens = []
        for c in caminhos[:50]:
            try:
                with ComoUsuario():
                    st = os.stat(c)
            except OSError:
                continue
            if os.path.isfile(c):
                itens.append({"nome": os.path.basename(c), "caminho": c, "tamanho": st.st_size})
        if itens:
            self._mandar({"t": "area_arquivos", "itens": itens})


# ------------------------------------------------------------------ tela
def listar_monitores():
    """Monitores em pixels reais (o processo é "DPI aware" por monitor), o principal primeiro."""
    import ctypes
    from ctypes import wintypes

    class MI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    u = ctypes.windll.user32
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MI)]
    out = []

    def cb(h, _dc, _r, _l):
        mi = MI()
        mi.cbSize = ctypes.sizeof(mi)
        if u.GetMonitorInfoW(h, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
                        "principal": bool(mi.dwFlags & 1)})
        return True

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    u.EnumDisplayMonitors(None, None, proc(cb), 0)
    out.sort(key=lambda m: (not m["principal"], m["x"], m["y"]))
    return out[:remoto.MAX_MONITORES] or [{"x": 0, "y": 0, "w": u.GetSystemMetrics(0), "h": u.GetSystemMetrics(1),
                                           "principal": True}]


def _dpi_por_monitor():
    """Pixels reais em todos os monitores: sem isso, com escala de 125%/150% a imagem e o mouse saem deslocados."""
    if not WIN:
        return
    import ctypes
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def _foto_tudo():
    from PIL import ImageGrab
    _na_tela_ativa()
    return ImageGrab.grab(all_screens=True)


class Telas:
    """Uma foto da área de trabalho inteira serve às trilhas de todos os monitores no mesmo instante."""

    def __init__(self, monitores, foto=None):
        self.monitores = monitores
        self._foto = foto or _foto_tudo
        self._lock = threading.Lock()
        self._img, self._ts = None, 0.0

    def quadro(self, i):
        with self._lock:
            agora = time.monotonic()
            if self._img is None or agora - self._ts > 0.8 / QUADROS_S:
                self._img, self._ts = self._foto(), agora
            img = self._img
        vx, vy, _, _ = area_virtual(self.monitores)
        m = self.monitores[i]
        return img.crop((m["x"] - vx, m["y"] - vy, m["x"] - vx + m["w"], m["y"] - vy + m["h"]))


def _para_video(img):
    """Reduzida para caber em LARGURA_MAX e com lados pares (exigência do vídeo)."""
    img = img.convert("RGB")
    if img.width > LARGURA_MAX:
        img = img.resize((LARGURA_MAX, round(img.height * LARGURA_MAX / img.width)))
    w, h = img.width - img.width % 2, img.height - img.height % 2
    return img if (w, h) == img.size else img.crop((0, 0, w, h))


def _quadro_de_espera():
    from PIL import Image
    return Image.new("RGB", (320, 180), (0, 0, 0))


class Agente:
    def __init__(self, ao_estado, stun=STUN_PADRAO, capturar=None, entrada=None, agora=time.time, turn=None,
                 monitores=None, ler_area=True, area=None, pastas=None, ao_arquivo=None, ao_log=None):
        self.sessao = remoto.Sessao(agora)
        self.area = area or AreaWindows()
        self.entrada = entrada or (Entrada(area=self.area.texto) if WIN else Entrada(
            enviar=lambda ev: _linux_tela().enviar_evento(ev, self.monitores), area=self.area.texto))
        self._ao_estado = ao_estado
        self._historico = ao_arquivo or (lambda item: ao_estado({"arquivo": item}))
        self._stun = list(stun or [])
        self._turn = turn            # (urls, usuário, senha) do retransmissor de reserva
        self._log = ao_log or (lambda msg: print("remoto:", msg, file=sys.stderr))
        if monitores is None:
            try:
                monitores = listar_monitores()
            except Exception as e:  # noqa: BLE001  (Linux sem tela ou Wayland sem autorização)
                print("remoto: não consegui ler os monitores:", e, file=sys.stderr)
                monitores = [{"x": 0, "y": 0, "w": 1280, "h": 720, "principal": True}]
        self.monitores = monitores[:remoto.MAX_MONITORES]
        self.entrada.monitores = self.monitores
        self._capturar = capturar or Telas(self.monitores).quadro
        self.visiveis = set(range(len(self.monitores)))
        # área de transferência: o técnico escolhe o modo; ler a daqui depende também de quem é atendido
        self.ler_area = ler_area
        self.modo_area = "para_atendido"     # até o técnico dizer o modo, só o que ele mandar de propósito
        self._seq_proprio = None             # número da área de transferência depois da última escrita daqui (não é cópia nova)
        self.pastas = pastas or pastas_do_usuario
        self.tecnico = ""
        # uma thread para a entrada (os eventos saem na ordem em que chegaram) e outra para a tela: um clique
        # não espera a captura do quadro em andamento. Arquivos têm a sua: um disco lento não trava o mouse.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remoto")
        self._pool_tela = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remoto-tela")
        self._pool_arq = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remoto-arquivos")
        self._convite = ""
        self._provando = False
        self._fechando = False
        self.fim = asyncio.Event()
        self.pc = None
        self.canal = None
        self.dados = None
        self.arquivos = None

    # ------------------------------------------------------------ estado para o serviço
    def _avisar(self, erro="", ordem=None):
        s = self.sessao
        d = {"estado": s.estado, "motivo": s.motivo, "conferencia": s.conferencia,
             "convite": self._convite if s.estado == "convite" else "",
             "senha": remoto.formatar_senha(s.senha) if s.estado == "convite" else "", "senha_errada": s.senha_errada,
             "tecnico": self.tecnico}
        if ordem is not None:        # resposta a uma ordem do serviço: uma linha por ordem, com o número dela
            d.update(erro=erro, id=ordem)
        self._ao_estado(d)

    # ------------------------------------------------------------ conexão
    async def iniciar(self):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection
        servidores = [RTCIceServer(urls=u) for u in self._stun]
        if self._turn:
            servidores.append(RTCIceServer(urls=turn_por_tcp(self._turn[0]), username=self._turn[1],
                                           credential=self._turn[2]))
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servidores))
        self.canal = self.pc.createDataChannel("controle")
        self.canal.on("message", lambda msg: asyncio.ensure_future(self._mensagem(msg)))
        for i in range(len(self.monitores)):
            self.pc.addTrack(_trilha(self, i))

        @self.pc.on("datachannel")
        def _novo_canal(c):
            # o canal de arquivos é aberto pelo técnico, e só depois do aceite serve para alguma coisa
            if c.label == "dados" and self.dados is None:
                self.dados = c
                self.arquivos = Arquivos(self)
                c.on("message", lambda msg: self.sessao.ativa and self.arquivos.chegou(msg))

        @self.pc.on("connectionstatechange")
        async def _mudou():
            self._log(f"conexão: {self.pc.connectionState}" + (f" ({self.rota()})" if self.pc.connectionState == "connected" else ""))
            if self.pc.connectionState in ("failed", "closed"):
                await self.encerrar("A conexão caiu.")

        @self.pc.on("iceconnectionstatechange")
        def _ice():
            self._log(f"ICE: {self.pc.iceConnectionState}")

        await self.pc.setLocalDescription(await self.pc.createOffer())    # já sai com os endereços (ICE completo)
        sdp = self.pc.localDescription.sdp
        self._log(f"convite: {remoto.resumo_candidatos(sdp)}; retransmissor: "
                  + (_transporte(turn_por_tcp(self._turn[0])[0]) if self._turn else "nenhum"))
        if self._turn and "typ relay" not in sdp:
            self._log("o retransmissor não deu endereço: porta 3478 bloqueada ou credencial vencida")
        self._convite = remoto.convite(sdp)
        self._vigia = asyncio.ensure_future(self._vigiar())
        self._caminho = asyncio.ensure_future(self._manter_caminho())
        self._avisar()

    def rota(self):
        """"direta" ou "retransmitida", pelo par que o ICE escolheu ("" se ainda não há par)."""
        try:
            for t in self.pc._RTCPeerConnection__iceTransports:
                for par in t._connection._nominated.values():
                    return f"{remoto.rota(par.local_candidate.type, par.remote_candidate.type)}: " \
                           f"{par.local_candidate.type} → {par.remote_candidate.type}"
        except Exception:  # noqa: BLE001  (campos internos do aiortc; só serve para o log)
            pass
        return ""

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
                self._log(f"resposta do técnico: {remoto.resumo_candidatos(sdp)}")
            except Exception:  # noqa: BLE001  (SDP que o WebRTC não aceita)
                await self.encerrar("O código de resposta não serve para este convite. Gere outro convite.")
                raise remoto.Erro(self.sessao.motivo) from None
        elif cmd == "senha":
            self.sessao.definir_senha(c.get("senha"))
        elif cmd == "aceitar":
            self.sessao.aceitar()
            mids = [t.mid for t in self.pc.getTransceivers()]
            self._mandar({"t": "ativa", "ler_area": self.ler_area,
                          "monitores": [dict(m, mid=mids[i] if i < len(mids) else None, n=i + 1)
                                        for i, m in enumerate(self.monitores)]})
            self._area_vigia = asyncio.ensure_future(self._vigiar_area())
        elif cmd == "area_permitir":
            self.ler_area = bool(c.get("valor"))
            self._mandar({"t": "area_teto", "ler_area": self.ler_area})
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

    def _mandar_dados(self, x):
        try:
            if self.dados and self.dados.readyState == "open":
                self.dados.send(x if isinstance(x, bytes) else json.dumps(x))
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _fila_dados(self):
        try:
            return self.dados.bufferedAmount
        except Exception:  # noqa: BLE001
            return 0

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
        elif not self.sessao.ativa:
            return
        elif t in ("mouse", "tecla", "atalho") or (t == "area" and remoto.area_permite(self.modo_area, "para_atendido")):
            self._pool.submit(self._aplicar, m)
        elif t == "ver" and isinstance(m.get("m"), list):
            self.visiveis = {i for i in m["m"] if isinstance(i, int) and not isinstance(i, bool)
                             and 0 <= i < len(self.monitores)}
        elif t == "area_modo" and m.get("modo") in remoto.MODOS_AREA:
            self.modo_area = m["modo"]
        elif t == "ola":
            self.tecnico = re.sub(r"[^0-9 ]", "", str(m.get("tecnico") or ""))[:15].strip()
            self._avisar()

    def _aplicar(self, m):
        try:
            if self.sessao.ativa:       # de novo: pode ter encerrado enquanto esperava na fila
                aviso = self.entrada.aplicar(m)
                if m.get("t") == "area":
                    self._seq_proprio = self.area.seq()     # o que o técnico mandou não volta para ele como cópia nova
                if aviso:
                    self._mandar({"t": "aviso", "msg": aviso})
        except Exception as e:  # noqa: BLE001
            print("remoto: entrada recusada pelo Windows:", e, file=sys.stderr)

    def _escrever_area(self, metodo, valor):
        getattr(self.area, metodo)(valor)
        self._seq_proprio = self.area.seq()

    async def _vigiar_area(self):
        """Quem é atendido copiou algo: vai para o técnico, se o modo e o teto deixarem."""
        loop = asyncio.get_running_loop()
        try:
            ultimo = await loop.run_in_executor(self._pool, self.area.seq)
        except Exception:  # noqa: BLE001
            return
        falhas = 0
        while self.sessao.ativa:
            await asyncio.sleep(VIGIAR_AREA_S)
            try:
                seq = await loop.run_in_executor(self._pool, self.area.seq)
                if seq == ultimo:
                    continue
                if seq == self._seq_proprio or not remoto.area_permite(self.modo_area, "para_tecnico", self.ler_area):
                    ultimo = seq
                    continue
                try:
                    conteudo = await loop.run_in_executor(self._pool, self.area.ler)
                except Exception:  # noqa: BLE001
                    # quem copiou ainda está com ela aberta (Excel, Office): tenta de novo na próxima volta
                    falhas += 1
                    if falhas < LER_AREA_TENTATIVAS:
                        continue
                    ultimo, falhas = seq, 0
                    raise
                ultimo, falhas = seq, 0
                if not conteudo:
                    continue
                tipo, dado = conteudo
                if tipo == "texto" and len(dado) <= _MAX_AREA:
                    self._mandar({"t": "area", "texto": dado})
                elif tipo == "imagem" and len(dado) <= remoto.MAX_IMAGEM_AREA and self.arquivos:
                    await self.arquivos.imagem_da_area(dado)
                elif tipo == "arquivos" and self.arquivos:
                    await self._no_disco_arq(self.arquivos.arquivos_da_area, dado)
            except Exception as e:  # noqa: BLE001  (área de transferência ocupada por outro programa)
                print("remoto: área de transferência:", e, file=sys.stderr)

    async def _no_disco_arq(self, f, *a):
        return await asyncio.get_running_loop().run_in_executor(self._pool_arq, f, *a)

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
        if self.arquivos:
            for r in list(self.arquivos.recebendo.values()):     # o que chegava pela metade não fica em Downloads
                Arquivos._descartar(r)
            self.arquivos.recebendo.clear()
        await asyncio.sleep(0.2)        # dá tempo de o aviso de fim sair antes de fechar
        try:
            await self.pc.close()
        except Exception:  # noqa: BLE001
            pass
        self._pool.shutdown(wait=False)
        self._pool_tela.shutdown(wait=False)
        self._pool_arq.shutdown(wait=False)
        self._avisar()
        self.fim.set()


def _trilha(agente, indice):
    import av
    from aiortc import MediaStreamTrack

    class Tela(MediaStreamTrack):
        kind = "video"

        def __init__(self):
            super().__init__()
            self._ini = None
            self._prox = 0.0
            self._espera = _quadro_de_espera()

        async def recv(self):
            # antes do aceite (e depois do fim) só sai um quadro preto, uma vez por segundo; o monitor que o
            # técnico não está vendo também anda a 1 quadro por segundo
            ativa = agente.sessao.ativa
            agora = time.time()
            if self._ini is None:
                self._ini = self._prox = agora
            else:
                rapido = ativa and indice in agente.visiveis
                # ritmo pelo relógio: o tempo da captura conta dentro do intervalo, sem somar atraso
                self._prox = max(self._prox + 1 / (QUADROS_S if rapido else QUADROS_ESCONDIDO), agora)
                await asyncio.sleep(self._prox - agora)
                ativa = agente.sessao.ativa
            img = self._espera
            if ativa:
                try:
                    img = await asyncio.get_running_loop().run_in_executor(
                        agente._pool_tela, lambda: _para_video(agente._capturar(indice)))
                except Exception:  # noqa: BLE001  (tela indisponível por um instante, fila fechada)
                    img = self._espera
                if not agente.sessao.ativa:
                    img = self._espera
            quadro = av.VideoFrame.from_image(img)
            quadro.pts = int((time.time() - self._ini) * 90000)
            quadro.time_base = fractions.Fraction(1, 90000)
            return quadro

    return Tela()


def _taxas():
    from aiortc.codecs import h264, vpx
    for mod, (minimo, inicial, maximo) in ((vpx, TAXAS["vpx"]), (h264, TAXAS["h264"])):
        mod.MIN_BITRATE, mod.DEFAULT_BITRATE, mod.MAX_BITRATE = minimo, inicial, maximo


def _transporte(url):
    return "tcp" if "transport=tcp" in url.lower() else "udp"


def turn_por_tcp(urls):
    """O aiortc só usa o primeiro endereço TURN da lista. Pelo TCP o retransmissor funciona também onde o UDP
    é bloqueado (4G de algumas operadoras, redes de empresa); onde o UDP passa, os caminhos diretos e o
    retransmissor do técnico continuam valendo, então o TCP só carrega a sessão quando não há outro jeito."""
    return sorted(urls, key=lambda u: _transporte(u) != "tcp")


async def testar(stun, turn, prazo=10):
    """Este PC alcança o STUN (UDP) e o retransmissor por UDP e por TCP? Cada um pede um endereço ao servidor
    como numa conexão de verdade. Devolve {stun, turn_udp, turn_tcp} (None: não havia o que testar)."""
    from aioice import Connection
    from aiortc.rtcicetransport import parse_stun_turn_uri

    async def tipo_obtido(tipo, **k):
        c = Connection(ice_controlling=True, **k)
        try:
            await asyncio.wait_for(c.gather_candidates(), prazo)
            return any(x.type == tipo for x in c.local_candidates)
        except Exception:  # noqa: BLE001  (porta bloqueada, nome que não resolve, credencial recusada)
            return False
        finally:
            await c.close()

    def servidor(u):
        p = parse_stun_turn_uri(u)
        return p["host"], p["port"]

    provas = {"stun": None, "turn_udp": None, "turn_tcp": None}
    if stun:
        provas["stun"] = tipo_obtido("srflx", stun_server=servidor(stun[0]))
    for url in (turn[0] if turn else []):
        chave = "turn_" + _transporte(url)
        if provas.get(chave) is None:
            provas[chave] = tipo_obtido("relay", turn_server=servidor(url), turn_username=turn[1],
                                        turn_password=turn[2], turn_transport=_transporte(url))
    feitas = {k: v for k, v in provas.items() if v is not None}
    return {**provas, **dict(zip(feitas, await asyncio.gather(*feitas.values())))}


async def _principal(stun, turn=None, ler_area=True):
    _taxas()
    loop = asyncio.get_running_loop()

    def estado(d):
        sys.stdout.write(json.dumps(d) + "\n")
        sys.stdout.flush()

    agente = Agente(estado, stun, turn=turn, ler_area=ler_area, ao_log=lambda msg: estado({"log": msg}))

    def ler():
        for linha in sys.stdin:
            try:
                c = json.loads(linha)
            except ValueError:
                continue
            asyncio.run_coroutine_threadsafe(agente.comando(c), loop)
        asyncio.run_coroutine_threadsafe(agente.encerrar("O CNT Monitor foi fechado."), loop)

    await agente.iniciar()
    threading.Thread(target=ler, daemon=True).start()
    await agente.fim.wait()
    if not WIN:
        _linux_tela().fechar()          # Wayland: fecha a sessão do portal (o aviso de compartilhamento some)


def _linux_tela():
    from monitorpc.linux import tela
    return tela


def _sas_linux():
    """Linux não tem a tela segura do Ctrl+Alt+Del: manda as três teclas (o ambiente decide: no GNOME abre o
    "Desligar/Sair", no KDE e no Mint, o menu de sessão)."""
    t = _linux_tela()
    for est in (0, T_SOBE):
        for scan, ext in ((0x1D, 0), (0x38, 0), (0x53, T_ESTENDIDA)) if est == 0 else ((0x53, T_ESTENDIDA), (0x38, 0), (0x1D, 0)):
            t.enviar_evento(("tecla", scan, T_SCAN | ext | est), [])
    return ""


if not WIN:     # Linux: X11 (mss + XTest) ou Wayland (portal + PipeWire); área de transferência por xclip/wl-clipboard
    from monitorpc.linux.tela import listar_monitores, Telas, pastas_do_usuario, unidades, bloquear as _bloquear  # noqa: E402,F811
    from monitorpc.linux.area import AreaLinux as AreaWindows  # noqa: E402,F811
    _ctrl_alt_del = _sas_linux


def main():
    _dpi_por_monitor()      # antes de medir os monitores ou capturar a tela
    stun = STUN_PADRAO
    if "--sem-stun" in sys.argv:
        stun = ()
    elif "--stun" in sys.argv:
        stun = tuple(u for u in sys.argv[sys.argv.index("--stun") + 1].split(",") if u.startswith(("stun:", "stuns:")))
    turn = None
    if "--turn" in sys.argv and "--sem-stun" not in sys.argv:
        try:
            a = sys.argv
            urls = [u for u in a[a.index("--turn") + 1].split(",") if u.startswith("turn:")]
            turn = (urls, a[a.index("--turn-usuario") + 1], a[a.index("--turn-senha") + 1]) if urls else None
        except (ValueError, IndexError):
            turn = None
    if "--testar" in sys.argv:
        print(json.dumps(asyncio.run(testar(stun, turn))), flush=True)
        return
    asyncio.run(_principal(stun, turn, ler_area="--sem-ler-area" not in sys.argv))


if __name__ == "__main__":
    main()
