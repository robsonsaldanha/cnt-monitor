"""Fluxo da captura no ícone da bandeja: capturar -> tarefas pós-captura (salvar, copiar, efeitos, aviso, fixar,
enviar…) -> tarefas pós-envio (copiar link, encurtar, compartilhar, QR…). Também liga os atalhos globais
(inclusive os dos fluxos de trabalho), controla a gravação de vídeo/GIF e as ferramentas (QR, hash, IA, envio de
arquivos, área de transferência e texto)."""
import io
import os
import json
import time
import base64
import threading
import subprocess
import webbrowser

from . import captura, efeitos, envio, gravacao, ia, telas

EXT_VIDEO = (".mp4", ".mkv", ".webm", ".mov", ".avi")

# menu de tarefas rápidas: (chave, rótulo, tarefas ligadas — as outras ficam desligadas)
RAPIDAS = [
    ("salvar_copiar", "Salvar e copiar", {"captura_salvar": True, "captura_copiar": True}),
    ("copiar", "Só copiar a imagem", {"captura_copiar": True}),
    ("salvar", "Só salvar", {"captura_salvar": True}),
    ("enviar", "Salvar e enviar (link copiado)", {"captura_salvar": True, "captura_enviar": True}),
    ("salvar_como", "Salvar como…", {"captura_perguntar": True}),
    ("editor", "Abrir no editor", {"captura_salvar": True, "captura_editor": True}),
    ("fixar", "Fixar na tela", {"captura_fixar": True, "captura_copiar": True}),
    ("ocr", "Copiar o texto (OCR)", {"captura_ocr_auto": True}),
    ("qr", "Ler código QR", {"captura_qr": True}),
    ("ia", "Analisar com IA", {"captura_ia": True}),
    ("imprimir", "Imprimir", {"captura_imprimir": True}),
]
# o que a janela "Pós-captura" oferece
POS = [("copiar", "Copiar imagem"), ("copiar_arquivo", "Copiar arquivo"), ("caminho", "Copiar caminho"),
       ("pasta", "Mostrar na pasta"), ("editor", "Abrir no editor"), ("editor_fotos", "Editor do Windows"),
       ("fixar", "Fixar na tela"), ("enviar", "Enviar"), ("ocr", "Copiar texto (OCR)"), ("qr", "Ler QR"),
       ("ia", "Analisar com IA"), ("imprimir", "Imprimir")]
# barra de ferramentas de ações
BARRA = [("regiao", "Região"), ("janela", "Janela"), ("tela", "Tela"), ("rolagem", "Rolagem"), ("video", "Vídeo"),
         ("gif", "GIF"), ("ocr", "OCR"), ("qr", "QR"), ("cor", "Cor"), ("regua", "Régua"), ("fixar", "Fixar"),
         ("enviar_arquivo", "Enviar arquivo")]


def tarefas_rapidas(c, chave):
    """Configuração para a opção do menu de tarefas rápidas: só as tarefas dela (aviso e pós-envio continuam)."""
    liga = next(t for k, _r, t in RAPIDAS if k == chave)
    pos_captura = [k for k in captura.TAREFAS if not k.startswith("captura_envio_")
                   and k not in ("captura_notificar", "captura_anotar", "captura_tarefas_rapidas")]
    return {**c, **{k: False for k in pos_captura}, **liga}


def tipo_arquivo(caminho):
    ext = os.path.splitext(caminho)[1].lower()
    if ext == ".gif":
        return "gif"
    if ext in EXT_VIDEO:
        return "video"
    if ext in efeitos.EXT_IMAGEM:
        return "imagem"
    return "arquivo"


class Capturador:
    def __init__(self, api_post, cfg, notificar, abrir_janela, esconder_janela, pasta_usuario, avisos=None):
        self.api_post = api_post
        self.cfg = cfg
        self.notificar = notificar               # (msg, título): aviso simples
        self.avisos = avisos                     # aviso.Avisos: aviso com miniatura, botões e clique
        self.abrir_janela = abrir_janela
        self.esconder_janela = esconder_janela   # () -> função que mostra de volta (ou None)
        self.pasta = pasta_usuario
        self.lock = threading.Lock()             # uma captura por vez
        self.ultima = None
        self.gravador = gravacao.Gravador()
        self.gif_final = None
        self.baixando = None
        self._aplicado = None
        self.estilo = {}                         # ferramenta, cor e espessura da anotação (lembradas na sessão)
        self.rolando = False
        self.parar_rolagem = threading.Event()
        self._sobre = {}                         # tarefas do fluxo de trabalho em andamento
        self.barra_fim = None                    # threading.Event da barra de ações aberta
        self._audio = None                       # dispositivos de áudio (lidos uma vez)
        self.atalhos = captura.Atalhos(self.executar)

    def _c(self, forcar=False):
        c = self.cfg(forcar)
        return {**c, **self._sobre} if self._sobre else c

    # ------------------------------------------------------------ atalhos e estado
    def sincronizar(self, c):
        """Registra os atalhos conforme as Configurações (só quando mudam). Com os atalhos desligados, fica só o
        de \"desligar / ligar os atalhos\" (senão não haveria como ligá-los de novo pelo teclado)."""
        todos = dict(c.get("captura_atalhos") or {})
        if c.get("captura_ativa", True):
            mapa = todos
            for f in c.get("captura_fluxos") or []:
                if f.get("atalho"):
                    mapa[f"fluxo:{f['id']}"] = f["atalho"]
        else:
            mapa = {"atalhos": todos["atalhos"]} if todos.get("atalhos") else {}
        chave = json.dumps(mapa, sort_keys=True)
        if chave == self._aplicado:
            return
        self._aplicado = chave
        usa_print = any((v or "").lower().split("+")[-1] == "print" for v in mapa.values())
        mudou = False
        try:
            if usa_print:
                mudou = captura.liberar_printscreen(self.pasta)
            else:
                captura.restaurar_printscreen(self.pasta)
        except OSError as e:
            print("print screen:", e)
        self.atalhos.definir(mapa)
        self.atalhos.registrado.wait(3)
        if mudou and any(st != "ok" for st in self.atalhos.estado.values()):
            time.sleep(1.5)  # a Ferramenta de Captura pode demorar para soltar a tecla
            self.atalhos.definir(mapa)
            self.atalhos.registrado.wait(3)
        self.reportar()

    def estado(self):
        g = self.gravador
        ff = gravacao.ffmpeg()
        if ff and self._audio is None:
            self._audio = gravacao.listar_audio(ff)
        import importlib.util
        qr = importlib.util.find_spec("zxingcpp") is not None
        c = self.cfg()
        return {"atalhos": self.atalhos.estado, "gravando": g.tipo if g.gravando else None,
                "gravando_desde": time.time() - g.segundos() if g.gravando else None, "pausado": g.pausado,
                "ffmpeg": bool(ff), "baixando": self.baixando, "audio": self._audio or [],
                "printscreen_liberado": os.path.exists(os.path.join(self.pasta, "printscreen.json")),
                "qr": qr, "ia_chave": ia.tem_chave(self.pasta, c.get("captura_ia_provedor") or "openai"),
                "barra": bool(self.barra_fim and not self.barra_fim.is_set())}

    def reportar(self):
        try:
            self.api_post("api/capturas/estado", self.estado())
        except Exception:
            pass

    # ------------------------------------------------------------ ações
    def executar(self, acao, origem="atalho"):
        titulo = captura.ACOES.get(acao, ("Captura",))[0]
        if self.rolando and acao in ("parar", "rolagem"):
            self.parar_rolagem.set()             # o mesmo atalho (ou "parar") termina a captura com rolagem
            return
        if acao == "parar" or (acao in ("video", "gif") and self.gravador.gravando):
            return self._parar_gravacao()
        sem_trava = {"pausar": self._pausar, "barra": self.alternar_barra, "atalhos": self._alternar_atalhos,
                     "enviar_arquivo": self.enviar_arquivo, "enviar_area": self.enviar_area}
        if acao in sem_trava:
            try:
                return sem_trava[acao]()
            except Exception as e:  # noqa: BLE001
                return self.notificar(f"Não consegui: {e}", titulo)
        fluxo = None
        if acao.startswith("fluxo:"):
            fluxo = next((f for f in self.cfg(True).get("captura_fluxos") or [] if f["id"] == acao[6:]), None)
            if not fluxo:
                return
            titulo = fluxo["nome"]
        if not self.lock.acquire(blocking=False):
            return
        mostrar = None
        try:
            if fluxo:
                self._sobre = {**fluxo["tarefas"], **({"captura_destino": fluxo["destino"]} if fluxo["destino"] else {})}
            if origem == "ui":
                mostrar = self.esconder_janela()
                time.sleep(0.35)  # espera a janela sumir antes de fotografar a tela
            self._rodar(fluxo["acao"] if fluxo else acao)
        except Exception as e:  # noqa: BLE001
            self.notificar(f"Não consegui: {e}", titulo)
        finally:
            self._sobre = {}
            self.lock.release()
            if mostrar and not self.gravador.gravando:
                mostrar()

    def _rodar(self, acao):
        c = self._c(True)
        if acao == "atraso":
            n = int(c.get("captura_atraso_s") or 3)
            self.notificar(f"Capturando em {n} s…", "Captura com atraso")
            time.sleep(n)
        if acao in ("regiao", "atraso", "rolagem", "ocr", "video", "gif", "cor", "regua", "regioes", "qr", "fixar"):
            img, ox, oy = self._foto(c)
            modo = acao if acao in ("cor", "regua", "regioes") else "regiao"
            sel = captura.selecionar(img, ox, oy, modo, captura.AreasDaTela() if modo in ("regiao", "regioes") else (),
                                     anotar=acao in ("regiao", "atraso") and c.get("captura_anotar", True),
                                     lupa=c.get("captura_lupa", True), estilo=self.estilo,
                                     escurecer=c.get("captura_escurecer", True), cruz=c.get("captura_cruz", True))
            if not sel:
                return
            if acao == "cor":
                hexa = "#%02X%02X%02X" % tuple(sel[:3])
                captura.copiar_texto(hexa)
                self.notificar(f"{hexa} copiado (rgb {sel[0]}, {sel[1]}, {sel[2]}).", "Seletor de cor")
                return
            if acao == "regua":
                captura.copiar_texto(f"{sel[0]}x{sel[1]}")
                self.notificar(f"{sel[0]} × {sel[1]} px (distância {((sel[0] - 1) ** 2 + (sel[1] - 1) ** 2) ** 0.5:.0f} px). "
                               f"Copiado.", "Régua")
                return
            if acao == "regioes":
                junta, _pos = efeitos.juntar_regioes(img, sel)
                return self._pronto(junta, acao)
            bbox = (sel[0] + ox, sel[1] + oy, sel[2] + ox, sel[3] + oy)
            if acao in ("regiao", "atraso"):
                self.ultima = bbox
                recorte = img.crop(sel[:4])
                if sel[4]:
                    recorte = captura.desenhar_formas(recorte, sel[4], sel[0], sel[1])
                return self._pronto(recorte, acao)
            if acao == "rolagem":
                return self._rolagem(bbox)
            if acao == "ocr":
                return self._ocr(img.crop(sel[:4]))
            if acao == "qr":
                return self._qr(img.crop(sel[:4]))
            if acao == "fixar":
                recorte = img.crop(sel[:4])
                threading.Thread(target=telas.fixar, args=(recorte, bbox[:2]), daemon=True).start()
                return
            return self._gravar(acao, bbox, c)
        if acao == "ultima":
            if not self.ultima:
                self.notificar("Ainda não há uma região capturada nesta sessão.", "Repetir a última região")
                return
            return self._pronto(self._foto(c, self.ultima)[0], acao)
        if acao == "janela":
            r = captura.janela_ativa()
            if not r:
                raise RuntimeError("nenhuma janela ativa")
            return self._pronto(self._foto(c, r)[0], acao)
        if acao == "monitor":
            return self._pronto(self._foto(c, captura.monitor_do_mouse())[0], acao)
        if acao == "tela":
            return self._pronto(self._foto(c)[0], acao)
        raise RuntimeError("ação de captura desconhecida")

    def _foto(self, c, bbox=None):
        """Fotografa a tela; com a opção ligada, afasta o mouse antes (some o efeito de passar o mouse) e devolve depois."""
        pos = captura.afastar_mouse(c.get("captura_afastar_mouse") or "")
        try:
            if pos:
                time.sleep(0.25)
            return captura.capturar(bbox)
        finally:
            if pos and c.get("captura_afastar_voltar", True):
                captura.devolver_mouse(pos)

    def _arquivo(self, c, ext):
        if c.get("captura_salvar", True):
            return captura.nome_arquivo(c, ext)
        pasta = os.path.join(self.pasta, "capturas")   # salvar desligado: guarda só para o histórico/editor/envio
        os.makedirs(pasta, exist_ok=True)
        return captura.nome_livre(pasta, time.strftime("%Y-%m-%d_%H-%M-%S"), ext)

    def _rolagem(self, bbox):
        self.parar_rolagem.clear()
        self.rolando = True
        try:
            img, motivo = captura.rolagem(bbox, self.parar_rolagem)
        finally:
            self.rolando = False
        if motivo == "emenda":
            self.notificar("Parei antes do fim: a página mudou demais entre um passo e outro. Salvei o que já tinha.",
                           "Captura com rolagem")
        elif motivo == "limite":
            self.notificar("Parei no limite de 40.000 px de altura. Salvei o que já tinha.", "Captura com rolagem")
        return self._pronto(img, "rolagem")

    # ------------------------------------------------------------ tarefas pós-captura
    def _pronto(self, img, modo):
        """Ordem: efeitos -> embelezar -> (menu rápido) -> salvar -> copiar imagem -> copiar arquivo -> miniatura ->
        copiar caminho -> copiar pasta -> OCR -> QR -> aviso -> mostrar na pasta -> editor do Windows -> imprimir ->
        ações -> fixar -> janela pós-captura -> IA -> enviar -> editor. Na área de transferência fica o último que copia."""
        c = self._c()
        if c.get("captura_efeitos"):
            img = efeitos.aplicar_efeitos(img, c)
        if c.get("captura_embelezar"):
            img = efeitos.aplicar_embelezar(img, c)
        if c.get("captura_tarefas_rapidas"):
            escolha = telas.menu_rapido(img, [(k, r) for k, r, _t in RAPIDAS])
            if not escolha:
                return
            c = tarefas_rapidas(c, escolha)
        ext = captura.extensao(c)
        caminho = None
        if c.get("captura_perguntar"):
            caminho = captura.perguntar_arquivo(captura.nome_arquivo(c, ext))   # cancelou: segue a regra normal
        caminho = caminho or self._arquivo(c, ext)
        captura.salvar_imagem(img, caminho, c)
        feito = []
        if c.get("captura_copiar", True):
            captura.copiar_imagem(img)
            feito.append("copiada")
        cid = self._registrar(caminho, "imagem", modo, img)
        if c.get("captura_salvar", True) or c.get("captura_perguntar"):
            feito.append(f"salva em {os.path.basename(caminho)}")
        if c.get("captura_copiar_arquivo"):
            captura.copiar_arquivos([caminho])
            feito.append("arquivo copiado")
        if c.get("captura_miniatura"):
            base, e = os.path.splitext(caminho)
            mini = captura.nome_livre(os.path.dirname(caminho), os.path.basename(base) + "_mini", e)
            captura.salvar_imagem(efeitos.miniatura(img, c.get("captura_miniatura_largura") or 300), mini, c)
            feito.append("miniatura salva")
        if c.get("captura_copiar_caminho"):
            captura.copiar_texto(caminho)
            feito.append("caminho copiado")
        if c.get("captura_copiar_pasta"):
            captura.copiar_texto(os.path.dirname(caminho))
            feito.append("pasta copiada")
        if c.get("captura_ocr_auto"):
            try:
                texto = captura.ocr(img)
            except RuntimeError as e:
                self.notificar(f"OCR: {e}", "Captura de tela")
                texto = ""
            if texto:
                captura.copiar_texto(texto)
                feito.append("texto copiado")
        if c.get("captura_qr"):
            feito.append(self._qr(img, aviso=False))
        if c.get("captura_notificar", True):
            self._aviso(c, "Captura de tela", f"{img.width} × {img.height} " + (", ".join(f for f in feito if f) or "capturada") + ".",
                        img, caminho, cid)
        if c.get("captura_mostrar_pasta"):
            captura.mostrar_no_explorer(caminho)
        if c.get("captura_editor_fotos"):
            captura.abrir_com(caminho, "edit")
        if c.get("captura_imprimir"):
            captura.abrir_com(caminho, "print")
        if c.get("captura_rodar_acoes"):
            self.rodar_acoes(caminho, c)
        if c.get("captura_fixar"):
            threading.Thread(target=telas.fixar, args=(img,), daemon=True).start()
        if c.get("captura_janela_pos"):
            threading.Thread(target=self._janela_pos, args=(img, caminho, cid, c), daemon=True).start()
        if c.get("captura_ia"):
            threading.Thread(target=self._ia, args=(img, c), daemon=True).start()
        if c.get("captura_enviar"):
            self._enviar(cid, caminho, c, img)
        if c.get("captura_editor") and cid:
            self.abrir_janela(f"editor/{cid}")

    def _aviso(self, c, titulo, msg, img=None, caminho=None, cid=None, url=None):
        """Aviso próprio com miniatura, botões e o clique escolhido nas opções; sem ele, aviso simples."""
        if not self.avisos:
            return self.notificar(msg, titulo)
        clique = self._ao_clicar(c.get("captura_aviso_clique") or "abrir", caminho, cid, url, img)
        botoes = []
        if c.get("captura_aviso_botoes", True) and caminho:
            imagem = tipo_arquivo(caminho) == "imagem"
            if imagem:
                botoes.append(("Copiar", lambda: captura.copiar_imagem(img) if img is not None else self.copiar(caminho)))
            if imagem and cid:
                botoes.append(("Editor", lambda: self.abrir_janela(f"editor/{cid}")))
            botoes.append(("Pasta", lambda: captura.mostrar_no_explorer(caminho)))
            if url:
                botoes.append(("Abrir link", lambda: webbrowser.open(url)))
            else:
                botoes.append(("Enviar", lambda: self._enviar(cid, caminho, self._c(True), img)))
        self.avisos.mostrar(titulo, msg, img=img, ao_clicar=clique, botoes=botoes)

    def _ao_clicar(self, modo, caminho, cid, url, img):
        if modo == "nada" or not (caminho or url):
            return None
        if modo == "link" and url:
            return lambda: webbrowser.open(url)
        if not caminho:
            return lambda: webbrowser.open(url)
        if modo == "pasta":
            return lambda: captura.mostrar_no_explorer(caminho)
        if modo == "editor" and cid and tipo_arquivo(caminho) == "imagem":
            return lambda: self.abrir_janela(f"editor/{cid}")
        if modo == "copiar":
            return lambda: self.copiar(caminho)
        if modo == "fixar" and tipo_arquivo(caminho) == "imagem":
            return lambda: self.fixar_arquivo(caminho)
        return lambda: captura.abrir_com(caminho)

    def _janela_pos(self, img, caminho, cid, c):
        def executar(k):
            if k == "copiar":
                captura.copiar_imagem(img)
            elif k == "copiar_arquivo":
                captura.copiar_arquivos([caminho])
            elif k == "caminho":
                captura.copiar_texto(caminho)
            elif k == "pasta":
                captura.mostrar_no_explorer(caminho)
            elif k == "editor" and cid:
                self.abrir_janela(f"editor/{cid}")
            elif k == "editor_fotos":
                captura.abrir_com(caminho, "edit")
            elif k == "fixar":
                threading.Thread(target=telas.fixar, args=(img,), daemon=True).start()
            elif k == "enviar":
                url = self._enviar(cid, caminho, self._c(True), img)
                return f"Link copiado: {url}" if url else "Envio cancelado ou com erro."
            elif k == "ocr":
                return self._ocr(img)
            elif k == "qr":
                return self._qr(img)
            elif k == "ia":
                self._ia(img, self._c(True))
            elif k == "imprimir":
                captura.abrir_com(caminho, "print")
            return None
        telas.pos_captura(img, f"{os.path.basename(caminho)} · {img.width} × {img.height}", POS, executar)

    def _ocr(self, img):
        texto = captura.ocr(img)
        if not texto:
            self.notificar("Não encontrei texto nessa área.", "OCR")
            return "Sem texto."
        captura.copiar_texto(texto)
        self.notificar(f"Texto copiado: {texto[:180]}", "OCR")
        return "Texto copiado."

    def _qr(self, img, aviso=True):
        """Lê os códigos QR da imagem, copia o primeiro e mostra todos numa janela."""
        try:
            textos = efeitos.ler_qr(img)
        except RuntimeError as e:
            self.notificar(str(e), "Código QR")
            return ""
        if not textos:
            if aviso:
                self.notificar("Não encontrei código QR nessa área.", "Código QR")
            return ""
        captura.copiar_texto(textos[0])
        url = textos[0] if textos[0].lower().startswith(("http://", "https://")) else None
        botoes = [("Abrir o link", lambda: webbrowser.open(url))] if url else []
        threading.Thread(target=telas.texto, args=("Código QR lido", "\n\n".join(textos)), kwargs={"botoes": botoes},
                         daemon=True).start()
        return "QR copiado"

    def _ia(self, img, c):
        self.notificar("Analisando a imagem…", "Analisar com IA")
        try:
            r = ia.analisar(img, c, self.pasta)
        except RuntimeError as e:
            self.notificar(str(e), "Analisar com IA")
            return
        telas.texto("Análise da imagem (IA)", r or "(a IA não respondeu nada)")

    def rodar_acoes(self, caminho, c):
        """\"Realizar ações\": roda cada programa configurado com o arquivo (sem shell)."""
        for a in c.get("captura_acoes") or []:
            if not a.get("ativo", True):
                continue
            try:
                subprocess.Popen(captura.montar_comando(a, caminho), close_fds=True)
            except (OSError, ValueError) as e:
                self.notificar(f"{a['nome']}: {e}", "Realizar ações")

    def _registrar(self, caminho, tipo, modo, img=None, duracao=None):
        try:
            r = self.api_post("api/capturas/registrar", {
                "caminho": caminho, "tipo": tipo, "modo": modo, "largura": img.width if img else 0,
                "altura": img.height if img else 0, "bytes": os.path.getsize(caminho),
                "mini": captura.miniatura(img) if img else "", "duracao": duracao}, timeout=10)
            return r.get("id")
        except Exception as e:  # noqa: BLE001
            print("registrar captura:", e)
            return None

    # ------------------------------------------------------------ envio e tarefas pós-envio
    def _enviar(self, cid, caminho, c, img=None):
        """Envia e roda as tarefas pós-envio. Devolve o link (ou None)."""
        destino = c.get("captura_destino", "catbox")
        if c.get("captura_pre_envio"):
            if img is None and tipo_arquivo(caminho) == "imagem":
                img = self._abrir(caminho)
            if not telas.confirmar_envio(img, os.path.basename(caminho), envio.DESTINOS.get(destino, destino)):
                return None
        try:
            url = envio.enviar_limpo(caminho, c)
        except (RuntimeError, OSError) as e:
            self.notificar(f"Não consegui enviar: {e}", "Envio")
            return None
        if c.get("captura_envio_encurtar"):
            try:
                url = envio.encurtar(url)
            except RuntimeError as e:
                self.notificar(f"Enviado, mas não consegui encurtar o link: {e}", "Envio")
        if cid:
            try:
                self.api_post("api/capturas/atualizar", {"id": cid, "url": url})
            except Exception:
                pass
        copiou = c.get("captura_envio_copiar", True)
        if copiou:
            captura.copiar_texto(envio.formatar_link(url, c.get("captura_envio_formato"), caminho))
        if c.get("captura_envio_abrir"):
            webbrowser.open(url)   # envio.enviar só devolve http(s)://
        if c.get("captura_envio_compartilhar") in envio.COMPARTILHAR:
            webbrowser.open(envio.link_compartilhar(c["captura_envio_compartilhar"], url))
        if c.get("captura_envio_janela") or c.get("captura_envio_qr"):
            threading.Thread(target=self._janela_link, args=(url, caminho, bool(c.get("captura_envio_janela"))),
                             daemon=True).start()
        if c.get("captura_envio_notificar", True):
            self._aviso(c, "Enviado", f"{'Link copiado' if copiou else 'Link'}: {url}", img, caminho, cid, url)
        if c.get("captura_apagar_local") and os.path.isfile(caminho):
            captura.para_lixeira(caminho)
        return url

    def _janela_link(self, url, caminho, completa):
        botoes = [("Abrir", lambda: webbrowser.open(url))]
        if completa:
            def encurtar():
                try:
                    curto = envio.encurtar(url)
                except RuntimeError as e:
                    return self.notificar(str(e), "Encurtar link")
                captura.copiar_texto(curto)
                self.notificar(f"Link curto copiado: {curto}", "Encurtar link")
            botoes.append(("Encurtar", encurtar))
            for k in ("whatsapp", "telegram", "email"):
                botoes.append((envio.COMPARTILHAR[k][0], lambda k=k: webbrowser.open(envio.link_compartilhar(k, url))))
        telas.texto("Enviado — " + os.path.basename(caminho) if completa else "Código QR do link", url,
                    img=efeitos.qr_imagem(url), botoes=botoes)

    def enviar(self, cid, caminho):
        if not os.path.isfile(caminho):
            return False, "O arquivo não existe mais."
        threading.Thread(target=self._enviar, args=(cid, caminho, self._c(True)), daemon=True).start()
        return True, "Enviando… o link vai para a área de transferência."

    def _enviar_arquivos(self, caminhos):
        c = self._c(True)
        for p in caminhos:
            t = tipo_arquivo(p)
            img = self._abrir(p) if t == "imagem" else None
            cid = self._registrar(p, t, "envio", img)
            self._enviar(cid, p, c, img)

    def enviar_arquivo(self):
        caminhos = captura.escolher_arquivos("Enviar arquivo", varios=True)
        if caminhos:
            self._enviar_arquivos(caminhos)

    def enviar_area(self):
        """Envia o que estiver na área de transferência: imagem, arquivos ou texto (um link vira link curto)."""
        tipo, dados = captura.ler_area_transferencia()
        c = self._c(True)
        if tipo == "imagem":
            caminho = self._arquivo(c, captura.extensao(c))
            captura.salvar_imagem(dados, caminho, c)
            self._enviar(self._registrar(caminho, "imagem", "area", dados), caminho, c, dados)
        elif tipo == "arquivos" and dados:
            self._enviar_arquivos(dados)
        elif tipo == "texto" and dados.strip():
            t = dados.strip()
            if t.lower().startswith(("http://", "https://")) and not any(ch.isspace() for ch in t):
                curto = envio.encurtar(t)
                captura.copiar_texto(curto)
                return self.notificar(f"Link curto copiado: {curto}", "Encurtar link")
            self.enviar_texto(t)
        else:
            self.notificar("A área de transferência está vazia.", "Enviar")

    def enviar_texto(self, texto=None):
        if texto is None:
            texto = telas.pedir_texto("Enviar texto", "Cole ou digite o texto. Ele vira um arquivo .txt e o link é copiado.",
                                      multilinha=True)
        if not texto or not texto.strip():
            return
        c = self._c(True)
        try:
            url = envio.enviar_texto(texto, c)
        except (RuntimeError, OSError) as e:
            return self.notificar(f"Não consegui enviar: {e}", "Enviar texto")
        captura.copiar_texto(url)
        self.notificar(f"Link copiado: {url}", "Enviar texto")

    # ------------------------------------------------------------ gravação
    def _obter_ffmpeg(self):
        ff = gravacao.ffmpeg()
        if ff:
            return ff
        self.notificar("Baixando o ffmpeg (cerca de 90 MB, só na primeira vez)…", "Gravação de tela")

        def prog(feito, total):
            self.baixando = round(feito * 100 / total) if total else None
        self.baixando = 0
        self.reportar()
        try:
            ff = gravacao.baixar_ffmpeg(prog)
        except Exception as e:  # noqa: BLE001
            self.notificar(f"Não consegui baixar o ffmpeg: {e}", "Gravação de tela")
            return None
        finally:
            self.baixando = None
            self.reportar()
        self._audio = None
        self.notificar("ffmpeg pronto.", "Gravação de tela")
        return ff

    def baixar_ffmpeg(self):
        return self._obter_ffmpeg() is not None

    def _gravar(self, acao, bbox, c):
        ff = self._obter_ffmpeg()
        if not ff:
            return
        atraso = int(c.get("captura_gravar_atraso") or 0)
        if atraso:
            self.notificar(f"A gravação começa em {atraso} s…", "Gravação de tela")
            time.sleep(atraso)
        if acao == "gif":
            self.gif_final = self._arquivo(c, ".gif")
            os.makedirs(os.path.join(self.pasta, "capturas"), exist_ok=True)
            mp4 = captura.nome_livre(os.path.join(self.pasta, "capturas"), "gravando-gif", ".mp4")
        else:
            mp4 = self._arquivo(c, ".mp4")
        self.gravador.iniciar(ff, bbox, mp4, "gif" if acao == "gif" else "video",
                              int(c.get("captura_gravar_fps") or 30), bool(c.get("captura_gravar_cursor", True)),
                              c.get("captura_gravar_audio") or "", bool(c.get("captura_gravar_cronometro", True)))
        self.notificar("Gravando. Aperte o mesmo atalho de novo (ou “Parar gravação” no menu do ícone) para terminar.",
                       "Gravação de tela")
        self.reportar()

    def _pausar(self):
        try:
            pausado = self.gravador.pausar()
        except RuntimeError as e:
            return self.notificar(str(e), "Gravação de tela")
        self.reportar()
        self.notificar("Gravação pausada." if pausado else "Gravação continuando.", "Gravação de tela")

    def _parar_gravacao(self):
        if not self.gravador.gravando:
            return
        try:
            arq, tipo, seg = self.gravador.parar()
        except RuntimeError as e:
            self.notificar(str(e), "Gravação de tela")
            self.reportar()
            return
        self.reportar()
        ff = gravacao.ffmpeg()
        try:
            if tipo == "gif":
                self.notificar("Convertendo para GIF…", "Gravação de tela")
                gravacao.para_gif(ff, arq, self.gif_final)
                os.remove(arq)
                arq = self.gif_final
            mini = gravacao.primeiro_quadro(ff, arq)
        except Exception as e:  # noqa: BLE001
            self.notificar(f"Não consegui terminar a gravação: {e}", "Gravação de tela")
            return
        cid = self._registrar(arq, tipo, tipo, mini, round(seg, 1))
        c = self._c(True)
        self._aviso(c, "Gravação de tela", f"{'GIF' if tipo == 'gif' else 'Vídeo'} de {seg:.0f} s salvo em {os.path.basename(arq)}.",
                    mini, arq, cid)
        if c.get("captura_enviar"):
            self._enviar(cid, arq, c)

    # ------------------------------------------------------------ atalhos e barra
    def _alternar_atalhos(self):
        ligar = not self.cfg(True).get("captura_ativa", True)
        self.api_post("api/config", {"captura_ativa": ligar})
        self.sincronizar(self.cfg(True))
        self.notificar("Atalhos de captura ligados." if ligar else
                       "Atalhos de captura desligados (o mesmo atalho liga de novo).", "Captura de tela")

    def alternar_barra(self):
        if self.barra_fim and not self.barra_fim.is_set():
            self.barra_fim.set()
            return
        self.barra_fim = fim = threading.Event()

        def rodar():
            try:
                telas.barra_acoes(BARRA, lambda k: self.executar(k, "barra"), fim)
            finally:
                fim.set()
                self.reportar()
        threading.Thread(target=rodar, daemon=True).start()
        self.reportar()

    # ------------------------------------------------------------ pedidos da janela, do menu e do Explorer
    @staticmethod
    def _abrir(caminho):
        from PIL import Image
        with Image.open(caminho) as im:
            im.load()
            return im.copy()

    def copiar(self, caminho):
        if caminho.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
            captura.copiar_imagem(self._abrir(caminho))
            return True, "Imagem copiada."
        captura.copiar_arquivos([caminho])
        return True, "Arquivo copiado (cole no Explorer ou num programa)."

    def fixar_arquivo(self, caminho):
        telas.fixar(self._abrir(caminho))

    def fixar_area(self):
        tipo, dados = captura.ler_area_transferencia()
        if tipo == "imagem":
            return telas.fixar(dados)
        if tipo == "arquivos" and dados and tipo_arquivo(dados[0]) == "imagem":
            return self.fixar_arquivo(dados[0])
        self.notificar("Não há uma imagem na área de transferência.", "Fixar na tela")

    def escolher_e(self, o_que):
        """Ferramentas que começam escolhendo um arquivo: fixar, hash, qr, ocr, embelezar, editor."""
        img_tipos = [("Imagens", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif")]
        arqs = captura.escolher_arquivos({"hash": "Arquivo para conferir o hash"}.get(o_que, "Escolha a imagem"),
                                         tipos=None if o_que == "hash" else img_tipos)
        if arqs:
            self.ferramenta(o_que, arqs[0])

    def ferramenta(self, o_que, caminho, cid=None):
        """Ferramentas sobre um arquivo (do histórico, do menu ou do Explorer). Devolve (ok, mensagem)."""
        if not os.path.isfile(caminho):
            return False, "O arquivo não existe mais."
        if o_que == "hash":
            threading.Thread(target=telas.hash_janela, args=(os.path.basename(caminho), efeitos.hash_arquivo(caminho)),
                             daemon=True).start()
            return True, "Hash calculado."
        if o_que == "enviar":
            threading.Thread(target=self._enviar_arquivos, args=([caminho],), daemon=True).start()
            return True, "Enviando…"
        if o_que == "copiar_arquivo":
            captura.copiar_arquivos([caminho])
            return True, "Arquivo copiado."
        if o_que in ("imprimir", "editor_fotos"):
            captura.abrir_com(caminho, "print" if o_que == "imprimir" else "edit")
            return True, "Aberto no Windows."
        if tipo_arquivo(caminho) != "imagem":
            return False, "Essa ferramenta só funciona com imagens."
        img = self._abrir(caminho)
        if o_que == "fixar":
            threading.Thread(target=telas.fixar, args=(img,), daemon=True).start()
            return True, "Imagem fixada na tela."
        if o_que == "qr":
            threading.Thread(target=self._qr, args=(img,), daemon=True).start()
            return True, "Lendo o código QR…"
        if o_que == "ocr":
            threading.Thread(target=self._ocr, args=(img,), daemon=True).start()
            return True, "Lendo o texto…"
        if o_que == "ia":
            threading.Thread(target=self._ia, args=(img, self._c(True)), daemon=True).start()
            return True, "Analisando…"
        if o_que == "embelezar":
            c = self._c(True)
            base, ext = os.path.splitext(caminho)
            destino = captura.nome_livre(os.path.dirname(caminho), os.path.basename(base) + "_embelezada",
                                         ".jpg" if ext.lower() in (".jpg", ".jpeg") else ext if ext.lower() == ".webp" else ".png")
            nova = efeitos.aplicar_embelezar(img, c)
            captura.salvar_imagem(nova, destino, c)
            novo = self._registrar(destino, "imagem", "embelezar", nova)
            self._aviso(c, "Imagem embelezada", f"Salva em {os.path.basename(destino)}.", nova, destino, novo)
            return True, f"Salva em {os.path.basename(destino)}."
        if o_que == "editor":
            novo = cid or self._registrar(caminho, "imagem", "arquivo", img)
            if novo:
                self.abrir_janela(f"editor/{novo}")
            return bool(novo), "Aberto no editor." if novo else "O serviço do Monitor não respondeu."
        return False, "Ferramenta desconhecida."

    def renomear(self, cid, caminho, novo):
        """Renomeia o arquivo na mesma pasta (mantém a extensão) e atualiza o histórico."""
        if not os.path.isfile(caminho):
            return False, "O arquivo não existe mais."
        novo = "".join(ch for ch in str(novo) if ch not in '<>:"/\\|?*' and ord(ch) >= 32).strip(" .")[:150]
        if not novo:
            return False, "Nome inválido."
        ext = os.path.splitext(caminho)[1]
        if not novo.lower().endswith(ext.lower()):
            novo += ext
        destino = os.path.join(os.path.dirname(caminho), novo)
        if os.path.exists(destino):
            return False, "Já existe um arquivo com esse nome."
        os.rename(caminho, destino)
        self.api_post("api/capturas/atualizar", {"id": cid, "caminho": destino})
        return True, f"Renomeado para {novo}."

    def importar_pasta(self):
        """Coloca no histórico as imagens e vídeos de uma pasta (sem copiar nem mover nada)."""
        p = captura.escolher_pasta(captura.pasta_capturas(self.cfg(True)))
        if not p:
            return False, "Nenhuma pasta escolhida."
        arqs = sorted((os.path.join(p, n) for n in os.listdir(p)
                       if tipo_arquivo(n) in ("imagem", "gif", "video") and os.path.isfile(os.path.join(p, n))),
                      key=os.path.getmtime)[-500:]
        n = 0
        for a in arqs:
            img = None
            if tipo_arquivo(a) == "imagem":
                try:
                    img = self._abrir(a)
                except OSError:
                    continue
            if self._registrar(a, tipo_arquivo(a), "importada", img):
                n += 1
        return True, f"{n} arquivo{'s' if n != 1 else ''} importado{'s' if n != 1 else ''} para o histórico."

    def cadastrar_chave_ia(self):
        prov = self.cfg(True).get("captura_ia_provedor") or "openai"
        nome = ia.PROVEDORES[prov][0]
        chave = telas.pedir_texto(f"Chave de API — {nome}",
                                  f"Cole a sua chave de API do {nome}. Ela fica cifrada pelo Windows só no seu usuário "
                                  "deste PC. Deixe vazio e confirme para apagar a chave guardada.", senha=True)
        if chave is None:
            return False, "Cancelado."
        ia.guardar_chave(self.pasta, prov, chave)
        self.reportar()
        return True, "Chave guardada." if chave.strip() else "Chave apagada."

    def escolher_pasta(self):
        p = captura.escolher_pasta(captura.pasta_capturas(self.cfg(True)))
        if not p:
            return False, "Nenhuma pasta escolhida."
        os.makedirs(p, exist_ok=True)
        r = self.api_post("api/config", {"captura_pasta": p})
        if (r.get("valores") or {}).get("captura_pasta") != p:
            return False, "Não consegui usar essa pasta."
        self.cfg(True)
        return True, f"As capturas vão para {p}."

    def abrir_pasta(self):
        p = captura.pasta_capturas(self.cfg(True))
        os.makedirs(p, exist_ok=True)
        os.startfile(p)
        return True, "Pasta aberta."

    def editada(self, cid, caminho, png_b64, nova, copiar):
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(png_b64)))
        img.load()
        c = self.cfg(True)
        if nova or not os.path.isfile(caminho):
            base, ext = os.path.splitext(caminho)
            destino = captura.nome_livre(os.path.dirname(caminho) or ".", os.path.basename(base) + "_editada", ext or ".png")
        else:
            destino = caminho
        captura.salvar_imagem(img, destino, c)
        if copiar:
            captura.copiar_imagem(img)
        if destino == caminho:
            self.api_post("api/capturas/atualizar", {"id": cid, "largura": img.width, "altura": img.height,
                                                     "bytes": os.path.getsize(destino), "mini": captura.miniatura(img), "url": ""})
            novo = cid
        else:
            novo = self._registrar(destino, "imagem", "editor", img)
        return True, {"id": novo, "msg": f"Salvo em {os.path.basename(destino)}." + (" Imagem copiada." if copiar else "")}
