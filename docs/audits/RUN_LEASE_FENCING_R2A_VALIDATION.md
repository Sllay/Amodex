# AMODEX — Tarefa 05 / R2A: validade temporal e fencing de Run

**Resultado: CR-04 corrigido no repositório compartilhado e no Stage1Worker. As 84 regressões novas e a revalidação dirigida de 184 testes passam. A regressão ampla tem 464 aprovados e oito falhas preexistentes de Model Intelligence, reproduzidas na base R1. Isso não constitui execução durável completa.**

Execução: **2026-11-07 UTC**. Branch: `fix/run-lease-fencing-r2a`. A branch não existia localmente nem no remoto quando foi criada. O HEAD remoto da referência `fix/runner-auth-ownership-r1` correspondeu ao informado: **`e591b7d92f36f362f3b1bc82d4bd7f2b917b2af2`**, sem diferenças na base inicial. Código e testes finais validados: **`c16fe85f005f807b2c15e9effc2dc86052cd4fc7`**; o commit seguinte acrescenta este documento e evidências.

Referências lidas: [auditoria de arquitetura](CHECKPOINT_RECOVERY_ARCHITECTURE_AUDIT.md) e [validação da R1](RUNNER_AUTH_OWNERSHIP_R1_VALIDATION.md). Seus registros históricos foram preservados.

## Contrato anterior e causa confirmada

`claim_next` emitia owner, fencing incremental e lease padrão de 30 segundos. `_lock_owned_run` adquiria `FOR UPDATE` e comparava owner/fence, mas aceitava a mesma identidade após `lease_expires_at`. O Stage1Worker não renovava a lease enquanto aguardava inferência síncrona. Além disso, o prazo inicial era calculado pelo relógio Python antes da transação.

Os dois testes CR-04 da auditoria demonstravam transição após expiração sem takeover e persistência de resposta tardia seguida de conclusão. Foram convertidos em regressões que exigem rejeição e preservação de STARTED. As caracterizações CR-03/05/06/07/08/09 continuam expondo seus problemas; não foram alteradas para apresentar falsas garantias.

## Autoridade implementada

Os quatro caminhos de mutação sob autoridade de Worker são `transition`, `mark_provider_request_started`, `mark_provider_request_completed` e o novo `renew_lease`. Todos passam pela mesma verificação transacional:

1. Lock da row de Run com `FOR UPDATE`.
2. Existência de owner e data de expiração.
3. Igualdade de worker e fencing token.
4. Leitura de `clock_timestamp()` **depois de obter o lock** e expiração estritamente no futuro.
5. Estado de origem ativo: CLAIMED ou RUNNING.

Não se usa horário do cliente nem `NOW()` para decidir validade: `NOW()` é fixado no início da transação e pode estar desatualizado depois da espera por lock. Cada UPDATE autorizado repete `lease_expires_at > clock_timestamp()`, evitando aceitar expiração entre a checagem inicial e a escrita. Estado, revision, sequência e evento continuam na mesma transação; falha no evento desfaz a mutação inteira, inclusive resposta e renovação.

O ponto de autorização/serialização é o UPDATE condicionado pelo relógio corrente do banco sob lock. O commit pode ocorrer posteriormente; o lock impede que um takeover ultrapasse essa transação. Não se promete que o instante de entrega do ACK ou término do commit seja anterior à expiração.

`StaleLeaseError` mantém compatibilidade de classe e passa a expor o motivo interno: `LEASE_MISSING`, `OWNER_MISMATCH`, `FENCING_MISMATCH`, `LEASE_EXPIRED` ou `STATE_CONFLICT`. A falha de transporte na renovação usa `RENEWAL_UNCONFIRMED`, sem afirmar que o banco confirmou perda temporal. As mensagens não incluem DSN, credenciais, prompts ou respostas.

`claim_next` mantém `FOR UPDATE SKIP LOCKED`, filtro dos controles humanos e incremento de fence a cada claim. Criação do prazo e identificação de leases expiradas usam PostgreSQL. Transições de Worker não podem enfileirar/reivindicar pela API genérica. Os contratos de intent NONE → STARTED → COMPLETED permanecem; completion continua exigindo chave correspondente.

Os comandos humanos de controle em `storage/product.py` usam autoridade administrativa própria e persistem pedido de pausa/cancelamento/retomada. Não são escritas de resultado de Worker e não receberam um bypass no contrato acima. Os únicos callers de mutação de Run encontrados no código de execução são os do Stage1Worker. Runner Job é outro contrato e não foi modificado nesta tarefa.

## Renovação e chamadas longas

`RunLeaseRenewal` é um helper local ao Stage1Worker, sem scheduler, tabela ou Worker paralelo. Um thread renova a autorização enquanto `process_once` está ativo, inclusive durante `provider.generate` síncrono. A primeira renovação acontece após o intervalo configurado; o caminho rápido continua produzindo os seis eventos originais.

`renew_lease` abre conexão/transação independente, verifica owner/fence/tempo/estado e grava `GREATEST(lease_expires_at, clock_timestamp() + duração)`. Não reduz o prazo existente, não ressuscita lease expirada, não troca fence e não renova estado terminal. O evento `LEASE_RENEWED`, revision e event_seq são atômicos. Duas renovações concorrentes são serializadas pelo mesmo lock no banco.

A conexão de renovação tem `connect_timeout=3`, `statement_timeout=3000 ms`, `lock_timeout=1000 ms`, `tcp_user_timeout=4000 ms` e keepalive 2/1/2. Os argumentos explícitos passam pelo caminho de conexão dedicado do pool existente. São limites de transporte/SQL de renovação, não prazo global de tarefa ou timeout de inferência.

| Configuração | Padrão | Limites |
|---|---:|---|
| `AMODEX_RUN_LEASE_SECONDS` | 30 s | inteiro 3..300 s no Worker |
| `AMODEX_RUN_LEASE_RENEW_INTERVAL_SECONDS` | 10 s | finito 0,1..60 s e no máximo duração/3 |

O CLI `amodex-worker` lê essas variáveis. Callers Python podem fornecer `RunLeasePolicy`; os quatro argumentos anteriores continuam válidos. Configure as duas variáveis em conjunto ao escolher duração menor que 30 s. O repositório aceita 1..300 s, preservando as leases de um segundo dos testes históricos de crash; valores inválidos falham antes da conexão. Nenhuma variável de ambiente de produção foi alterada.

Renovação e escritas do Worker compartilham um lock local, nunca mantido durante inferência. Isso impede aceitar uma resposta enquanto uma renovação pendente termina em falha. Ao liberar a lease, o Worker sinaliza parada sob o mesmo lock. Na saída normal ou excepcional, o thread é parado e aguardado; os testes verificam ausência de thread sobrevivente.

### Falha ou resposta tardia

- STARTED é confirmado duravelmente antes de chamar o provedor; um teste observa esse commit por conexão separada.
- A primeira falha de renovação é registrada com Run ID, motivo e classe do erro, sem texto bruto de exceção. Ela fica retida até o fim da execução. Não há retry de renovação ou de inferência.
- Uma falha de transporte pode ter ocorrido depois de a renovação ter sido confirmada no servidor. O Worker continua conservador: não presume autorização, não repete a renovação e não aceita resultado com base no prazo possivelmente estendido.
- O provedor síncrono pode continuar processando. Não se assume cancelamento nem se mata a chamada. Quando ela retorna, `check`/escrita protegida recusa a resposta; nenhum novo efeito externo é iniciado após a perda conhecida.
- Se o prazo expirar sem falha antecipadamente observada pelo thread, o repositório ainda recusa completion/transition. A proteção não depende exclusivamente do thread.
- O Worker antigo **não escreve UNKNOWN_OUTCOME depois de perder autorização**. O intent fica STARTED, sem resposta durável. Somente um novo claim válido, depois da expiração, permite ao caminho conservador existente classificá-lo UNKNOWN_OUTCOME sem nova inferência.
- Se COMPLETED já foi confirmado e a interrupção acontece antes da conclusão do Run, outro claim válido reutiliza o resultado durável sem chamar novamente o provedor. Resposta apenas recebida em memória continua sendo resultado incerto.

## Arquivos alterados

| Arquivo | Alteração |
|---|---|
| `src/amodex/storage/postgres.py` | Relógio PostgreSQL no claim, validação compartilhada, guard em UPDATE, renovação transacional e motivos de rejeição |
| `src/amodex/runtime/models.py` | Evento aditivo LEASE_RENEWED |
| `src/amodex/runtime/run_lease.py` | Política limitada, renovação em fundo, retenção da falha e encerramento |
| `src/amodex/runtime/worker.py` | Claim configurado, integração das escritas e verificação antes da chamada externa |
| `tests/integration/test_checkpoint_recovery_audit.py` | Conversão exclusiva dos dois cenários CR-04 e descrição atualizada |
| `tests/integration/test_run_lease_fencing_r2a.py` | 53 casos com PostgreSQL real, concorrência e falhas controladas |
| `tests/unit/test_run_lease_policy.py` | 31 casos de configuração, perda de comunicação e limpeza |
| `.github/workflows/stage1-runtime.yml` | Inclusão da matriz de integração na etapa determinística existente; sem alteração de triggers ou do smoke live anterior |
| Este documento e `evidence/task05/` | Relatório, resultados reais sanitizados e hashes de fonte |

A tabela existente já possui todos os campos necessários. **Nenhuma migration histórica, dependência, lockfile, provider, seleção de modelos, timeout HTTP, retry, autenticação R1 ou heartbeat de Runner Job foi modificado.**

## Evidências e matriz de falhas

| Cenário exigido | Evidência executada | Resultado |
|---|---|---|
| 1. Transição autorizada válida | `test_valid_transition_and_renewal_extend_without_shortening` | PASSOU |
| 2–6. Expirada sem takeover, owner/fence divergentes, ausente e takeover | Matriz `test_all_authorized_mutations_reject_lost_authority`: transition/start/complete/renew; inclui mesmo worker ID com fence novo | PASSOU |
| 7–10. Renovação válida/expirada/terminal/concorrente | Matriz acima e `test_concurrent_renewals_are_sequential_and_never_reduce_expiry` | PASSOU |
| Relógio depois do lock | Transação concorrente começa antes do novo prazo; prazo vence enquanto a row está bloqueada; checagem posterior rejeita | PASSOU |
| Expiração entre lock e UPDATE | `test_expiry_between_lock_validation_and_update_rolls_back`, quatro operações | PASSOU |
| 11. Provedor rápido | Uma chamada, COMPLETED, seis eventos e thread encerrado | PASSOU |
| 12. Chamada ultrapassa prazo sem renovação efetiva | Expiração SQL durante chamada; sucesso tardio e erro ambíguo são recusados | PASSOU |
| 13. Renovação durante chamada longa | Barreira/Event mantém provedor bloqueado até renovação real independente estender o prazo | PASSOU |
| 14–15. Perda de renovação/autoridade durante processamento | Falha de transporte, encerramento real do backend da conexão de renovação, expiração e takeover; resposta recusada e thread encerrado | PASSOU |
| Falha antes do efeito externo | STARTED confirmado, renovação falha antes de generate; zero chamadas | PASSOU |
| 16–18. Resposta recebida mas perdida e COMPLETED recuperado | Interrupção antes de completion, falha do evento dentro da transação, interrupção depois de completion durável; apenas uma inferência | PASSOU |
| 19. Sequência/duplicação | Sequências contíguas, IDs únicos, eventos de intent únicos após recuperação e renovações concorrentes | PASSOU |
| 20. Rollback do evento | Falha de evento em transition/start/complete/renew; estado, resultado, prazo, revision e eventos anteriores preservados | PASSOU |
| R1 | 47 integrações do workspace real e 29 contratos unitários | PASSOU |

Expiração dos testes novos é controlada por SQL, sem sleep. Concorrência usa locks, Barrier e Event com timeout de proteção contra travamento. As falhas externas são simuladas; o encerramento de conexão atua somente no backend da renovação no PostgreSQL local descartável. O teste de operação longa reproduz a janela de prazo remanescente e aguarda renovação em thread; não executa inferência real de vários minutos. Os testes históricos de crash continuam usando processo SIGKILL e espera temporal própria.

### Totais reais

| Execução | Testes | Aprovados | Falhas | Erros | Skips/XFAIL |
|---|---:|---:|---:|---:|---:|
| Stage1 + auditoria inicial | 30 | 30 | 0 | 0 | 0/0 |
| Novas regressões R2A | 84 | 84 | 0 | 0 | 0/0 |
| Ampla: unit + contract + integração, sem arquivos live | 472 | 464 | 8 | 0 | 0/0 |
| Mesmo módulo de Model Intelligence na base R1 exportada | 11 | 3 | 8 | 0 | 0/0 |
| Revalidação dirigida intermediária após reset histórico do tracker | 184 | 183 | 1 | 0 | 0/0 |
| Revalidação dirigida final após migrador local | 184 | 184 | 0 | 0 | 0/0 |

As execuções se sobrepõem: são **472 casos distintos na branch candidata**, não a soma das linhas. A suíte ampla inclui Stage1, Run Events/fencing, checkpoint lifecycle, DAG, scoped tools, promoção, routing/providers simulados, backup/upgrade, APIs, readiness, autenticação humana, R1, Runner e heartbeat. A aprovação de uma caracterização de defeito aberto significa que o defeito ainda foi reproduzido.

**Oito falhas remanescentes confirmadas na base:** `tests/integration/test_postbaseline_model_intelligence.py`, por `TypeError: FakeRegistry.client_for() got an unexpected keyword argument 'operation'`. As oito identidades de teste coincidem entre candidato e exportação do commit R1 com `PYTHONPATH` apontando para a fonte antiga e outro banco. Não foram corrigidas fora do escopo nem convertidas em skips. A lista exata está em [results.json](evidence/task05/results.json).

**Ocorrência de ambiente local:** `test_stage1_migrations.py` apaga e recria `amodex_schema_migrations` para testar bootstrap, removendo o manifesto/checksums do conjunto operacional. Ao repetir a R1 depois da suíte ampla, `/readyz` retornou 503 (`database_unavailable_or_schema_missing`), enquanto os demais 183 casos passaram. Reaplicar o migrador guardado existente no banco local repôs 13/13 versões/checksums e `/readyz` voltou a passar, sem alterar código/teste de readiness ou enfraquecer sua asserção. A falha intermediária foi preservada nas evidências. Separar DB de bootstrap do DB de readiness é uma melhoria futura do harness.

Há um warning preexistente de depreciação Starlette/httpx. Os arquivos live foram explicitamente excluídos para não executar provedores reais; não são testes aprovados ou skips ocultos.

### Comandos reproduzíveis

Ambiente reutilizado: Python 3.13.5, pytest 9.0.2, psycopg 3.3.6 e PostgreSQL 17.11. Terminal/Git foram usados para inspeção, execução e entrega; o skill de runtime cloud foi consultado. Nenhuma dependência nova foi instalada. Navegador/MCP Playwright não foi necessário para alterações sem UI. Não houve verificação de Render, Neon ou WebKit nesta R2A.

Em `/workspace/Amodex`, banco descartável exclusivamente em loopback:

```bash
export AMODEX_TEST_DATABASE_URL=postgresql://amodex@127.0.0.1:55432/amodex_qa_task05
AMODEX_DATABASE_URL="$AMODEX_TEST_DATABASE_URL" \
AMODEX_MIGRATION_DATABASE_URL="$AMODEX_TEST_DATABASE_URL" \
AMODEX_DATABASE_ENDPOINT_GUARD=127 .venv/bin/python scripts/migrate_production.py

.venv/bin/pytest tests/unit tests/contract tests/integration \
  --ignore=tests/integration/test_stage1_gemini_live.py \
  --ignore=tests/integration/test_stage5_live_providers.py \
  --junitxml=artifacts/qa/task05/regression.xml -ra

# Reaplique o migrador LOCAL acima após a suíte que testa bootstrap.
.venv/bin/pytest tests/unit/test_run_lease_policy.py \
  tests/integration/test_run_lease_fencing_r2a.py \
  tests/integration/test_checkpoint_recovery_audit.py \
  tests/integration/test_runner_auth_ownership_r1.py \
  tests/unit/test_runner_authorization_r1.py \
  tests/unit/test_runner_heartbeat_contract.py \
  --junitxml=artifacts/qa/task05/final-contracts-restored.xml -ra
```

Baseline: exportação por `git archive e591b7d...` para `/tmp/amodex-r2a-baseline`; executar `python -m pytest tests/integration/test_postbaseline_model_intelligence.py` com `PYTHONPATH=/tmp/amodex-r2a-baseline/src`, cwd nessa cópia e `AMODEX_TEST_DATABASE_URL` apontando para o banco separado `amodex_qa_task05_baseline`.

[Evidências sanitizadas](evidence/task05/): JUnit reais com nomes, contagens e tempos; tracebacks/captured output removidos nas cópias versionadas, mantendo mensagens resumidas das falhas. [Metadados e hashes](evidence/task05/results.json) identificam as fontes validadas. Artefatos brutos ficam em `artifacts/qa/task05/`, ignorado pelo Git. A integração foi executada localmente; execução do workflow no GitHub não é apresentada como verificada.

## Limites e próximas dependências

- A lease de Run não é fencing criptográfico de um serviço externo. Entre autorização durável e envio de uma chamada há uma janela inevitável; perda de lease não revoga uma operação já enviada. Não se afirma cancelamento remoto nem exatamente uma vez no provedor.
- Uma chamada síncrona arbitrária que não retorna não pode ser interrompida por este helper. Enquanto DB/Worker permanecem ativos, a renovação pode continuar; não foi criado deadline global. Falha de DB encerra renovação, mas não mata inferência nem processo. Supervisão de subprocessos do Runner continua reservada à R4.
- Timeouts de conexão/SQL/TCP limitam a renovação nos ambientes PostgreSQL/TCP suportados. Não se simulou cada falha possível de kernel, DNS ou infraestrutura. A segurança de escrita permanece no banco mesmo que o thread atrase.
- `LEASE_RENEWED` é novo valor de evento: leitores com enum estrito precisam da versão correspondente. Nenhum formato de tabela mudou.
- STARTED abandonado não é autorização de replay. UNKNOWN_OUTCOME exige reconciliação/intervenção; não há redispatch, retry ou Recovery completo novo.
- Owner/fence ainda pertencem ao Run, não a um modelo persistente de Attempt. Credencial compartilhada e identidade declarada do Runner continuam com os limites descritos na R1.
- CR-03 (dispatch/claim), CR-05 (supervisão), CR-06 (report/audit/ack), CR-07/08/09 (idempotência, publicação e reconstrução/validação) permanecem fora desta correção. As oito falhas do fake de Model Intelligence e o isolamento do harness de bootstrap também ficam explicitamente abertos.

**R2B:** modelar identidade persistente de tentativa, vincular fencing a ela sem enfraquecer a guarda temporal entregue; definir consistência transacional de reports/auditoria e acknowledgements idempotentes; distinguir falha confirmada de commit com ACK perdido. A futura reconciliação deve usar claim válido e recibos duráveis, nunca ressuscitar lease nem repetir operação externa incerta por expiração.

Commits de implementação: `3a008b5` (validade temporal), `3cfed71` (renovação), `070bc37` (Worker), `c16fe85` (testes/CI). As referências anteriores foram verificadas e preservadas. Somente a branch R2A recebe os commits; sem merge, PR, deploy, mudança de infraestrutura/credenciais ou operação contra produção.
