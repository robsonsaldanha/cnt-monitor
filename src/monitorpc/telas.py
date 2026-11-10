"""Janelas pequenas da captura (Tk, tema escuro do Monitor): fixar na tela, menu de tarefas rápidas,
"Pós-captura", "Pré-envio", "Pós-envio" (link + QR), texto (OCR/QR/IA), hash e a barra de ferramentas de ações.

Todas bloqueiam até fechar: chame de uma thread própria. Cada uma cria o seu Tk e coleta o lixo ao sair
(regra do projeto: vários Tk em threads diferentes).
"""
import threading

from . import aviso
from .captura import coletar_tk

FUNDO, BORDA, TXT, TXT2, DESTAQUE, BOTAO, PERIGO = (aviso.FUNDO, aviso.BORDA, aviso.TXT, aviso.TXT2, aviso.DESTAQUE,
                                                    aviso.BOTAO, "#c62828")


def _em_thread_tk(fn):
    """Decorador: roda a janela e coleta o lixo do Tk na mesma thread."""
    def f(*a, **kw):
        try:
            return fn(*a, **kw)
        finally:
            coletar_tk()
    f.__name__, f.__doc__ = fn.__name__, fn.__doc__
    return f


def _raiz(titulo, topo=True):
    import tkinter as tk
    root = tk.Tk()
    root.title(titulo)
    root.configure(bg=FUNDO)
    if topo:
        root.attributes("-topmost", True)
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    return root


def _botao(pai, texto, cmd, pri=False, perigo=False):
    import tkinter as tk
    bg = DESTAQUE if pri else PERIGO if perigo else BOTAO
    fg = "#081018" if pri else TXT
    b = tk.Label(pai, text=texto, bg=bg, fg=fg, font=("Segoe UI", 9, "bold" if pri else "normal"), padx=11, pady=5,
                 cursor="hand2")
    b.bind("<Button-1>", lambda _e: cmd())
    b.bind("<Enter>", lambda _e: b.configure(bg=BORDA if not pri else "#7cbcf0"))
    b.bind("<Leave>", lambda _e: b.configure(bg=bg))
    return b


def _centro_no_mouse(root, w, h):
    x, y = root.winfo_pointerxy()
    l, t, r, b = aviso.area_trabalho()
    x = max(l, min(x - w // 2, r - w))
    y = max(t, min(y - h // 2, b - h))
    root.geometry(f"{w}x{h}+{x}+{y}")


def _frente(root):
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.after(30, root.focus_force)


def _previa(root, pai, img, maximo=(520, 320)):
    import tkinter as tk
    from PIL import ImageTk
    m = img.convert("RGBA")
    m.thumbnail(maximo)
    foto = ImageTk.PhotoImage(m, master=root)
    tk.Label(pai, image=foto, bg="#0c0f13", bd=0).pack(pady=(0, 10))
    return foto


def _copiar_texto(txt):
    from .captura import copiar_texto
    copiar_texto(txt)


# ---------------------------------------------------------------- fixar na tela
@_em_thread_tk
def fixar(img, pos=None):
    """Imagem sempre por cima. Arrastar move; roda muda o tamanho; Ctrl+roda, a opacidade; clique do meio volta
    ao normal; clique duplo minimiza; Ctrl+C copia; setas movem 1 px (Shift: 10); Esc ou botão direito fecha."""
    import tkinter as tk
    from PIL import Image, ImageTk
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=DESTAQUE)
    l, t, r, b = aviso.area_trabalho()
    caber = min(1.0, (r - l) * 0.8 / img.width, (b - t) * 0.8 / img.height)
    st = {"esc": caber, "alfa": 1.0, "mini": False, "dx": 0, "dy": 0, "foto": None}
    rot = tk.Label(root, bd=0, bg=FUNDO, cursor="fleur")
    rot.pack(padx=1, pady=1)

    def desenhar():
        esc = 120 / img.width if st["mini"] else st["esc"]
        w, h = max(8, round(img.width * esc)), max(8, round(img.height * esc))
        im = img if (w, h) == img.size else img.resize((w, h), Image.LANCZOS if esc < 1 else Image.NEAREST)
        st["foto"] = ImageTk.PhotoImage(im, master=root)
        rot.configure(image=st["foto"])
        root.geometry(f"{w + 2}x{h + 2}")

    def apertar(e):
        st["dx"], st["dy"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def arrastar(e):
        root.geometry(f"+{e.x_root - st['dx']}+{e.y_root - st['dy']}")

    def roda(e):
        sobe = e.delta > 0
        if e.state & 0x4:
            st["alfa"] = max(0.2, min(1.0, st["alfa"] + (0.1 if sobe else -0.1)))
            root.attributes("-alpha", st["alfa"])
            return
        st["mini"] = False
        st["esc"] = max(0.1, min(5.0, st["esc"] * (1.1 if sobe else 1 / 1.1)))
        desenhar()

    def normal(_e=None):
        st.update(esc=1.0, alfa=1.0, mini=False)
        root.attributes("-alpha", 1.0)
        desenhar()

    def minimizar(_e=None):
        st["mini"] = not st["mini"]
        desenhar()

    def mover(dx, dy, e):
        n = 10 if e.state & 0x1 else 1
        root.geometry(f"+{root.winfo_x() + dx * n}+{root.winfo_y() + dy * n}")

    def copiar(_e=None):
        from .captura import copiar_imagem
        copiar_imagem(img)

    rot.bind("<ButtonPress-1>", apertar)
    rot.bind("<B1-Motion>", arrastar)
    rot.bind("<Double-Button-1>", minimizar)
    rot.bind("<Button-2>", normal)
    for k in ("<Button-3>", "<Escape>"):
        root.bind(k, lambda _e: root.destroy())
    root.bind("<MouseWheel>", roda)
    root.bind("<Control-c>", copiar)
    root.bind("<Control-C>", copiar)
    for k, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
        root.bind(f"<{k}>", lambda e, dx=dx, dy=dy: mover(dx, dy, e))
    root.bind("<Enter>", lambda _e: root.focus_force())
    desenhar()
    if pos:
        root.geometry(f"+{int(pos[0]) - 1}+{int(pos[1]) - 1}")
    else:
        root.update_idletasks()
        _centro_no_mouse(root, root.winfo_reqwidth(), root.winfo_reqheight())
    root.mainloop()


# ---------------------------------------------------------------- menu de tarefas rápidas
@_em_thread_tk
def menu_rapido(img, opcoes):
    """Lista perto do mouse; devolve a chave escolhida ou None (Esc / fechar)."""
    import tkinter as tk
    root = _raiz("O que fazer com a captura?")
    root.overrideredirect(True)
    root.configure(bg=BORDA)
    caixa = tk.Frame(root, bg=FUNDO, padx=8, pady=8)
    caixa.pack(padx=1, pady=1)
    res = {"v": None}
    foto = _previa(root, caixa, img, (260, 140)) if img is not None else None

    def escolher(k):
        res["v"] = k
        root.destroy()

    for n, (k, rot) in enumerate(opcoes):
        item = tk.Label(caixa, text=(f"{n + 1}  " if n < 9 else "    ") + rot, bg=FUNDO, fg=TXT, anchor="w",
                        font=("Segoe UI", 10), padx=10, pady=4, cursor="hand2", width=30)
        item.pack(fill="x")
        item.bind("<Button-1>", lambda _e, k=k: escolher(k))
        item.bind("<Enter>", lambda _e, i=item: i.configure(bg=BOTAO))
        item.bind("<Leave>", lambda _e, i=item: i.configure(bg=FUNDO))
        if n < 9:
            root.bind(str(n + 1), lambda _e, k=k: escolher(k))
    tk.Label(caixa, text="Esc cancela", bg=FUNDO, fg=TXT2, font=("Segoe UI", 8)).pack(anchor="e", pady=(6, 0))
    root.bind("<Escape>", lambda _e: root.destroy())
    root.bind("<FocusOut>", lambda _e: root.after(200, lambda: root.focus_get() is None and root.destroy()))
    root.update_idletasks()
    _centro_no_mouse(root, root.winfo_reqwidth(), root.winfo_reqheight())
    _frente(root)
    root.mainloop()
    del foto
    return res["v"]


# ---------------------------------------------------------------- janela "Pós-captura"
@_em_thread_tk
def pos_captura(img, info, opcoes, executar):
    """Prévia + botões; cada botão roda executar(chave) numa thread e a janela continua aberta até "Fechar"."""
    import tkinter as tk
    root = _raiz("Captura de tela — CNT Monitor")
    caixa = tk.Frame(root, bg=FUNDO, padx=16, pady=14)
    caixa.pack(fill="both", expand=True)
    foto = _previa(root, caixa, img)
    tk.Label(caixa, text=info, bg=FUNDO, fg=TXT2, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))
    grade = tk.Frame(caixa, bg=FUNDO)
    grade.pack(fill="x")
    msg = tk.Label(caixa, text="", bg=FUNDO, fg=DESTAQUE, font=("Segoe UI", 9))
    msg.pack(anchor="w", pady=(8, 0))

    def rodar(k, rot):
        msg.configure(text=f"{rot}…")

        def tarefa():
            try:
                r = executar(k)
            except Exception as e:  # noqa: BLE001
                r = f"Não consegui: {e}"
            try:
                root.after(0, lambda: msg.configure(text=r or f"{rot}: feito."))
            except RuntimeError:
                pass
        threading.Thread(target=tarefa, daemon=True).start()

    for n, (k, rot) in enumerate(opcoes):
        _botao(grade, rot, lambda k=k, rot=rot: rodar(k, rot)).grid(row=n // 4, column=n % 4, padx=3, pady=3, sticky="we")
    _botao(caixa, "Fechar", root.destroy, pri=True).pack(anchor="e", pady=(10, 0))
    root.bind("<Escape>", lambda _e: root.destroy())
    _frente(root)
    root.mainloop()
    del foto


# ---------------------------------------------------------------- "Pré-envio"
@_em_thread_tk
def confirmar_envio(img, nome, destino):
    """Pergunta antes de enviar. True = enviar."""
    import tkinter as tk
    root = _raiz("Enviar captura?")
    caixa = tk.Frame(root, bg=FUNDO, padx=16, pady=14)
    caixa.pack(fill="both", expand=True)
    foto = _previa(root, caixa, img, (420, 260)) if img is not None else None
    tk.Label(caixa, text=f"Enviar {nome} para {destino}?", bg=FUNDO, fg=TXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
    tk.Label(caixa, text="Quem tiver o link consegue abrir o arquivo.", bg=FUNDO, fg=TXT2,
             font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 10))
    res = {"v": False}

    def sim():
        res["v"] = True
        root.destroy()
    linha = tk.Frame(caixa, bg=FUNDO)
    linha.pack(anchor="e")
    _botao(linha, "Cancelar", root.destroy).pack(side="left", padx=4)
    _botao(linha, "Enviar", sim, pri=True).pack(side="left")
    root.bind("<Return>", lambda _e: sim())
    root.bind("<Escape>", lambda _e: root.destroy())
    _frente(root)
    root.mainloop()
    del foto
    return res["v"]


# ---------------------------------------------------------------- texto (OCR, QR lido, IA, link)
@_em_thread_tk
def texto(titulo, conteudo, img=None, botoes=()):
    """Texto selecionável com "Copiar" (e botões extras [(rótulo, função)]). img = QR ou prévia, opcional."""
    import tkinter as tk
    root = _raiz(titulo)
    caixa = tk.Frame(root, bg=FUNDO, padx=16, pady=14)
    caixa.pack(fill="both", expand=True)
    foto = _previa(root, caixa, img, (300, 300)) if img is not None else None
    linhas = min(18, max(2, conteudo.count("\n") + 1 + len(conteudo) // 70))
    t = tk.Text(caixa, width=70, height=linhas, bg="#0f1318", fg=TXT, insertbackground=TXT, relief="flat",
                font=("Segoe UI", 10), wrap="word", padx=8, pady=6)
    t.insert("1.0", conteudo)
    t.pack(fill="both", expand=True)
    linha = tk.Frame(caixa, bg=FUNDO)
    linha.pack(anchor="e", pady=(10, 0))
    for rot, fn in botoes:
        _botao(linha, rot, lambda fn=fn: threading.Thread(target=fn, daemon=True).start()).pack(side="left", padx=4)
    _botao(linha, "Copiar", lambda: _copiar_texto(t.get("1.0", "end-1c"))).pack(side="left", padx=4)
    _botao(linha, "Fechar", root.destroy, pri=True).pack(side="left")
    root.bind("<Escape>", lambda _e: root.destroy())
    _frente(root)
    root.mainloop()
    del foto


# ---------------------------------------------------------------- pedir um texto (chave da IA, texto para enviar)
@_em_thread_tk
def pedir_texto(titulo, rotulo, senha=False, multilinha=False):
    import tkinter as tk
    root = _raiz(titulo)
    caixa = tk.Frame(root, bg=FUNDO, padx=16, pady=14)
    caixa.pack(fill="both", expand=True)
    tk.Label(caixa, text=rotulo, bg=FUNDO, fg=TXT, font=("Segoe UI", 10), justify="left", wraplength=440).pack(anchor="w",
                                                                                                          pady=(0, 8))
    if multilinha:
        e = tk.Text(caixa, width=60, height=10, bg="#0f1318", fg=TXT, insertbackground=TXT, relief="flat",
                    font=("Segoe UI", 10))
        ler = lambda: e.get("1.0", "end-1c")   # noqa: E731
    else:
        e = tk.Entry(caixa, width=52, bg="#0f1318", fg=TXT, insertbackground=TXT, relief="flat", font=("Segoe UI", 10),
                     show="•" if senha else "")
        ler = e.get
    e.pack(fill="x", ipady=4)
    res = {"v": None}

    def ok():
        res["v"] = ler()
        root.destroy()
    linha = tk.Frame(caixa, bg=FUNDO)
    linha.pack(anchor="e", pady=(10, 0))
    _botao(linha, "Cancelar", root.destroy).pack(side="left", padx=4)
    _botao(linha, "OK", ok, pri=True).pack(side="left")
    if not multilinha:
        root.bind("<Return>", lambda _e: ok())
    root.bind("<Escape>", lambda _e: root.destroy())
    _frente(root)
    root.after(60, e.focus_force)
    root.mainloop()
    return res["v"]


# ---------------------------------------------------------------- verificador de hash
@_em_thread_tk
def hash_janela(nome, somas):
    """Mostra MD5, SHA-1 e SHA-256 e confere com um valor colado."""
    import tkinter as tk
    from .efeitos import confere_hash
    root = _raiz(f"Hash — {nome}")
    caixa = tk.Frame(root, bg=FUNDO, padx=16, pady=14)
    caixa.pack(fill="both", expand=True)
    tk.Label(caixa, text=nome, bg=FUNDO, fg=TXT, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
    for alg, v in somas.items():
        ln = tk.Frame(caixa, bg=FUNDO)
        ln.pack(fill="x", pady=2)
        tk.Label(ln, text=alg, bg=FUNDO, fg=TXT2, width=8, anchor="w", font=("Segoe UI", 9)).pack(side="left")
        en = tk.Entry(ln, width=68, bg="#0f1318", fg=TXT, relief="flat", font=("Consolas", 9))
        en.insert(0, v)
        en.configure(state="readonly", readonlybackground="#0f1318")
        en.pack(side="left", padx=6, ipady=3)
        _botao(ln, "Copiar", lambda v=v: _copiar_texto(v)).pack(side="left")
    tk.Label(caixa, text="Conferir com (cole o valor esperado):", bg=FUNDO, fg=TXT2,
             font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 2))
    alvo = tk.Entry(caixa, width=80, bg="#0f1318", fg=TXT, insertbackground=TXT, relief="flat", font=("Consolas", 9))
    alvo.pack(fill="x", ipady=3)
    res = tk.Label(caixa, text="", bg=FUNDO, fg=TXT2, font=("Segoe UI", 10, "bold"))
    res.pack(anchor="w", pady=(6, 0))

    def conferir(*_):
        v = alvo.get().strip()
        if not v:
            res.configure(text="", fg=TXT2)
            return
        k = confere_hash(somas, v)
        res.configure(text=f"✓ Confere ({k})" if k else "✗ Não confere", fg="#43a047" if k else "#ef5350")
    alvo.bind("<KeyRelease>", conferir)
    _botao(caixa, "Fechar", root.destroy, pri=True).pack(anchor="e", pady=(10, 0))
    root.bind("<Escape>", lambda _e: root.destroy())
    _frente(root)
    root.mainloop()


# ---------------------------------------------------------------- barra de ferramentas de ações
@_em_thread_tk
def barra_acoes(itens, executar, fechar_evento=None):
    """Barra flutuante sempre por cima com os botões de captura. Arraste pela alça ⠿; ✕ fecha.
    itens = [(chave, rótulo)]; executar(chave) roda numa thread."""
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=BORDA)
    caixa = tk.Frame(root, bg=FUNDO, padx=4, pady=4)
    caixa.pack(padx=1, pady=1)
    alca = tk.Label(caixa, text="⠿", bg=FUNDO, fg=TXT2, font=("Segoe UI Symbol", 12), cursor="fleur", padx=4)
    alca.pack(side="left")
    st = {"dx": 0, "dy": 0}
    alca.bind("<ButtonPress-1>", lambda e: st.update(dx=e.x_root - root.winfo_x(), dy=e.y_root - root.winfo_y()))
    alca.bind("<B1-Motion>", lambda e: root.geometry(f"+{e.x_root - st['dx']}+{e.y_root - st['dy']}"))
    for k, rot in itens:
        b = tk.Label(caixa, text=rot, bg=FUNDO, fg=TXT, font=("Segoe UI", 9), padx=8, pady=4, cursor="hand2")
        b.pack(side="left", padx=1)
        b.bind("<Button-1>", lambda _e, k=k: threading.Thread(target=executar, args=(k,), daemon=True).start())
        b.bind("<Enter>", lambda _e, b=b: b.configure(bg=BOTAO))
        b.bind("<Leave>", lambda _e, b=b: b.configure(bg=FUNDO))
    x = tk.Label(caixa, text="✕", bg=FUNDO, fg=TXT2, font=("Segoe UI", 10), padx=6, cursor="hand2")
    x.pack(side="left")
    x.bind("<Button-1>", lambda _e: root.destroy())
    root.update_idletasks()
    l, t, r, _b = aviso.area_trabalho()
    root.geometry(f"+{(l + r - root.winfo_reqwidth()) // 2}+{t + 12}")

    def vigiar():
        if fechar_evento is not None and fechar_evento.is_set():
            root.destroy()
            return
        root.after(300, vigiar)
    root.after(300, vigiar)
    root.mainloop()
