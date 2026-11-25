# Estratégia: funções Premium para empresas

Decisões do Robson (2026-11-24). Tudo o que existe hoje **continua 100% gratuito**. O Premium é só para **empresas**,
vendido com licença por prazo (X dias), e começa pela tela Chamadas.

## Princípios

- Gratuito continua gratuito: PDF e monitoramento P2P que já existem não viram pagos. Pago é o que for **novo ou
  melhorado** para empresas (primeiro: histórico salvo na VPS, PDF e exportação avançados, monitorar vários PCs).
- O valor Premium fica no que depende da **infraestrutura da CNT** (dados guardados na VPS, painel, suporte), porque
  uma trava só no programa instalado sempre pode ser contornada por quem tem o código.
- Preços tabelados: etapa posterior.

## Licença do software

- Versões até a 3.11.1: GPL v3, continuam livres para quem as obteve (não se revoga).
- A partir da 3.12.0: Licença de Uso própria (`LICENSE`). Autor único do código (as duas identidades de e-mail no Git são
  do Robson); dependências são permissivas ou LGPL, sem GPL forte.
- Não se reescreve o histórico do Git nem se apagam releases antigas.
- **Repositório privado em duas etapas**, porque os programas já instalados (até a 3.11.1) consultam as releases do GitHub
  **sem login**. Se o repo virar privado cedo, eles nunca mais veriam uma atualização.
  1. 3.12.0 (esta): o programa passa a buscar a atualização primeiro na VPS (`/download/ultima.json`, com a assinatura
     Ed25519 sempre conferida); o GitHub fica como plano B. O repo segue público.
  2. Depois que a maioria estiver na 3.12 ou mais nova: tornar o repo privado, com token no `baixar-instalador.py`,
     deploy key para o `git pull` da VPS, e o GitHub passa a servir só ao CI. Tarefa separada, decidida pelo Robson.

## Arquitetura

| Peça | Onde | Detalhe |
|---|---|---|
| Conta e login | hub `cntoficial.com.br` (Express + MySQL, repo `cnt-hub`) | Já existe conta única (`/entrar`, `/conta`, SSO). `/login` leva a ela. O e-mail do Robson é o usuário **master**. |
| Licenças | hub, migração `008_licencas.sql` | Empresa, licença (função, quantidade de PCs, início e fim), dispositivos ativados, auditoria. |
| Painel master | hub, `/admin` | Criar empresa, gerar/estender/revogar licença, ver uso. Só o master. |
| API de licença | hub, `/v1/licenca` | O programa pede a licença com a conta da empresa e recebe uma **permissão assinada** (Ed25519) com validade curta, renovada sozinha. Funciona offline até vencer. |
| Cliente no programa | este repo | Tela para entrar com o login da empresa, guarda a permissão, libera as funções Premium e mostra o que falta. |
| Dados Premium | VPS | Por empresa, isolados, com retenção por licença. |

## Fases

| # | Fase | Situação |
|---|---|---|
| 1 | Licença de uso própria e atualização pela VPS (3.12.0) | feita |
| 2 | Hub no GitHub (`robsonsaldanha/cnt-hub`, privado) com o mesmo fluxo de branches e PR | feita |
| 3 | Contas por empresa e licenças no hub: migração 008, painel `/admin` aba Licenças, API `/v1/licenca` (hub 1.8) | feita |
| 4 | Cliente da licença no programa: login da empresa, permissão assinada, renovação automática (3.13.0) | feita |
| 5a | `chamadas-historico`: histórico na nuvem da empresa, cifrado (hub 1.9, 3.14.0) | feita |
| 5b | `chamadas-relatorios` e `chamadas-multi-pc`: relatório, PDF, CSV, computadores e ao vivo (hub 1.10, 3.15.0) | feita |
| 5c | Modelo comercial por computador (R$ 9,90): endereço da empresa, cadastro por ID, conexão sem chave, uso do computador e painel online (hub 1.11, 3.16.0). Detalhes em [PLANO-EMPRESAS.md](PLANO-EMPRESAS.md) | feita |
| 6 | Preços e cobrança | **a definir pelo Robson** (tabela de preços; nada foi inventado) |
| 7 | Repositório do Monitor privado | **feita em 2026-11-25** (CI envia os instaladores à VPS por chave restrita; a VPS só publica com assinatura Ed25519 válida e faz `git pull` por deploy key somente leitura) |

Funções Premium previstas além de Chamadas entram pelo mesmo caminho: um identificador novo em `FUNCOES` (hub `src/licenca.js` e
`src/monitorpc/licenca.py`), o painel passa a oferecê-lo e o programa o trava com `licenca.tem("...")`.

## Segurança

- A permissão de licença é assinada com uma chave só do servidor; o programa só guarda a pública.
- Senhas e tokens nunca em log nem em changelog. Tentativas de login com limite por IP (o hub já tem).
- Dados de uma empresa nunca aparecem para outra: toda consulta filtra pela empresa da licença.
