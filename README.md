# Monitor do PC

Monitor do computador (Windows 10/11) mais detalhado que o Gerenciador de Tarefas — com otimizações seguras,
Modo Jogo, regras por programa, acesso pelo celular e monitoramento de servidores (VPS).

**Baixar:** [última versão](https://github.com/robsonsaldanha/monitor-pc/releases/latest) → `MonitorDoPC-Setup-x.y.z.exe`.
Depois de instalado, ele se atualiza sozinho (Configurações → Atualizações).

## O que ele faz

| Tela | O que mostra / faz |
|---|---|
| Visão geral | CPU por núcleo, memória, disco, rede, temperatura e GPU ao vivo (a cada 250 ms). |
| Programas | Cada programa com todos os processos que abriu, em árvore. Botão direito: mostrar, abrir pasta, fechar, encerrar. |
| Processos | Todos os processos com caminho, usuário, threads, handles, rede, GPU, conexões e IPs. |
| Rede e IPs | Quanto cada programa envia e recebe, com quais IPs conversa (com nome) e as conexões abertas. |
| Temperatura e hardware | CPU, GPU, SSD, placa-mãe, ventoinhas, clocks e consumo (LibreHardwareMonitor + PawnIO). |
| Captura de tela | Prints por atalho (região com lupa e “ímã” de janela, janela ativa, monitor, tela inteira, com atraso, com rolagem até o fim da página), anotação na própria seleção antes de capturar (seta, retângulo, elipse, linha, caneta, texto, números, marca-texto, desfocar, pixelar, tarja, cursor), tarefas pós-captura e pós-envio, pasta escolhida pelo usuário, gravação em MP4/GIF, texto da tela (OCR do Windows), seletor de cor, régua, editor, histórico e envio com link (Catbox, Litterbox, Uguu, tmpfiles, Imgur ou uploader personalizado). Opções em Captura de tela › Opções e no menu do ícone. |
| Modo Jogo | Fecha o que pesa, plano de alto desempenho, prioridade ao jogo, silencia notificações e mede o FPS. |
| Otimizar | Diagnóstico rápido, limpeza de disco segura, cache dos navegadores, otimizar unidades, componentes do Windows e detecção de vazamento de memória. |
| Regras por programa | Prioridade, núcleos, não deixar abrir, avisar ao abrir, limite de memória (avisar ou encerrar). |
| Inicialização / Energia / Serviços | Programas que abrem com o Windows, planos e ajustes de energia, serviços de terceiros. |
| Histórico / Lentidão / Alertas | 7 dias de histórico (configurável), momentos de lentidão com os culpados e alertas na tela. |
| Relatório / Comparar períodos | Relatório completo (HTML, PDF, CSV) com recomendações e comparação lado a lado de dois períodos. |
| Servidores (VPS) | CPU, memória, disco, rede, serviços systemd, containers Docker e sites dos seus servidores Linux via SSH. |
| Configurações | Mini painel, várias janelas, acesso pelo celular (com QR code), chave da API, backup das configurações. |

### Segurança

- O que é do Windows nunca é encerrado nem alterado (processos críticos, pasta do Windows, Defender, serviços do Windows).
- A janela só funciona neste PC (`127.0.0.1`). Alterações exigem um token secreto que só a janela conhece.
- O acesso pelo celular é opcional, exige senha (guardada como hash), libera a porta só na rede **privada** do firewall
  e pode ficar em “somente ver”.
- Senhas e chaves de VPS ficam criptografadas com o DPAPI do Windows (`segredos.bin`), fora do `config.json` e fora do backup.
- A captura de tela roda no ícone perto do relógio, com as permissões do usuário: o serviço nunca lê os arquivos de
  captura do disco (o editor recebe a imagem pelo ícone). Os atalhos com Print Screen desligam só para o seu usuário a opção
  do Windows que abre a Ferramenta de Captura, e ela volta quando o Print Screen sai dos atalhos ou o ícone é fechado.
- A limpeza de disco só apaga arquivos que o Windows recria (temporários com mais de 1 dia, relatórios de erro,
  cache de atualizações…). A limpeza dos navegadores apaga só o cache — não senhas, histórico, favoritos ou cookies.

## API para outros programas (somente consulta)

`GET http://127.0.0.1:48731/api/v1/resumo` — veja [docs/API.md](docs/API.md).

## Como funciona por dentro

- `MonitorPCServico.exe` — serviço do Windows (SYSTEM). Lê todos os processos numa chamada do Windows (NT),
  mede a rede por processo via ETW, GPU por contadores, sensores pelo LibreHardwareMonitor, FPS pelo PresentMon.
- `MonitorPC.exe` — ícone perto do relógio, mini painel, captura de tela e janela (Edge em modo aplicativo).
  A gravação de tela usa o ffmpeg, baixado na primeira gravação para `%LOCALAPPDATA%\MonitorPC\ffmpeg` (SHA-256 conferido).
- Dados em `C:\ProgramData\MonitorPC` (`historico.db`, `config.json`, `servico.log`).

## Gerar o instalador

O GitHub Actions (`.github/workflows/build.yml`) gera o instalador num Windows e testa serviço, API, ações,
instalação e desinstalação. Rodar manualmente (`workflow_dispatch`) publica a release `v{versão}` com o SHA-256
usado pela atualização automática.

Para testar a interface fora do Windows: `python dev/demo.py` e abra http://127.0.0.1:48731/

Testes da captura de tela (atalhos, emenda da rolagem, anotações, uploader, opções): `python build/teste_captura.py`
