/* Sidebar recolhível. Script clássico, sem dependência.
   Inicia sozinho em todo [data-cnt-sidebar]. Para HTML inserido depois: cntSidebar.iniciar(elemento).

   Devolve { recolher(sim), abrir(), fechar() }.
   Eventos (no elemento, sobem na árvore): cnt:sidebar:recolheu { compacta }, cnt:sidebar:abriu, cnt:sidebar:fechou. */
(function () {
  var CHAVE = "cnt-sidebar-compacta";
  var estreita = window.matchMedia("(max-width: 767.98px)");

  function ler() {
    try { return localStorage.getItem(CHAVE); } catch (e) { return null; }
  }

  function gravar(valor) {
    try { localStorage.setItem(CHAVE, valor); } catch (e) { /* armazenamento bloqueado: vale só nesta página */ }
  }

  function iniciar(el) {
    if (el.cntSidebar) return el.cntSidebar;

    var fundo = el.parentNode.querySelector("[data-cnt-sidebar-fundo]");
    var botaoRecolher = el.querySelector("[data-cnt-sidebar-recolher]");
    var quemAbriu = null;

    function emitir(acao, detalhe) {
      el.dispatchEvent(new CustomEvent("cnt:sidebar:" + acao, { bubbles: true, detail: detalhe }));
    }

    function compacta() {
      return el.classList.contains("cnt-sidebar--compacta");
    }

    function pintarCompacta(sim) {
      el.classList.toggle("cnt-sidebar--compacta", sim);
      if (botaoRecolher) botaoRecolher.setAttribute("aria-label", sim ? "Expandir menu" : "Recolher menu");
      // Só com ícone, o nome do item aparece como dica.
      el.querySelectorAll(".cnt-sidebar__item").forEach(function (item) {
        var rotulo = item.querySelector(".cnt-sidebar__rotulo");
        if (sim && rotulo) item.title = rotulo.textContent.trim();
        else item.removeAttribute("title");
      });
    }

    function recolher(sim) {
      pintarCompacta(sim);
      gravar(sim ? "1" : "0");
      emitir("recolheu", { compacta: sim });
    }

    function alternarGrupo(botao, abrir) {
      var sub = document.getElementById(botao.getAttribute("aria-controls"));
      botao.setAttribute("aria-expanded", String(abrir));
      if (sub) sub.hidden = !abrir;
    }

    function focaveis() {
      return Array.prototype.filter.call(el.querySelectorAll("a[href], button:not([disabled])"), function (item) {
        return item.offsetParent !== null;
      });
    }

    function abrir() {
      if (el.classList.contains("is-aberta")) return;
      quemAbriu = document.activeElement;
      el.classList.add("is-aberta");
      if (fundo) fundo.hidden = false;
      var primeiro = focaveis()[0];
      if (primeiro) primeiro.focus();
      emitir("abriu");
    }

    function fechar() {
      if (!el.classList.contains("is-aberta")) return;
      el.classList.remove("is-aberta");
      if (fundo) fundo.hidden = true;
      if (quemAbriu && quemAbriu.focus) quemAbriu.focus();
      quemAbriu = null;
      emitir("fechou");
    }

    el.addEventListener("click", function (evento) {
      var alvo = evento.target.closest(".cnt-sidebar__item, .cnt-sidebar__marca, [data-cnt-sidebar-recolher]");
      if (!alvo || !el.contains(alvo)) return;

      if (alvo === botaoRecolher) {
        recolher(!compacta());
      } else if (alvo.hasAttribute("aria-expanded")) {
        // Grupo clicado com a sidebar só em ícones: expande para mostrar o submenu.
        if (compacta() && !estreita.matches) {
          recolher(false);
          alternarGrupo(alvo, true);
        } else {
          alternarGrupo(alvo, alvo.getAttribute("aria-expanded") !== "true");
        }
      } else if (alvo.hasAttribute("href")) {
        fechar();
      }
    });

    el.addEventListener("keydown", function (evento) {
      if (!el.classList.contains("is-aberta")) return;
      if (evento.key === "Escape") {
        fechar();
      } else if (evento.key === "Tab") {
        // Com a gaveta aberta, o foco não sai dela.
        var lista = focaveis();
        var primeiro = lista[0];
        var ultimo = lista[lista.length - 1];
        if (evento.shiftKey && document.activeElement === primeiro) {
          evento.preventDefault();
          ultimo.focus();
        } else if (!evento.shiftKey && document.activeElement === ultimo) {
          evento.preventDefault();
          primeiro.focus();
        }
      }
    });

    if (fundo) fundo.addEventListener("click", fechar);

    // Botão de abrir, em qualquer lugar da página: data-cnt-sidebar-abrir="<id da sidebar>" (ou vazio).
    document.addEventListener("click", function (evento) {
      var botao = evento.target.closest("[data-cnt-sidebar-abrir]");
      if (!botao) return;
      var id = botao.getAttribute("data-cnt-sidebar-abrir");
      if (!id || id === el.id) abrir();
    });

    estreita.addEventListener("change", function () {
      if (!estreita.matches) fechar();
    });

    // Estado inicial: a escolha salva vence a classe que veio do servidor.
    var salvo = ler();
    pintarCompacta(salvo === null ? compacta() : salvo === "1");

    // O grupo da página atual começa aberto.
    el.querySelectorAll(".cnt-sidebar__sub").forEach(function (sub) {
      if (!sub.id || !sub.querySelector('[aria-current="page"]')) return;
      var botao = el.querySelector('[aria-controls="' + sub.id + '"]');
      if (botao) alternarGrupo(botao, true);
    });

    el.cntSidebar = { recolher: recolher, abrir: abrir, fechar: fechar };
    return el.cntSidebar;
  }

  function iniciarTodas() {
    document.querySelectorAll("[data-cnt-sidebar]").forEach(iniciar);
  }

  window.cntSidebar = { iniciar: iniciar };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciarTodas);
  else iniciarTodas();
})();
