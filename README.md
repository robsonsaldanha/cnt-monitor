# Monitor do PC

Monitor de uso do computador (Windows) mais detalhado que o Gerenciador de Tarefas.

- **Visão geral**: CPU (por núcleo), memória, disco, rede, temperatura e GPU ao vivo.
- **Programas**: cada programa soma tudo o que ele abriu (ex.: Maestri + agentes). Memória, CPU, disco, rede, conexões e IPs.
- **Processos**: todos os processos com caminho, linha de comando, usuário, threads, handles, conexões e IPs.
- **Rede e IPs**: quanto cada programa/processo envia e recebe, com quais IPs conversa (TCP e UDP, com nome DNS) e todas as conexões abertas.
- **Temperatura**: CPU, GPU, SSD, placa-mãe, ventoinhas, clocks e consumo (LibreHardwareMonitor + PawnIO).
- **Histórico** (7 dias, limpeza automática), **Momentos de lentidão** e **Relatório** (PDF/CSV).

## Como funciona

- `MonitorPCServico.exe` — serviço do Windows (roda como administrador/SYSTEM). Coleta tudo a cada 5 s,
  mede a rede por processo via ETW e lê os sensores. Expõe a API só em `127.0.0.1:48731`.
- `MonitorPC.exe` — ícone perto do relógio; abre a janela do programa.
- Dados em `C:\ProgramData\MonitorPC` (histórico apagado automaticamente após 7 dias).

## Gerar o instalador

O GitHub Actions (`.github/workflows/build.yml`) gera o `MonitorDoPC-Setup-x.y.z.exe` num Windows,
testa o serviço, a instalação e a desinstalação. Para publicar uma versão: crie uma tag `vX.Y.Z`.

Para testar a interface fora do Windows: `python dev/demo.py` e abra http://127.0.0.1:48731/
