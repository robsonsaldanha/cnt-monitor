"""Aviso próprio do Monitor (no lugar da notificação do Windows): janelinha no canto da tela, com miniatura,
botões e uma ação ao clicar. Some sozinho; com o mouse em cima, espera. Vários avisos ficam empilhados.

Cada aviso roda numa thread com o próprio Tk (como a seleção e a moldura da gravação): PhotoImage sempre com
master=root e coleta de lixo na mesma thread ao fechar (senão a Tcl derruba o processo).
"""
import threading

FUNDO, BORDA, TXT, TXT2, DESTAQUE, BOTAO = "#171c22", "#2f3946", "#e6ebf0", "#9aa6b2", "#4ea1ff", "#232b35"
LARGURA, PASSO, MAX_PILHA = 380, 12, 6


def posicao(indice, area, canto, w, h, alturas_antes=0, margem=14):
    """Canto da tela -> (x, y) do aviso. `alturas_antes` = soma das alturas (com espaço) dos avisos abaixo dele."""
    l, t, r, b = area
    x = l + margem if canto.endswith("esq") else r - w - margem
    if canto.startswith("sup"):
        y = t + margem + alturas_antes
    else:
        y = b - h - margem - alturas_antes
    return int(x), int(y)


def area_trabalho():
    """Área útil do monitor principal (sem a barra de tarefas)."""
    try:
        import ctypes
        from ctypes import wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)   # SPI_GETWORKAREA
        if r.right > r.left:
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, 1920, 1040


def som():
    try:
        import winsound
        winsound.MessageBeep(0x40)   # MB_ICONASTERISK
    except Exception:
        pass


class Avisos:
    def __init__(self, cfg, reserva=None):
        """cfg() -> configuração; reserva(msg, titulo) = notificação do Windows, usada se o aviso próprio falhar."""
        self.cfg = cfg
        self.reserva = reserva
        self._lock = threading.Lock()
        self._pilha = {}   # índice -> altura

    def mostrar(self, titulo, msg, img=None, ao_clicar=None, botoes=(), segundos=None):
        """botoes = [(rótulo, função)]. As funções rodam numa thread à parte e o aviso fecha."""
        c = self.cfg() or {}
        if c.get("captura_som"):
            som()
        threading.Thread(target=self._rodar, args=(str(titulo)[:80], str(msg)[:400], img, ao_clicar, list(botoes),
                                                   segundos or int(c.get("captura_aviso_segundos") or 6),
                                                   c.get("captura_aviso_canto") or "inf_dir"), daemon=True).start()

    def _rodar(self, titulo, msg, img, ao_clicar, botoes, segundos, canto):
        with self._lock:
            i = next((n for n in range(MAX_PILHA) if n not in self._pilha), MAX_PILHA - 1)
            self._pilha[i] = 0
        try:
            self._janela(i, titulo, msg, img, ao_clicar, botoes, segundos, canto)
        except Exception as e:  # noqa: BLE001
            print("aviso:", e)
            if self.reserva:
                self.reserva(msg, titulo)
        finally:
            with self._lock:
                self._pilha.pop(i, None)
            from .captura import coletar_tk
            coletar_tk()

    def _abaixo(self, i):
        with self._lock:
            return sum(h + PASSO for n, h in self._pilha.items() if n < i)

    def _janela(self, i, titulo, msg, img, ao_clicar, botoes, segundos, canto):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BORDA)
        caixa = tk.Frame(root, bg=FUNDO)
        caixa.pack(padx=1, pady=1, fill="both", expand=True)
        tk.Frame(caixa, bg=DESTAQUE, width=4).pack(side="left", fill="y")
        corpo = tk.Frame(caixa, bg=FUNDO, padx=12, pady=10)
        corpo.pack(side="left", fill="both", expand=True)
        clicaveis = [caixa, corpo]
        foto = None
        if img is not None:
            from PIL import ImageTk
            m = img.convert("RGB")
            m.thumbnail((112, 84))
            foto = ImageTk.PhotoImage(m, master=root)
            lb = tk.Label(corpo, image=foto, bg=FUNDO, bd=0, cursor="hand2" if ao_clicar else "")
            lb.pack(side="left", anchor="n", padx=(0, 12))
            clicaveis.append(lb)
        texto = tk.Frame(corpo, bg=FUNDO)
        texto.pack(side="left", fill="both", expand=True)
        cab = tk.Frame(texto, bg=FUNDO)
        cab.pack(fill="x")
        t = tk.Label(cab, text=titulo, bg=FUNDO, fg=TXT, font=("Segoe UI", 10, "bold"), anchor="w")
        t.pack(side="left", fill="x", expand=True)
        fechar = tk.Label(cab, text="✕", bg=FUNDO, fg=TXT2, font=("Segoe UI", 10), cursor="hand2", padx=4)
        fechar.pack(side="right")
        larg_txt = LARGURA - (150 if foto else 40)
        corpo_msg = tk.Label(texto, text=msg, bg=FUNDO, fg=TXT2, font=("Segoe UI", 9), justify="left", anchor="w",
                             wraplength=larg_txt)
        corpo_msg.pack(fill="x", pady=(2, 0))
        clicaveis += [texto, cab, t, corpo_msg]
        estado = {"resta": segundos * 1000, "dentro": False, "fim": False}

        def sair():
            if not estado["fim"]:
                estado["fim"] = True
                root.destroy()

        def rodar(fn):
            sair()
            threading.Thread(target=fn, daemon=True).start()

        if botoes:
            linha = tk.Frame(texto, bg=FUNDO)
            linha.pack(fill="x", pady=(8, 0))
            for rot, fn in botoes[:4]:
                b = tk.Label(linha, text=rot, bg=BOTAO, fg=TXT, font=("Segoe UI", 9), padx=9, pady=3, cursor="hand2")
                b.pack(side="left", padx=(0, 6))
                b.bind("<Button-1>", lambda _e, fn=fn: rodar(fn))
                b.bind("<Enter>", lambda _e, b=b: b.configure(bg=BORDA))
                b.bind("<Leave>", lambda _e, b=b: b.configure(bg=BOTAO))
        fechar.bind("<Button-1>", lambda _e: sair())
        for w in clicaveis:
            w.bind("<Button-1>", lambda _e: rodar(ao_clicar) if ao_clicar else sair())
            if ao_clicar:
                w.configure(cursor="hand2")
        root.bind("<Button-3>", lambda _e: sair())

        def entrar(_e):
            estado["dentro"] = True

        def sair_mouse(_e):
            x, y = root.winfo_pointerxy()
            dentro = root.winfo_rootx() <= x < root.winfo_rootx() + root.winfo_width() and \
                root.winfo_rooty() <= y < root.winfo_rooty() + root.winfo_height()
            estado["dentro"] = dentro
        root.bind("<Enter>", entrar)
        root.bind("<Leave>", sair_mouse)

        root.update_idletasks()
        w, h = LARGURA, max(64, root.winfo_reqheight())
        with self._lock:
            self._pilha[i] = h
        x, y = posicao(i, area_trabalho(), canto, w, h, self._abaixo(i))
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.attributes("-alpha", 0.0)
        root.deiconify()
        root.update_idletasks()
        try:   # não rouba o foco de quem está digitando e não aparece na barra de tarefas
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
            u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)
        except Exception:
            pass

        def tick(alfa=0.0):
            if estado["fim"]:
                return
            if alfa < 0.97:
                alfa = min(0.97, alfa + 0.16)
                root.attributes("-alpha", alfa)
            if not estado["dentro"]:
                estado["resta"] -= 50
            if estado["resta"] <= 0:
                return sair()
            root.after(50, tick, alfa)
        root.after(10, tick)
        root.mainloop()
        del foto
