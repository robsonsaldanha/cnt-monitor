"""Fluxo da captura no ícone da bandeja: capturar -> salvar, copiar, registrar no histórico, enviar, abrir no editor.
Também liga os atalhos globais e controla a gravação de vídeo/GIF."""
import io
import os
import json
import time
import base64
import threading
import webbrowser

from . import captura, envio, gravacao


class Capturador:
    def __init__(self, api_post, cfg, notificar, abrir_janela, esconder_janela, pasta_usuario):
        self.api_post = api_post
        self.cfg = cfg
        self.notificar = notificar
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
        self.atalhos = captura.Atalhos(self.executar)

    # ------------------------------------------------------------ atalhos e estado
    def sincronizar(self, c):
        """Registra os atalhos conforme as Configurações (só quando mudam)."""
        mapa = dict(c.get("captura_atalhos") or {}) if c.get("captura_ativa", True) else {}
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
        return {"atalhos": self.atalhos.estado, "gravando": g.tipo if g.gravando else None,
                "gravando_desde": g.inicio if g.gravando else None, "ffmpeg": bool(gravacao.ffmpeg()),
                "baixando": self.baixando, "printscreen_liberado": os.path.exists(os.path.join(self.pasta, "printscreen.json"))}

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
        if not self.lock.acquire(blocking=False):
            return
        mostrar = None
        try:
            if origem == "ui":
                mostrar = self.esconder_janela()
                time.sleep(0.35)  # espera a janela sumir antes de fotografar a tela
            self._rodar(acao)
        except Exception as e:  # noqa: BLE001
            self.notificar(f"Não consegui: {e}", titulo)
        finally:
            self.lock.release()
            if mostrar and not self.gravador.gravando:
                mostrar()

    def _rodar(self, acao):
        c = self.cfg(True)
        if acao == "atraso":
            n = int(c.get("captura_atraso_s") or 3)
            self.notificar(f"Capturando em {n} s…", "Captura com atraso")
            time.sleep(n)
        if acao in ("regiao", "atraso", "rolagem", "ocr", "video", "gif", "cor", "regua"):
            img, ox, oy = captura.capturar()
            modo = acao if acao in ("cor", "regua") else "regiao"
            sel = captura.selecionar(img, ox, oy, modo, captura.janelas_visiveis() if modo == "regiao" else (),
                                     anotar=acao in ("regiao", "atraso") and c.get("captura_anotar", True),
                                     lupa=c.get("captura_lupa", True), estilo=self.estilo)
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
                texto = captura.ocr(img.crop(sel[:4]))
                if not texto:
                    self.notificar("Não encontrei texto nessa área.", "OCR")
                    return
                captura.copiar_texto(texto)
                self.notificar(f"Texto copiado: {texto[:180]}", "OCR")
                return
            return self._gravar(acao, bbox, c)
        if acao == "ultima":
            if not self.ultima:
                self.notificar("Ainda não há uma região capturada nesta sessão.", "Repetir a última região")
                return
            return self._pronto(captura.capturar(self.ultima)[0], acao)
        if acao == "janela":
            r = captura.janela_ativa()
            if not r:
                raise RuntimeError("nenhuma janela ativa")
            return self._pronto(captura.capturar(r)[0], acao)
        if acao == "monitor":
            return self._pronto(captura.capturar(captura.monitor_do_mouse())[0], acao)
        if acao == "tela":
            return self._pronto(captura.capturar()[0], acao)
        raise RuntimeError("ação de captura desconhecida")

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

    def _pronto(self, img, modo):
        """Tarefas pós-captura, nesta ordem (na área de transferência fica a última que copiar algo):
        salvar -> copiar imagem -> copiar caminho -> texto (OCR) -> aviso -> mostrar na pasta -> enviar -> editor."""
        c = self.cfg()
        ext = ".jpg" if c.get("captura_formato") == "jpg" else ".png"
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
        if c.get("captura_copiar_caminho"):
            captura.copiar_texto(caminho)
            feito.append("caminho copiado")
        if c.get("captura_ocr_auto"):
            try:
                texto = captura.ocr(img)
            except RuntimeError as e:
                self.notificar(f"OCR: {e}", "Captura de tela")
                texto = ""
            if texto:
                captura.copiar_texto(texto)
                feito.append("texto copiado")
        if c.get("captura_notificar", True):
            self.notificar(f"{img.width} × {img.height} " + (", ".join(feito) or "capturada") + ".", "Captura de tela")
        if c.get("captura_mostrar_pasta"):
            captura.mostrar_no_explorer(caminho)
        if c.get("captura_enviar"):
            self._enviar(cid, caminho, c)
        if c.get("captura_editor") and cid:
            self.abrir_janela(f"editor/{cid}")

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

    def _enviar(self, cid, caminho, c):
        """Envia e roda as tarefas pós-envio (copiar o link no formato escolhido, abrir no navegador, aviso)."""
        try:
            url = envio.enviar(caminho, c)
        except (RuntimeError, OSError) as e:
            self.notificar(f"Não consegui enviar: {e}", "Envio")
            return None
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
        if c.get("captura_envio_notificar", True):
            self.notificar(f"{'Link copiado' if copiou else 'Enviado'}: {url}", "Envio")
        return url

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
        self.notificar("ffmpeg pronto.", "Gravação de tela")
        return ff

    def baixar_ffmpeg(self):
        return self._obter_ffmpeg() is not None

    def _gravar(self, acao, bbox, c):
        ff = self._obter_ffmpeg()
        if not ff:
            return
        if acao == "gif":
            self.gif_final = self._arquivo(c, ".gif")
            os.makedirs(os.path.join(self.pasta, "capturas"), exist_ok=True)
            mp4 = captura.nome_livre(os.path.join(self.pasta, "capturas"), "gravando-gif", ".mp4")
        else:
            mp4 = self._arquivo(c, ".mp4")
        self.gravador.iniciar(ff, bbox, mp4, "gif" if acao == "gif" else "video",
                              int(c.get("captura_gravar_fps") or 30), bool(c.get("captura_gravar_cursor", True)))
        self.notificar("Gravando. Aperte o mesmo atalho de novo (ou “Parar gravação” no menu do ícone) para terminar.",
                       "Gravação de tela")
        self.reportar()

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
        c = self.cfg(True)
        self.notificar(f"{'GIF' if tipo == 'gif' else 'Vídeo'} de {seg:.0f} s salvo em {os.path.basename(arq)}.",
                       "Gravação de tela")
        if c.get("captura_enviar"):
            self._enviar(cid, arq, c)

    # ------------------------------------------------------------ pedidos da janela (histórico e editor)
    def copiar(self, caminho):
        if caminho.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            from PIL import Image
            with Image.open(caminho) as im:
                captura.copiar_imagem(im.copy())
            return True, "Imagem copiada."
        captura.copiar_texto(caminho)
        return True, "Caminho do arquivo copiado."

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

    def enviar(self, cid, caminho):
        if not os.path.isfile(caminho):
            return False, "O arquivo não existe mais."
        threading.Thread(target=self._enviar, args=(cid, caminho, self.cfg(True)), daemon=True).start()
        return True, "Enviando… o link vai para a área de transferência."

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
