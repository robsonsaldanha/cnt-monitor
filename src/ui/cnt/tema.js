/* Troca de tema de cor, marca e tema de estilo. Script clássico, sem dependência: funciona em qualquer stack.
   Carregue no <head>, sem defer, para aplicar antes da primeira pintura.

   cntTema.aplicar({ tema: "claro" | "escuro" | "auto", marca: "oceano" | "ameixa" | "", estilo: "personalizado" | "classico" | ... })
   cntTema.atual() -> { tema, marca, temaAplicado, estilo }

   "auto" segue o sistema operacional. marca "" volta para a marca padrão.
   Tema de estilo: a página carrega uma folha de _estilos/ marcada com data-cnt-estilo, antes deste script:
     <link rel="stylesheet" href=".../_estilos/personalizado.css" data-cnt-estilo>
   Sem escolha salva vale window.cntEstilos.padrao (carregue _estilos/estilos.js antes deste script) e, sem ele, o arquivo do href.
   aplicar({ estilo }) troca só o nome do arquivo. Sem esse link, estilo fica "". */
(function () {
  var CHAVE_TEMA = "cnt-tema";
  var CHAVE_MARCA = "cnt-marca";
  var CHAVE_ESTILO = "cnt-estilo";
  var ARQUIVO_ESTILO = /([a-z0-9-]+)\.css(?=$|[?#])/;
  var raiz = document.documentElement;
  var prefereEscuro = window.matchMedia("(prefers-color-scheme: dark)");

  function ler(chave) {
    try { return localStorage.getItem(chave); } catch (e) { return null; }
  }

  function gravar(chave, valor) {
    try { localStorage.setItem(chave, valor); } catch (e) { /* armazenamento bloqueado: vale só nesta página */ }
  }

  // Sem escolha salva, respeita o que o servidor já pôs no HTML.
  var tema = ler(CHAVE_TEMA) || raiz.getAttribute("data-cnt-tema") || "auto";
  var marca = ler(CHAVE_MARCA);
  if (marca === null) marca = raiz.getAttribute("data-cnt-marca") || "";
  var estilo = ler(CHAVE_ESTILO) || "";
  if (!/^[a-z0-9-]+$/.test(estilo)) estilo = "";

  function temaAplicado() {
    if (tema === "claro" || tema === "escuro") return tema;
    return prefereEscuro.matches ? "escuro" : "claro";
  }

  function pintar() {
    raiz.setAttribute("data-cnt-tema", temaAplicado());
    if (marca) raiz.setAttribute("data-cnt-marca", marca);
    else raiz.removeAttribute("data-cnt-marca");
  }

  function folhasDeEstilo() {
    return document.querySelectorAll("link[data-cnt-estilo]");
  }

  function estiloDaFolha(folha) {
    var achado = ARQUIVO_ESTILO.exec(folha.getAttribute("href") || "");
    return achado ? achado[1] : "";
  }

  // Troca a folha sem deixar a tela sem estilo: a nova entra ao lado e a antiga só sai quando a nova carrega.
  // Se a nova não existir (estilo salvo que foi removido), vale a que já estava.
  function pintarEstilo() {
    var folhas = folhasDeEstilo();
    var folha = folhas[folhas.length - 1];
    if (!folha) return;
    var naFolha = estiloDaFolha(folha);
    if (!estilo) {
      var padrao = window.cntEstilos && window.cntEstilos.padrao;
      estilo = /^[a-z0-9-]+$/.test(padrao || "") ? padrao : naFolha;
    }
    raiz.setAttribute("data-cnt-estilo", estilo);
    if (!naFolha || estilo === naFolha) return;
    var nova = folha.cloneNode();
    nova.setAttribute("href", folha.getAttribute("href").replace(ARQUIVO_ESTILO, estilo + ".css"));
    // Só saem as folhas anteriores a esta: uma troca mais recente, ainda carregando, fica.
    nova.addEventListener("load", function () {
      if (!nova.parentNode) return;
      var todas = folhasDeEstilo();
      for (var i = 0; i < todas.length && todas[i] !== nova; i++) todas[i].remove();
    });
    nova.addEventListener("error", function () {
      nova.remove();
      var restantes = folhasDeEstilo();
      if (!restantes.length) return;
      estilo = estiloDaFolha(restantes[restantes.length - 1]);
      gravar(CHAVE_ESTILO, estilo);
      raiz.setAttribute("data-cnt-estilo", estilo);
      avisar();
    });
    folha.after(nova);
  }

  function avisar() {
    raiz.dispatchEvent(new CustomEvent("cnt:tema", { detail: atual() }));
  }

  function atual() {
    return { tema: tema, marca: marca, temaAplicado: temaAplicado(), estilo: estilo };
  }

  function aplicar(opcoes) {
    opcoes = opcoes || {};
    if (opcoes.tema !== undefined) {
      tema = opcoes.tema;
      gravar(CHAVE_TEMA, tema);
    }
    if (opcoes.marca !== undefined) {
      marca = opcoes.marca || "";
      gravar(CHAVE_MARCA, marca);
    }
    if (opcoes.estilo !== undefined && /^[a-z0-9-]+$/.test(opcoes.estilo)) {
      estilo = opcoes.estilo;
      gravar(CHAVE_ESTILO, estilo);
    }
    pintar();
    pintarEstilo();
    avisar();
  }

  prefereEscuro.addEventListener("change", function () {
    if (tema === "auto") aplicar();
  });

  pintar();
  pintarEstilo();
  // Link de estilo declarado depois deste script: aplica quando o HTML terminar de ser lido.
  if (!folhasDeEstilo().length) document.addEventListener("DOMContentLoaded", pintarEstilo);
  window.cntTema = { aplicar: aplicar, atual: atual };
})();
