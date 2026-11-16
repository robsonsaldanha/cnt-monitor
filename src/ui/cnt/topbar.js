/* Topbar (barra de topo). Script clássico, sem dependência.
   Inicia sozinho em todo [data-cnt-topbar]. Para HTML inserido depois: cntTopbar.iniciar(elemento).

   Devolve { abrirMenu(id), fecharMenu(), abrirBusca(), fecharBusca(), selo(id, valor), destruir() }.
   Eventos (no elemento, sobem na árvore):
     cnt:topbar:acao   { acao, rotulo }   botão com data-cnt-topbar-acao (na barra ou num menu)
     cnt:topbar:buscou { termo }          envio da busca; cancelável (preventDefault não envia o formulário)
     cnt:topbar:menu   { id, aberto }     menu suspenso abriu ou fechou */
(function () {
  var estreita = window.matchMedia("(max-width: 767.98px)");

  function iniciaisDe(nome) {
    var partes = String(nome || "").trim().split(/\s+/).filter(Boolean);
    if (!partes.length) return "";
    var primeira = partes[0].charAt(0);
    var ultima = partes.length > 1 ? partes[partes.length - 1].charAt(0) : "";
    return (primeira + ultima).toUpperCase();
  }

  function digitando(alvo) {
    return alvo && (alvo.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName));
  }

  function iniciar(el) {
    if (el.cntTopbar) return el.cntTopbar;

    var form = el.querySelector("[data-cnt-topbar-busca]");
    var campo = form && form.querySelector(".cnt-topbar__busca-campo");
    var aberto = null; // botão do menu aberto
    var seletorRolagem = el.getAttribute("data-cnt-topbar-rolagem");
    var rolagem = (seletorRolagem && document.querySelector(seletorRolagem)) || window;

    function emitir(acao, detalhe, cancelavel) {
      return el.dispatchEvent(new CustomEvent("cnt:topbar:" + acao, { bubbles: true, cancelable: !!cancelavel, detail: detalhe }));
    }

    function menuDe(botao) {
      return document.getElementById(botao.getAttribute("aria-controls"));
    }

    function opcoes(menu) {
      return Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]:not([disabled])'));
    }

    function botaoDoMenu(id) {
      return el.querySelector('[aria-haspopup="menu"][data-cnt-topbar-id="' + id + '"]') ||
        el.querySelector('[aria-haspopup="menu"][aria-controls="' + id + '"]');
    }

    function abrirMenu(botao, focar) {
      if (typeof botao === "string") botao = botaoDoMenu(botao);
      if (!botao || aberto === botao) return;
      var menu = menuDe(botao);
      if (!menu) return;
      fecharMenu(false);
      aberto = botao;
      botao.setAttribute("aria-expanded", "true");
      menu.classList.add("is-aberto");
      var lista = opcoes(menu);
      var alvo = focar === "ultimo" ? lista[lista.length - 1] : focar === false ? null : lista[0];
      if (alvo) alvo.focus();
      emitir("menu", { id: menu.id, aberto: true });
    }

    function fecharMenu(devolverFoco) {
      if (!aberto) return;
      var botao = aberto;
      var menu = menuDe(botao);
      aberto = null;
      botao.setAttribute("aria-expanded", "false");
      if (menu) menu.classList.remove("is-aberto");
      if (devolverFoco) botao.focus();
      emitir("menu", { id: menu ? menu.id : "", aberto: false });
    }

    function abrirBusca() {
      if (!campo) return;
      el.classList.add("is-buscando");
      fecharMenu(false);
      campo.focus();
      campo.select();
    }

    function fecharBusca() {
      if (!el.classList.contains("is-buscando")) return;
      el.classList.remove("is-buscando");
      var abrir = el.querySelector("[data-cnt-topbar-busca-abrir]");
      if (estreita.matches && abrir) abrir.focus();
    }

    function pulsar(selo) {
      selo.classList.remove("is-mudou");
      void selo.offsetWidth; // reinicia a animação
      selo.classList.add("is-mudou");
    }

    // Atualiza o contador de uma ação (data-cnt-topbar-id). 0, null ou "" tiram o selo; true vira ponto.
    function selo(id, valor) {
      var botao = el.querySelector('[data-cnt-topbar-id="' + id + '"]');
      if (!botao) return;
      var atual = botao.querySelector(".cnt-topbar__selo");
      if (valor === 0 || valor === null || valor === undefined || valor === "" || valor === false) {
        if (atual) atual.remove();
        return;
      }
      if (!atual) {
        atual = document.createElement("span");
        botao.appendChild(atual);
      }
      atual.className = valor === true ? "cnt-topbar__selo cnt-topbar__selo--ponto" : "cnt-topbar__selo";
      atual.textContent = valor === true ? "" : String(valor);
    }

    function pintarTema() {
      var botao = el.querySelector("[data-cnt-topbar-tema]");
      if (!botao) return;
      if (!window.cntTema) {
        botao.hidden = true;
        return;
      }
      var escuro = window.cntTema.atual().temaAplicado === "escuro";
      var rotulo = escuro ? "Usar tema claro" : "Usar tema escuro";
      botao.setAttribute("aria-label", rotulo);
      botao.title = rotulo;
    }

    function alternarTema() {
      if (!window.cntTema) return;
      var escuro = window.cntTema.atual().temaAplicado === "escuro";
      window.cntTema.aplicar({ tema: escuro ? "claro" : "escuro" });
    }

    function marcarRolagem() {
      var y = rolagem === window ? window.scrollY : rolagem.scrollTop;
      el.classList.toggle("is-rolada", y > 0);
    }

    // ---------- Ouvintes no elemento ----------
    function aoClicar(evento) {
      var alvo = evento.target.closest("[aria-haspopup='menu'], [role='menuitem'], [data-cnt-topbar-acao], [data-cnt-topbar-tema], [data-cnt-topbar-busca-abrir], [data-cnt-topbar-busca-fechar]");
      if (!alvo || !el.contains(alvo)) return;

      if (alvo.getAttribute("aria-haspopup") === "menu") {
        if (aberto === alvo) fecharMenu(false);
        // Clique de mouse não destaca a primeira opção; Enter e espaço (detail 0) levam o foco a ela.
        else abrirMenu(alvo, evento.detail === 0 ? "primeiro" : false);
        return;
      }
      if (alvo.hasAttribute("data-cnt-topbar-tema")) return alternarTema();
      if (alvo.hasAttribute("data-cnt-topbar-busca-abrir")) return abrirBusca();
      if (alvo.hasAttribute("data-cnt-topbar-busca-fechar")) return fecharBusca();

      var dentroDeMenu = alvo.getAttribute("role") === "menuitem";
      if (alvo.hasAttribute("data-cnt-topbar-acao")) {
        var texto = alvo.querySelector(".cnt-topbar__opcao-rotulo, .cnt-topbar__oculto");
        emitir("acao", { acao: alvo.getAttribute("data-cnt-topbar-acao"), rotulo: (texto || alvo).textContent.trim() });
      }
      if (dentroDeMenu) fecharMenu(!alvo.hasAttribute("href"));
    }

    function aoTeclar(evento) {
      var tecla = evento.key;

      if (tecla === "Escape") {
        if (aberto) {
          evento.preventDefault();
          fecharMenu(true);
        } else if (el.classList.contains("is-buscando") && evento.target === campo && !campo.value) {
          fecharBusca();
        }
        return;
      }

      var botao = evento.target.closest("[aria-haspopup='menu']");
      if (botao && el.contains(botao) && (tecla === "ArrowDown" || tecla === "ArrowUp")) {
        evento.preventDefault();
        if (aberto !== botao) abrirMenu(botao, tecla === "ArrowUp" ? "ultimo" : "primeiro");
        else {
          var listaAberta = opcoes(menuDe(botao));
          var destino = tecla === "ArrowUp" ? listaAberta[listaAberta.length - 1] : listaAberta[0];
          if (destino) destino.focus();
        }
        return;
      }

      if (!aberto || evento.target.getAttribute("role") !== "menuitem") return;
      var lista = opcoes(menuDe(aberto));
      var i = lista.indexOf(evento.target);
      var proximo = null;
      if (tecla === "ArrowDown") proximo = lista[(i + 1) % lista.length];
      else if (tecla === "ArrowUp") proximo = lista[(i - 1 + lista.length) % lista.length];
      else if (tecla === "Home") proximo = lista[0];
      else if (tecla === "End") proximo = lista[lista.length - 1];
      if (proximo) {
        evento.preventDefault();
        proximo.focus();
      }
    }

    // Foco saiu do botão e do menu abertos (Tab ou clique em outro lugar): fecha.
    function aoPerderFoco(evento) {
      if (!aberto) return;
      var grupo = aberto.closest(".cnt-topbar__grupo") || aberto.parentNode;
      if (evento.relatedTarget && grupo.contains(evento.relatedTarget)) return;
      if (!evento.relatedTarget) return; // clique em área sem foco: tratado em aoTocarFora
      fecharMenu(false);
    }

    function aoEnviar(evento) {
      if (evento.target !== form) return;
      var termo = campo ? campo.value.trim() : "";
      if (!termo || !emitir("buscou", { termo: termo }, true)) evento.preventDefault();
    }

    // ---------- Ouvintes no documento ----------
    function aoTocarFora(evento) {
      if (aberto) {
        var grupo = aberto.closest(".cnt-topbar__grupo") || aberto.parentNode;
        if (!grupo.contains(evento.target)) fecharMenu(false);
      }
      if (el.classList.contains("is-buscando") && !el.contains(evento.target)) fecharBusca();
    }

    // Ctrl K / ⌘ K de qualquer lugar; "/" fora de campo de texto.
    function aoAtalho(evento) {
      if (!campo || el.offsetParent === null && getComputedStyle(el).position !== "fixed") return;
      var k = (evento.ctrlKey || evento.metaKey) && !evento.altKey && evento.key.toLowerCase() === "k";
      var barra = evento.key === "/" && !evento.ctrlKey && !evento.metaKey && !digitando(evento.target);
      if (!k && !barra) return;
      evento.preventDefault();
      abrirBusca();
    }

    function aoMudarLargura() {
      if (!estreita.matches) el.classList.remove("is-buscando");
    }

    // Selo novo ou com número novo pulsa, venha a mudança deste script, do React, do Vue ou do servidor.
    var observador = new MutationObserver(function (mudancas) {
      var vistos = [];
      mudancas.forEach(function (mudanca) {
        var no = mudanca.target.nodeType === 1 ? mudanca.target : mudanca.target.parentElement;
        var alvos = [];
        if (no && no.closest) alvos.push(no.closest(".cnt-topbar__selo"));
        Array.prototype.forEach.call(mudanca.addedNodes || [], function (novo) {
          if (novo.nodeType === 1 && novo.classList.contains("cnt-topbar__selo")) alvos.push(novo);
        });
        alvos.forEach(function (s) {
          if (s && vistos.indexOf(s) < 0) vistos.push(s);
        });
      });
      vistos.forEach(pulsar);
    });
    observador.observe(el, { childList: true, characterData: true, subtree: true });

    function aoFimDaAnimacao(evento) {
      if (evento.target.classList.contains("cnt-topbar__selo")) evento.target.classList.remove("is-mudou");
    }

    el.addEventListener("click", aoClicar);
    el.addEventListener("keydown", aoTeclar);
    el.addEventListener("focusout", aoPerderFoco);
    el.addEventListener("submit", aoEnviar);
    el.addEventListener("animationend", aoFimDaAnimacao);
    document.addEventListener("pointerdown", aoTocarFora);
    document.addEventListener("keydown", aoAtalho);
    document.documentElement.addEventListener("cnt:tema", pintarTema);
    rolagem.addEventListener("scroll", marcarRolagem, { passive: true });
    estreita.addEventListener("change", aoMudarLargura);

    // Estado inicial.
    el.querySelectorAll(".cnt-topbar__avatar[data-cnt-topbar-nome]").forEach(function (avatar) {
      if (!avatar.textContent.trim() && !avatar.querySelector("img")) avatar.textContent = iniciaisDe(avatar.getAttribute("data-cnt-topbar-nome"));
    });
    var atalho = el.querySelector(".cnt-topbar__atalho");
    if (atalho && /Mac|iPhone|iPad/.test(navigator.platform || "")) atalho.textContent = "⌘ K";
    pintarTema();
    marcarRolagem();

    function destruir() {
      fecharMenu(false);
      observador.disconnect();
      el.removeEventListener("click", aoClicar);
      el.removeEventListener("keydown", aoTeclar);
      el.removeEventListener("focusout", aoPerderFoco);
      el.removeEventListener("submit", aoEnviar);
      el.removeEventListener("animationend", aoFimDaAnimacao);
      document.removeEventListener("pointerdown", aoTocarFora);
      document.removeEventListener("keydown", aoAtalho);
      document.documentElement.removeEventListener("cnt:tema", pintarTema);
      rolagem.removeEventListener("scroll", marcarRolagem);
      estreita.removeEventListener("change", aoMudarLargura);
      el.classList.remove("is-buscando", "is-rolada");
      delete el.cntTopbar;
    }

    el.cntTopbar = {
      abrirMenu: function (id) { abrirMenu(id, "primeiro"); },
      fecharMenu: function () { fecharMenu(false); },
      abrirBusca: abrirBusca,
      fecharBusca: fecharBusca,
      selo: selo,
      destruir: destruir,
    };
    return el.cntTopbar;
  }

  function iniciarTodas() {
    document.querySelectorAll("[data-cnt-topbar]").forEach(iniciar);
  }

  window.cntTopbar = { iniciar: iniciar, iniciais: iniciaisDe };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciarTodas);
  else iniciarTodas();
})();
