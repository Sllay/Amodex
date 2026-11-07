# AMODEX — Tarefa 04 / R1: autenticação e propriedade de Runner Jobs

Data: **2026-11-07, America/Recife**.

**Resultado: a máquina autentica sem cookie humano nas quatro rotas POST permitidas; running/report exigem identidade compatível com o claim e lease válida. A credencial permanece compartilhada: não há prova criptográfica de que o caller é o workflow informado, nem isolamento do token por Job/Attempt.**

Branch: `fix/runner-auth-ownership-r1`, criada depois de confirmar ausência local e remota. Base verificada no GitHub: `audit/checkpoint-recovery-v1`, **`618450b22ad014408881ea1aa0f3a497c58a0208`**. Fonte e testes validados correspondem a **`2815e17`**; os commits seguintes acrescentam somente este relatório/evidências.

Escopo: R1 de [CHECKPOINT_RECOVERY_ARCHITECTURE_AUDIT.md](CHECKPOINT_RECOVERY_ARCHITECTURE_AUDIT.md), principalmente CR-01/02 e ordem de autenticação de CR-10. Não houve implementação de R2, R3, R4 ou Recovery, nova migration, modificação de dependências/lockfile, alteração Render/Neon, inferência real, merge, PR ou deploy.

## Causas e correções

### CR-01 — identidade opcional/inexistente nas mutações

Antes, `mark_running` não recebia owner, e `report_runner_job` só comparava `github_run_id` quando o caller fornecia o campo. Report normal também aceitava Jobs DISPATCHED/DISPATCH_UNKNOWN sem claim.

Agora:

- Claim, running, heartbeat e report exigem `github_run_id` explicitamente no contrato, no client e no repository.
- A identidade é uma string opaca de 1–100 caracteres imprimíveis, sem whitespace; não é normalizada para outro proprietário nem inferida do cookie/token.
- Running inclui o owner no predicado SQL da transição, além de estado CLAIMED e lease ainda válida. Owner divergente recebe 409; nenhuma transição/renovação ocorre.
- Heartbeat conserva o predicado de owner, estado e expiry, com validação de entrada compartilhada.
- Report só permite estados de origem CLAIMED/RUNNING, compara owner obrigatoriamente e verifica lease. DISPATCHED/DISPATCH_UNKNOWN não podem ser concluídos por um report normal, mesmo com bearer válido e ID informado.
- IDs ausentes/inválidos são recusados antes de qualquer operação SQL do repository. `github_run_id` continua opcional no **registro** do Job, porque Jobs ainda não reivindicados legitimamente não têm owner; ele é obrigatório nas **mutações** de máquina.

Não foi criado endpoint administrativo. Encerramento anterior ao claim requer um contrato administrativo separado e explicitamente autorizado em tarefa futura. FAILED/CANCELLED continuam permitidos para o dono depois de claim, inclusive quando a falha acontece antes de RUNNING.

### CR-02 — gate privado não reconhecia a credencial de máquina

Antes, o middleware de acesso exigia cookie humano em todas as APIs privadas; a validação do bearer só acontecia dentro dos handlers do Control Plane. O workflow não recebia cookie e era bloqueado antes de chegar ao claim.

Foi extraído `RunnerAuthorization` em `api/runner_auth.py`, preservando a comparação segura de credenciais com `hmac.compare_digest`. O Control Plane configura **uma instância** em `app.state`; middleware de máquina, dependências dos handlers e gate privado usam essa mesma autoridade. O gate não relê outra credencial do ambiente nem ganha uma chave independente. Um `base_app` sem autoridade configurada falha fechado.

O gate privado só permite passagem sem cookie para requests que correspondam a **método e caminho completos** abaixo, depois de validar o bearer. Não há exceção de prefixo para `/v1` ou `/v1/runner-jobs`.

## Contrato final de autenticação e identidade

Bearer somente em `Authorization: Bearer <token>`. Nenhum token é transportado em query string, URL, resposta ou mensagem de erro. O campo não aparece no repr da autoridade; headers duplicados ou inválidos são recusados.

| Método e caminho exato | Identidade exigida | Autorização/estado |
|---|---|---|
| `POST /v1/runner-jobs/{UUID}/claim` | Query `github_run_id` | Bearer válido; estado claimable existente. Preserva o transporte anterior da identidade, que não é segredo |
| `POST /v1/runner-jobs/{UUID}/running` | JSON `github_run_id` | Bearer + mesmo owner do claim + CLAIMED + lease válida |
| `POST /v1/runner-jobs/{UUID}/heartbeat` | JSON `github_run_id` | Bearer + owner + CLAIMED/RUNNING + lease válida |
| `POST /v1/runner-jobs/{UUID}/report` | JSON `github_run_id`, estado e resumo | Bearer + owner + CLAIMED/RUNNING + lease válida + estado terminal permitido |

`{UUID}` tem a forma completa hexadecimal com hífens. Outros métodos, sufixos, barras extras, paths semelhantes e GET de Job **não recebem passagem de máquina**. Os handlers continuam com dependência de autenticação própria, inclusive se o router encontrar uma entrada fora da allowlist usada pelo gate.

APIs humanas continuam exigindo a sessão quando o modo privado está habilitado. O bearer não libera workspace, projetos, environments, provider connections, memórias, settings/system status, modelos, audit, docs ou secrets. Sessão humana válida, sozinha, não substitui o bearer nas quatro mutações; bearer incorreto também é recusado na presença dessa sessão.

Sem passcode configurado, o modo local sem gate humano continua sendo o comportamento anterior da aplicação. A R1 não transforma esse modo intencionalmente aberto em deployment privado; os testes de isolamento foram feitos **com gate privado real habilitado**.

### Ordem de autenticação e validação: 401 / 422 / 503

Para os quatro paths de máquina permitidos, o middleware autentica antes de ler/validar body e query, inclusive JSON malformado. A dependência de rota reutiliza o mesmo validador. Só depois a identidade é validada e o handler acessa o repository.

| Situação | Resultado |
|---|---|
| Token não configurado, vazio ou só whitespace | 503, `RUNNER_AUTH_NOT_CONFIGURED` |
| Header ausente/incorreto/duplicado | 401, mensagem genérica sem credencial |
| Token correto, identidade ausente/nula/vazia/inválida | 422, sem efeitos no Job/audit |
| Identidade válida mas divergente do claim | 409; running/report usam `RUNNER_LEASE_OWNER_MISMATCH`; heartbeat preserva o erro `RUNNER_LEASE_LOST` existente |
| Estado de report anterior ao claim ou terminal já persistido | 409, `RUNNER_JOB_TERMINAL_CONFLICT` |
| Lease expirada | 409; sem renovação ou conclusão |
| Bearer em método/path privado não permitido, sem sessão humana | 401 pelo gate privado |

O teste legado não foi enfraquecido: seu claim **sem token e sem identidade** volta a responder 401 pela autenticação antecipada. Foi acrescentada a verificação distinta de **token correto e identidade ausente → 422**, mantendo o Job DISPATCHED. O request positivo de running foi atualizado para fornecer o JSON agora obrigatório; sua asserção de sucesso foi preservada.

## Client, CLI e workflow

`RunnerControlClient` valida a identidade antes de enviar a request. Running passa a enviá-la em JSON; report deixa de ter default `None`. Os quatro comandos CLI exigem `--github-run-id`. Claim continua usando query e heartbeat continua usando JSON.

O workflow envia `--github-run-id "$GITHUB_RUN_ID"` em running, além dos usos anteriores em claim/heartbeat/report. Marca `RUNNER_JOB_CLAIMED=true` somente depois de um claim CLI bem-sucedido. O report de falha é tentado apenas quando essa marca existe: não tenta mais finalizar um Job nunca reivindicado pelo fluxo normal.

Intervalo de 30 s, lease de dois minutos, mecanismo de heartbeat em fundo e cleanup foram preservados. Não foi implementada supervisão de subprocessos. Se o claim foi aceito, mas o ack se perdeu, a flag pode não existir: isso continua exigindo reconciliação de dispatch/execução, sem retry ou report pré-claim improvisado.

Compatibilidade: callers antigos de running sem body/flag ou report sem identidade serão recusados. Não há fallback inseguro. A aplicação, o client e o workflow devem ser atualizados juntos quando uma tarefa de publicação for autorizada. Esta tarefa não publicou nenhuma dessas mudanças no Render.

## Limites de segurança ainda abertos

- O token continua global e compartilhado para essas quatro operações. Não foi vinculado criptograficamente a um Job, Environment, Task ou Attempt.
- `github_run_id` é informado pelo caller. Um detentor do bearer pode informar um owner conhecido; a igualdade com a row protege contra omissão/inconsistência, **não contra impersonação por outro detentor do mesmo segredo**.
- Claim não atesta GitHub OIDC, assinatura, audience, repository ou workflow. Não se afirma identidade exclusiva do processo.
- `GITHUB_RUN_ATTEMPT` e attempt/fence persistidos não foram adicionados. Reruns com mesmo run ID ainda precisam do contrato de tentativas da R2.
- Jobs legados CLAIMED sem owner não são “corrigidos” preenchendo identidade arbitrária nem ganham bypass de report. Precisam de tratamento explícito de reconciliação fora da R1.
- CR-03 a CR-09 continuam abertos: corrida de dispatch/claim, lease de Run sem checagem temporal completa, heartbeat sem supervisão, atomicidade/ack terminal, idempotência de intake/efeitos, publicação parcial de checkpoint e composição do contexto/validação. Suas caracterizações continuam reproduzindo esses limites.

A emissão de credenciais por Job/Attempt foi deliberadamente deixada fora desta etapa: alteraria provisionamento, revogação, armazenamento e o contrato do workflow. Continua necessária se o objetivo for isolamento entre runners mutuamente não confiáveis; não é uma garantia entregue pela R1 atual.

## Testes e evidências

Banco **exclusivamente local**: `amodex_qa_task04` em `127.0.0.1:55432`, preparado com o migrador existente e guard `127`. Todas as credenciais usadas nos testes são sintéticas. Reutilizados Python 3.13.5, pytest 9.0.2, Playwright 1.64.1 e Chromium instalado; nenhuma dependência foi adicionada.

| Execução final | Aprovados | Falhas | XFAIL | Skips |
|---|---:|---:|---:|---:|
| Unidade/contratos/integrações relacionadas | 319 | 0 | 0 | 0 |
| Navegador HTTPS: baseline e regressões UI existentes | 35 | 0 | 0 | 0 |
| Cache offline separado no localhost HTTP | 1 | 0 | 0 | 0 |
| **Total distinto** | **355** | **0** | **0** | **0** |
| Matriz workspace R1 dirigida, já incluída nos 319 | 47 | 0 | 0 | 0 |
| Unidade R1 dirigida, já incluída nos 319 | 29 | 0 | 0 | 0 |

O caso offline foi desmarcado no HTTPS e executado separadamente, não skipped. As execuções dirigidas são subconjuntos e não devem ser somadas novamente. As últimas asserções adicionais de isolamento de provider connections/memórias e sessão humana com bearer incorreto passaram no rerun dirigido dos 47 casos. Não houve mudança de produto depois da regressão ampla.

### Integração efetiva do workspace

Os testes instanciam `create_workspace_app(base_app=create_app(...))`, com o **gate real**, cache middleware, routers e PostgreSQL. Passcode/sessão humana e bearer são distintos. O token explícito do Control Plane é propositalmente diferente do valor sintético no ambiente para comprovar que o gate não cria uma segunda autoridade.

Foram exercitados:

- Claim → running → heartbeat → report sem cookie humano, persistindo as transições no banco.
- Matriz dos quatro métodos/paths válidos contra token ausente/incorreto, identidade inválida e JSON malformado; snapshots do Job e contagem de audit permanecem iguais nas recusas.
- Métodos GET/HEAD/OPTIONS/PUT/PATCH/DELETE e paths semelhantes sem passagem de máquina.
- APIs humanas de leitura e escrita recusadas ao bearer, incluindo provider connections e memórias; sessão humana funcionando normalmente, mas insuficiente nas mutações de runner.
- Token não configurado, vazio e validação fail-closed; owner divergente; report antes de claim em DISPATCHED/DISPATCH_UNKNOWN e todos os estados terminais; lease expirada.
- Login/logout/limpeza de cache, `/healthz` e `/readyz` sem alteração de contrato.
- CLI **real**, parser e client atravessando toda a composição workspace usando apenas transporte HTTP local TestClient. Não foi substituído o gate por mock permissivo e nenhum serviço externo foi chamado.
- Validação direta no repository antes de SQL, headers duplicados/não ASCII e presença dos argumentos obrigatórios no workflow.

### Comandos executados

Em `/workspace/Amodex`, com `AMODEX_TEST_DATABASE_URL=postgresql://amodex@127.0.0.1:55432/amodex_qa_task04`:

```bash
.venv/bin/pytest tests/unit tests/contract \
  tests/integration/test_activation_readiness.py \
  tests/integration/test_postbaseline_environment_product.py \
  tests/integration/test_checkpoint_recovery_audit.py \
  tests/integration/test_runner_auth_ownership_r1.py \
  tests/integration/test_postbaseline_environment_storage.py \
  tests/integration/test_postbaseline_web_workspace.py \
  tests/integration/test_workspace_ui_snapshots.py \
  tests/integration/test_stage6_product_surfaces.py \
  tests/integration/test_stage1_crash_recovery.py \
  --junitxml=artifacts/qa/task04/regression.xml -ra

.venv/bin/pytest tests/integration/test_runner_auth_ownership_r1.py \
  --junitxml=artifacts/qa/task04/workspace-r1.xml -ra
.venv/bin/pytest tests/unit/test_runner_authorization_r1.py \
  tests/unit/test_runner_heartbeat_contract.py \
  --junitxml=artifacts/qa/task04/unit-r1.xml -ra

AMODEX_E2E_BASE_URL=https://localhost:8765 \
AMODEX_E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
.venv/bin/pytest tests/e2e -k 'not cached_chat_survives_offline_reload' \
  --browser chromium --junitxml=artifacts/qa/task04/browser.xml -ra
AMODEX_E2E_BASE_URL=http://localhost:8766 \
AMODEX_E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
.venv/bin/pytest tests/e2e/test_workspace_baseline.py \
  -k cached_chat_survives_offline_reload --browser chromium \
  --junitxml=artifacts/qa/task04/offline.xml -ra
```

Também executados `compileall` nas áreas alteradas e `git diff --check`. Warning de depreciação Starlette/httpx permanece, sem falha.

Tentativas intermediárias: o teste legado de running falhou até seu request receber a identidade obrigatória (11 aprovados, 1 falha). A primeira matriz workspace teve 45 aprovados e duas falhas com whitespace em query: o AfterValidator não estava preservado junto do Query nessa forma de anotação. A definição foi corrigida colocando ambos no `Annotated`; os seis casos de shape passaram, seguidos pela matriz completa e pela regressão ampla. Essas entradas não alteraram o Job/audit; as falhas não foram convertidas em skips.

Evidências sanitizadas: [results.json](evidence/task04/results.json), [regression.xml](evidence/task04/regression.xml), [workspace-r1.xml](evidence/task04/workspace-r1.xml), [unit-r1.xml](evidence/task04/unit-r1.xml), [browser.xml](evidence/task04/browser.xml), [offline.xml](evidence/task04/offline.xml). Hashes dos oito arquivos de produto/workflow estão no JSON. Logs intermediários e capturas temporárias ficam em `artifacts/qa/task04/`, ignorado pelo Git, sem headers/credenciais reais versionados.

## Arquivos e divisão da entrega

- Autenticação: `src/amodex/api/runner_auth.py` (novo helper), `api/access_gate.py`, `api/app.py`, comentário de composição em `api/workspace_app.py`.
- Identidade/ownership: `src/amodex/environments/product.py`, `storage/environment_product.py`.
- Compatibilidade: `src/amodex/runners/client.py`, `.github/workflows/environment-runner.yml`.
- Testes: `tests/integration/test_runner_auth_ownership_r1.py` e `tests/unit/test_runner_authorization_r1.py` (novos); regressões convertidas em `test_checkpoint_recovery_audit.py`; contrato atualizado em `test_postbaseline_environment_product.py` e `test_runner_heartbeat_contract.py`.
- Contrato operacional `docs/POST_BASELINE_ENVIRONMENT_PRODUCT_RUNNERS_V1.md`, este relatório e `docs/audits/evidence/task04/`.

Commits: `e56331f` introduz helper/gate; `8d48c6a` compõe a autoridade no Control Plane e exige ownership/client/workflow; `2815e17` registra a cobertura. Os históricos e evidências anteriores foram preservados.

## Impacto sobre R2, R3 e R4

- **R2:** agora recebe identidades obrigatórias e consistentes, mas ainda deve tratar attempts/fencing, expiração de lease de Run, prova mais forte de ownership e atomicidade/ack terminal. Não aceitar este ID declarativo como substituto do fence.
- **R3:** dispatch/claim ainda pode produzir conflito/500 e intake repetido pode criar novo Job. R1 não acrescenta retry nem redispatch; não mudar os testes de caracterização para indicar uma correção inexistente.
- **R4:** heartbeat conserva intervalo e comportamento de fundo. O workflow só evita report normal anterior ao claim; não ganhou watchdog, supervisão ou tratamento completo de cancelamento.

Não restam testes falhando nas suítes executadas. Isso não fecha os riscos fora de R1: inclusive as caracterizações que passam mostrando comportamento inseguro continuam explícitas. Não houve validação de GitHub Actions real, Render, WebKit ou credenciais reais; nenhuma alteração foi implantada.
