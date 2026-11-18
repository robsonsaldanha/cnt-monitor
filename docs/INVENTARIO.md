# Inventário da spec de evolução × código atual

Levantamento de 2026-11-17 (branch `pacotao`, v3.5.0 + mudanças locais). Feito por busca dirigida e leitura parcial, sem rodar o
programa. Status: **implementada** (conferida no código), **parcial**, **só UI**, **ausente**, **desconhecida** (não verifiquei).

## Já existe

| Item | Status | Onde |
|---|---|---|
| Histórico SQLite (WAL, `synchronous=NORMAL`, vacuum incremental) | implementada | `src/monitorpc/banco.py:34-45` |
| Coleta configurável (`intervalo_ms`, sensores em ritmo próprio) | implementada | `motor.py` |
| CPU, memória, disco, rede, GPU, temperatura, processos | implementada | `motor.py`, `nt.py`, `gpu.py`, `temperatura.py`, `discos.py`, `rede.py` |
| Qualidade de rede (MOS, bufferbloat, percentis, quedas) | implementada | `qualidade.py`, `rede_programa.py` |
| Regras por programa, alertas, lentidão, comparar períodos | implementada | `regras.py`, `alertas.py`, `banco.py` |
| Limpeza, inicialização, serviços, planos de energia | implementada | `limpeza.py`, `inicializacao.py`, `servicos.py`, `energia.py` |
| Serviço Windows com ACL na pasta de dados | implementada | `servico.py:33-61` |
| Segredos com DPAPI; chaves SSH da VPS presas ao endereço | implementada | `vps.py`, `build/teste_seguranca.py` |
| Atualização com SHA-256 e instalador travado | parcial | `atualizacao.py` (hash vem das notas da release: A1) |
| Suíte de segurança | implementada | `build/teste_seguranca.py` |

## Conflitos com a spec (§7 "não fazer")

| Item | Status | Onde / observação |
|---|---|---|
| Esvaziar standby list | **conflita** | `jogo.py:43` (`NtSetSystemInformation`). Virar opt-in medido ou sair |
| Prioridade de processo e `kill()` | **conflita** | `jogo.py`, `regras.py`, `motor.py`. Falta allowlist de críticos e confirmação; checar se "realtime" é barrado |
| Serviço como SYSTEM, sem conta restrita | **conflita** | `servico.py` |
| UI/agente por HTTP local com token, não Named Pipe | difere | `servidor.py`; alternativa mais barata: manter HTTP e reduzir privilégios do serviço |
| Motor coleta a 500 ms (spec: CPU/RAM 1–2 s) | difere | `motor.py`; a UI lê de cache |

## Ausente ou mínimo

| Item | Status |
|---|---|
| SMART/NVMe | ausente |
| ETW, DPC/ISR | ausente |
| Reliability Monitor, eventos críticos do Windows | ausente |
| Retransmissões TCP (Windows) | ausente (há `tcp_estats` só no Linux) |
| Baseline e anomalia por máquina/horário | ausente |
| Diagnóstico explicável (observado / hipótese / causa) | ausente |
| Pipeline detect → evidência → snapshot → verify → rollback → auditoria | ausente |
| Medição antes/depois de uma otimização | ausente |
| SFC/DISM sob demanda, TRIM, indexação | ausente |
| Assinatura do executável e da atualização | ausente (A1) |
| Pausa da coleta em bateria/jogo/carga | desconhecida |
| Metas de consumo do agente (<1% CPU, <100 MB) | desconhecida: nunca medido; agente em Python pode estourar |

## Fora da spec

- Linux (Ubuntu/Debian/Mint): existe e é mantido (`src/monitorpc/linux/`). A spec fala em produto Windows; decisão do dono (2026-11-17): Linux continua, com as mesmas regras de segurança da spec traduzidas para Linux.
- Acesso remoto, captura/gravação de tela, acesso pelo celular, monitoramento de VPS: não aparecem na spec.

## Segurança: estado dos achados da auditoria de 17/11

| Achado | Estado |
|---|---|
| A2 Pillow 11.3.0 | corrigido nesta rodada: `pillow==13.0.0`; imagem recebida do técnico abre só como PNG (`remoto_agente.py`, `capturador.py`) |
| A3 SSH da VPS com senha e root | aplicado na VPS (fora do repositório; confirmado com `sshd -T` e nova conexão por chave): `PasswordAuthentication no`; `PermitRootLogin prohibit-password` (root segue entrando por chave, em uso real) |
| A4 token local aceita acesso remoto | mitigado: `ajuda_aceitar` só vale se vier do `MonitorPC.exe` instalado (`servidor.py`, Windows instalado) |
| A1 atualização confia só no GitHub | implementado no código (2026-11-17): releases assinadas com Ed25519 (`build/assinar_release.py` no CI, chave pública em `atualizacao.PUBLICA`); o atualizador não instala sem `.sig` válido, no Windows e no .deb. Falta o secret `RELEASE_SIGNING_KEY` no GitHub; sem ele o CI não publica |

## Ordem sugerida

1. Medir consumo do agente (Fase 0). 2. Resolver os conflitos do §7. 3. Pipeline de ação com rollback.
4. Só então SMART, ETW, baseline e anomalia, um PR por item.
