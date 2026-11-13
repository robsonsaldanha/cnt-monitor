# CNT Monitor

Monitor do computador para **Windows 10/11 e Linux** (Ubuntu, Debian e Mint), mais detalhado que o Gerenciador de Tarefas, com otimizações seguras,
Modo Jogo, regras por programa, controle de ventoinhas, captura e gravação de tela no nível do ShareX, acesso
remoto por ID e senha, acesso pelo celular e monitoramento de servidores (VPS).

**Software livre e 100% gratuito**, sob a licença [GNU GPL v3](LICENSE): sem anúncios, sem conta, sem versão paga.
Você pode usar, estudar, modificar e redistribuir, desde que as versões redistribuídas continuem livres sob a mesma licença.

**Baixar:** https://monitor.cntoficial.com.br/baixar (entrega o arquivo do seu sistema) ou a
[última versão](https://github.com/robsonsaldanha/cnt-monitor/releases/latest):

- **Windows:** `CNTMonitor-Setup-x.y.z.exe`.
- **Linux** (Ubuntu 22.04+, Debian 12+, Mint 21+, 64 bits): `cnt-monitor_x.y.z_amd64.deb`, instalado com
  `sudo apt install ./cnt-monitor_x.y.z_amd64.deb`. Funciona em X11 e em Wayland.

Depois de instalado, ele se atualiza sozinho (Configurações → Atualizações).

## Funcionalidades

### Monitoramento

| Tela | O que mostra / faz |
|---|---|
| Visão geral | CPU por núcleo, memória, disco, rede, temperatura e GPU ao vivo (a cada 250 ms). |
| Programas | Cada programa com todos os processos que abriu, em árvore. Botão direito: mostrar, abrir pasta, fechar, encerrar. |
| Processos | Todos os processos com caminho, usuário, threads, handles, rede, GPU, conexões e IPs. |
| Rede e IPs | Quanto cada programa envia e recebe, com quais IPs conversa (nome, país e operadora), TCP × UDP, pacotes/s, protocolo pela porta (HTTPS, QUIC, DNS, Steam…) e as conexões abertas. |
| Detalhes de um IP | Ping 1/s enquanto você olha (latência mín./média/mediana/máx., P95, P99, desvio, jitter, perda e perda em rajada), MOS e pontuações, gráfico de tráfego, programas, RTT e retransmissões de cada conexão TCP, traceroute e Path MTU. Os IPs de um programa ou processo também mostram latência, jitter e perda. |
| Análise de rede | **Conexões em andamento** com a qualidade de cada uma (taxa, pacotes/s, RTT e retransmissões do TCP, jitter de chegada e maior silêncio do UDP, ping e nota MOS). **Teste real até um destino** por ICMP, TCP (tempo de conexão na porta) ou UDP, com gráfico de latência e faixa de jitter; no UDP, silêncio não conta como perda e o eco do servidor da CNT aparece como referência separada. **Monitoramento detalhado por programa** (até 4, para comparar): taxa segundo a segundo, pacotes, TCP × UDP, latência e jitter até os servidores do programa, chamadas (RTP: jitter RFC 3550, perda, ordem e codec), MOS e o que mais competia pela rede; ao parar, exporta um `.log` (uma linha por segundo, separada por TAB). Em Ferramentas: grava uma análise contínua (até 24 h) para o roteador, a internet e os IPs escolhidos: quedas, disponibilidade, MTTR/MTBF, bufferbloat, pontuações de saúde e contadores do Windows (retransmissões TCP, erros UDP, descartes, fragmentação, erros por placa). Mais: teste de velocidade com bufferbloat (só no clique), teste de DNS, tempos de HTTP/TLS e Wi-Fi (sinal, RSSI, canal, redes no mesmo canal). |
| Temperatura e hardware | CPU, GPU, SSD, placa-mãe, ventoinhas, clocks e consumo (LibreHardwareMonitor + PawnIO). |
| Histórico | 7 dias de histórico (configurável) do PC, de cada programa e das temperaturas. |
| Lentidão | Momentos em que o PC ficou lento, com os programas culpados. |
| Alertas | Alertas na tela por CPU, memória, disco, temperatura e outros limites que você define. |
| Relatório / Comparar | Relatório completo (HTML, PDF, CSV) com recomendações e comparação lado a lado de dois períodos. |
| Mini painel | Painel pequeno sempre por cima, inclusive de jogos em janela ou sem borda (o mouse passa através dele), com os números principais. Em tela cheia exclusiva nenhuma janela aparece sobre o jogo: use “Tela cheia em janela”. |

### Desempenho e manutenção

| Tela | O que faz |
|---|---|
| Modo Jogo | Fecha o que pesa, plano de alto desempenho, prioridade ao jogo, silencia notificações e mede o FPS (PresentMon). |
| Otimizar | Diagnóstico rápido, limpeza de disco segura, cache dos navegadores, otimizar unidades, componentes do Windows e detecção de vazamento de memória. |
| Regras por programa | Prioridade, núcleos, não deixar abrir, avisar ao abrir, limite de memória (avisar ou encerrar). |
| Inicialização | Programas que abrem com o Windows: ver, ligar e desligar. |
| Energia | Planos e ajustes de energia, modo de energia do Windows. |
| Serviços | Serviços de terceiros: iniciar, parar, reiniciar e mudar o tipo de inicialização (os do Windows ficam protegidos). |

### Personalizar

| | |
|---|---|
| Barra e relógio (Windows) | Alinhamento dos ícones, Pesquisa, Visão de tarefas, Widgets, ocultar a barra sozinha, segundos no relógio, “Finalizar tarefa” no clique direito, extensões e arquivos ocultos no Explorer e menu de contexto clássico. Lido e gravado no próprio Windows, sem brigar com as Configurações dele. |
| Ícones ao lado do relógio (Windows) | Escolher quais ficam visíveis e quais vão para a setinha (^). |
| Tema e papel de parede | Claro/escuro agora ou por horário (só na virada, sem desfazer o que você trocar à mão), papel de parede para cada tema e rotação a partir de uma pasta. No Linux: GNOME/Ubuntu, Cinnamon/Mint, KDE e XFCE. |
| Perfis | Trabalho, Jogo, Apresentação (e os seus): tema, papel, plano de energia, Modo Jogo, mini painel, barra, ícones da área de trabalho, não dormir e programas para abrir, num clique, no menu do ícone ou por atalho. |
| Lançador rápido | Alt+Espaço (configurável): apps do Iniciar (inclusive da Microsoft Store), janelas abertas, telas do Monitor, páginas das Configurações, contas, `?pesquisa` e `>comando`. No Linux, `cnt-monitor --lancador` num atalho do sistema. |
| Renomear em massa | Procurar e trocar (texto ou regex), maiúsculas/minúsculas, prefixo, sufixo e numeração, com prévia, conflitos marcados e desfazer. |
| Remapear teclas (Windows) | Tecla → tecla, tecla → nada, atalho → atalho e tecla ou atalho → abrir programa, em todos os programas ou só num. |

A grade de ícones ao lado do relógio (como o mod do Windhawk) não está aqui: no Windows 11 ela só é possível com código
nativo injetado no Explorer.

### Ventoinhas

- Controle **fixo** ou por **curva de temperatura** (pontos, sensor à escolha e histerese) de cada canal que o hardware
  libera: processador, water cooler, placa de vídeo, ventoinhas da placa-mãe.
- **Sem conflito com outros programas:** se o Gigabyte Control Center (ou outro) mudar uma ventoinha, o Monitor só
  acompanha o valor novo, sem brigar e sem aviso; você retoma o controle aqui quando quiser.
- Nome próprio por ventoinha, mínimo de velocidade e proteção de temperatura configuráveis (com explicação “?” em cada opção).
- Ao parar o serviço, tudo volta ao automático. Só pode ser alterado no próprio PC.

### Captura e gravação de tela

- **Capturas por atalho:** região (lupa, linhas-guia e “ímã” que reconhece a janela e as partes dela, como painéis, barras e áreas da página; a roda do mouse escolhe a parte maior ou menor), várias regiões numa imagem, janela ativa,
  monitor, tela inteira, repetir a última região, com atraso e **com rolagem** até o fim da página (ou até “Parar e salvar”).
- **Anotação antes de capturar**, na própria seleção, com barra móvel: setas, retângulo, elipse, linha, caneta, texto,
  números, marca-texto, desfocar, pixelar, tarja e cursor. Enter salva, Ctrl+C só copia (sem salvar) e Esc fecha sem salvar.
  Opção de afastar o mouse antes da foto.
- **Aviso próprio** do programa: o clique abre a imagem, a pasta, o editor ou o link; botões de copiar, editar e enviar no aviso.
- **Tarefas após a captura:** copiar, salvar, salvar como, menu de tarefas rápidas, janela pós-captura, embelezar,
  borda e marca d’água, fixar na tela, imprimir, miniatura, copiar arquivo/caminho/pasta, OCR (do próprio Windows),
  ler código QR, análise por IA (com a chave do usuário), ações externas, pré-envio e apagar depois de enviar.
- **Envio:** Catbox, Litterbox, Uguu e tmpfiles (sem login), Imgur (com Client-ID gratuito) e uploader personalizado,
  com importação de `.sxcu` do ShareX. Envio de arquivo, área de transferência e texto.
- **Tarefas após o envio:** copiar o link (URL, Markdown, HTML ou BBCode), encurtar (is.gd), compartilhar, QR do link,
  abrir no navegador e janela pós-envio.
- **Gravação** MP4 e GIF com pausa, cronômetro, áudio e moldura da área gravada (ffmpeg).
- **Editor** na janela do programa: setas com estilos, formas, tracejado, numeração 1/A/a/I, lupa, holofote, pixelar,
  desfocar, recortar, desfazer/refazer.
- **Histórico** com miniaturas, favoritas, etiquetas, renomear, importar pasta e estatísticas.
- Fluxos de trabalho com atalho próprio, PNG/JPG/WebP, remoção de metadados, verificador de hash, seletor de cor,
  régua, barra de ferramentas de ações e “CNT Monitor” no botão direito do Explorer.

### Acesso remoto

- Tela inicial do programa (dá para trocar nas Configurações). Cada PC mostra um **ID de 9 números** e uma
  **senha temporária**, com botões para copiar como texto (o principal) ou como imagem.
- O técnico digita os dois e quem está no computador recebe um **pop-up na frente das outras janelas** para
  **Aceitar** ou **Recusar**, com um código de conferência.
- Vários atendimentos ao mesmo tempo, cada um numa janela que abre ocupando a tela. A tela remota fica embaixo de uma
  **barra própria** (nada por cima dela): ID e tempo, monitores, layout, Ajustar/Tamanho real, Tela cheia, Opções,
  Área de transferência, Arquivos e Encerrar.
- **Vários monitores:** lado a lado (como estão de verdade), um ou outro (botões 1 · 2) ou **janelas separadas**, uma
  por monitor, para arrastar cada uma para um monitor do técnico. O padrão fica em Configurações → Acesso remoto.
- **Atalhos do Windows** (toggle em Opções; padrão em Configurações): ligado, com uma tela do acesso na frente, Win,
  Win+R, Alt+Tab, Alt+F4, Ctrl+Esc, Ctrl+Shift+Esc, Print Screen e Ctrl+W/T/N vão só para o outro PC; em qualquer
  outra janela do técnico ficam no PC dele. Desligado, ficam sempre no PC do técnico. Quem faz isso é o ícone da
  bandeja do técnico (gancho de teclado só enquanto a sessão está na frente). Ctrl+Alt+Del vai pelo botão em Opções.
- **Área de transferência compartilhada** (texto, imagem e arquivos) nos dois sentidos, num só, ou desligada. Quem é
  atendido pode proibir o técnico de ler a área de transferência dele (Configurações).
- **Arquivos** pela própria conexão criptografada: enviar (escolher, arrastar ou Ctrl+V), buscar e baixar do outro PC
  com as permissões de quem está nele, SHA-256 conferido. Quem é atendido vê o histórico (90 dias) na tela Acesso remoto.
- Com o técnico conectado, quem é atendido recebe uma notificação do Windows, o ícone ganha um ponto vermelho e o
  menu do ícone mostra **“Encerrar conexão do técnico”**.
- A tela e os comandos vão direto de um PC ao outro, **criptografados de ponta a ponta**; o servidor da CNT só
  apresenta os dois lados e, quando a ligação direta é impossível, um retransmissor repassa os pacotes sem conseguir lê-los.
- A senha troca depois de cada acesso e o ID quando o PC reinicia.

### Linux

O mesmo programa e a mesma interface, com as partes do sistema trocadas pelas do Linux:

| Recurso | No Linux |
|---|---|
| Serviço | unidade systemd `cnt-monitor` (root); dados em `/var/lib/cnt-monitor` |
| Rede por programa e chamadas (RTP) | captura `AF_PACKET` + tabela de sockets do kernel; ping ICMP próprio (traceroute e MTU), RTT e retransmissões pelo `ss`, contadores de `/proc/net/snmp`, Wi-Fi pelo `iw`, DNS pelo `resolvectl` |
| Sensores e ventoinhas | `/sys/class/hwmon` (o que o lm-sensors mostra), bateria e `nvidia-smi`; ventoinhas pelos canais `pwm` que o chip libera (em muitos notebooks só leitura) |
| GPU | `/sys/class/drm` (AMD, Intel) e `nvidia-smi`; uso por processo pelo `fdinfo` |
| Energia | perfis do power-profiles-daemon (economia, equilibrado, desempenho) ou o governor da CPU; brilho pelo `/sys/class/backlight` |
| Inicialização | autostart do XDG (`/etc/xdg/autostart` e `~/.config/autostart`) |
| Serviços | `systemctl` (os essenciais ficam protegidos) |
| Otimizar | `/tmp`, `~/.cache`, miniaturas, lixeira, relatórios de erro, pacotes do apt, journal antigo, `fstrim` e `apt autoremove` |
| Modo Jogo | perfil de desempenho, prioridade (`nice`) e fechar programas; FPS só no Windows (no Linux use o MangoHud) |
| Ícone e janela | ícone na bandeja (no GNOME, com a extensão AppIndicator, que já vem no Ubuntu); janela no Chrome/Chromium/Edge em modo aplicativo ou no navegador padrão |
| Acesso remoto (atendido) | **X11:** tela pelo XRandR + XShm e mouse/teclado pelo XTest. **Wayland:** portal RemoteDesktop/ScreenCast + PipeWire; o próprio sistema pergunta, a cada sessão, quais telas compartilhar. Área de transferência pelo `xclip` / `wl-clipboard` |
| Acesso remoto (técnico) | igual ao Windows; os atalhos (Super, Alt+Tab…) vão para o outro PC pelo X11 com a sessão na frente, ou em tela cheia em qualquer sessão |

Windows acessa Linux e Linux acessa Windows: o protocolo é o mesmo.

**Limites do Linux (regras do próprio sistema):** no Wayland a pessoa atendida autoriza o compartilhamento a cada
sessão e a tela de login e a de bloqueio não podem ser vistas nem controladas; o pedido de senha de administrador
(polkit) aparece para o técnico, mas quem digita é a pessoa; Ctrl+Alt+Del não tem a tela segura do Windows (vai como
teclas). A captura e gravação de tela no estilo ShareX, o mini painel e o FPS ainda são só do Windows.

### Servidores (VPS)

CPU, memória, disco, rede, serviços systemd, containers Docker e sites dos seus servidores Linux via SSH, com
histórico, processos, serviços e alertas (ex.: disco cheio). A identidade do servidor é conferida antes de mandar a senha.

### Configurações e integração

- Acesso pelo celular (com QR code), várias janelas, tela inicial, atualização automática, backup e restauração das configurações.
- API somente leitura para outros programas: `GET http://127.0.0.1:48731/api/v1/resumo` (veja [docs/API.md](docs/API.md)).

## Segurança

- O que é do Windows nunca é encerrado nem alterado (processos críticos, pasta do Windows, Defender, serviços do Windows).
- A janela só funciona neste PC (`127.0.0.1`). Alterações exigem um token secreto que só a janela conhece.
- O acesso pelo celular é opcional, exige senha (guardada como hash), libera a porta só na rede **privada** do firewall
  e pode ficar em “somente ver”. Pelo celular não dá para mudar ventoinhas, captura, ações externas, IA nem fluxos.
- Senhas e chaves de VPS e a chave da IA ficam criptografadas com o DPAPI do Windows, fora do `config.json` e fora do backup.
  No Linux, que não tem DPAPI, ficam em arquivos só do dono (0600: o root para as de VPS, o usuário para a da IA).
- A captura de tela roda no ícone perto do relógio, com as permissões do usuário: o serviço nunca lê os arquivos de
  captura do disco (o editor recebe a imagem pelo ícone). Os atalhos com Print Screen desligam só para o seu usuário a opção
  do Windows que abre a Ferramenta de Captura, e ela volta quando o Print Screen sai dos atalhos ou o ícone é fechado.
- A limpeza de disco só apaga arquivos que o Windows recria (temporários com mais de 1 dia, relatórios de erro,
  cache de atualizações…). A limpeza dos navegadores apaga só o cache, não senhas, histórico, favoritos ou cookies.

## Como funciona por dentro

- `MonitorPCServico.exe`: serviço do Windows (SYSTEM). Lê todos os processos numa chamada do Windows (NT),
  mede a rede por processo via ETW, GPU por contadores, sensores e ventoinhas pelo LibreHardwareMonitor, FPS pelo PresentMon.
- `MonitorPC.exe`: ícone perto do relógio, mini painel, captura de tela, avisos, pop-up de aceite do acesso remoto
  e janela (Edge em modo aplicativo). A gravação de tela usa o ffmpeg, baixado na primeira gravação para
  `%LOCALAPPDATA%\MonitorPC\ffmpeg` (SHA-256 conferido).
- `remoto\`: agente do acesso remoto (WebRTC com aiortc).
- Dados em `C:\ProgramData\MonitorPC` (`historico.db`, `config.json`, `servico.log`). Os nomes internos (serviço
  `MonitorPC`, pasta de dados, executáveis) continuam os da época em que o programa se chamava Monitor do PC, para
  as instalações antigas seguirem atualizando.
- `servidor-vps/`: o que roda em https://monitor.cntoficial.com.br (`/var/www/html_cntmonitor`, clone deste
  repositório): site com o download, ponto de encontro do acesso por ID (`encontro.js`, Node sem dependências,
  serviço `cnt-monitor-encontro`, que também responde o eco UDP na porta 48790 usado como referência nos testes UDP)
  e a configuração do coturn (retransmissor). Atualizar: `git pull` na VPS e
  `sudo systemctl restart cnt-monitor-encontro`; a porta 48790/udp precisa estar liberada no firewall da VPS.

No Linux: `/opt/cnt-monitor/servico/cnt-monitor-servico` (serviço `cnt-monitor`), `/opt/cnt-monitor/cnt-monitor`
(ícone e janela), `/opt/cnt-monitor/remoto/` (agente) e o código específico em `src/monitorpc/linux/`: cada módulo
preso ao Windows importa, no fim, as mesmas funções e classes da versão Linux, então o motor, a API e a
interface são os mesmos nos dois sistemas.

## Gerar o instalador e testar

O GitHub Actions (`.github/workflows/build.yml`) gera o instalador num Windows e o `.deb` num Ubuntu 22.04, e testa
serviço, API, ações, instalação e desinstalação nos dois em cada push e PR. Uma tag `v{versão}` (ou rodar
manualmente com `workflow_dispatch`) publica a release com os dois arquivos e o SHA-256 de cada um, usados pela
atualização automática (`SHA256:` do `.exe` e `SHA256 (deb):` do `.deb`).

O `.deb` sai de `instalador/linux/montar-deb.sh` (num Linux com `build/requirements-linux.txt` instalado).

Para testar a interface fora do Windows: `python dev/demo.py` e abra http://127.0.0.1:48731/

Testes automáticos (rodam sem tocar na tela):

- `python build/teste_captura.py`: atalhos, emenda da rolagem, anotações, uploader e `.sxcu`, efeitos, QR, hash, gravação, aviso, IA, histórico, opções.
- `python build/teste_ventoinhas.py`: curvas, histerese, convivência com outros programas, proteção e liberação.
- `python build/teste_personalizar.py`: tema por horário, rotação do papel, perfis, renomear em massa (prévia,
  conflitos, troca e desfazer), lançador e calculadora, remapeamento de teclas e, no CI, o registro do Windows.
- `python build/teste_encontro.py`: ponto de encontro do acesso por ID, troca de ID e conexão.
- `python build/teste_remoto.py` e `python build/teste_seguranca.py`: acesso remoto e regras de segurança.
- `python build/teste_rede.py` e `python build/teste_rede_programa.py`: estatísticas de rede, RTP, sondas TCP/UDP,
  monitoramento por programa e o `.log`.
- `python build/teste_linux.py`: partes do Linux (leitores de `/proc`, `ss`, `iw`, systemd e autostart; como root,
  captura de pacotes e ping; com Xvfb e xclip, tela, mouse, teclado e área de transferência do acesso remoto).

## Contribuir

Contribuições são bem-vindas: abra uma issue ou um pull request. Ao contribuir, você concorda em licenciar o seu
código sob a GPL v3 (ou posterior).

## Licença

Copyright (C) 2026 Róbson Saldanha

Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob os termos da GNU General Public
License, publicada pela Free Software Foundation, na versão 3 da licença ou (a seu critério) qualquer versão posterior.
Ele é distribuído na esperança de ser útil, mas **sem nenhuma garantia**; veja o arquivo [LICENSE](LICENSE).

Componentes de terceiros mantêm as próprias licenças: LibreHardwareMonitor (MPL-2.0), PawnIO, PresentMon (MIT),
ffmpeg (baixado à parte, GPL), aiortc e PyAV (BSD), paramiko e pystray (LGPL), python-xlib (LGPL), mss (MIT), jeepney (MIT), Pillow, psutil, pywin32, qrcode,
zxing-cpp (Apache-2.0), maxminddb (Apache-2.0), a base de países e operadoras [IP Geolocation by DB-IP](https://db-ip.com)
(CC BY 4.0, embutida e consultada no próprio PC) e a fonte Barlow ([SIL OFL 1.1](src/ui/fontes/OFL-LICENSE.txt)).
