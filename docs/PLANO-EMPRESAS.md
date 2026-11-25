# Plano comercial: CNT Monitor para empresas

Decidido pelo Robson em 2026-11-25. Substitui, para a venda, o desenho de "login da empresa dentro do programa" do
[ESTRATEGIA-PREMIUM.md](ESTRATEGIA-PREMIUM.md) (a base técnica de licenças e permissão assinada continua a mesma).

## Regras do negócio

- **Um programa só.** O gratuito continua gratuito, sem conta e sem licença. O mesmo programa, quando conectado a uma empresa,
  libera as funções para empresas.
- **R$ 9,90 por computador ativo** (o mesmo valor para qualquer computador, de administrador ou não). O preço fica em
  `config.preco_computador_centavos` no hub (painel master → Textos e contato). O período é **por mês** (a confirmar) e ainda
  não há meio de pagamento ligado: o painel só mostra quantos computadores estão ativos e o total.
- Cada empresa tem **uma página própria**: `cntoficial.com.br/<nome-da-empresa>`.
- Administrar a empresa **não exige o programa instalado**: as chamadas, o relatório e o uso dos computadores aparecem no
  painel online.

## Como funciona

1. A CNT (painel master `/admin`, aba Licenças) cria a empresa com o endereço (`slug`), liga a conta CNT do dono e emite a
   assinatura (licença com as funções e o prazo).
2. O dono entra em `cntoficial.com.br/<empresa>` com a conta CNT e **cadastra o ID** de cada computador (o ID de 9 números
   que o programa mostra em Acesso remoto, fixo por computador).
3. No computador, em **Configurações → Empresa (Premium)**, a pessoa digita só o endereço da empresa. O programa envia o ID;
   se o administrador já o cadastrou, o hub devolve uma **chave do computador** (sem senha, sem API para digitar).
4. Daí em diante o programa envia, sozinho:
   - as **chamadas telefônicas** já encerradas (números, horários, qualidade; cifradas no hub) e as chamadas em andamento;
   - o **uso do computador**: processador, memória e disco em %, tempo ligado, sistema, modelo do processador, memória e
     discos (uma medida por minuto no máximo, guardada 35 dias).
5. O painel mostra os computadores (online/offline, uso agora, ficha da máquina), **sugere upgrade** (memória média acima de
   85%, processador acima de 80%, disco acima de 90%), o relatório de chamadas (PDF e CSV no programa; CSV no painel) e o
   valor a pagar.

## O que NUNCA é enviado

Tela, arquivos, senhas, teclas digitadas, nomes de janelas, lista de programas ou de processos. O programa mostra isto ao
conectar, e conectar é o aceite do funcionário/empresa: não há envio sem a pessoa digitar o endereço.

## Segurança e limites conhecidos

- A chave do computador fica só como hash no hub; "Reiniciar conexão" no painel libera a troca de máquina; suspender corta
  o envio e a cobrança na hora. O ID é fixo por computador, mas **quem conhece o endereço da empresa e um ID cadastrado
  ainda não conectado poderia conectar primeiro**: por isso o administrador deve conectar logo após cadastrar e conferir o
  computador no painel (a conexão aparece com data, IP e nome da máquina). Se algo estiver errado, "Reiniciar conexão".
- Revogar a licença ou o computador corta o acesso aos dados na hora (o hub confere a cada pedido).

## Fases

| Fase | Situação |
|---|---|
| Hub 1.11 e programa 3.16: endereço da empresa, cadastro por ID, conexão, métricas, painel online, cobrança calculada | feita (aguardando publicação) |
| Pagamento (Pix/boleto/cartão), notas e cobrança recorrente | **a definir** (qual meio, qual emissor) |
| Acesso remoto **sem senha** pelo painel da empresa | **a desenhar antes de codar** (abaixo) |
| Repositório do Monitor privado | feita (2026-11-25), depois da v3.16.0 passar pelo caminho novo |

### Acesso remoto sem senha (pedido, ainda não feito)

Hoje o acesso remoto exige ID e senha, com a aceitação de quem está no computador. Pelo painel da empresa, o administrador
abriria o computador do funcionário sem a senha dele. É a função mais sensível do produto: antes de qualquer código, definir
(1) se o funcionário é avisado e vê um indicador enquanto alguém acessa, (2) se há registro de quem acessou, quando e por
quanto tempo (proposta: sempre, visível ao funcionário e à empresa), (3) como o painel prova ao programa que o acesso é
autorizado (proposta: pedido assinado pelo hub, curto, de uso único, vinculado à chave do computador) e (4) se vale para
todos os computadores ou só os marcados pelo dono. Esses pontos também importam para a LGPD e para a relação de trabalho.
