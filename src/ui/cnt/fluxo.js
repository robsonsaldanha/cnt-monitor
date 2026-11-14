/* Editor de fluxos. Script clássico, sem dependência.

   var fluxo = cntFluxo.criar(elemento, {
     nos: [{ id, tipo, titulo, descricao, estado, icone, x, y }],
     conexoes: [{ id, de, para, tipo: "solida" | "pontilhada", rotulo }],
     direcao: "horizontal" | "vertical",
     somenteLeitura: false,
     tipoConexao: "solida",        // tipo das conexões criadas pela pessoa
     rotulo: "Fluxo"               // nome do quadro para leitor de tela
   });

   Inicia sozinho em [data-cnt-fluxo], com os dados em <script type="application/json"> dentro do elemento.
   Métodos: dados(), carregar(dados), adicionarNo(no), removerNo(id), conectar(de, para, extra),
            desconectar(id), ajustar(), zoom(fator), destruir().
   Eventos (sobem na árvore): cnt:fluxo:mudou { motivo, dados }, cnt:fluxo:selecionou { tipo, id }.

   icone é SVG escrito pelo desenvolvedor (entra sem escape): nunca passe dado de usuário. */
(function () {
  var NS = "http://www.w3.org/2000/svg";
  var ZOOM_MIN = 0.25;
  var ZOOM_MAX = 2;
  var RECUO_SETA = 7; // a linha termina antes da porta de entrada, para a seta não ficar embaixo dela
  var instancias = 0;

  function icone(desenho) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + desenho + "</svg>";
  }

  var TIPOS = {
    gatilho: { rotulo: "Gatilho", icone: icone('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>') },
    condicao: { rotulo: "Condição", icone: icone('<path d="M12 3l9 9-9 9-9-9z"/>') },
    acao: { rotulo: "Ação", icone: icone('<path d="M7 4l12 8-12 8z"/>') },
    etapa: { rotulo: "Etapa", icone: icone('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12l3 3 5-6"/>') },
    pessoa: { rotulo: "", icone: icone('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>') }
  };

  var ESTADOS = { concluido: "Concluído", andamento: "Em andamento", pendente: "Pendente", erro: "Erro" };

  function el(tag, classe, texto) {
    var item = document.createElement(tag);
    if (classe) item.className = classe;
    if (texto != null) item.textContent = texto;
    return item;
  }

  function svg(nome, classe) {
    var item = document.createElementNS(NS, nome);
    if (classe) item.setAttribute("class", classe);
    return item;
  }

  function limitar(valor, minimo, maximo) {
    return Math.min(maximo, Math.max(minimo, valor));
  }

  function criar(raiz, opcoes) {
    if (raiz.cntFluxo) return raiz.cntFluxo;
    opcoes = opcoes || {};

    var vertical = opcoes.direcao === "vertical";
    var leitura = !!opcoes.somenteLeitura;
    var tipoPadrao = opcoes.tipoConexao === "pontilhada" ? "pontilhada" : "solida";
    var uid = "cnt-fluxo-" + (++instancias);
    var nos = {};      // id -> { dado, el }
    var conexoes = {}; // id -> { dado, grupo, alvo, linha, pulso, rotulo }
    var vista = { x: 0, y: 0, z: 1 };
    var selecao = null; // { tipo: "no" | "conexao", id }
    var arrasto = null;
    var ligando = null; // id do nó de origem, na conexão por teclado

    /* Estrutura */
    raiz.textContent = "";
    raiz.classList.add("cnt-fluxo");
    raiz.classList.toggle("cnt-fluxo--vertical", vertical);
    raiz.classList.toggle("cnt-fluxo--leitura", leitura);

    var quadro = el("div", "cnt-fluxo__quadro");
    quadro.setAttribute("role", "group");
    quadro.setAttribute("aria-label", opcoes.rotulo || "Fluxo");
    var palco = el("div", "cnt-fluxo__palco");
    var linhas = svg("svg", "cnt-fluxo__linhas");
    linhas.innerHTML = "<defs>" +
      '<marker id="' + uid + '-seta" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto"><path class="cnt-fluxo__seta" d="M0 0L10 5L0 10z"/></marker>' +
      '<marker id="' + uid + '-seta-sel" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto"><path class="cnt-fluxo__seta cnt-fluxo__seta--selecionada" d="M0 0L10 5L0 10z"/></marker>' +
      "</defs>";
    var provisoria = svg("path", "cnt-fluxo__linha cnt-fluxo__linha--pontilhada");
    provisoria.style.display = "none";
    linhas.appendChild(provisoria);
    palco.appendChild(linhas);
    quadro.appendChild(palco);

    var barra = el("div", "cnt-fluxo__barra");
    barra.setAttribute("role", "group");
    barra.setAttribute("aria-label", "Zoom");
    var mostrador = el("span", "cnt-fluxo__zoom");

    function botao(texto, nome, acao) {
      var item = el("button", "cnt-fluxo__botao", texto);
      item.type = "button";
      item.setAttribute("aria-label", nome);
      item.addEventListener("click", acao);
      barra.appendChild(item);
    }

    botao("−", "Diminuir zoom", function () { zoom(1 / 1.2); });
    barra.appendChild(mostrador);
    botao("+", "Aumentar zoom", function () { zoom(1.2); });
    botao("Ajustar", "Ajustar à tela", ajustar);

    var aviso = el("p", "cnt-fluxo__aviso");
    aviso.setAttribute("aria-live", "polite");

    raiz.appendChild(quadro);
    raiz.appendChild(barra);
    raiz.appendChild(aviso);

    /* Utilidades */
    function emitir(acao, detalhe) {
      raiz.dispatchEvent(new CustomEvent("cnt:fluxo:" + acao, { bubbles: true, detail: detalhe }));
    }

    function dados() {
      return {
        nos: Object.keys(nos).map(function (id) { return Object.assign({}, nos[id].dado); }),
        conexoes: Object.keys(conexoes).map(function (id) { return Object.assign({}, conexoes[id].dado); })
      };
    }

    function mudou(motivo) {
      emitir("mudou", { motivo: motivo, dados: dados() });
    }

    function avisar(texto) {
      aviso.textContent = texto;
    }

    function novoId(prefixo, mapa) {
      var n = Object.keys(mapa).length + 1;
      while (mapa[prefixo + n]) n++;
      return prefixo + n;
    }

    function nomeNo(dado) {
      var tipo = TIPOS[dado.tipo];
      return (tipo && tipo.rotulo ? tipo.rotulo + ": " : "") + (dado.titulo || dado.id);
    }

    /* Vista: posição e zoom */
    function aplicarVista() {
      palco.style.transform = "translate(" + vista.x + "px, " + vista.y + "px) scale(" + vista.z + ")";
      quadro.style.setProperty("--cnt-fluxo-x", vista.x + "px");
      quadro.style.setProperty("--cnt-fluxo-y", vista.y + "px");
      quadro.style.setProperty("--cnt-fluxo-z", vista.z);
      mostrador.textContent = Math.round(vista.z * 100) + "%";
    }

    // Zoom mantendo fixo o ponto (cx, cy) do quadro.
    function zoomEm(z, cx, cy) {
      z = limitar(z, ZOOM_MIN, ZOOM_MAX);
      vista.x = cx - (cx - vista.x) * (z / vista.z);
      vista.y = cy - (cy - vista.y) * (z / vista.z);
      vista.z = z;
      aplicarVista();
    }

    function zoom(fator) {
      zoomEm(vista.z * fator, quadro.clientWidth / 2, quadro.clientHeight / 2);
    }

    function ajustar() {
      var ids = Object.keys(nos);
      var largura = quadro.clientWidth;
      var altura = quadro.clientHeight;
      if (!ids.length || !largura || !altura) {
        vista = { x: 0, y: 0, z: 1 };
        return aplicarVista();
      }
      var x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
      ids.forEach(function (id) {
        var no = nos[id];
        x1 = Math.min(x1, no.dado.x);
        y1 = Math.min(y1, no.dado.y);
        x2 = Math.max(x2, no.dado.x + no.el.offsetWidth);
        y2 = Math.max(y2, no.dado.y + no.el.offsetHeight);
      });
      var margem = 48;
      var z = limitar(Math.min((largura - margem * 2) / (x2 - x1), (altura - margem * 2) / (y2 - y1), 1), ZOOM_MIN, ZOOM_MAX);
      vista.z = z;
      vista.x = (largura - (x2 - x1) * z) / 2 - x1 * z;
      vista.y = (altura - (y2 - y1) * z) / 2 - y1 * z;
      aplicarVista();
    }

    // Ponteiro (coordenadas da janela) -> coordenadas do palco.
    function ponto(evento) {
      var caixa = quadro.getBoundingClientRect();
      return { x: (evento.clientX - caixa.left - vista.x) / vista.z, y: (evento.clientY - caixa.top - vista.y) / vista.z };
    }

    /* Traçado: sempre da saída da origem para a entrada do destino.
       É esse sentido que as animações do CSS seguem, em qualquer posição dos nós. */
    function saida(no) {
      return vertical
        ? [no.dado.x + no.el.offsetWidth / 2, no.dado.y + no.el.offsetHeight]
        : [no.dado.x + no.el.offsetWidth, no.dado.y + no.el.offsetHeight / 2];
    }

    function entrada(no) {
      return vertical
        ? [no.dado.x + no.el.offsetWidth / 2, no.dado.y - RECUO_SETA]
        : [no.dado.x - RECUO_SETA, no.dado.y + no.el.offsetHeight / 2];
    }

    function curva(a, b) {
      // Limitada para a curva não abrir demais quando o destino está longe ou atrás da origem.
      var folga = limitar(Math.abs(vertical ? b[1] - a[1] : b[0] - a[0]) / 2, 40, 160);
      // Destino atrás da origem: a curva desvia para o lado, em vez de passar por trás dos nós.
      var desvio = (vertical ? b[1] < a[1] : b[0] < a[0]) ? 120 : 0;
      var c1 = vertical ? [a[0] + desvio, a[1] + folga] : [a[0] + folga, a[1] + desvio];
      var c2 = vertical ? [b[0] + desvio, b[1] - folga] : [b[0] - folga, b[1] + desvio];
      return "M" + a[0] + " " + a[1] + " C" + c1[0] + " " + c1[1] + " " + c2[0] + " " + c2[1] + " " + b[0] + " " + b[1];
    }

    function desenhar(conexao) {
      var d = curva(saida(nos[conexao.dado.de]), entrada(nos[conexao.dado.para]));
      conexao.alvo.setAttribute("d", d);
      conexao.linha.setAttribute("d", d);
      if (conexao.pulso) conexao.pulso.setAttribute("d", d);
      if (conexao.rotulo) {
        var meio = conexao.linha.getPointAtLength(conexao.linha.getTotalLength() / 2);
        conexao.rotulo.style.left = meio.x + "px";
        conexao.rotulo.style.top = meio.y + "px";
      }
    }

    function desenharDoNo(id) {
      Object.keys(conexoes).forEach(function (chave) {
        var conexao = conexoes[chave];
        if (conexao.dado.de === id || conexao.dado.para === id) desenhar(conexao);
      });
    }

    /* Seleção */
    function marcar(sim) {
      if (!selecao) return;
      if (selecao.tipo === "no" && nos[selecao.id]) {
        nos[selecao.id].el.classList.toggle("is-selecionado", sim);
      } else if (selecao.tipo === "conexao" && conexoes[selecao.id]) {
        conexoes[selecao.id].grupo.classList.toggle("is-selecionado", sim);
        conexoes[selecao.id].linha.setAttribute("marker-end", "url(#" + uid + (sim ? "-seta-sel" : "-seta") + ")");
      }
    }

    function selecionar(tipo, id) {
      if (selecao ? selecao.tipo === tipo && selecao.id === id : !tipo) return;
      marcar(false);
      selecao = tipo ? { tipo: tipo, id: id } : null;
      marcar(true);
      emitir("selecionou", selecao || { tipo: null, id: null });
    }

    /* Nós */
    function moverNo(no, x, y) {
      no.dado.x = Math.round(x);
      no.dado.y = Math.round(y);
      no.el.style.left = no.dado.x + "px";
      no.el.style.top = no.dado.y + "px";
      desenharDoNo(no.dado.id);
    }

    function adicionarNo(origem, silencioso) {
      origem = origem || {};
      var dado = Object.assign({}, origem);
      dado.id = origem.id == null ? novoId("no", nos) : String(origem.id);
      if (nos[dado.id]) return null;
      dado.x = Number(origem.x) || 0;
      dado.y = Number(origem.y) || 0;

      var tipo = TIPOS[dado.tipo];
      var item = el("div", "cnt-fluxo__no" + (tipo ? " cnt-fluxo__no--" + dado.tipo : ""));
      item.setAttribute("data-id", dado.id);
      item.setAttribute("role", "group");
      item.setAttribute("aria-label", nomeNo(dado) + (ESTADOS[dado.estado] ? ", " + ESTADOS[dado.estado] : ""));
      item.tabIndex = 0;
      item.style.left = dado.x + "px";
      item.style.top = dado.y + "px";

      item.appendChild(el("span", "cnt-fluxo__porta cnt-fluxo__porta--entrada"));

      var marca = el("span", "cnt-fluxo__icone");
      marca.innerHTML = dado.icone || (tipo ? tipo.icone : "");
      if (marca.innerHTML) item.appendChild(marca);

      var texto = el("span", "cnt-fluxo__texto");
      if (tipo && tipo.rotulo) texto.appendChild(el("span", "cnt-fluxo__tipo", tipo.rotulo));
      texto.appendChild(el("span", "cnt-fluxo__titulo", dado.titulo || ""));
      if (dado.descricao) texto.appendChild(el("span", "cnt-fluxo__descricao", dado.descricao));
      if (ESTADOS[dado.estado]) texto.appendChild(el("span", "cnt-fluxo__estado cnt-fluxo__estado--" + dado.estado, ESTADOS[dado.estado]));
      item.appendChild(texto);

      var porta;
      if (leitura) {
        porta = el("span", "cnt-fluxo__porta cnt-fluxo__porta--saida");
      } else {
        porta = el("button", "cnt-fluxo__porta cnt-fluxo__porta--saida cnt-fluxo__botao-porta");
        porta.type = "button";
        porta.setAttribute("aria-label", "Conectar a partir de " + nomeNo(dado));
      }
      item.appendChild(porta);

      palco.appendChild(item);
      nos[dado.id] = { dado: dado, el: item };
      if (!silencioso) mudou("no-adicionado");
      return dado.id;
    }

    function removerNo(id) {
      var no = nos[id];
      if (!no) return false;
      Object.keys(conexoes).forEach(function (chave) {
        if (conexoes[chave].dado.de === id || conexoes[chave].dado.para === id) desconectar(chave, true);
      });
      if (selecao && selecao.tipo === "no" && selecao.id === id) selecionar(null);
      if (ligando === id) pararDeLigar();
      no.el.remove();
      delete nos[id];
      mudou("no-removido");
      return true;
    }

    /* Conexões */
    function conectar(de, para, extra, silencioso) {
      de = String(de);
      para = String(para);
      extra = extra || {};
      if (!nos[de] || !nos[para] || de === para) return null;
      var repetida = Object.keys(conexoes).some(function (chave) {
        return conexoes[chave].dado.de === de && conexoes[chave].dado.para === para;
      });
      if (repetida) return null;

      var dado = Object.assign({}, extra);
      dado.id = extra.id == null ? novoId("c", conexoes) : String(extra.id);
      if (conexoes[dado.id]) return null;
      dado.de = de;
      dado.para = para;
      dado.tipo = extra.tipo === "pontilhada" || extra.tipo === "solida" ? extra.tipo : tipoPadrao;

      var grupo = svg("g", "cnt-fluxo__conexao");
      grupo.setAttribute("data-id", dado.id);
      var alvo = svg("path", "cnt-fluxo__alvo");
      alvo.setAttribute("tabindex", "0");
      alvo.setAttribute("role", "img");
      alvo.setAttribute("aria-label", "Conexão de " + nomeNo(nos[de].dado) + " para " + nomeNo(nos[para].dado) + (dado.rotulo ? ": " + dado.rotulo : ""));
      var linha = svg("path", "cnt-fluxo__linha" + (dado.tipo === "pontilhada" ? " cnt-fluxo__linha--pontilhada" : ""));
      linha.setAttribute("marker-end", "url(#" + uid + "-seta)");
      grupo.appendChild(alvo);
      grupo.appendChild(linha);
      var pulso = null;
      if (dado.tipo === "solida") {
        pulso = svg("path", "cnt-fluxo__pulso");
        grupo.appendChild(pulso);
      }
      linhas.appendChild(grupo);

      var rotulo = null;
      if (dado.rotulo) {
        rotulo = el("span", "cnt-fluxo__rotulo", dado.rotulo);
        rotulo.setAttribute("aria-hidden", "true");
        palco.insertBefore(rotulo, linhas.nextSibling); // acima das linhas, abaixo dos nós
      }

      conexoes[dado.id] = { dado: dado, grupo: grupo, alvo: alvo, linha: linha, pulso: pulso, rotulo: rotulo };
      desenhar(conexoes[dado.id]);
      if (!silencioso) mudou("conectou");
      return dado.id;
    }

    function desconectar(id, silencioso) {
      var conexao = conexoes[id];
      if (!conexao) return false;
      if (selecao && selecao.tipo === "conexao" && selecao.id === id) selecionar(null);
      conexao.grupo.remove();
      if (conexao.rotulo) conexao.rotulo.remove();
      delete conexoes[id];
      if (!silencioso) mudou("desconectou");
      return true;
    }

    function carregar(novos) {
      novos = novos || {};
      selecionar(null);
      pararDeLigar();
      Object.keys(conexoes).forEach(function (id) { desconectar(id, true); });
      Object.keys(nos).forEach(function (id) { nos[id].el.remove(); delete nos[id]; });
      (novos.nos || []).forEach(function (no) { adicionarNo(no, true); });
      (novos.conexoes || []).forEach(function (c) { conectar(c.de, c.para, c, true); });
    }

    /* Conexão por teclado: Enter na porta de saída, depois Enter no nó de destino. */
    function pararDeLigar() {
      ligando = null;
      raiz.classList.remove("is-ligando");
    }

    function ligarPara(id) {
      var de = ligando;
      pararDeLigar();
      if (conectar(de, id)) avisar("Conectado: " + nomeNo(nos[de].dado) + " para " + nomeNo(nos[id].dado) + ".");
      else avisar("Essa conexão não é possível ou já existe.");
    }

    /* Ponteiro: mover o quadro, arrastar nó, puxar conexão */
    function aoPressionar(evento) {
      if (evento.button !== 0) return;
      var item = evento.target.closest(".cnt-fluxo__no");
      var porta = evento.target.closest(".cnt-fluxo__botao-porta");
      var alvo = evento.target.closest(".cnt-fluxo__alvo");

      if (porta) {
        arrasto = { modo: "ligar", de: item.getAttribute("data-id") };
      } else if (item) {
        var no = nos[item.getAttribute("data-id")];
        selecionar("no", no.dado.id);
        if (leitura) return;
        var p = ponto(evento);
        arrasto = { modo: "no", no: no, dx: p.x - no.dado.x, dy: p.y - no.dado.y, moveu: false };
      } else if (alvo) {
        selecionar("conexao", alvo.parentNode.getAttribute("data-id"));
        return;
      } else {
        selecionar(null);
        arrasto = { modo: "mover", x: evento.clientX - vista.x, y: evento.clientY - vista.y };
        quadro.classList.add("is-movendo");
      }
      // Evento sintético (teste automatizado) não tem ponteiro ativo para capturar.
      try { quadro.setPointerCapture(evento.pointerId); } catch (erro) { /* segue sem captura */ }
    }

    function aoMover(evento) {
      if (!arrasto) return;
      if (arrasto.modo === "mover") {
        vista.x = evento.clientX - arrasto.x;
        vista.y = evento.clientY - arrasto.y;
        aplicarVista();
        return;
      }
      var p = ponto(evento);
      if (arrasto.modo === "no") {
        arrasto.moveu = true;
        moverNo(arrasto.no, p.x - arrasto.dx, p.y - arrasto.dy);
      } else {
        provisoria.style.display = "";
        provisoria.setAttribute("d", curva(saida(nos[arrasto.de]), [p.x, p.y]));
      }
    }

    function aoSoltar(evento) {
      if (!arrasto) return;
      var feito = arrasto;
      arrasto = null;
      quadro.classList.remove("is-movendo");
      provisoria.style.display = "none";
      if (quadro.hasPointerCapture(evento.pointerId)) quadro.releasePointerCapture(evento.pointerId);
      if (evento.type === "pointercancel") return;

      if (feito.modo === "no" && feito.moveu) {
        mudou("moveu");
      } else if (feito.modo === "ligar") {
        var embaixo = document.elementFromPoint(evento.clientX, evento.clientY);
        var destino = embaixo && embaixo.closest(".cnt-fluxo__no");
        if (destino && palco.contains(destino)) conectar(feito.de, destino.getAttribute("data-id"));
      }
    }

    function aoRolar(evento) {
      evento.preventDefault();
      if (evento.ctrlKey || evento.metaKey) {
        // Ctrl + roda (ou pinça no touchpad): zoom no ponto do cursor.
        var caixa = quadro.getBoundingClientRect();
        zoomEm(vista.z * Math.exp(-evento.deltaY * 0.0015), evento.clientX - caixa.left, evento.clientY - caixa.top);
      } else {
        vista.x -= evento.deltaX;
        vista.y -= evento.deltaY;
        aplicarVista();
      }
    }

    // Clique vindo do teclado (Enter ou espaço) na porta de saída. Com o mouse, a porta é arrastada.
    function aoClicar(evento) {
      var porta = evento.target.closest(".cnt-fluxo__botao-porta");
      if (!porta || evento.detail !== 0) return;
      ligando = porta.parentNode.getAttribute("data-id");
      raiz.classList.add("is-ligando");
      avisar("Vá até o nó de destino e aperte Enter. Esc cancela.");
    }

    quadro.addEventListener("pointerdown", aoPressionar);
    quadro.addEventListener("pointermove", aoMover);
    quadro.addEventListener("pointerup", aoSoltar);
    quadro.addEventListener("pointercancel", aoSoltar);
    quadro.addEventListener("wheel", aoRolar, { passive: false });
    quadro.addEventListener("click", aoClicar);

    /* Teclado */
    var SETAS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

    function aoTeclar(evento) {
      if (evento.key === "Escape" && ligando) {
        pararDeLigar();
        avisar("Conexão cancelada.");
        return;
      }
      var alvo = evento.target;
      var apagar = evento.key === "Delete" || evento.key === "Backspace";

      if (alvo.classList.contains("cnt-fluxo__no")) {
        var no = nos[alvo.getAttribute("data-id")];
        if (evento.key === "Enter" && ligando && ligando !== no.dado.id) {
          evento.preventDefault();
          ligarPara(no.dado.id);
        } else if (leitura) {
          return;
        } else if (SETAS[evento.key]) {
          evento.preventDefault();
          var passo = evento.shiftKey ? 1 : 16;
          moverNo(no, no.dado.x + SETAS[evento.key][0] * passo, no.dado.y + SETAS[evento.key][1] * passo);
          mudou("moveu");
        } else if (apagar) {
          evento.preventDefault();
          avisar(nomeNo(no.dado) + " removido.");
          removerNo(no.dado.id);
        }
      } else if (alvo.classList.contains("cnt-fluxo__alvo") && apagar && !leitura) {
        evento.preventDefault();
        avisar("Conexão removida.");
        desconectar(alvo.parentNode.getAttribute("data-id"));
      }
    }

    function aoFocar(evento) {
      var alvo = evento.target;
      if (alvo.classList.contains("cnt-fluxo__no")) selecionar("no", alvo.getAttribute("data-id"));
      else if (alvo.classList.contains("cnt-fluxo__alvo")) selecionar("conexao", alvo.parentNode.getAttribute("data-id"));
    }

    raiz.addEventListener("keydown", aoTeclar);
    raiz.addEventListener("focusin", aoFocar);

    function destruir() {
      pararDeLigar();
      raiz.removeEventListener("keydown", aoTeclar);
      raiz.removeEventListener("focusin", aoFocar);
      raiz.textContent = "";
      delete raiz.cntFluxo;
    }

    carregar(opcoes);
    ajustar();

    raiz.cntFluxo = {
      dados: dados,
      carregar: function (novos) { carregar(novos); ajustar(); },
      adicionarNo: function (no) { return adicionarNo(no); },
      removerNo: removerNo,
      conectar: function (de, para, extra) { return conectar(de, para, extra); },
      desconectar: function (id) { return desconectar(id); },
      ajustar: ajustar,
      zoom: zoom,
      destruir: destruir
    };
    return raiz.cntFluxo;
  }

  // Marcação vinda do servidor: opções em data-*, dados em <script type="application/json">.
  function iniciarTodos() {
    document.querySelectorAll("[data-cnt-fluxo]").forEach(function (raiz) {
      if (raiz.cntFluxo) return;
      var fonte = raiz.querySelector('script[type="application/json"]');
      var opcoes = {};
      try {
        if (fonte) opcoes = JSON.parse(fonte.textContent) || {};
      } catch (erro) {
        console.error("cntFluxo: JSON inválido em", raiz, erro);
      }
      if (raiz.hasAttribute("data-cnt-fluxo-direcao")) opcoes.direcao = raiz.getAttribute("data-cnt-fluxo-direcao");
      if (raiz.hasAttribute("data-cnt-fluxo-somente-leitura")) opcoes.somenteLeitura = true;
      if (raiz.hasAttribute("data-cnt-fluxo-tipo-conexao")) opcoes.tipoConexao = raiz.getAttribute("data-cnt-fluxo-tipo-conexao");
      if (raiz.hasAttribute("aria-label")) opcoes.rotulo = raiz.getAttribute("aria-label");
      criar(raiz, opcoes);
    });
  }

  window.cntFluxo = { criar: criar, iniciarTodos: iniciarTodos };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciarTodos);
  else iniciarTodos();
})();
