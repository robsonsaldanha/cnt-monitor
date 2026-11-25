# Estado do projeto: CNT Monitor

Atualizado em 2026-11-18. As funcionalidades completas estão no [README](../README.md); a API está em [API.md](API.md).

## Versão atual

- **3.14.0** (`src/monitorpc/config.py`, `VERSAO`), tag `v3.14.0`. A release no GitHub traz
  `CNTMonitor-Setup-<versão>.exe`, `MonitorDoPC-Setup-<versão>.exe` (nome antigo, para instalações antigas) e
  `cnt-monitor_<versão>_amd64.deb`.
- Repositório: `robsonsaldanha/cnt-monitor` (público por enquanto; até a 3.11.1 GPL v3, da 3.12.0 em diante Licença de Uso, veja [ESTRATEGIA-PREMIUM.md](ESTRATEGIA-PREMIUM.md)).
- Download público: https://cntoficial.com.br/baixar (servido pela VPS, cópia conferida da última release).

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
| 3.2.0 | Acesso remoto: retransmissor também por TCP, credencial renovada sozinha, botão "Testar conexão", selo Direta/Retransmitida e Ctrl+C que não se perde; captura: partes das janelas (UI Automation) e Ctrl+C/Enter/Esc na seleção; Linux: ícone por AppIndicator/GTK (sem travar a tela) e textos "com o Linux" |
| 3.3.0 | Interface com a base de front-end CNT (`src/ui/cnt`): sidebar recolhível, topbar, componentes e temas da base, opção "Estilo da interface"; relatório, sessão remota, login pela rede e site da VPS no mesmo visual; fonte Barlow removida |
| 3.4.0 | Interface mais compacta (texto, controles, menu e topbar menores); visão em fluxo (editor de fluxos da base, só leitura) em Alertas, Regras por programa e Fluxos de trabalho da Captura |
| 3.5.0 | Página Áudio (modo Básico e Avançado): volume, silenciar e troca de aparelho, mixer por programa, dB, balanço por canal, formato, aparelho de chamadas, ligar/desligar aparelho (ajudante na sessão do usuário; `pactl` no Linux); dicas (balões) em todos os botões e campos da interface |
| 3.5.1 | Gráficos não crescem nem encolhem sozinhos com zoom do navegador (Ctrl +/−) ou escala de tela diferente de 100%; gráficos limitados à largura do painel em telas pequenas |
| 3.5.2 | Linux: a janela do monitor se junta ao atalho instalado no dock (`--class` e `StartupWMClass`), sem parecer um segundo aplicativo |
| 3.6.0 | Segurança: a atualização só instala release assinada (Ed25519, chave pública embutida; `.sig` de cada `.exe` e `.deb` gerado no CI); aceite de acesso remoto só vindo do ícone instalado; imagem do técnico só em PNG (Pillow 13.0.0); prioridade do Modo Jogo desligada por padrão; processos essenciais do Linux protegidos; CI com token só leitura e instalador do PawnIO com SHA-256 fixo |
| 3.6.1 | Menu lateral mais compacto (cabe sem rolagem em ~880 px); acesso remoto: sai "Copiar como imagem", "Copiar como texto" vira "Copiar informações", cartões alinhados e centralizados |
| 3.7.0 | Janela própria do CNT Monitor (WebView2 no Windows, WebKitGTK no Linux, sem Edge/Chrome e com ícone da CNT); coleta a cada 1 s por padrão em todos os PCs (250 e 500 ms seguem nas opções; a partir daqui vale a escolha da pessoa); checagem de atualização a cada 1 h, sem travar o monitor; acesso remoto até 12 h; rolagem de print só na região sob o mouse |
| 3.7.1 | Acesso remoto abre dentro da janela do CNT Monitor (não mais no Chrome); sessão do técnico pedida pelo serviço; "Janelas separadas" por monitor removida (não funciona em janela embutida; configs antigas viram "lado a lado"). |
| 3.7.2 | Modo debug (desligado por padrão, em Configurações) que grava o estado do acesso remoto no log; o instalador baixado é apagado da pasta de atualização (só fica o último, até a próxima verificação); texto de atualização "a cada 1 hora" |
| 3.7.3 | Diagnóstico do acesso remoto: o agente grava no `servico.log` o estado de cada par de candidatos ICE (`remoto: pares ICE`) enquanto a conexão não fecha; o motivo do encerramento entra na linha `debug:` da bandeja. |
| 3.7.4 | Tela **Logs** (aparece no menu com o Modo debug ligado): mostra, filtra e copia o fim do `servico.log`, do `bandeja.log` e do log da atualização, só neste computador (`/api/logs`, com o token do ícone). No modo debug cada linha dos logs leva data e hora, as ações pedidas são registradas e o agente do acesso remoto grava o que o aioice/aiortc registram (cada teste de caminho, o retransmissor, erros do asyncio). |
| 3.8.0 | Acesso remoto passa a usar o retransmissor TURN por TLS na porta 443 (coturn em 5349, atrás do `stream` do nginx por SNI `turn.cntoficial.com.br`), para redes que só liberam web. O agente testa as opções TCP e coloca primeiro a que responde (o aiortc só usa o primeiro TURN); o diagnóstico ganhou "Retransmissor seguro por TLS (porta 443)"; o ponto de encontro devolve também `turns:`. |
| 3.9.0 | Tela **Chamadas** (Call flow, ligado em Configurações): captura SIP/PJSIP/WebSocket/HEP em qualquer porta, ladder e fluxo de cards, histórico com quem desligou, API com chave (validade padrão 6 h), monitoramento de outros PCs pelo ID do compartilhamento online (P2P, `aiortc`) com sincronização a cada 30 s por Call-ID; ID do acesso remoto passa a mudar só na virada do dia e clicar no ID/senha copia o valor. Exige `servidor-vps/encontro.js` novo na VPS. |
| 3.10.0 | Chamadas: o fluxo abre em pop-up com resumo no topo (origem, destino, tempo total e de conversa, quem desligou); cards movíveis e setas que saem pelo lado e chegam por cima; textos e gráficos menores. PDF gerado pelo Chrome (ou Edge) em segundo plano (`src/monitorpc/pdf.py`, `POST /api/pdf`), com links clicáveis entre o fluxo e os detalhes de cada mensagem; sem navegador, cai na janela de impressão. ID do acesso remoto fixo por computador (UUID da placa-mãe, `src/monitorpc/maquina.py`), igual depois de formatar; a troca diária e a ação `ajuda_trocar_id` saíram (a senha continua nova a cada sessão). |
| 3.11.0 | Fluxo Git por branches e PR (main protegida, skill `/monitor-tarefa`, gancho que bloqueia mexer na main) e changelog público em https://cntoficial.com.br/monitor/changelog/ (clicar na versão do rodapé abre a página); `servidor-vps/implantar.sh` atualiza a VPS depois de cada merge |
| 3.11.1 | Corrige o nginx da página de changelog (dava erro 500) e o `implantar.sh` (não busca tags na VPS) |
| 3.12.0 | Licença de Uso própria (versões até a 3.11.1 seguem GPL); atualização buscada primeiro na VPS, com assinatura conferida, para permitir repositório privado depois; estratégia Premium para empresas em `docs/ESTRATEGIA-PREMIUM.md` |
| 3.12.1 | Gancho que protege a main passa a olhar o repositório do comando (`cd`, `git -C`), ignora o que roda dentro de `ssh` e serve também ao hub |
| 3.13.0 | Cliente da licença Premium (`src/monitorpc/licenca.py`): entrar com a conta CNT da empresa em Configurações, permissão assinada Ed25519 (até 7 dias, renovada de hora em hora se faltarem menos de 3 dias), `licenca.tem("função")` para travar funções Premium; rotas locais `/api/licenca`. Nenhuma função existente foi travada |
| 3.14.0 | Premium `chamadas-historico`: histórico de chamadas na nuvem da empresa (`src/monitorpc/nuvem.py`; hub `/v1/chamadas`, dados cifrados, retenção e cota por licença). Aba **Nuvem (Premium)** na tela Chamadas; opção `chamadas_nuvem` desligada por padrão; envio a cada 30 s só do que o Call flow já guardou |

## Como publicar uma versão

O fluxo completo está em [FLUXO-GIT.md](FLUXO-GIT.md) e é conduzido pela skill `/monitor-tarefa`. Resumo:

1. Branch `tipo/descricao-curta` a partir da `main` (`feature`, `fix`, `refactor`, `hotfix`, `featfix`); a `main` é protegida.
2. Na PR: mudar `VERSAO` em `src/monitorpc/config.py`, acrescentar a entrada em `servidor-vps/changelog/changelog.json` e regenerar a página (`servidor-vps/changelog/gerar_changelog.py`). O CI (`.github/workflows/build.yml`) roda em PR, push na `main` e tag.
3. Merge (squash) depois do CI passar; o GitHub não deixa o autor aprovar a própria PR.
4. Tag `v<versão>` na `main` e push: o job `release` publica o `.exe` e o `.deb` com o SHA-256 de cada um.
5. Todo merge termina na VPS: `ssh cnt-vps 'bash /var/www/html_cntmonitor/servidor-vps/implantar.sh'` (atalho `cnt-vps` no `~/.ssh/config` de cada computador, fora do Git).

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
- 3.2.0 não testada em uso real: acesso remoto em 4G/5G, Ctrl+C com Excel num PC atendido, partes da janela fora do
  Chrome e o ícone AppIndicator em Ubuntu/Mint/KDE reais (Super+L e uso longo). Redes que só liberam 80/443 ficam de fora.
- 3.5.0 não testada instalada: ajudante de áudio aberto pelo serviço na conta do usuário (só testado direto, fora do serviço) e
  volume pelo acesso do celular.
- A pasta de trabalho é `D:\monitor-pc` (a antiga, no OneDrive, não deve ser usada).

## Onde está cada coisa

- Serviço, ícone, interface e módulos: ver "Como funciona por dentro" no README.
- VPS (site, ponto de encontro, TURN, download): `servidor-vps/` e a wiki do projeto em `.forge/wiki/`.
