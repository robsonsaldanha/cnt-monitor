# Estado do projeto: CNT Monitor

Atualizado em 2026-11-11. As funcionalidades completas estão no [README](../README.md); a API está em [API.md](API.md).

## Versão atual

- **3.1.0** (`src/monitorpc/config.py`, `VERSAO`), tag `v3.1.0`. A release no GitHub traz
  `CNTMonitor-Setup-<versão>.exe`, `MonitorDoPC-Setup-<versão>.exe` (nome antigo, para instalações antigas) e
  `cnt-monitor_<versão>_amd64.deb`.
- Repositório: `robsonsaldanha/cnt-monitor` (público, GPL v3 ou posterior).
- Download público: https://monitor.cntoficial.com.br/baixar (servido pela VPS, cópia conferida da última release).

## Linha do tempo resumida

| Versão | O que trouxe |
|---|---|
| 2.3.2 – 2.4.1 | Captura de tela estilo ShareX: atalhos, rolagem até o fim, anotação na seleção, tarefas pós-captura/pós-envio, aviso próprio, editor, gravação MP4/GIF, envio sem login |
| 2.5.0 – 2.5.2 | Controle de ventoinhas sem conflito com outros programas; correções de segurança |
| 2.6.x – 2.7.0 | Acesso remoto ponta a ponta (WebRTC), depois por ID e senha com ponto de encontro e TURN na VPS; nova marca CNT Monitor |
| 2.7.1 – 2.7.3 | Licença GPL v3, README completo, pop-up de aceite, mini painel por cima dos jogos, FPS sem Modo Jogo |
| 2.8.0 – 2.9.2 | Estatísticas de rede por IP, aba Análise de rede, monitoramento por programa (RTP/MOS), vários monitores no acesso remoto, transferência de arquivos |
| 3.0.0 | Versão Linux nativa (.deb para Ubuntu 22.04+, Debian 12+, Mint 21+; X11 e Wayland) |
| 3.1.0 | Página Personalizar: barra e relógio do Windows, ícones ao lado do relógio, tema e papel automáticos, perfis, lançador rápido, renomear em massa e remapear teclas (tudo em Python) |

## Como publicar uma versão

1. Mudar `VERSAO` em `src/monitorpc/config.py`.
2. Branch `v<versão>`, PR para `main`; o CI (`.github/workflows/build.yml`) roda em PR, push na `main` e tag.
3. Merge (squash) depois do CI passar; o GitHub não deixa o autor aprovar a própria PR.
4. Tag `v<versão>` na `main` e push: o job `release` publica o `.exe` e o `.deb` com o SHA-256 de cada um.

Push na `main` sem tag **não** gera release.

## Como testar

- Testes automáticos em `build/teste_*.py` (lista no README, seção "Gerar o instalador e testar"). Rodam sem tocar na tela.
- Interface fora do serviço: `python dev/demo.py`. O serviço instalado ocupa a porta 48731; para o demo, use
  `MONITORPC_DADOS` numa pasta temporária e outra porta.
- Não testar na tela do Robson (janelas, mouse ou teclado simulados): verificar com testes automáticos e o CI.

## Pontos em aberto

- O teste ponta a ponta do acesso remoto (`aiortc`) já travou uma vez no CI ao encerrar a conexão; rodar de novo resolveu.
- Partes visuais (janelas Tk da captura, aviso, pop-up de aceite, abas novas da interface) foram verificadas só por
  testes automáticos e sintaxe, não vistas funcionando.
- Não verificados em uso real: FPS com o CS aberto, Wi-Fi na Análise de rede (o PC de desenvolvimento usa cabo),
  controle de ventoinhas em hardware real e envio real para os sites de upload.
- Personalizar (3.1.0): nenhuma janela vista funcionando (lançador, página nova) e o gancho de remapear teclas não foi
  exercitado com teclado real; a gravação no registro só é testada no CI. A grade de ícones do Windhawk ficou de fora
  (exige código nativo no Explorer).
- O git desta pasta não tem `user.name`/`user.email`; os commits usam o autor dos commits anteriores.
- A pasta é sincronizada pelo OneDrive com outro PC: antes de mexer, conferir `git status` e comparar com `origin/main`.

## Onde está cada coisa

- Serviço, ícone, interface e módulos: ver "Como funciona por dentro" no README.
- VPS (site, ponto de encontro, TURN, download): `servidor-vps/` e a wiki do projeto em `.forge/wiki/`.
