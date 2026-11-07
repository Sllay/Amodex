# R3 — Dispatch/claim e intake idempotente

**R3_PASS.** Base `adb72c8861b3b6fd742a451aa452c8e07bcf4725` de `feat/roblox-agent-execution-v1`; branch `fix/runner-dispatch-idempotency-r3`. Implementação: `5c9cc86`. Checkpoint VALIDATED para este escopo, sem promoção. Data: 2026-11-07.

## Defeitos corrigidos

CR-03: o reconhecimento do dispatch não exige mais que o Job permaneça QUEUED. ACK normal ou erro após claim mantém CLAIMED, RUNNING ou estado terminal, sem apagar owner, lease, heartbeat, commit ou resumo do trabalho. O resultado externo tem campos próprios, independentes do progresso do Job. Os testes de caracterização que esperavam 500 agora exigem resposta normal com o estado atual.

CR-07: a criação exige identidade estável da intenção, garantida no PostgreSQL. Retry da mesma intenção retorna o mesmo Job e a mesma tentativa; não cria outro Job nem outro envio. Não há outro scheduler, Worker, Director, serviço de filas ou controlador de background.

## Contrato de escrita

`POST /v1/environments/{environment_id}/runner-jobs` exige **`Idempotency-Key`**. A chave é opaca, não secreta, sensível a maiúsculas, tem 1–128 caracteres ASCII, começa com letra/dígito e aceita depois letras, dígitos, `.`, `_`, `:` e `-`. Sua identidade é o par `(environment_id, chave)`; a mesma chave em outro Environment não colide. Não há expiração automática; a identidade permanece enquanto o Job existir.

- Mesma chave e mesmo conteúdo canônico: reutiliza Job/tentativa, inclusive em outro processo.
- Mesma chave com intenção diferente: **409 `IDEMPOTENCY_KEY_CONFLICT`**, sem criar outro Job ou chamar o dispatcher.
- Chave ausente/inválida: **422**, sem efeito. Não há chave gerada pelo servidor como fallback.
- Outra intenção legítima: outra chave. **Não trocar de chave para contornar timeout ou UNKNOWN.**

O digest SHA256 usa JSON com chaves ordenadas e separadores fixos, versão do contrato `1`, Environment, Run, Session, perfil, classe de orçamento e caminhos de escrita ordenados. IDs são canonicalizados como UUIDv7. Defaults explícitos/omitidos têm a mesma intenção; a ordem dos caminhos não muda o escopo. O texto de cada caminho continua significativo; não se removem partes de caminho para produzir o digest. Campos extras e versões de escrita diferentes de `1` são recusados pelo contrato.

O chamador interno existente é o CLI; o dashboard apenas lista Jobs. O comando agora exige a chave fornecida pelo proprietário:

```bash
amodex env dispatch ENVIRONMENT_ID RUN_ID SESSION_ID \
  --idempotency-key owner-intent-123 --profile implementation \
  --task-class STANDARD --write 'src/**'
```

Um retry deve repetir a mesma chave e intenção. O CLI não gera outra chave a cada chamada. Fluxos de claim/running/heartbeat/report não criam Jobs e mantêm seus contratos de autenticação R1.

## Respostas e compatibilidade de leitura

As respostas headless incluem `job` com estado atual, `job_url` para GET e `dispatch_operation` com `attempt_id`, status, `may_have_been_accepted` e `automatic_redispatch=false`.

| Situação | HTTP e comportamento |
| --- | --- |
| Primeiro envio confirmado | 201, tentativa CONFIRMED; Job DISPATCHED ou progresso posterior preservado |
| Retry já confirmado | 200, mesmo Job; nenhum envio |
| Envio em progresso / processo perdido | 202, STARTED; pode ter sido aceito, nenhum novo envio |
| Erro/timeout após início | 202, UNKNOWN; Job só passa a DISPATCH_UNKNOWN se ainda estava QUEUED |
| Dispatcher indisponível | 202, PENDING e `DISPATCH_UNAVAILABLE`; intenção preservada, nenhum envio iniciado |
| Escopo desativado antes do primeiro envio | 202, PENDING; aquisição recusada |
| Desktop Studio | 201, mesmo Job quando repetido, NOT_REQUIRED; nenhum envio Actions |

Mudanças de escrita: header obrigatório; schema_version limitado a `1`; ambiguidade deixa de ser 502 e passa a 202 consultável; indisponibilidade do dispatcher deixa de descartar a intenção com 503 e preserva PENDING com 202. Erros reais de banco/processo continuam podendo interromper uma resposta; não são confundidos com conflitos normais de dispatch/claim. Um teste simula falha antes de persistir o ACK: a primeira resposta falha, mas o retry responde 202 sem reenviar.

GET individual/listagem mantêm campos anteriores e acrescentam metadados opcionais de dispatch: tentativa, status, destino/correlação, início/fim e código sanitizado de erro. Jobs anteriores à migration continuam legíveis, com esses campos nulos; não recebem autorização retroativa para dispatch. Não há como inferir uma chave histórica de uma solicitação antiga: clientes antigos precisam adotar o novo contrato de escrita. Aplicar a migration antes de ativar este código; a checagem de schema inclui os campos R1 e R3.

## Concorrência e intenção durável

A migration **aditiva** `migrations/postbaseline/0008_runner_dispatch_idempotency.sql` estende a tabela de Jobs, sem reescrever as migrations antigas ou mudar estados existentes. Adiciona constraint UNIQUE `(environment_id,idempotency_key)`, unicidade do ID da tentativa e metadados próprios de operação. NULL permite os registros legados.

1. Uma transação curta valida o escopo e insere Job, chave, digest, ID da tentativa e PENDING juntos. `INSERT ... ON CONFLICT DO NOTHING` resolve criações concorrentes no banco. O perdedor lê a linha vencedora numa instrução seguinte, sob READ COMMITTED, e compara o digest; não depende de cache Python.
2. `begin_dispatch` executa um UPDATE condicional PENDING → STARTED apenas para Job QUEUED e Environment/Session ainda ativos. Persiste horário de início e destino não secreto (`repository`, `workflow`, `ref` no adapter GitHub). Só o controlador que recebe a linha no RETURNING pode enviar.
3. A transação termina **antes** do HTTP externo. O fake de teste consegue obter `FOR UPDATE NOWAIT` por outra conexão durante a chamada, além de observar os dados já persistidos.
4. O reconhecimento grava CONFIRMED ou UNKNOWN para aquela tentativa, sob lock curto da linha. O Job só muda a partir de QUEUED; estados posteriores não regridem. O primeiro resultado persistido não é sobrescrito por um reconhecimento repetido. Um ID de tentativa divergente é recusado.

O Job e os metadados de dispatch são a correlação durável; não se depende dos antigos eventos de audit de dispatch gravados em transações posteriores. O audit desktop existente permanece. Essa mudança não altera o audit de claim/report de R1.

## Ambiguidade e reinício

PENDING significa que **nenhuma autorização de envio foi consumida**: uma repetição pode adquirir a primeira e única tentativa existente, se o escopo continuar ativo. Não cria outra operação.

STARTED significa que o envio pode estar em andamento **ou já ter sido aceito**, mesmo que o processo tenha caído antes de realizar o HTTP. Não existe lease de dispatch reutilizável, reset por timeout, varredura de órfãos ou retry automático. Um processo novo retorna o registro e a possibilidade de resultado desconhecido; não readquire STARTED. UNKNOWN também permanece bloqueado. Esse bloqueio conservador pode exigir reconciliação manual mesmo quando a falha ocorreu antes do HTTP, mas impede retransmissão pelo AMODEX.

O erro externo é sanitizado, sem corpo HTTP, headers ou credenciais. Timeout, HTTP diferente de 204 ou exceção após início são tratados conservadoramente como UNKNOWN. O adapter não segue redirects nem repete o POST. **HTTP 204 do GitHub confirma a requisição de dispatch, não comprova execução do workflow.** A prova de claim/progresso continua separada. Não se afirma exactly-once do GitHub Actions, nem se usa uma chave local como garantia externa.

## Arquivos alterados

- `src/amodex/storage/environment_product.py`: intake idempotente, aquisição persistente e reconhecimento monotônico.
- `src/amodex/api/app.py`: header obrigatório, respostas consultáveis, dispatch fora de transação; handler síncrono para IO síncrono no threadpool existente.
- `src/amodex/environments/product.py`: validação da chave/versão e metadados opcionais de leitura.
- `src/amodex/runners/dispatch.py`: correlação não secreta, erro sanitizado e redirects desabilitados.
- `src/amodex/cli.py`: chave explícita propagada no header, sem geração em retry.
- Migration `0008_runner_dispatch_idempotency.sql`.
- Testes novos `test_runner_dispatch_idempotency_r3.py` e `test_runner_dispatch_contract_r3.py`; expectativas CR-03/07 atualizadas em `test_checkpoint_recovery_audit.py`; chamadores de teste de produto/R1 atualizados para o header. A fixture Web R1 passou a preparar o manifesto completo de migrations localmente, removendo dependência de um banco previamente preparado.

Nenhum arquivo Hermes, Roblox, Godot, R2A, Render ou Neon foi alterado.

## Testes e evidências

**242 casos distintos aprovados, zero falhas/erros/skips no resultado final consolidado.** PostgreSQL 17.11 isolado em loopback, banco descartável `amodex_qa_r3`; Python 3.13.5, psycopg 3.3.6, pytest 9.0.2. Toda concorrência usa conexões/transações PostgreSQL reais; todos os dispatchers são falsos.

| Escopo | Casos aprovados |
| --- | ---: |
| R3 novo: concorrência, crashes, contrato/header/adapter | 48 |
| Caracterizações CR-03/07 corrigidas | 3 |
| Regressões R1 de autenticação/ownership | 65 |
| Regressões R2A de lease/fencing | 84 |
| Tarefa 08: agente determinístico e análise local, HTTP simulado | 35 |
| Produto Environment e contratos associados | 7 |

Inclui claim antes de ACK/erro nos estados CLAIMED, RUNNING e três terminais; intake simultâneo com payload igual e diferente; duas aquisições concorrentes; retry durante envio e após confirmação/UNKNOWN; timeout após possível aceitação; crash após intenção, após STARTED e antes de persistir ACK; processo Python novo; chave isolada por Environment; runtime sem dispatcher; migration reentrante com dado legado; identidade incorreta e ausência de segundo envio.

A primeira bateria teve dois checks Web/readiness de R1 bloqueados por migrations ausentes no banco recém-criado. A preparação da fixture foi corrigida; os 47 casos R1 foram repetidos e aprovados. O JUnit consolidado usa a última execução de cada caso; os demais resultados aprovados foram preservados para não repetir a suíte sem necessidade. Há um aviso preexistente de depreciação Starlette/httpx.

Comandos completos e composição das baterias estão em [results.json](evidence/task09/results.json); [JUnit consolidado](evidence/task09/tests.xml). Foram executados apenas testes novos e regressões diretamente afetadas; a checagem de Tarefa 08 foi incluída explicitamente para comprovar sua preservação. Sem suíte completa, E2E de navegador, inferência paga, dispatch GitHub Actions real ou APIs Roblox. A análise Luau foi local, com o executável oficial já disponível; não houve nova execução remota da fixture.

## Limitações restantes

R2B continua pendente: report terminal/ack idempotente e a ligação entre Job, Run, Attempt e evidências não foram implementados. Um Job COMPLETED ainda não conclui automaticamente o Run. R4 continua pendente: esta mudança não supervisiona globalmente heartbeat nem interrompe processos externos. Não há reconciliação automática de STARTED/UNKNOWN, cancelamento remoto, Recovery avançado ou checkpoints novos. As oito falhas de Model Intelligence e a consolidação/deploy não foram investigados.

R1 e R2A permanecem nas autoridades originais, com regressões aprovadas. Não houve novo scheduler, refatoração Hermes, merge, PR ou deploy.
