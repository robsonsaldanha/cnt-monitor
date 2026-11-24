# Fluxo de trabalho com Git

A `main` é **produção**. Ninguém mexe direto nela: todo trabalho acontece numa branch própria, entra por Pull Request (PR)
e, depois do merge, o instalador novo sai e a VPS é atualizada.

## Regras

1. **Nunca** commitar, dar push ou editar arquivos direto na `main`. Isso é bloqueado em dois lugares:
   - no GitHub, pela proteção da branch `main` (só aceita mudança por PR);
   - no Claude Code, pelo gancho `.claude/hooks/bloquear-main.py` (configurado em `.claude/settings.json`).
2. Toda tarefa começa baixando a `main` mais recente e criando uma branch **a partir dela**.
3. Nome da branch: `tipo/descricao-curta`, em minúsculas, com hífens, sem acentos.

| Tipo | Quando usar | Exemplo |
|---|---|---|
| `feature` | funcionalidade nova | `feature/tela-login` |
| `fix` | correção de bug | `fix/correcao-login-email` |
| `refactor` | reorganizar código sem mudar o comportamento | `refactor/separar-leitor-rede` |
| `hotfix` | correção urgente de algo que está quebrado em produção | `hotfix/servico-nao-inicia` |
| `featfix` | funcionalidade nova **e** correção na mesma entrega | `featfix/tela-login-bug-horario` |

4. Cada PR traz **a versão nova** (`VERSAO` em `src/monitorpc/config.py`) e **a entrada no changelog**
   (`servidor-vps/changelog/changelog.json`), e a página gerada (`python servidor-vps/changelog/gerar_changelog.py`).
5. Versão: `feature` e `featfix` sobem o número do meio (3.10.0 → 3.11.0); `fix`, `hotfix` e `refactor` sobem o último
   (3.10.0 → 3.10.1).
6. O merge é **squash**. O GitHub não deixa o autor aprovar a própria PR, então a PR não exige aprovação, só os testes.

## O ciclo de uma tarefa

1. `git switch main` e `git pull --ff-only`: baixa a `main` mais recente.
2. `git switch -c tipo/descricao-curta`: cria a branch.
3. Trabalhar, testar (`build/teste_*.py`; nunca testar na tela do Robson) e fazer o commit.
4. `git push -u origin tipo/descricao-curta` e abrir a PR para a `main`.
5. **A cada PR aberta, decidir:**
   - **Mergear agora:** merge (squash), tag `v<versão>` na `main`, o CI gera o instalador, a VPS é atualizada (abaixo).
   - **Continuar trabalhando:** voltar ao passo 1 e criar outra branch a partir da `main`. Dá para empilhar várias PRs
     antes de mergear. Se outra PR entrar antes, atualize a sua branch com a `main` e acerte o número da versão e o
     changelog.

## Depois do merge: instalador e VPS (sempre)

**Todo merge na `main` publica o instalador novo e atualiza a VPS.** Não existe merge sem isso.

1. Criar e enviar a tag `v<versão>` na `main`. O job `release` do CI (`.github/workflows/build.yml`) gera
   `CNTMonitor-Setup-<versão>.exe`, o `.deb` e as assinaturas, e publica a release no GitHub.
2. Esperar o CI terminar com sucesso (Windows, Linux e release).
3. Na VPS, rodar `servidor-vps/implantar.sh`: puxa a `main`, reinicia o que mudou, garante o trecho do nginx do
   changelog, copia o instalador da release para o download (`https://cntoficial.com.br/baixar`) e confere que tudo
   responde.
4. Conferir: a versão nova aparece em `/baixar` e a página de novidades abre.

O acesso à VPS **não fica no Git**. Cada computador usa um atalho `cnt-vps` no `~/.ssh/config` (host, porta e usuário
só existem lá). Sem o atalho, a skill pergunta os dados e o cria.

## Changelog público

- Página: `https://cntoficial.com.br/monitor/changelog/` (a mais nova em cima, a mais antiga embaixo).
- No programa, clicar no número da versão (rodapé do menu) abre a página na versão em uso.
- Fonte: `servidor-vps/changelog/changelog.json`. A página `index.html` é gerada dele e conferida pelo CI
  (`build/teste_changelog.py`).
- É escrito **para pessoas**, técnicas ou não: o que melhorou e o que foi corrigido, em linguagem simples.
- **Nunca** colocar nada interno ou sensível: caminhos, portas, endereços, chaves, detalhes de segurança que ajudem um
  ataque, nomes de servidor. Falhas de segurança entram só como "correções de segurança" ou "mais segurança em X".
- Tipos de item: `novo`, `melhoria`, `correcao`.

## Skill

`/monitor-tarefa` (em `.claude/skills/monitor-tarefa/`, vai junto com o repositório) conduz todo esse ciclo no Claude
Code. Basta clonar o repositório em outro computador.
