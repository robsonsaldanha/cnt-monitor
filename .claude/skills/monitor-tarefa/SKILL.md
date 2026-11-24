---
name: monitor-tarefa
description: Fluxo completo de uma tarefa no CNT Monitor (repo robsonsaldanha/cnt-monitor): baixa a main, cria branch tipo/descricao-curta, commit, push, PR, pergunta se mergeia, e ao mergear gera o instalador e atualiza a VPS. Use quando o Robson disser /monitor-tarefa ou pedir para fazer uma mudança, correção ou nova função no Monitor.
---

# /monitor-tarefa

Conduz uma tarefa do CNT Monitor do começo ao fim. Regras completas em `docs/FLUXO-GIT.md`. Fale em português.

## Regras que não mudam

- `main` = **produção**. **Nunca** editar, commitar nem dar push na `main`. O GitHub e o gancho
  `.claude/hooks/bloquear-main.py` bloqueiam; se algo for bloqueado, crie a branch, não tente contornar.
- Branch: `tipo/descricao-curta` (minúsculas, hífens, sem acentos). Tipos: `feature`, `fix`, `refactor`, `hotfix`,
  `featfix` (funcionalidade + correção). Você **sugere o nome** a partir do que o Robson pediu e ele confirma.
- **Todo merge na `main` publica o instalador e atualiza a VPS. Sempre. Sem exceção.**
- Changelog: toda PR acrescenta a entrada em `servidor-vps/changelog/changelog.json` (mais nova em cima) e regenera a página.
  Texto para **pessoas** (técnicas e não técnicas), simples, só melhorias e correções. **Nada interno ou inseguro**:
  sem caminhos, portas, IPs, chaves, nomes de servidor nem detalhes que ajudem um ataque.
- Nunca testar na tela do Robson (mouse/teclado simulados, janelas). Só testes automáticos e CI.
- **Nunca** gravar no Git o endereço, a porta ou o usuário da VPS. Use sempre o atalho `ssh cnt-vps`.

## Passos

### 1. Entender a tarefa
Se o Robson não disse o que fazer, **pergunte** o que ele quer fazer. Depois proponha o nome da branch e o tipo.
Escolha do tipo: coisa nova → `feature`; bug → `fix`; urgente em produção → `hotfix`; reorganizar sem mudar
comportamento → `refactor`; nova + correção juntas → `featfix`.

### 2. Baixar a main e criar a branch
```
git fetch --all --tags
git switch main
git pull --ff-only origin main
git switch -c <tipo>/<descricao-curta>
```
Se houver mudanças soltas no diretório, mostre ao Robson antes de seguir; não as descarte.

### 3. Trabalhar
Faça a mudança só na branch nova. Rode os testes que couberem (`python build/teste_*.py`).
Em toda PR:
- Subir `VERSAO` em `src/monitorpc/config.py`: `feature`/`featfix` sobem o número do meio (3.10.0 → 3.11.0);
  `fix`/`hotfix`/`refactor` sobem o último (3.10.0 → 3.10.1).
- Acrescentar a entrada da versão no topo de `servidor-vps/changelog/changelog.json` (campos `versao`, `data`, `itens`
  com `tipo` = `novo` | `melhoria` | `correcao` e `texto`).
- `python servidor-vps/changelog/gerar_changelog.py` e `python build/teste_changelog.py`.
- Atualizar `docs/STATUS.md` (versão atual e linha da tabela).

### 4. Commit, push e PR
```
git add <arquivos>          # nunca segredos; nada de git add -A às cegas
git commit -m "v<versão>: <resumo>"
git push -u origin <branch>
gh pr create --base main --title "v<versão>: <resumo>" --body "<o que mudou e como foi testado>"
```
Acompanhe o CI da PR (`gh pr checks --watch`). Se falhar, corrija na branch.

### 5. Perguntar: mergear agora?
Depois da PR aberta e do CI verde, **sempre pergunte** ao Robson (AskUserQuestion): "Já mergeio a PR na main?"

- **Não** → volte ao passo 1: nova branch **a partir da main** para as próximas coisas. Pode empilhar várias PRs antes de
  mergear. Quando mergear uma, as outras precisam atualizar com a main (conflito em `config.py` e no changelog:
  renumere a versão).
- **Sim** → passo 6.

### 6. Merge, instalador e VPS (tudo, na sequência)
```
gh pr merge <número> --squash --delete-branch
git switch main
git pull --ff-only origin main
git tag v<versão>
git push origin v<versão>
```
O push da tag dispara o job `release`. Espere terminar com sucesso:
```
gh run list --workflow build.yml --limit 3
gh run watch <id> --exit-status
gh release view v<versão>
```
Confirme que a release tem `CNTMonitor-Setup-<versão>.exe`, o `.deb` e as assinaturas. Se o CI falhar, **não** diga que
publicou: mostre o erro.

Depois atualize a VPS (sempre):
```
ssh cnt-vps 'bash /var/www/html_cntmonitor/servidor-vps/implantar.sh'
```
Se `ssh cnt-vps` não existir neste computador, peça ao Robson o acesso (usuário, host e porta), crie o atalho
`Host cnt-vps` no `~/.ssh/config` (fora do Git) e rode de novo.

Confira a saída: a versão nova em `ultima.json`, `200` no changelog e `302` em `/baixar`, serviço `active`.
Se o `implantar.sh` mexer no nginx, ele testa antes (`nginx -t`) e guarda backup do vhost.

### 7. Fechar
Resuma em poucas linhas: PR, versão, release, VPS e link do changelog
(`https://cntoficial.com.br/monitor/changelog/`). Então pergunte o que vem a seguir (nova tarefa → passo 1).

## Bloqueios e erros comuns

- "BLOQUEADO: a branch atual é 'main'": você está na main. `git switch -c <tipo>/<descricao>` e continue.
- Push recusado na main pelo GitHub: é a proteção da branch; abra PR.
- Conflito ao atualizar a branch com a main: `git merge origin/main` na **branch** (nunca na main), resolva e siga.
