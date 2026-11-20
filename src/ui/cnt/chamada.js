/* Fluxo de chamadas. Script clássico, sem dependência.

   var chamada = cntChamada.criar(elemento, {
     titulo: "Chamada 2001 → 5511988887777",
     subtitulo: "Capturada em 18/11 às 14:32",
     resumo: [{ rotulo: "Duração", valor: "00:42" }],
     participantes: [{ id, nome, detalhe, tipo: "telefone" | "servidor" | "nuvem" | "usuario", tom, icone, campos, secoes }],
     mensagens: [{ id, de, para, rotulo, titulo, resumo, tempo, intervalo, tom, tipo: "solida" | "pontilhada" | "rtp",
                   requisicao: true | false, portas: [origem, destino],
                   campos: [{ rotulo, valor, mono }], secoes: [{ titulo, mono, campos: [{ rotulo, valor }] }], bruto }],
     detalhe: true,                // clique abre o painel de detalhes; false = só seleciona
     rotulo: "Fluxo de chamadas"   // nome do diagrama para leitor de tela
   });

   A ordem das mensagens na lista é a ordem de cima para baixo; a dos participantes, da esquerda para a direita.
   tom: "primaria" | "sucesso" | "alerta" | "perigo" | "info" | "neutro".
   Cores no padrão do sngrep: sem tom, requisicao: true fica vermelha (perigo) e requisicao: false verde (sucesso);
   tipo "rtp" é sempre uma seta tracejada em tom neutro, com as portas nas pontas (portas) e o rótulo "RTP (PCMU) 1980".
   Inicia sozinho em [data-cnt-chamada], com os dados em <script type="application/json"> dentro do elemento.
   Métodos: dados(), carregar(dados), adicionarMensagem(mensagem), abrir(id), abrirParticipante(id), fechar(),
            reproduzir(), parar(), mostrarRtp(ligado), exportar("png" | "pdf"), relatorio(), destruir().
   exportar("png") baixa o diagrama como imagem (cores do tema atual); exportar("pdf") abre a impressão de um relatório
   (diagrama + detalhes de cada mensagem) para salvar como PDF; relatorio() devolve o HTML desse relatório.
   Eventos (sobem na árvore): cnt:chamada:selecionou { tipo, id }, cnt:chamada:abriu { tipo, id },
            cnt:chamada:fechou { tipo, id },
            cnt:chamada:exportou { formato }, cnt:chamada:mudou { motivo, dados }.

   Todo texto entra como texto puro. icone é SVG escrito pelo desenvolvedor (entra sem escape): nunca passe dado de usuário. */
(function () {
  var PASSO = 650; // ms entre uma mensagem e a seguinte na reprodução
  var TONS = { primaria: 1, sucesso: 1, alerta: 1, perigo: 1, info: 1, neutro: 1 };
  var TOM_DO_TIPO = { telefone: "primaria", usuario: "primaria", servidor: "info" };
  var instancias = 0;

  function icone(desenho) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + desenho + "</svg>";
  }

  var ICONES = {
    telefone: icone('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 005 5L15 13l5 2v4a2 2 0 01-2 2A16 16 0 013 6a2 2 0 012-2z"/>'),
    servidor: icone('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
    usuario: icone('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
    nuvem: icone('<path d="M7 18a4 4 0 01-.5-8A6 6 0 0118 11a3.5 3.5 0 01-.5 7z"/>'),
    padrao: icone('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/>'),
    play: icone('<path d="M7 4l12 8-12 8z"/>'),
    parar: icone('<rect x="6" y="6" width="12" height="12" rx="2"/>'),
    fechar: icone('<path d="M6 6l12 12M18 6L6 18"/>'),
    acima: icone('<path d="M6 15l6-6 6 6"/>'),
    abaixo: icone('<path d="M6 9l6 6 6-6"/>'),
    copiar: icone('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/>'),
    seta: icone('<path d="M5 12h14M13 6l6 6-6 6"/>'),
    imagem: icone('<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M21 16l-5-5-8 8"/>'),
    pdf: icone('<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 14h6M10 17.5h4"/>'),
    rtp: icone('<path d="M3 12h3M9 12h3M15 12h3M21 12h0"/><path d="M16 7l5 5-5 5"/>')
  };

  function el(tag, classe, texto) {
    var item = document.createElement(tag);
    if (classe) item.className = classe;
    if (texto != null) item.textContent = texto;
    return item;
  }

  function tomDe(valor, padrao) {
    return TONS[valor] ? valor : padrao;
  }

  function achar(lista, id) {
    for (var i = 0; i < lista.length; i++) if (lista[i].id === id) return lista[i];
    return null;
  }

  function indiceDe(lista, id) {
    for (var i = 0; i < lista.length; i++) if (lista[i].id === id) return i;
    return -1;
  }

  // Padrão do sngrep: requisição vermelha, resposta verde, RTP neutro e tracejado.
  function tomDaMensagem(m) {
    if (m.tipo === "rtp") return "neutro";
    if (TONS[m.tom]) return m.tom;
    if (m.requisicao === true) return "perigo";
    if (m.requisicao === false) return "sucesso";
    return "primaria";
  }

  function esc(texto) {
    return String(texto == null ? "" : texto).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function temValor(valor) {
    return valor != null && String(valor) !== "";
  }

  function normalizarParticipantes(lista) {
    var saida = [];
    (lista || []).forEach(function (item) {
      if (!item) return;
      var dado = Object.assign({}, item);
      dado.id = item.id == null ? "p" + (saida.length + 1) : String(item.id);
      if (!achar(saida, dado.id)) saida.push(dado);
    });
    return saida;
  }

  // Mensagem cujo origem ou destino não existe entre os participantes é descartada.
  function normalizarMensagens(lista, partes) {
    var saida = [];
    (lista || []).forEach(function (item) {
      if (!item) return;
      var dado = Object.assign({}, item);
      dado.de = String(item.de);
      dado.para = String(item.para);
      if (!achar(partes, dado.de) || !achar(partes, dado.para)) return;
      var n = saida.length + 1;
      dado.id = item.id == null ? "m" + n : String(item.id);
      while (item.id == null && achar(saida, dado.id)) dado.id = "m" + (++n);
      if (!achar(saida, dado.id)) saida.push(dado);
    });
    return saida;
  }

  function criar(raiz, opcoes) {
    if (raiz.cntChamada) return raiz.cntChamada;
    opcoes = opcoes || {};

    var detalhe = opcoes.detalhe !== false;
    var partes = normalizarParticipantes(opcoes.participantes);
    var msgs = normalizarMensagens(opcoes.mensagens, partes);
    var cabecalho = { titulo: opcoes.titulo, subtitulo: opcoes.subtitulo, resumo: opcoes.resumo };
    var selecao = null; // { tipo: "mensagem" | "participante", id }
    var aberto = false; // painel de detalhes visível
    var tocando = false;
    var timer = null;
    var botaoPlay = null;
    var comRtp = true; // setas RTP visíveis
    var idUnico = "cnt-chamada-" + (++instancias);

    /* Estrutura */
    raiz.textContent = "";
    raiz.classList.add("cnt-chamada");

    var principal = el("div", "cnt-chamada__principal");
    var topo = el("header", "cnt-chamada__topo");
    var rolagem = el("div", "cnt-chamada__rolagem");
    var diagrama = el("div", "cnt-chamada__diagrama");
    diagrama.setAttribute("role", "group");
    diagrama.setAttribute("aria-label", opcoes.rotulo || "Fluxo de chamadas");
    var colunas = el("div", "cnt-chamada__participantes");
    var corpo = el("div", "cnt-chamada__corpo");
    var vidas = el("div", "cnt-chamada__vidas");
    vidas.setAttribute("aria-hidden", "true");
    var lista = el("ol", "cnt-chamada__lista");
    corpo.appendChild(vidas);
    corpo.appendChild(lista);
    diagrama.appendChild(colunas);
    diagrama.appendChild(corpo);
    rolagem.appendChild(diagrama);
    principal.appendChild(topo);
    principal.appendChild(rolagem);

    var painel = el("aside", "cnt-chamada__painel");
    painel.id = idUnico + "-detalhes";
    painel.hidden = true;
    painel.tabIndex = -1;
    painel.setAttribute("aria-label", "Detalhes");

    var aviso = el("p", "cnt-chamada__aviso");
    aviso.setAttribute("aria-live", "polite");

    raiz.appendChild(principal);
    raiz.appendChild(painel);
    raiz.appendChild(aviso);

    /* Utilidades */
    function emitir(acao, dados) {
      raiz.dispatchEvent(new CustomEvent("cnt:chamada:" + acao, { bubbles: true, detail: dados }));
    }

    function avisar(texto) {
      aviso.textContent = texto;
    }

    function dados() {
      return {
        titulo: cabecalho.titulo,
        subtitulo: cabecalho.subtitulo,
        resumo: cabecalho.resumo,
        participantes: partes.map(function (p) { return Object.assign({}, p); }),
        mensagens: msgs.map(function (m) { return Object.assign({}, m); })
      };
    }

    function nomeDe(id) {
      var p = achar(partes, id);
      return p ? (p.nome || p.id) : id;
    }

    function tomDoParticipante(p) {
      return tomDe(p.tom, TOM_DO_TIPO[p.tipo] || "neutro");
    }

    function avatar(p) {
      var item = el("span", "cnt-chamada__avatar");
      item.setAttribute("aria-hidden", "true");
      item.innerHTML = p.icone || ICONES[p.tipo] || ICONES.padrao;
      return item;
    }

    function rotuloDe(m, i) {
      return m.rotulo || m.titulo || "Mensagem " + (i + 1);
    }

    /* Topo */
    function atualizarBotao() {
      botaoPlay.innerHTML = ICONES[tocando ? "parar" : "play"] + "<span>" + (tocando ? "Parar" : "Reproduzir") + "</span>";
    }

    function botaoTexto(nome, rotulo, acao) {
      var item = el("button", "cnt-chamada__botao");
      item.type = "button";
      item.innerHTML = ICONES[nome] + "<span>" + rotulo + "</span>";
      item.addEventListener("click", acao);
      return item;
    }

    function desenharTopo() {
      topo.textContent = "";
      var titulos = el("div", "cnt-chamada__titulos");
      if (temValor(cabecalho.titulo)) titulos.appendChild(el("p", "cnt-chamada__titulo", cabecalho.titulo));
      if (temValor(cabecalho.subtitulo)) titulos.appendChild(el("p", "cnt-chamada__subtitulo", cabecalho.subtitulo));
      var legendas = [];
      if (msgs.some(function (m) { return m.requisicao === true; })) legendas.push(["cnt-chamada__tom--perigo", "Requisição"]);
      if (msgs.some(function (m) { return m.requisicao === false; })) legendas.push(["cnt-chamada__tom--sucesso", "Resposta"]);
      if (msgs.some(function (m) { return m.tipo === "rtp"; })) legendas.push(["cnt-chamada__msg--rtp", "RTP"]);
      if (legendas.length) {
        var cores = el("ul", "cnt-chamada__legenda-cores");
        legendas.forEach(function (item) { cores.appendChild(el("li", item[0], item[1])); });
        titulos.appendChild(cores);
      }
      topo.appendChild(titulos);

      var itens = (cabecalho.resumo || []).filter(function (item) { return item && temValor(item.valor); });
      if (itens.length) {
        var resumo = el("dl", "cnt-chamada__resumo");
        itens.forEach(function (item) {
          var grupo = el("div", "cnt-chamada__resumo-item");
          grupo.appendChild(el("dt", null, item.rotulo || ""));
          grupo.appendChild(el("dd", null, item.valor));
          resumo.appendChild(grupo);
        });
        topo.appendChild(resumo);
      }

      var ferramentas = el("div", "cnt-chamada__ferramentas");
      if (msgs.some(function (m) { return m.tipo === "rtp"; })) {
        var botaoRtp = botaoTexto("rtp", "RTP", function () { mostrarRtp(!comRtp); });
        botaoRtp.setAttribute("aria-pressed", String(comRtp));
        botaoRtp.title = "Mostrar ou esconder os pacotes RTP";
        ferramentas.appendChild(botaoRtp);
      }
      var botaoImagem = botaoTexto("imagem", "Imagem", function () { exportar("png"); });
      botaoImagem.title = "Baixar o diagrama como imagem (PNG)";
      var botaoPdf = botaoTexto("pdf", "PDF", function () { exportar("pdf"); });
      botaoPdf.title = "Abrir o relatório para salvar como PDF";
      botaoImagem.disabled = botaoPdf.disabled = !msgs.length;
      ferramentas.appendChild(botaoImagem);
      ferramentas.appendChild(botaoPdf);
      topo.appendChild(ferramentas);

      botaoPlay = el("button", "cnt-chamada__botao cnt-chamada__botao--primario");
      botaoPlay.type = "button";
      botaoPlay.disabled = !msgs.length;
      botaoPlay.addEventListener("click", function () { if (tocando) parar(); else reproduzir(); });
      atualizarBotao();
      topo.appendChild(botaoPlay);
    }

    /* Diagrama */
    function itemMensagem(m, i, animar) {
      var a = indiceDe(partes, m.de);
      var b = indiceDe(partes, m.para);
      var propria = a === b;
      var primeira = Math.min(a, b);
      var span = Math.abs(a - b) + 1;
      var rtp = m.tipo === "rtp";
      var pontilhada = m.tipo === "pontilhada" || rtp;

      var li = el("li", "cnt-chamada__item" + (rtp ? " cnt-chamada__item--rtp" : "") + (animar ? " is-chegando" : ""));
      li.setAttribute("data-id", m.id);
      li.style.setProperty("--cnt-chamada-i", String(i));

      var tempo = el("div", "cnt-chamada__tempo");
      if (temValor(m.tempo)) tempo.appendChild(el("span", "cnt-chamada__hora", m.tempo));
      if (temValor(m.intervalo)) tempo.appendChild(el("span", "cnt-chamada__intervalo", m.intervalo));
      li.appendChild(tempo);

      var classe = "cnt-chamada__msg cnt-chamada__tom--" + tomDaMensagem(m) +
        (propria ? " cnt-chamada__msg--propria" : a < b ? " cnt-chamada__msg--direita" : " cnt-chamada__msg--esquerda") +
        (pontilhada ? " cnt-chamada__msg--pontilhada" : "") + (rtp ? " cnt-chamada__msg--rtp" : "");
      var botao = el("button", classe);
      botao.type = "button";
      botao.setAttribute("data-id", m.id);
      botao.style.gridColumn = (primeira + 2) + " / span " + span;
      botao.style.setProperty("--cnt-chamada-span", String(span));
      botao.style.setProperty("--cnt-chamada-n", String(i));
      botao.setAttribute("aria-label", (i + 1) + ". " + rotuloDe(m, i) + (propria ? ", em " + nomeDe(m.de) : ", de " + nomeDe(m.de) + " para " + nomeDe(m.para)) +
        (temValor(m.tempo) ? ", " + m.tempo : "") + (temValor(m.resumo) ? ". " + m.resumo : ""));

      botao.appendChild(el("span", "cnt-chamada__rotulo", rotuloDe(m, i)));
      var linha = el("span", "cnt-chamada__linha");
      linha.appendChild(el("span", "cnt-chamada__pulso"));
      if (rtp && !propria && m.portas && m.portas.length === 2) {
        linha.appendChild(el("span", "cnt-chamada__porta cnt-chamada__porta--" + (a < b ? "origem" : "destino"), String(m.portas[0])));
        linha.appendChild(el("span", "cnt-chamada__porta cnt-chamada__porta--" + (a < b ? "destino" : "origem"), String(m.portas[1])));
      }
      botao.appendChild(linha);
      if (temValor(m.resumo)) botao.appendChild(el("span", "cnt-chamada__legenda", m.resumo));
      li.appendChild(botao);
      return li;
    }

    function desenhar(animar) {
      parar();
      diagrama.style.setProperty("--cnt-chamada-colunas", String(Math.max(partes.length, 1)));
      raiz.classList.toggle("is-sem-rtp", !comRtp);
      desenharTopo();

      colunas.textContent = "";
      vidas.textContent = "";
      lista.textContent = "";
      corpo.querySelectorAll(".cnt-chamada__vazio").forEach(function (item) { item.remove(); });
      colunas.appendChild(el("div", "cnt-chamada__canto", "Tempo"));
      vidas.appendChild(el("div"));

      partes.forEach(function (p) {
        var coluna = el("div", "cnt-chamada__coluna");
        var botao = el("button", "cnt-chamada__participante cnt-chamada__tom--" + tomDoParticipante(p));
        botao.type = "button";
        botao.setAttribute("data-id", p.id);
        botao.appendChild(avatar(p));
        botao.appendChild(el("span", "cnt-chamada__nome", p.nome || p.id));
        if (temValor(p.detalhe)) botao.appendChild(el("span", "cnt-chamada__endereco", p.detalhe));
        coluna.appendChild(botao);
        colunas.appendChild(coluna);

        var vida = el("div", "cnt-chamada__vida");
        vida.setAttribute("data-id", p.id);
        vidas.appendChild(vida);
      });

      msgs.forEach(function (m, i) { lista.appendChild(itemMensagem(m, i, animar)); });
      if (!msgs.length) corpo.appendChild(el("p", "cnt-chamada__vazio", "Nenhuma mensagem para mostrar."));
      marcar();
      atualizarPainel();
    }

    // Estado visual da seleção: faixa na linha, linhas de vida e cartões dos participantes envolvidos.
    function marcar() {
      var mensagemId = selecao && selecao.tipo === "mensagem" ? selecao.id : null;
      var alvoTab = mensagemId || (msgs[0] && msgs[0].id);
      lista.querySelectorAll(".cnt-chamada__item").forEach(function (li) {
        var id = li.getAttribute("data-id");
        var botao = li.querySelector(".cnt-chamada__msg");
        li.classList.toggle("is-selecionado", id === mensagemId);
        if (id === mensagemId) botao.setAttribute("aria-current", "true");
        else botao.removeAttribute("aria-current");
        botao.tabIndex = id === alvoTab ? 0 : -1;
      });

      var envolvidos = {};
      if (mensagemId) {
        var m = achar(msgs, mensagemId);
        envolvidos[m.de] = true;
        envolvidos[m.para] = true;
      } else if (selecao) {
        envolvidos[selecao.id] = true;
      }
      colunas.querySelectorAll(".cnt-chamada__participante").forEach(function (botao) {
        var id = botao.getAttribute("data-id");
        var escolhido = !!selecao && selecao.tipo === "participante" && selecao.id === id;
        botao.classList.toggle("is-selecionado", escolhido);
        botao.classList.toggle("is-envolvido", !!envolvidos[id]);
        if (escolhido) botao.setAttribute("aria-current", "true");
        else botao.removeAttribute("aria-current");
      });
      vidas.querySelectorAll(".cnt-chamada__vida").forEach(function (vida) {
        vida.classList.toggle("is-envolvida", !!envolvidos[vida.getAttribute("data-id")]);
      });
    }

    /* Painel de detalhes */
    function botaoIcone(nome, rotulo, acao, desativado) {
      var item = el("button", "cnt-chamada__botao cnt-chamada__botao--icone");
      item.type = "button";
      item.setAttribute("aria-label", rotulo);
      item.innerHTML = ICONES[nome];
      item.disabled = !!desativado;
      item.addEventListener("click", acao);
      return item;
    }

    function topoPainel(tipo, indice) {
      var cima = el("header", "cnt-chamada__painel-topo");
      cima.appendChild(el("span", "cnt-chamada__painel-tipo", tipo));
      if (indice != null) {
        cima.appendChild(botaoIcone("acima", "Mensagem anterior", function () { irPara(indice - 1); }, indice === 0));
        cima.appendChild(botaoIcone("abaixo", "Próxima mensagem", function () { irPara(indice + 1); }, indice === msgs.length - 1));
      }
      cima.appendChild(botaoIcone("fechar", "Fechar detalhes", function () { fechar(true); }));
      return cima;
    }

    function irPara(indice) {
      if (indice < 0 || indice >= msgs.length) return;
      selecionar("mensagem", msgs[indice].id, true);
      var botao = lista.querySelector('.cnt-chamada__msg[data-id="' + msgs[indice].id.replace(/["\\]/g, "\\$&") + '"]');
      if (botao) botao.scrollIntoView({ block: "nearest", inline: "nearest" });
    }

    function grade(itens) {
      var validos = itens.filter(function (item) { return temValor(item.valor); });
      if (!validos.length) return null;
      var dl = el("dl", "cnt-chamada__fatos");
      validos.forEach(function (item) {
        var fato = el("div", "cnt-chamada__fato" + (item.mono ? " cnt-chamada__fato--mono" : ""));
        fato.appendChild(el("dt", null, item.rotulo || ""));
        fato.appendChild(el("dd", null, item.valor));
        dl.appendChild(fato);
      });
      return dl;
    }

    function secao(titulo, conteudo, extra) {
      var bloco = el("section", "cnt-chamada__secao");
      var cima = el("div", "cnt-chamada__secao-topo");
      cima.appendChild(el("h4", "cnt-chamada__secao-titulo", titulo));
      if (extra) cima.appendChild(extra);
      bloco.appendChild(cima);
      bloco.appendChild(conteudo);
      return bloco;
    }

    function secoes(itens) {
      return (itens || []).map(function (item) {
        var validos = (item.campos || []).filter(function (campo) { return campo && temValor(campo.valor); });
        if (!validos.length) return null;
        var dl = el("dl", "cnt-chamada__linhas" + (item.mono ? " cnt-chamada__linhas--mono" : ""));
        validos.forEach(function (campo) {
          var linha = el("div");
          linha.appendChild(el("dt", null, campo.rotulo || ""));
          linha.appendChild(el("dd", null, campo.valor));
          dl.appendChild(linha);
        });
        return secao(item.titulo || "", dl);
      }).filter(Boolean);
    }

    // Texto completo da mensagem: a primeira linha em negrito e o nome de cada campo (antes de ":" ou "=") em destaque.
    function textoBruto(texto) {
      var pre = el("pre", "cnt-chamada__bruto");
      String(texto).split(/\r?\n/).forEach(function (linha, i) {
        var item = el("span", "cnt-chamada__bruto-linha" + (i === 0 ? " cnt-chamada__bruto-linha--primeira" : ""));
        var campo = /^([A-Za-z][\w.-]*)(\s*[:=]\s*)(.*)$/.exec(linha);
        if (campo) {
          item.appendChild(el("span", "cnt-chamada__bruto-chave", campo[1]));
          item.appendChild(document.createTextNode(campo[2] + campo[3]));
        } else {
          item.textContent = linha || " ";
        }
        pre.appendChild(item);
      });
      return pre;
    }

    function botaoCopiar(texto) {
      if (!navigator.clipboard || !navigator.clipboard.writeText) return null;
      var item = el("button", "cnt-chamada__botao");
      item.type = "button";
      item.innerHTML = ICONES.copiar + "<span>Copiar</span>";
      var legenda = item.lastChild;
      item.addEventListener("click", function () {
        navigator.clipboard.writeText(texto).then(function () {
          legenda.textContent = "Copiado";
          avisar("Copiado.");
          setTimeout(function () { legenda.textContent = "Copiar"; }, 1600);
        }, function () {
          avisar("Não foi possível copiar.");
        });
      });
      return item;
    }

    function noDaRota(p) {
      var item = el("button", "cnt-chamada__rota-no cnt-chamada__tom--" + tomDoParticipante(p));
      item.type = "button";
      item.appendChild(avatar(p));
      item.appendChild(el("span", null, p.nome || p.id));
      item.addEventListener("click", function () { selecionar("participante", p.id, true); });
      return item;
    }

    function painelMensagem(m, indice) {
      var tom = tomDaMensagem(m);
      painel.appendChild(topoPainel("Mensagem " + (indice + 1) + " de " + msgs.length, indice));
      var conteudo = el("div", "cnt-chamada__painel-corpo");

      var titulo = el("div", "cnt-chamada__painel-titulo cnt-chamada__tom--" + tom);
      if (m.titulo && m.rotulo) titulo.appendChild(el("span", "cnt-chamada__rotulo", m.rotulo));
      titulo.appendChild(el("h3", "cnt-chamada__painel-nome", m.titulo || rotuloDe(m, indice)));
      conteudo.appendChild(titulo);

      var rota = el("div", "cnt-chamada__rota cnt-chamada__tom--" + tom);
      rota.appendChild(noDaRota(achar(partes, m.de)));
      var seta = el("span", "cnt-chamada__rota-seta");
      seta.setAttribute("aria-hidden", "true");
      seta.innerHTML = ICONES.seta;
      rota.appendChild(seta);
      if (m.de !== m.para) rota.appendChild(noDaRota(achar(partes, m.para)));
      else rota.appendChild(el("span", "cnt-chamada__painel-texto", "nele mesmo"));
      conteudo.appendChild(rota);

      if (temValor(m.resumo)) conteudo.appendChild(el("p", "cnt-chamada__painel-texto", m.resumo));

      var fatos = grade([
        { rotulo: "Horário", valor: m.tempo, mono: true },
        { rotulo: "Intervalo", valor: m.intervalo, mono: true },
        { rotulo: "Origem", valor: nomeDe(m.de) },
        { rotulo: "Destino", valor: nomeDe(m.para) },
        { rotulo: "Portas", valor: m.portas && m.portas.length === 2 ? m.portas[0] + " → " + m.portas[1] : "", mono: true }
      ].concat((m.campos || []).filter(Boolean)));
      if (fatos) conteudo.appendChild(fatos);

      secoes(m.secoes).forEach(function (bloco) { conteudo.appendChild(bloco); });
      if (temValor(m.bruto)) conteudo.appendChild(secao("Mensagem completa", textoBruto(m.bruto), botaoCopiar(String(m.bruto))));
      painel.appendChild(conteudo);
    }

    function painelParticipante(p) {
      var enviadas = msgs.filter(function (m) { return m.de === p.id; }).length;
      var recebidas = msgs.filter(function (m) { return m.para === p.id && m.de !== p.id; }).length;
      painel.appendChild(topoPainel("Participante"));
      var conteudo = el("div", "cnt-chamada__painel-corpo");

      var titulo = el("div", "cnt-chamada__painel-titulo cnt-chamada__tom--" + tomDoParticipante(p));
      titulo.appendChild(el("h3", "cnt-chamada__painel-nome", p.nome || p.id));
      if (temValor(p.detalhe)) titulo.appendChild(el("span", "cnt-chamada__endereco", p.detalhe));
      conteudo.appendChild(titulo);

      var fatos = grade([
        { rotulo: "Enviou", valor: enviadas },
        { rotulo: "Recebeu", valor: recebidas }
      ].concat((p.campos || []).filter(Boolean)));
      if (fatos) conteudo.appendChild(fatos);
      secoes(p.secoes).forEach(function (bloco) { conteudo.appendChild(bloco); });

      var dele = msgs.filter(function (m) { return m.de === p.id || m.para === p.id; });
      if (dele.length) {
        var ul = el("ul", "cnt-chamada__lista-mensagens");
        dele.forEach(function (m) {
          var li = el("li");
          var botao = el("button", "cnt-chamada__lista-item cnt-chamada__tom--" + tomDaMensagem(m));
          botao.type = "button";
          botao.appendChild(el("span", "cnt-chamada__lista-hora", temValor(m.tempo) ? m.tempo : String(indiceDe(msgs, m.id) + 1)));
          botao.appendChild(el("span", "cnt-chamada__rotulo", rotuloDe(m, indiceDe(msgs, m.id))));
          botao.appendChild(el("span", "cnt-chamada__lista-com", m.de === m.para ? "nele mesmo" : m.de === p.id ? "para " + nomeDe(m.para) : "de " + nomeDe(m.de)));
          botao.addEventListener("click", function () { selecionar("mensagem", m.id, true); });
          li.appendChild(botao);
          ul.appendChild(li);
        });
        conteudo.appendChild(secao("Mensagens", ul));
      }
      painel.appendChild(conteudo);
    }

    function atualizarPainel() {
      painel.textContent = "";
      var visivel = !!(aberto && selecao);
      painel.hidden = !visivel;
      raiz.classList.toggle("is-detalhe", visivel);
      if (!visivel) return;
      if (selecao.tipo === "mensagem") painelMensagem(achar(msgs, selecao.id), indiceDe(msgs, selecao.id));
      else painelParticipante(achar(partes, selecao.id));
    }

    /* Seleção */
    function selecionar(tipo, id, comDetalhe) {
      id = String(id);
      if (!achar(tipo === "mensagem" ? msgs : partes, id)) return false;
      var estava = aberto;
      var noPainel = painel.contains(document.activeElement);
      var mudou = !selecao || selecao.tipo !== tipo || selecao.id !== id;
      selecao = { tipo: tipo, id: id };
      if (comDetalhe && detalhe) aberto = true;
      marcar();
      atualizarPainel();
      // O painel foi redesenhado: quem navegava por ele não perde o lugar.
      if (noPainel && aberto) painel.focus({ preventScroll: true });
      if (mudou) emitir("selecionou", { tipo: tipo, id: id });
      if (aberto && !estava) {
        emitir("abriu", { tipo: tipo, id: id });
        avisar(tipo === "mensagem"
          ? "Detalhes de " + rotuloDe(achar(msgs, id), indiceDe(msgs, id)) + " abertos. Tab chega ao painel; Esc fecha."
          : "Detalhes de " + nomeDe(id) + " abertos. Tab chega ao painel; Esc fecha.");
      }
      return true;
    }

    function focar(alvo) {
      var seletor = alvo.tipo === "mensagem" ? ".cnt-chamada__msg" : ".cnt-chamada__participante";
      var botao = raiz.querySelector(seletor + '[data-id="' + alvo.id.replace(/["\\]/g, "\\$&") + '"]');
      if (botao) botao.focus({ preventScroll: true });
    }

    // devolverFoco: o foco volta ao item que estava aberto (Esc ou botão de fechar).
    function fechar(devolverFoco) {
      if (!selecao) return false;
      var antes = selecao;
      var estava = aberto;
      var noPainel = painel.contains(document.activeElement);
      aberto = false;
      selecao = null;
      marcar();
      atualizarPainel();
      if (estava) emitir("fechou", antes);
      emitir("selecionou", { tipo: null, id: null });
      if (devolverFoco || noPainel) focar(antes);
      return true;
    }

    /* Reprodução: as mensagens aparecem uma a uma, na ordem. */
    function reproduzir() {
      parar();
      var itens = Array.prototype.filter.call(lista.querySelectorAll(".cnt-chamada__item"), function (li) {
        return comRtp || !li.classList.contains("cnt-chamada__item--rtp");
      });
      if (!itens.length) return;
      tocando = true;
      atualizarBotao();
      avisar("Reprodução iniciada.");
      rolagem.scrollTop = 0;
      itens.forEach(function (li) {
        li.classList.remove("is-chegando");
        li.classList.add("is-oculto");
      });
      var i = 0;
      (function passo() {
        if (i >= itens.length) {
          parar();
          return;
        }
        var li = itens[i++];
        li.classList.remove("is-oculto");
        li.style.setProperty("--cnt-chamada-i", "0");
        void li.offsetWidth; // reinicia a animação de chegada
        li.classList.add("is-chegando");
        var area = rolagem.getBoundingClientRect();
        var caixa = li.getBoundingClientRect();
        if (caixa.bottom > area.bottom) rolagem.scrollTop += caixa.bottom - area.bottom + 16;
        timer = setTimeout(passo, PASSO);
      })();
    }

    function parar() {
      clearTimeout(timer);
      timer = null;
      if (!tocando) return;
      tocando = false;
      lista.querySelectorAll(".is-oculto").forEach(function (li) { li.classList.remove("is-oculto"); });
      atualizarBotao();
    }

    /* RTP e exportação */
    function mostrarRtp(ligado) {
      comRtp = ligado !== false;
      raiz.classList.toggle("is-sem-rtp", !comRtp);
      var botao = topo.querySelector("[aria-pressed]");
      if (botao) botao.setAttribute("aria-pressed", String(comRtp));
      if (selecao && selecao.tipo === "mensagem") {
        var atual = achar(msgs, selecao.id);
        if (!comRtp && atual && atual.tipo === "rtp") fechar(false);
      }
    }

    function visiveis() {
      return msgs.filter(function (m) { return comRtp || m.tipo !== "rtp"; });
    }

    // Papel é sempre claro, qualquer que seja o tema da tela.
    var PAPEL = { fundo: "#ffffff", superficie: "#f4f6f9", borda: "#d3d9e1", bordaForte: "#7d8896", texto: "#171c23", suave: "#566070",
      primaria: "#2457d6", sucesso: "#17824b", perigo: "#c4262e", alerta: "#a96100", info: "#0b78a3",
      fonte: "system-ui, 'Segoe UI', Arial, sans-serif", mono: "Consolas, monospace" };

    function coresDaTela() {
      var estilo = getComputedStyle(raiz);
      function v(nome, padrao) { return estilo.getPropertyValue(nome).trim() || padrao; }
      return { fundo: v("--cnt-cor-fundo", PAPEL.fundo), superficie: v("--cnt-cor-superficie", PAPEL.superficie), borda: v("--cnt-cor-borda", PAPEL.borda),
        bordaForte: v("--cnt-cor-borda-forte", PAPEL.bordaForte), texto: v("--cnt-cor-texto", PAPEL.texto), suave: v("--cnt-cor-texto-suave", PAPEL.suave),
        primaria: v("--cnt-cor-primaria", PAPEL.primaria), sucesso: v("--cnt-cor-sucesso", PAPEL.sucesso), perigo: v("--cnt-cor-perigo", PAPEL.perigo),
        alerta: v("--cnt-cor-alerta", PAPEL.alerta), info: v("--cnt-cor-info", PAPEL.info),
        fonte: v("--cnt-fonte-base", PAPEL.fonte), mono: v("--cnt-fonte-mono", PAPEL.mono) };
    }

    var medidor = null;
    function larguraDoTexto(texto, tamanho, peso, fonte) {
      medidor = medidor || document.createElement("canvas").getContext("2d");
      medidor.font = peso + " " + tamanho + "px " + fonte;
      return medidor.measureText(texto).width;
    }

    function cortar(texto, maximo, tamanho, fonte) {
      texto = String(texto);
      if (larguraDoTexto(texto, tamanho, 400, fonte) <= maximo) return texto;
      while (texto.length > 1 && larguraDoTexto(texto + "…", tamanho, 400, fonte) > maximo) texto = texto.slice(0, -1);
      return texto + "…";
    }

    // Diagrama redesenhado em SVG a partir dos dados: independe do CSS da página e serve para imagem e papel.
    function montarSvg(c) {
      var lista = visiveis();
      var MARGEM = 24, TEMPO = 96, COL = 176, LINHA = 56, CAIXA = 46;
      var cor = { primaria: c.primaria, sucesso: c.sucesso, alerta: c.alerta, perigo: c.perigo, info: c.info, neutro: c.suave };
      var largura = MARGEM * 2 + TEMPO + Math.max(partes.length, 1) * COL;
      var saida = [];
      var zonas = []; // áreas clicáveis (coordenadas do SVG) para o relatório ligar cada seta aos seus detalhes
      var y = MARGEM;

      function texto(x, yy, conteudo, tamanho, peso, preenchimento, ancora, fonte) {
        saida.push('<text x="' + x + '" y="' + yy + '" font-size="' + tamanho + '" font-weight="' + peso + '" fill="' + preenchimento + '"' +
          (ancora ? ' text-anchor="' + ancora + '"' : "") + (fonte ? ' font-family="' + esc(fonte) + '"' : "") + ">" + esc(conteudo) + "</text>");
      }

      function traco(x1, y1, x2, y2, cortante, largura, tracejado) {
        saida.push('<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + cortante + '" stroke-width="' + largura + '"' +
          (tracejado ? ' stroke-dasharray="' + tracejado + '"' : "") + "/>");
      }

      function pilula(cx, yy, largura2, tom, tracejada) {
        saida.push('<rect x="' + (cx - largura2 / 2) + '" y="' + yy + '" width="' + largura2 + '" height="20" rx="10" fill="' + tom +
          '" fill-opacity="0.14" stroke="' + tom + '" stroke-opacity="0.45"' + (tracejada ? ' stroke-dasharray="3 3"' : "") + "/>");
      }

      texto(MARGEM, y + 14, cabecalho.titulo || "Fluxo de chamadas", 18, 700, c.texto);
      y += 18;
      if (temValor(cabecalho.subtitulo)) { texto(MARGEM, y + 14, cabecalho.subtitulo, 12, 400, c.suave); y += 18; }
      var itensResumo = (cabecalho.resumo || []).filter(function (r) { return r && temValor(r.valor); })
        .map(function (r) { return (r.rotulo ? r.rotulo + ": " : "") + r.valor; });
      if (itensResumo.length) { texto(MARGEM, y + 14, itensResumo.join("   ·   "), 12, 600, c.texto); y += 18; }

      var lx = MARGEM;
      [[true, "Requisição", c.perigo], [false, "Resposta", c.sucesso]].forEach(function (l) {
        if (!lista.some(function (m) { return m.requisicao === l[0] && m.tipo !== "rtp"; })) return;
        traco(lx, y + 10, lx + 22, y + 10, l[2], 2);
        texto(lx + 28, y + 14, l[1], 11, 400, c.suave);
        lx += 28 + larguraDoTexto(l[1], 11, 400, c.fonte) + 18;
      });
      if (lista.some(function (m) { return m.tipo === "rtp"; })) {
        traco(lx, y + 10, lx + 22, y + 10, c.suave, 2, "6 4");
        texto(lx + 28, y + 14, "RTP", 11, 400, c.suave);
      }
      y += 26;

      var topoCaixas = y;
      var inicioLinhas = topoCaixas + CAIXA + 12;
      var altura = inicioLinhas + lista.length * LINHA + MARGEM;
      function centro(id) { return MARGEM + TEMPO + indiceDe(partes, id) * COL + COL / 2; }

      partes.forEach(function (p, i) {
        var cx = MARGEM + TEMPO + i * COL + COL / 2;
        zonas.push({ tipo: "participante", id: p.id, x: cx - COL / 2 + 12, y: topoCaixas, w: COL - 24, h: CAIXA });
        traco(cx, topoCaixas + CAIXA, cx, altura - MARGEM / 2, c.borda, 2, "5 5");
        saida.push('<rect x="' + (cx - COL / 2 + 12) + '" y="' + topoCaixas + '" width="' + (COL - 24) + '" height="' + CAIXA + '" rx="10" fill="' + c.superficie + '" stroke="' + c.borda + '"/>');
        saida.push('<rect x="' + (cx - COL / 2 + 12) + '" y="' + (topoCaixas + 10) + '" width="4" height="' + (CAIXA - 20) + '" rx="2" fill="' + cor[tomDoParticipante(p)] + '"/>');
        texto(cx, topoCaixas + 20, cortar(p.nome || p.id, COL - 48, 12, c.fonte), 12, 700, c.texto, "middle");
        if (temValor(p.detalhe)) texto(cx, topoCaixas + 36, cortar(p.detalhe, COL - 48, 10, c.mono), 10, 400, c.suave, "middle", c.mono);
      });
      texto(MARGEM, topoCaixas + CAIXA / 2 + 4, "TEMPO", 10, 700, c.suave);

      lista.forEach(function (m, i) {
        var topoLinha = inicioLinhas + i * LINHA;
        var ya = topoLinha + 34;
        var tom = cor[tomDaMensagem(m)];
        var rtp = m.tipo === "rtp";
        var tracejada = rtp || m.tipo === "pontilhada";
        var x1 = centro(m.de), x2 = centro(m.para);
        var rot = cortar(rotuloDe(m, indiceDe(msgs, m.id)), Math.max(Math.abs(x2 - x1), COL) - 24, 11, c.fonte);
        var larguraPilula = larguraDoTexto(rot, 11, 700, c.fonte) + 20;
        zonas.push({ tipo: "mensagem", id: m.id, x: MARGEM + TEMPO, y: topoLinha, w: largura - MARGEM * 2 - TEMPO, h: LINHA });

        if (i % 2) saida.push('<rect x="' + MARGEM + '" y="' + topoLinha + '" width="' + (largura - MARGEM * 2) + '" height="' + LINHA + '" fill="' + c.texto + '" opacity="0.03"/>');
        if (temValor(m.tempo)) texto(MARGEM + 4, topoLinha + 26, m.tempo, 11, 700, c.texto, null, c.mono);
        if (temValor(m.intervalo)) texto(MARGEM + 4, topoLinha + 40, m.intervalo, 10, 400, c.suave, null, c.mono);

        if (x1 === x2) {
          var yp = topoLinha + 24;
          saida.push('<path d="M' + x1 + " " + (yp - 8) + 'h34v16h-28" fill="none" stroke="' + tom + '" stroke-width="2"' + (tracejada ? ' stroke-dasharray="7 5"' : "") + "/>");
          saida.push('<polygon points="' + (x1 + 4) + "," + yp + " " + (x1 + 12) + "," + (yp - 4.5) + " " + (x1 + 12) + "," + (yp + 4.5) + '" fill="' + tom + '"/>');
          pilula(x1 + 44 + larguraPilula / 2, yp - 10, larguraPilula, tom, rtp);
          texto(x1 + 54, yp + 4, rot, 11, 700, tom);
          return;
        }

        var dir = x2 > x1 ? 1 : -1;
        pilula((x1 + x2) / 2, topoLinha + 6, larguraPilula, tom, rtp);
        texto((x1 + x2) / 2, topoLinha + 20, rot, 11, 700, tom, "middle");
        traco(x1, ya, x2 - dir * 8, ya, tom, 2, tracejada ? "7 5" : "");
        saida.push('<polygon points="' + x2 + "," + ya + " " + (x2 - dir * 10) + "," + (ya - 5) + " " + (x2 - dir * 10) + "," + (ya + 5) + '" fill="' + tom + '"/>');
        if (rtp && m.portas && m.portas.length === 2) {
          texto(x1 + dir * 6, ya + 14, m.portas[0], 10, 400, c.suave, dir > 0 ? "start" : "end", c.mono);
          texto(x2 - dir * 6, ya + 14, m.portas[1], 10, 400, c.suave, dir > 0 ? "end" : "start", c.mono);
        } else if (temValor(m.resumo)) {
          texto((x1 + x2) / 2, ya + 14, cortar(m.resumo, Math.abs(x2 - x1) - 24, 10, c.fonte), 10, 400, c.suave, "middle");
        }
      });

      var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + largura + '" height="' + altura + '" viewBox="0 0 ' + largura + " " + altura +
        '" font-family="' + esc(c.fonte) + '"><rect width="100%" height="100%" fill="' + c.fundo + '"/>' + saida.join("") + "</svg>";
      return { svg: svg, largura: largura, altura: altura, zonas: zonas };
    }

    function nomeDoArquivo() {
      var base = String(cabecalho.titulo || "fluxo-de-chamadas").normalize("NFD").replace(/[̀-ͯ]/g, "")
        .replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
      return base || "fluxo-de-chamadas";
    }

    function baixar(blob, nome) {
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = nome;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    }

    function exportarPng() {
      var desenho = montarSvg(coresDaTela());
      return new Promise(function (resolver, rejeitar) {
        var imagem = new Image();
        imagem.onload = function () {
          var escala = Math.min(2, 16000 / desenho.altura, 16000 / desenho.largura);
          var tela = document.createElement("canvas");
          tela.width = Math.round(desenho.largura * escala);
          tela.height = Math.round(desenho.altura * escala);
          tela.getContext("2d").drawImage(imagem, 0, 0, tela.width, tela.height);
          tela.toBlob(function (blob) {
            if (!blob) { rejeitar(new Error("Não foi possível gerar a imagem.")); return; }
            baixar(blob, nomeDoArquivo() + ".png");
            resolver(blob);
          }, "image/png");
        };
        imagem.onerror = function () { rejeitar(new Error("Não foi possível gerar a imagem.")); };
        imagem.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(desenho.svg);
      });
    }

    function grupoDeFatos(itens) {
      var validos = (itens || []).filter(function (i) { return i && temValor(i.valor); });
      if (!validos.length) return "";
      return '<dl class="fatos">' + validos.map(function (i) {
        return '<div><dt>' + esc(i.rotulo) + "</dt><dd" + (i.mono ? ' class="mono"' : "") + ">" + esc(i.valor) + "</dd></div>";
      }).join("") + "</dl>";
    }

    function blocoDeSecao(titulo, corpo) {
      return '<section class="secao"><h4>' + esc(titulo) + "</h4>" + corpo + "</section>";
    }

    function blocosDeSecoes(itens) {
      return (itens || []).map(function (sec) {
        var validos = (sec.campos || []).filter(function (campo) { return campo && temValor(campo.valor); });
        if (!validos.length) return "";
        return blocoDeSecao(sec.titulo || "", '<dl class="linhas' + (sec.mono ? " mono" : "") + '">' + validos.map(function (campo) {
          return "<div><dt>" + esc(campo.rotulo || "") + "</dt><dd>" + esc(campo.valor) + "</dd></div>";
        }).join("") + "</dl>");
      }).join("");
    }

    function brutoHtml(texto) {
      return "<pre>" + String(texto).split(/\r?\n/).map(function (linha, i) {
        var campo = /^([A-Za-z][\w.-]*)(\s*[:=]\s*)(.*)$/.exec(linha);
        var conteudo = campo ? "<b>" + esc(campo[1]) + "</b>" + esc(campo[2] + campo[3]) : esc(linha || " ");
        return '<span class="' + (i === 0 ? "primeira" : "") + '">' + conteudo + "</span>";
      }).join("") + "</pre>";
    }

    // Relatório dinâmico: cada seta e cada participante do diagrama é um link para o seu cartão de detalhes
    // (os mesmos dados do painel da tela); o PDF gerado mantém os links, e cada cartão volta ao diagrama.
    function relatorio() {
      var desenho = montarSvg(PAPEL);
      var lista = visiveis();
      var cor = { primaria: PAPEL.primaria, sucesso: PAPEL.sucesso, alerta: PAPEL.alerta, perigo: PAPEL.perigo, info: PAPEL.info, neutro: PAPEL.suave };
      function ancoraDe(tipo, id) { return (tipo === "mensagem" ? "m" : "p") + indiceDe(tipo === "mensagem" ? msgs : partes, id); }
      function pct(v, total) { return (v / total * 100).toFixed(4) + "%"; }

      var zonas = desenho.zonas.map(function (z) {
        var alvo = achar(z.tipo === "mensagem" ? msgs : partes, z.id);
        var nome = z.tipo === "mensagem" ? rotuloDe(alvo, indiceDe(msgs, z.id)) : (alvo.nome || alvo.id);
        return '<a class="zona" href="#' + ancoraDe(z.tipo, z.id) + '" title="Ver detalhes: ' + esc(nome) + '" style="left:' + pct(z.x, desenho.largura) +
          ";top:" + pct(z.y, desenho.altura) + ";width:" + pct(z.w, desenho.largura) + ";height:" + pct(z.h, desenho.altura) + '"></a>';
      }).join("");

      function noDaRota(p) {
        return '<a class="no" style="--tom:' + cor[tomDoParticipante(p)] + '" href="#' + ancoraDe("participante", p.id) + '"><i>' +
          esc(String(p.nome || p.id).charAt(0).toUpperCase()) + "</i>" + esc(p.nome || p.id) + "</a>";
      }

      var corpoMsgs = lista.map(function (m, pos) {
        var n = indiceDe(msgs, m.id);
        var tom = cor[tomDaMensagem(m)];
        var anterior = lista[pos - 1], proxima = lista[pos + 1];
        var portas = m.portas && m.portas.length === 2 ? m.portas[0] + " → " + m.portas[1] : "";
        var navegacao = '<nav><a href="#diagrama">↑ Diagrama</a>' +
          (anterior ? '<a href="#' + ancoraDe("mensagem", anterior.id) + '">‹ Anterior</a>' : "") +
          (proxima ? '<a href="#' + ancoraDe("mensagem", proxima.id) + '">Próxima ›</a>' : "") + "</nav>";
        return '<article class="cartao" id="' + ancoraDe("mensagem", m.id) + '" style="--tom:' + tom + '">' +
          '<header><small>Mensagem ' + (n + 1) + " de " + msgs.length + "</small>" + navegacao + "</header>" +
          '<div class="titulo">' + (m.titulo && m.rotulo ? '<span class="rotulo">' + esc(m.rotulo) + "</span>" : "") +
          "<h3>" + esc(m.titulo || rotuloDe(m, n)) + "</h3></div>" +
          '<div class="rota">' + noDaRota(achar(partes, m.de)) + '<span class="seta">→</span>' +
          (m.de !== m.para ? noDaRota(achar(partes, m.para)) : '<span class="texto">nele mesmo</span>') + "</div>" +
          (temValor(m.resumo) ? '<p class="texto">' + esc(m.resumo) + "</p>" : "") +
          grupoDeFatos([
            { rotulo: "Horário", valor: m.tempo, mono: true }, { rotulo: "Intervalo", valor: m.intervalo, mono: true },
            { rotulo: "Origem", valor: nomeDe(m.de) }, { rotulo: "Destino", valor: nomeDe(m.para) },
            { rotulo: "Portas", valor: portas, mono: true }
          ].concat((m.campos || []).filter(Boolean))) +
          blocosDeSecoes(m.secoes) +
          (temValor(m.bruto) ? blocoDeSecao("Mensagem completa", brutoHtml(m.bruto)) : "") + "</article>";
      }).join("");

      var corpoPartes = partes.map(function (p) {
        var enviadas = lista.filter(function (m) { return m.de === p.id; }).length;
        var recebidas = lista.filter(function (m) { return m.para === p.id && m.de !== p.id; }).length;
        var dele = lista.filter(function (m) { return m.de === p.id || m.para === p.id; });
        return '<article class="cartao" id="' + ancoraDe("participante", p.id) + '" style="--tom:' + cor[tomDoParticipante(p)] + '">' +
          '<header><small>Participante</small><nav><a href="#diagrama">↑ Diagrama</a></nav></header>' +
          '<div class="titulo"><h3>' + esc(p.nome || p.id) + "</h3>" + (temValor(p.detalhe) ? '<span class="endereco">' + esc(p.detalhe) + "</span>" : "") + "</div>" +
          grupoDeFatos([{ rotulo: "Enviou", valor: enviadas }, { rotulo: "Recebeu", valor: recebidas }].concat((p.campos || []).filter(Boolean))) +
          blocosDeSecoes(p.secoes) +
          (dele.length ? blocoDeSecao("Mensagens", '<ul class="lista">' + dele.map(function (m) {
            var i = indiceDe(msgs, m.id);
            return '<li><a style="--tom:' + cor[tomDaMensagem(m)] + '" href="#' + ancoraDe("mensagem", m.id) + '"><span class="hora">' +
              esc(temValor(m.tempo) ? m.tempo : String(i + 1)) + '</span><b>' + esc(rotuloDe(m, i)) + "</b><span>" +
              esc(m.de === m.para ? "nele mesmo" : m.de === p.id ? "para " + nomeDe(m.para) : "de " + nomeDe(m.de)) + "</span></a></li>";
          }).join("") + "</ul>") : "") + "</article>";
      }).join("");

      var estilo = "@page{size:A4 landscape;margin:12mm}*{box-sizing:border-box}" +
        "body{margin:0;color:" + PAPEL.texto + ";font:12px/1.45 " + PAPEL.fonte + ";-webkit-print-color-adjust:exact;print-color-adjust:exact}" +
        "a{color:inherit;text-decoration:none}.dica{margin:6px 0 10px;color:" + PAPEL.suave + ";font-size:11px}" +
        ".diagrama{position:relative;max-width:900px;margin:0 auto}.diagrama svg{display:block;width:100%;height:auto}.zona{position:absolute;display:block;border-radius:6px}.zona:hover{background:" + PAPEL.primaria + "1a}" +
        "h2{margin:18px 0 8px;font-size:15px;break-after:avoid}" +
        ".cartao{break-inside:avoid;margin:0 0 12px;padding:10px 12px;border:1px solid " + PAPEL.borda + ";border-left:4px solid var(--tom);border-radius:8px;background:" + PAPEL.fundo + "}" +
        ".cartao>header{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:6px}.cartao>header small{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:" + PAPEL.suave + "}" +
        "nav{display:flex;gap:6px}nav a{padding:1px 8px;border:1px solid " + PAPEL.borda + ";border-radius:999px;font-size:10px;font-weight:600;color:" + PAPEL.primaria + "}" +
        ".titulo{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px}h3{margin:0;font-size:15px;color:var(--tom)}" +
        ".rotulo{padding:1px 9px;border:1px solid var(--tom);border-radius:999px;background:" + PAPEL.superficie + ";font-size:11px;font-weight:700;color:var(--tom)}" +
        ".endereco{font:11px " + PAPEL.mono + ";color:" + PAPEL.suave + "}.texto{margin:4px 0;color:" + PAPEL.suave + "}" +
        ".rota{display:flex;align-items:center;gap:8px;margin:4px 0 8px}.seta{color:var(--tom);font-weight:700}" +
        ".no{display:inline-flex;align-items:center;gap:6px;padding:3px 10px 3px 4px;border:1px solid " + PAPEL.borda + ";border-radius:999px;background:" + PAPEL.superficie + ";font-weight:600}" +
        ".no i{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;background:var(--tom);color:#fff;font-style:normal;font-size:10px}" +
        ".fatos{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;margin:6px 0}.fatos div{padding:5px 9px;border:1px solid " + PAPEL.borda + ";border-radius:6px;background:" + PAPEL.superficie + "}" +
        "dt{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:" + PAPEL.suave + "}dd{margin:0;font-weight:600;overflow-wrap:anywhere}.mono,.mono dd{font-family:" + PAPEL.mono + "}" +
        ".secao{margin-top:8px}.secao h4{margin:0 0 4px;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:" + PAPEL.suave + "}" +
        ".linhas{margin:0}.linhas div{display:flex;gap:12px;padding:2px 0;border-bottom:1px dashed " + PAPEL.borda + "}.linhas dt{flex:0 0 28%;text-transform:none;letter-spacing:0;font-size:11px;font-weight:600}.linhas dd{font-weight:400}" +
        "pre{margin:0;padding:8px 10px;border:1px solid " + PAPEL.borda + ";border-radius:6px;background:" + PAPEL.superficie + ";font:11px/1.5 " + PAPEL.mono + ";white-space:pre-wrap;overflow-wrap:anywhere}" +
        "pre span{display:block}pre .primeira{font-weight:700;color:var(--tom)}pre b{color:" + PAPEL.info + ";font-weight:600}" +
        ".lista{list-style:none;margin:0;padding:0}.lista a{display:flex;gap:10px;align-items:baseline;padding:3px 0;border-bottom:1px dashed " + PAPEL.borda + "}.lista b{color:var(--tom)}.lista .hora{font-family:" + PAPEL.mono + ";color:" + PAPEL.suave + "}";

      return '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>' + esc(cabecalho.titulo || "Fluxo de chamadas") + "</title><style>" + estilo + "</style></head><body>" +
        '<div id="diagrama" class="diagrama">' + desenho.svg + zonas + '</div><p class="dica">Clique em uma seta ou em um participante do diagrama para ir aos detalhes dele; cada cartão tem um link de volta.</p>' +
        "<h2>Mensagens</h2>" + corpoMsgs + (corpoPartes ? "<h2>Participantes</h2>" + corpoPartes : "") + "</body></html>";
    }

    function exportarPdf() {
      var quadro = document.createElement("iframe");
      quadro.setAttribute("aria-hidden", "true");
      quadro.tabIndex = -1;
      quadro.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
      function remover() { if (quadro.parentNode) quadro.remove(); }
      quadro.onload = function () {
        var janela = quadro.contentWindow;
        janela.onafterprint = remover;
        janela.focus();
        janela.print();
        setTimeout(remover, 120000);
      };
      quadro.srcdoc = relatorio();
      document.body.appendChild(quadro);
      return Promise.resolve();
    }

    function exportar(formato) {
      if (!msgs.length) return Promise.resolve();
      formato = formato === "pdf" ? "pdf" : "png";
      emitir("exportou", { formato: formato });
      avisar(formato === "pdf" ? "Relatório aberto para imprimir ou salvar como PDF." : "Imagem do diagrama baixada.");
      return formato === "pdf" ? exportarPdf() : exportarPng();
    }

    /* Eventos */
    function aoClicar(evento) {
      var participante = evento.target.closest(".cnt-chamada__participante");
      if (participante) {
        selecionar("participante", participante.getAttribute("data-id"), true);
        return;
      }
      var item = evento.target.closest(".cnt-chamada__item");
      if (item) {
        selecionar("mensagem", item.getAttribute("data-id"), true);
        return;
      }
      // Clique no espaço vazio do diagrama desfaz a seleção.
      if (selecao && corpo.contains(evento.target)) fechar(false);
    }

    function aoAnimar(evento) {
      if (evento.animationName === "cnt-chamada-entrar" && evento.target.classList.contains("cnt-chamada__item")) {
        evento.target.classList.remove("is-chegando");
      }
    }

    // Setas, Home e End percorrem as mensagens; com o painel aberto, ele acompanha a mensagem em foco.
    function aoTeclar(evento) {
      if (evento.key === "Escape" && selecao) {
        evento.preventDefault();
        fechar(true);
        return;
      }
      var botao = evento.target.closest(".cnt-chamada__msg");
      if (!botao) return;
      var botoes = Array.prototype.filter.call(lista.querySelectorAll(".cnt-chamada__msg"), function (item) { return item.offsetParent !== null; });
      var i = botoes.indexOf(botao);
      var alvo = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: botoes.length - 1 }[evento.key];
      if (alvo == null) return;
      evento.preventDefault();
      alvo = Math.min(botoes.length - 1, Math.max(0, alvo));
      botoes[alvo].focus();
      if (aberto) selecionar("mensagem", botoes[alvo].getAttribute("data-id"), true);
    }

    function aoFocar(evento) {
      var botao = evento.target.closest && evento.target.closest(".cnt-chamada__msg");
      if (!botao) return;
      lista.querySelectorAll(".cnt-chamada__msg").forEach(function (outro) { outro.tabIndex = outro === botao ? 0 : -1; });
    }

    rolagem.addEventListener("click", aoClicar);
    lista.addEventListener("animationend", aoAnimar);
    raiz.addEventListener("keydown", aoTeclar);
    raiz.addEventListener("focusin", aoFocar);

    /* API */
    function carregar(novos) {
      novos = novos || {};
      selecao = null;
      aberto = false;
      partes = normalizarParticipantes(novos.participantes);
      msgs = normalizarMensagens(novos.mensagens, partes);
      cabecalho = { titulo: novos.titulo, subtitulo: novos.subtitulo, resumo: novos.resumo };
      desenhar(true);
    }

    function adicionarMensagem(mensagem) {
      var antes = msgs.length;
      msgs = normalizarMensagens(msgs.concat([mensagem]), partes);
      if (msgs.length === antes) return null;
      desenhar(false);
      emitir("mudou", { motivo: "mensagem-adicionada", dados: dados() });
      return msgs[msgs.length - 1].id;
    }

    function destruir() {
      parar();
      rolagem.removeEventListener("click", aoClicar);
      lista.removeEventListener("animationend", aoAnimar);
      raiz.removeEventListener("keydown", aoTeclar);
      raiz.removeEventListener("focusin", aoFocar);
      raiz.textContent = "";
      raiz.classList.remove("cnt-chamada", "is-detalhe");
      delete raiz.cntChamada;
    }

    desenhar(true);

    raiz.cntChamada = {
      dados: dados,
      carregar: carregar,
      adicionarMensagem: adicionarMensagem,
      abrir: function (id) { return selecionar("mensagem", id, true); },
      abrirParticipante: function (id) { return selecionar("participante", id, true); },
      fechar: function () { return fechar(false); },
      reproduzir: reproduzir,
      parar: parar,
      mostrarRtp: mostrarRtp,
      exportar: exportar,
      relatorio: relatorio,
      destruir: destruir
    };
    return raiz.cntChamada;
  }

  // Marcação vinda do servidor: opções em data-*, dados em <script type="application/json">.
  function iniciarTodos() {
    document.querySelectorAll("[data-cnt-chamada]").forEach(function (raiz) {
      if (raiz.cntChamada) return;
      var fonte = raiz.querySelector('script[type="application/json"]');
      var opcoes = {};
      try {
        if (fonte) opcoes = JSON.parse(fonte.textContent) || {};
      } catch (erro) {
        console.error("cntChamada: JSON inválido em", raiz, erro);
      }
      if (raiz.hasAttribute("data-cnt-chamada-sem-detalhe")) opcoes.detalhe = false;
      if (raiz.hasAttribute("aria-label")) opcoes.rotulo = raiz.getAttribute("aria-label");
      criar(raiz, opcoes);
    });
  }

  window.cntChamada = { criar: criar, iniciarTodos: iniciarTodos };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciarTodos);
  else iniciarTodos();
})();
