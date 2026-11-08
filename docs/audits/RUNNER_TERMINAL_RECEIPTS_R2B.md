# R2B — Reports terminais e recibos

**R2B_PASS.** Branch `fix/runner-terminal-receipts-r2b`, base `c0886fb05d8b1c30bf4a2633d610f66d62205df2` de `fix/runner-supervision-r4`. Implementação: `c8324b4`. Checkpoint VALIDATED para este escopo, sem promoção, merge, PR ou deploy.

## CR-06 corrigido: um commit terminal

O endpoint não grava mais o Job e depois chama `append_audit` em outra transação. `PostgresEnvironmentProductRepository.report_runner_job` agora registra, na mesma transação/conexão PostgreSQL:

1. Estado terminal, resumo sanitizado, commit de resultado e referência do recibo no Job.
2. Identidade da execução autorizada e digest canônico do report.
3. Evento terminal de auditoria, reutilizando `_insert_audit` do repositório de produto com o cursor da transação.
4. Recibo estável com identidade, evidências permitidas e correlação Job/Environment/Session/Run.

Falha em qualquer escrita, constraint ou validação final provoca rollback completo. O retorno ao handler só ocorre depois do commit, incluindo constraints diferidas. Uma falha depois desse commit pode perder a resposta HTTP; ela não perde o recibo nem autoriza duplicar conclusão/audit.

## Identidade de execução e contrato de escrita

O claim autorizado gera `execution_attempt_id`, UUIDv7 persistido no Job e devolvido no contexto de claim/heartbeat. Ele é **distinto** de `dispatch_attempt_id` do R3: dispatch é uma tentativa de envio; execution identifica a execução que pode reportar.

`POST /v1/runner-jobs/{job_id}/report` mantém a autenticação de máquina R1 e agora exige `attempt_id` além de `github_run_id`, estado terminal, resumo e campos de resultado/evidência. O primeiro commit exige estado CLAIMED/RUNNING, owner e tentativa atuais e lease válida. Nenhuma tentativa é inventada no report.

O campo é uma identidade de correlação/anti-obsolescência, **não uma prova criptográfica do workflow**. O ID opaco de workflow continua sendo uma afirmação do caller sob o bearer R1. Esta tarefa não substitui esse modelo por credenciais criptográficas individuais nem cria o sistema completo de Attempts do R5.

O digest SHA256 inclui a versão `1`, Job, tentativa, owner, estado, resumo original normalizado, result_commit e evidência. Usa JSON com chaves ordenadas, separadores fixos e defaults explícitos; IDs UUIDv7 são canonicalizados. O digest vem do conteúdo, não do simples estado COMPLETED. Não é necessário report_id adicional: um Job tem no máximo um recibo terminal e a tentativa/digest distinguem sua intenção.

- Primeiro report válido: HTTP 200 com `RunnerTerminalReceipt`, após commit.
- Repetição idêntica da identidade e do conteúdo: HTTP 200 com **o mesmo recibo**, sem UPDATE no Job, renovação de lease ou nova auditoria.
- Conteúdo diferente: HTTP 409 `RUNNER_REPORT_CONFLICT`.
- Tentativa diferente: HTTP 409 `RUNNER_ATTEMPT_MISMATCH`; owner diferente: 409 `RUNNER_LEASE_OWNER_MISMATCH`.
- Sem recibo anterior, lease ausente/expirada ou estado incompatível impedem uma nova conclusão.
- Com recibo anterior, o reconhecimento exato é uma **leitura** e não depende de uma lease posterior ainda válida. Não concede autoridade para trabalho novo.

Mudanças de contrato: `attempt_id` é obrigatório no POST/CLI; a resposta do POST passa do Job mutável para o recibo. O CLI exige `--attempt-id` e imprime somente metadados seguros de confirmação: Job, recibo, tentativa, digest e estado. GET/listagem de Jobs continuam disponíveis, com dois campos opcionais novos (`execution_attempt_id`, `terminal_receipt_id`). Os chamadores internos e testes foram atualizados.

## Recibo e consulta separada

O recibo contém receipt_id, Job, tentativa, owner, digest, estado, resumo sanitizado, commit, evidências, audit_id e referências Environment/Session/Run, com timestamp estável do registro terminal. Ele não significa que um Run/Task/Checkpoint foi concluído e não é prova criptográfica independente do Control Plane.

`GET /v1/runner-jobs/{job_id}/terminal-receipt` exige autenticação de máquina e os parâmetros `github_run_id`, `attempt_id` e `report_digest`. Retorna somente o recibo exato ou 404/409; nunca renova lease ou cria dados. A resposta é `Cache-Control: no-store`. A exceção do gate privado é restrita a esse GET exato: não amplia acesso a outros GETs, métodos ou sufixos. O teste R1 passa pelo gate real sem cookie humano e confirma essas recusas.

Consulta pelo cliente:

```bash
python -m amodex.runners.client receipt "$JOB_ID" \
  --github-run-id "$GITHUB_RUN_ID" --attempt-id "$ATTEMPT_ID" \
  --report-digest "$REPORT_DIGEST"
```

O cliente valida o recibo completo e sua identidade/digest. Um JSON com apenas `state=COMPLETED`, recibo de outra tentativa ou digest diferente não produz confirmação. Resumos de apresentação podem ter sido redigidos; não reconstruir um POST divergente a partir deles. Para reconhecimento, usar o digest da intenção original e a consulta explícita.

## Evidências e dados sanitizados

Evidência não é um canal para armazenar headers ou logs arbitrários. O contrato limita o objeto a 8 KiB e admite campos tipados: flags Godot/MCP, checks/score inteiros limitados, source_sha hexadecimal, identificação de runner/engine e owner consistente. Referências limitadas podem usar `artifact://` ou `evidence://`, sem query, credenciais ou travessia. Chaves não permitidas, logs brutos, URLs HTTP com credenciais e tipos incompatíveis são recusados com 422 `REPORT_EVIDENCE_INVALID`.

O resumo de apresentação remove controles e redige padrões usuais de credenciais/Bearer/URLs. O hash considera o resumo original normalizado **antes** dessa redação: duas intenções diferentes que gerem o mesmo texto redigido continuam conflitantes. A auditoria armazena tentativa, recibo, digest, commit e evidência permitida; não duplica texto bruto do request. Não há impressão de tokens, headers ou corpos HTTP sensíveis.

## PostgreSQL, concorrência e lease no commit

Migration aditiva: `migrations/postbaseline/0009_runner_terminal_receipts.sql`. Nenhuma migration histórica foi modificada.

- Adiciona campos opcionais de execução/recibo ao Job e índice único da identidade de execução.
- Cria `amodex_runner_terminal_receipts`: Job como chave primária, receipt_id único, unicidade Job/tentativa e audit_id único com FK para a auditoria.
- Uma constraint exige referência de recibo para um terminal com identidade de execução emitida; FK diferida liga Job e recibo do mesmo Job. Assim, o caminho novo não confirma um terminal sem recibo/audit correspondentes. Uma auditoria referenciada não pode ser apagada isoladamente.
- O report bloqueia a linha do Job com `FOR UPDATE`. O vencedor persiste tudo; o concorrente lê o recibo vencedor e reconhece igualdade ou retorna conflito. Não há cache de idempotência em memória.
- A autorização temporal é validada ao iniciar a conclusão e por constraint trigger diferida no commit, usando `clock_timestamp()` e o prazo autorizado capturado sob lock. Lease que expire durante as escritas causa rollback/409 `RUNNER_LEASE_LOST`, inclusive se a auditoria demorou. Releituras de recibos existentes não executam essa validação de mutação.

Todos os locks são locais à transação de banco; não há HTTP externo dentro dela. Não existe execução de provider, código, dispatch ou outro efeito externo nesse commit.

## Integração com R4 e resposta perdida

O supervisor vincula a primeira identidade de execução confirmada ao seu arquivo privado. Se um heartbeat indicar outra tentativa, ele bloqueia em LEASE_LOST; não adota silenciosamente a nova identidade. Antes do report, salva somente a descrição necessária à confirmação (tentativa, digest e estado), junto com `terminal_attempted`, sem persistir o request bruto.

Após um resultado incerto, continua impedindo outro POST/report divergente, fallback FAILED, execução ou dispatch. A nova opção explícita é:

```bash
python -m amodex.runners.supervisor "$JOB_ID" \
  --github-run-id "$GITHUB_RUN_ID" \
  --state-file "$RUNNER_TEMP/runner-supervision.json" --reconcile-receipt
```

Essa operação faz **somente GET**, com prazo limitado, mesmo se a lease local já expirou ou a autoridade estiver bloqueada. Com recibo exato, retorna RECONCILED e seu ID; sem confirmação, mantém UNKNOWN_OUTCOME. Nunca chama heartbeat, reenvia o POST, altera a intenção ou limpa o bloqueio de trabalho. O workflow não ganhou um retry automático; a reconciliação é explicitamente invocada. Consultar um recibo novamente é permitido; repetir trabalho não é.

## Legados e limites

Não se geram tentativas ou recibos retroativos para Jobs ativos/terminais existentes. Um Job legado QUEUED recebe identidade em um claim legítimo; um legado já ativo sem identidade não pode concluir pelo novo contrato. Terminal legado sem recibo continua sem confirmação: consulta retorna 404 e report não fabrica história nem autoriza nova execução. Na adoção, aplicar a migration antes do novo código e coordenar runners legados; não trocar automaticamente de Job/Attempt para contornar ausência de recibo.

R1, leases e ownership continuam obrigatórios para mutações novas. R2A não foi alterado. R3 continua impedindo redispatch de STARTED/UNKNOWN. R4 continua bloqueando trabalho após autoridade perdida/incerta; uma reconciliação bem-sucedida não reativa o runner. O recibo registra referências do Run, mas não chama transição de Run, Task, Checkpoint ou fencing. Isso exige seus próprios critérios/evidências.

Não há reidratação de TaskPackage, reconstrução de trabalho, Recovery R5–R10, cancelamento global novo ou supervisor adicional. O estado local de R4 ainda depende do runner; se esse arquivo for perdido, a consulta exige conservar a identidade/digest da intenção por meio autorizado. Retenção deve preservar as dependências Job/recibo/audit; não é implementado GC novo. Não se promete exactly-once de serviços externos ou reversão de efeitos já iniciados.

## Arquivos e validação

Implementação: `storage/environment_product.py`, `environments/product.py`, `api/app.py`, `api/runner_auth.py`, `runners/client.py`, `runners/supervisor.py` e migration 0009. A escrita de auditoria de `storage/product.py` é reutilizada com o cursor existente, sem abrir outra conexão. Os testes de regressão levam a identidade emitida pelo claim; os CR-06 que esperavam conflito no retry e terminal sem audit passaram a exigir recibo estável e rollback.

**355 casos distintos aprovados; zero falhas, erros ou skips finais.** PostgreSQL 17.11 isolado em loopback/banco descartável, Python 3.13.5, psycopg 3.3.6 e pytest 9.0.2.

| Grupo | Casos aprovados |
| --- | ---: |
| Novos R2B: transação, recibo, evidências e contrato do cliente | 35 |
| R1 | 66 |
| R2A | 84 |
| R3 | 48 |
| R4/heartbeat | 64 |
| Environment/produto | 7 |
| Tarefa 08 com HTTP simulado | 35 |
| Caracterizações Recovery diretamente relacionadas, incluindo CR-06 | 16 |

Cobertura: primeiro commit; retry exato; divergências de conteúdo/owner/tentativa; lease antes e durante o commit; leitura após expiry; rollback por falha de audit/recibo; perda de resposta depois do commit; concorrência igual e divergente; reinício em processo Python novo; tentativa antiga versus atual; Job terminal sem concluir Run; legado sem prova; proteção SQL de terminal/audit; reconciliação R4 só por GET, com e sem recibo; bloqueio de trabalho e report após perda de autoridade.

A bateria principal teve 328 aprovações. A etapa final validou o contrato e as caracterizações relevantes; o CLI foi verificado pelo gate R1 real. O JUnit consolida a última execução de cada caso, sem reexecutar regressões aprovadas desnecessariamente. Avisos observados: depreciação Starlette/httpx e metadados de alias Pydantic durante criação concorrente dos apps de teste; sem falhas.

[Comandos e resultados](evidence/task11/results.json), [JUnit consolidado](evidence/task11/tests.xml). Sem suíte completa, inferência paga, Actions real, Roblox Open Cloud, deploy, merge ou PR. A regressão da Tarefa 08 é determinística/local, sem repetir sua validação remota. Nenhum Core, Worker, scheduler, integração Roblox/Godot ou configuração Render/Neon foi criado ou alterado.
