# AMODEX V1 supervisionada — fechamento B/C

**V1_BLOCKED / REAL_MODEL_BLOCKED.** O fluxo de produto offline de B e C está implementado, integrado e testado. Falta a demonstração com um modelo externo gratuito autorizado; a versão implantada e Safari/iPhone físico também não foram validados. Não há declaração de V1 concluída nem inferência simulada apresentada como real.

## Preparação final do Render — continuação sobre 885a458

**Preparação técnica local concluída; piloto Render real pendente. V1_BLOCKED permanece**, pois autenticação, cota, inferência e consumo real do serviço implantado não foram comprovados. As conexões Groq/NVIDIA/OpenRouter/Gemini classificadas free na Web foram confirmadas pelo proprietário; não foram tratadas como credenciais ausentes no Render nem como prova de vínculo ao filho Hermes. Não transferimos chaves ao Codex. A observação anterior de ausência de chave descreve somente o executor Codex.

Código desta preparação: **`5566b7623f4a9bee045548f0cc38d0b9a1f95a85`**. O HEAD posterior registra este relatório/evidência; o SHA final de entrega é informado na mensagem de entrega. B/C abaixo são histórico preservado, não checkpoints refeitos nesta continuação.

- O wheel e seu extra eram válidos, mas o build antigo do Render instalava somente `pip install .`. O candidato usa `sh scripts/build_render.sh`, com `'.[supervised]'`, bootstrap versionado de Hermes e Luau, checkout rastreado e manifesto. Prova de instalação **limpa**, imports de site-packages em Python isolado, seguida de Hermes real com provider loopback simulado: PASS, quatro chamadas e análise Luau real. Nenhuma inferência externa.
- Hermes fixado em `adecc4024456b387b1518813b90bd60c82b51287`; Luau **0.746** teve zip e executável verificados por SHA-256. O download do asset oficial também foi exercitado, sem retry. Os hashes, revisão da fonte reconstruída e métricas locais constam na [proveniência Render](evidence/v1-supervised/render/preparation.json). A fonte dos assets de teste foi o commit base 885a458; isso não é uma implantação da release.
- Grants do host suportam tokens públicos verificados `@runtime/project`, `@runtime/hermes`, `@runtime/luau-analyze`, `@release`, `@pinned`, remontáveis via Secret File do Render. Não há chave nesses grants. Resultados, snapshots e autoridade continuam no PostgreSQL; assets são material de build reconstruível e trabalho em andamento é temporário. Nenhum persistent disk pago.
- Novo startup verifica runtime/grants e inicia um único Uvicorn worker. Migrations automáticas ficam **desligadas por padrão**; só o flag explicitamente autorizado delega ao runner/guard já existentes. Runtime habilitado sem assets/grants bloqueia startup/readiness. Não foi criada migration nesta tarefa. O manifesto e `/readyz` foram verificados em banco isolado, preservando o baseline validado e aplicando somente a feature necessária no clone de teste.
- Preferência Groq **entre rotas já elegíveis**, com medição de implementação/tool-calling/contexto e observação recente de cota. Governor e registry continuam gates separados. Nenhuma mudança de unknown para free. GET privado `/v1/supervised/host-status` compara metadados Web e processo, presença de credencial e rota medida, sem imprimir valor, referência secreta ou headers; não faz inferência e não comprova cota por si só.
- Uma aprovação supervisionada ativa por instalação, coordenada transacionalmente antes de qualquer processo/HTTP. A segunda fica pendente com `SUPERVISED_CAPACITY_BUSY`/409. Nenhum lock SQL durante chamada externa. Estado incerto continua sem replay, respeitando R1/R2A/R2B/R3/R4 e os limites de B.
- **17 testes direcionados aprovados**, zero falha/erro/skip: sete integrações de preparação (composição de produção, artefato, `/readyz`, runtime/grants, capacidade e startup sem migration) e dez testes de seleção, incluindo a nova preferência Groq. Os testes de seleção foram reexecutados porque esse método mudou; não houve auditoria nem bateria A/B/C completa. [JUnit](evidence/v1-supervised/render/preparation.xml), [Hermes do wheel limpo](evidence/v1-supervised/render/clean-wheel-hermes.json).
- Hospedagem Free: o processo Python Hermes mediu localmente ~162 MiB de pico RSS; **não** é o pico combinado Web+filho nem prova de capacidade real do Render. Boot frio, suspensão, OOM e rede ainda podem bloquear o piloto. Os três URLs oficiais do Render consultados retornaram HTTP 403 neste executor; limites atuais do plano e montagem/retenção dos assets exigem confirmação no painel/documentação pelo operador. Não alegamos verificação oficial ou funcionamento implantado que não ocorreu.

O serviço de produção informado continua **My Workspace / Amodex**, branch `deploy/render-neon-cloudinary-v1`, SHA `9ef70070ddf4b825276160fedcba53f9822fdd9c`, sem alteração por este trabalho. `render.yaml` é proposta versionada, não configuração aplicada; autoDeploy está false na proposta, Free preservado e nenhum serviço novo foi criado. Nenhum deploy, migração Neon, merge, chamada Roblox, inferência paga ou coleta de credenciais ocorreu.

O [procedimento completo de piloto e rollback](RENDER_SUPERVISED_PILOT.md) lista configuração sem valores secretos, UUIDs a vincular, host-status, aprovação separada de migrations/ativação, uma única tarefa pequena e preservação de UNKNOWN. Não presumir que só 0010 está pendente frente ao SHA antigo de produção. Rollback preserva schema/receipts e **quarentena Runs supervisionados antes de reativar Worker antigo**, que desconhece sua exclusão do claim genérico. Não há down migration destrutiva.

Próximo passo depende de autorização do proprietário para a janela de piloto no serviço existente, sem transferir segredos. A existência das chaves no Render permite preparar esse piloto no próprio host; não autoriza executá-lo neste turno nem declarar V1_SUPERVISED_PASS.

Branch: `release/amodex-v1-supervised`. Base desta continuação: `e1309cacd8c6c95fc8e9ae816e4e23dc4617d36e`. Checkpoints funcionais:

- **B — `ab1e041`**: autorização Web, Run/TaskPackage persistentes, execução Hermes limitada, artefato/resultado transacionais, 43 testes direcionados.
- **C — `7e6d256`**: interface mobile, download do código, prova do launcher isolado, dependências opcionais e evidências. Este é o **SHA funcional validado**; os hashes completos constam em [proveniência](evidence/v1-supervised/bc/provenance.json).
- O commit posterior registra o fechamento e a compatibilidade da fixture QA, sem alterar código de produto. O SHA final de entrega é o HEAD da branch e é informado na mensagem de entrega; ele não é confundido com prova de deploy.

A orientação mais recente do proprietário autorizou concluir o trabalho offline mesmo sem credencial. Portanto B/C foram implementados depois do bloqueio registrado no closeout anterior. **Checkpoint A foi preservado, sem refazer sua auditoria, suas migrations históricas ou a bateria de 65 testes.** Houve 19 regressões diretamente relacionadas às fronteiras compartilhadas alteradas, explicadas abaixo.

## Funcionalidades disponíveis

O link **Luau supervisionado** abre `/supervised` no Workspace existente. O proprietário seleciona um rascunho READY, projeto/Environment e arquivo Luau previamente autorizados pelo host. A preparação grava, numa única transação, Run, TaskPackage, snapshot/hash da fonte, grant e correlação com o rascunho. Não há inferência nessa etapa.

Depois da revisão, uma aprovação explícita do hash adquire uma tentativa por CAS no PostgreSQL. O launcher abre um processo isolado, usa **o Stage1Worker e o HermesScopedWorker existentes**, adquire/renova a lease R2A e revalida autoridade antes de HTTP e de cada ferramenta. Nenhum novo Director, Worker, scheduler ou serviço de filas foi criado.

O Hermes lê e altera somente o arquivo autorizado através de ScopedWorkspaceTools e writer lease. A nova ferramenta condicional `amodex_luau_validate` reutiliza o analisador isolado já utilizado pela integração Roblox. O serviço também analisa independentemente os bytes finais; a fala do modelo não é evidência de aprovação. Resultado, artefato, validação e transição/evento terminal de Run são gravados na mesma transação. Uma falha do evento final desfaz a conclusão e deixa a projeção UNKNOWN, sem novo trabalho.

O código permanece em artefato PostgreSQL consultável e baixável, com caminho, hash e revisão base. Não altera o checkout do host nem o jogo. `COMPLETED` exige edição pela ferramenta autorizada e análise estática PASS; não significa que testes dinâmicos ou o comportamento pedido foram comprovados. A aceitação funcional permanece supervisionada.

## Fluxo e segurança de custo

```text
Web privada → rascunho READY → Run + TaskPackage + fonte persistidos
→ revisão/hash → aprovação única → processo isolado / Stage1Worker com lease
→ Model Intelligence medido + Provider Governor + registry autorizado
→ preflight somente leitura → Hermes / leitura / patch / análise Luau
→ validação final independente → artefato + resultado + evento Run atômicos
→ consulta / histórico / download no celular
```

A seleção reutiliza o roteamento medido de implementação. Requer rota ELIGIBLE, tool-calling/coding, contexto suficiente, pool e rota `free`, observação de disponibilidade HEALTHY/cota disponível com no máximo cinco minutos. Não modifica `unknown` para `free`. Reutiliza os adapters OpenAI-compatible existentes; não há novo gateway/provider. OpenRouter exige o modelo medido `:free`, sem roteador aleatório ou fallback pago.

Quando os gates locais autorizarem uma credencial, o processo fará uma única verificação GET `/models`, sem retry, verificando também a presença do modelo selecionado. Seu 200 não será interpretado como prova de gratuidade/cota. A inferência fica limitada a 6 chamadas, 512 tokens de saída/chamada, 48.000 bytes acumulados de entrada HTTP, 12 ferramentas, 60 segundos Hermes e 90 segundos do processo completo. Fonte/artefato: 16 KB; resposta HTTP: 1 MB; eventos: 40. Cada chamada tem intenção durável antes do transporte, sem lock SQL durante HTTP.

A prova com o **Hermes real fixado no commit `adecc4024456b387b1518813b90bd60c82b51287`** identificou que ele recria o cliente HTTP por chamada. O guard cobre também essa fábrica de clientes; proteger somente o cliente inicial era insuficiente. SDK/loop/fallback ficam bloqueados após falha ou resultado incerto. `.env` global no checkout Hermes é recusado nesse caminho; proxy/CA do host são preservados. Credenciais, respostas HTTP brutas, argumentos de ferramentas e texto livre do SDK não vão para a projeção de logs.

A interface não recebe roots, executáveis, chaves ou URLs de provider. Requer sessão privada do proprietário e aprovação de mesma origem; bearer de Runner não substitui a sessão humana. Os endpoints/projeções usam `private, no-store`.

## Persistência, retries e autoridade

`Idempotency-Key` estável identifica a preparação por Environment/payload canônico. Retry retorna a mesma execução/Run; payload divergente retorna 409. O primeiro pedido congela rascunho, fonte e grant. Um rascunho origina no máximo uma execução. Aprovação repetida e consulta após perda de resposta não iniciam outro processo nem outra chamada.

Os Runs desse caminho são excluídos do claim genérico, inclusive após expiração. Queda após início, timeout ou impossibilidade de confirmar autoridade preserva UNKNOWN e exige reconciliação; não há replay automático. O GET reconcilia somente a projeção depois de deadline/lease vencidos, sem adquirir nova autoridade. Não há promessa de exactly-once do provider externo.

Run/Task/artefato são correlacionados sem alterar os contratos de Runner Job R1/R3/R4/R2B. Um Job COMPLETED continua sem concluir automaticamente Run/Task/Checkpoint. Perda da lease não permite report tardio de sucesso; o teste de fence expirada prova essa recusa. Pausa/cancelamento existente do Run é verificado antes de novos efeitos.

O alvo é validado contra o vínculo projeto/Workspace/Environment ativo e o recurso conectado. Este caminho de release admite exclusivamente Universe **10769712453** / Place **118200154785968**, recusando o nome protegido. Os IDs não viram constantes universais da integração Roblox. **Nenhuma chamada Roblox é oferecida neste novo fluxo**; a ferramenta existente da Tarefa 08 permanece disponível somente por seu contrato anterior. Nenhuma publicação, SavePlaceAsync, DataStore, economia ou outra experiência foi acessada.

## Evidências reais e separação de resultados

| Prova | Resultado | Natureza |
| --- | --- | --- |
| Web → aprovação → ferramentas Hermes → edição → artefato persistente | PASS | Provider/agente determinísticos somente nos testes; PostgreSQL e analisador reais |
| Launcher de produção, com filho Python separado e sem modelo configurado | BLOCKED / REAL_MODEL_BLOCKED persistido | Processo e validação locais reais, **zero inferência** |
| Runtime Hermes fixado + provedor HTTP em loopback | PASS; 4 chamadas, 31.093 bytes de entrada, read/patch/analyze | Hermes/SDK/analisador reais, **provider simulado**, não modelo real |
| Hermes fixado + HTTP 500 simulado | PASS da proteção; uma chamada, zero retransmissões, UNKNOWN | Prova negativa local; não chamada externa |
| Chromium mobile 390×844, toque, login/revisão/aprovação/download/reload | PASS | Browser real; provider simulado e cenário sem credencial |
| Modelo externo real gratuito | **REAL_MODEL_BLOCKED** | Nenhuma credencial gratuita autorizada neste executor |
| Roblox Open Cloud | Não executado | Evidência histórica da Tarefa 07 não foi repetida |
| iPhone físico/Safari e serviço implantado | Não validados | Exigem piloto autorizado posterior |

As [capturas mobile](evidence/v1-supervised/bc/mobile-simulated.png) identificam expressamente **IA simulada**. O [cenário bloqueado](evidence/v1-supervised/bc/mobile-blocked.png) mostra análise Luau PASS e zero chamadas ao modelo. Não se afirma que um modelo real escreveu o código desses testes.

O [preflight atual](evidence/v1-supervised/bc/provider-preflight.json), executado sem alterar a configuração do host, continua com `credential_bound=false` e billing `unknown` nos quatro pools suportados. Exit 2, `BLOCKED_NO_APPROVED_FREE_PROVIDER`; cota não verificada, inferência não realizada. Não houve GET autenticado a provider real porque a primeira condição já bloqueia. Essas observações descrevem este executor, não a configuração de outras contas ou do Render.

## Testes e arquivos

**47 casos novos distintos e 19 regressões diretamente afetadas passaram, sem falha, erro ou skip final.** Alguns casos de B foram repetidos em C porque a validação de alvo e a projeção mobile mudaram; a contagem remove essa sobreposição. Não foi rodada a suíte completa nem a bateria de A.

- [B — 43 casos](evidence/v1-supervised/bc/checkpoint-b.xml): intake concorrente, conflitos, alvo/path/fonte divergentes, sessão/origem, ausência de credencial, custo unknown, execução estruturada, análise inválida, lease perdida, timeout/HTTP 500, reinício, ausência de replay, rollback terminal, orçamento, SSE e fábrica de clientes Hermes.
- [C — 10 casos](evidence/v1-supervised/bc/checkpoint-c.xml): dois percursos Chromium, composição Web privada e negações de alvo, incluindo projeto desativado. Recarregar recupera o artefato/bloqueio; perder o ACK de aprovação causa uma única aprovação e uma única tentativa.
- [Launcher isolado — 1 caso](evidence/v1-supervised/bc/isolated-launcher.xml): comando real do filho salva bloqueio/validação sem fixture de provider no subprocesso.
- [Compatibilidade da fixture QA](evidence/v1-supervised/bc/qa-baseline-compatibility.xml): o mesmo teste do launcher também passou partindo de um banco QA que já contém a migration nova; não é contado duas vezes.
- [19 regressões](evidence/v1-supervised/bc/affected-regressions.xml): gate privado/R1, claim e transição/renovação R2A e Worker antigo; intake concorrente R3; recibo/ACK perdido R2B; rejeição R4 antes de processo/report; superfície Hermes anterior e fluxo Roblox com HTTP estritamente simulado. São necessárias porque `claim_next`, transação Run, gate e bridge Hermes são fronteiras compartilhadas alteradas aqui. Não são um novo teste Open Cloud.
- [Hermes loopback](evidence/v1-supervised/bc/pinned-hermes-loopback.json) e [HTTP 500](evidence/v1-supervised/bc/pinned-hermes-http500.json): compatibilidade real do pin/SDK e recusa de retransmissão; executados pelo probe offline, separados de inferência real.

Falhas intermediárias de fixtures/uso de Playwright e o guard que inicialmente não alcançava os novos clientes Hermes foram corrigidos e revalidados. Não se esconderam erros com skip/xfail ou sucesso sintético. Há um warning de depreciação da combinação FastAPI/TestClient/HTTPX existente, sem falha de teste.

Arquivos de produção alterados: `api/access_gate.py`, `api/workspace_app.py`, novos `api/supervised_routes.py` e `api/supervised_web.py`; `runtime/hermes_worker.py`, `runtime/worker.py`, novos `runtime/supervised_execution.py` e `runtime/supervised_guard.py`; `storage/postgres.py`, `model_intelligence/service.py`, `providers/openai_compatible.py`. A análise isolada de `tools/roblox_luau.py` foi extraída para função compartilhada, preservando o comportamento da ferramenta Cloud. Também foram adicionados migration 0010, extras opcionais em `pyproject.toml`, probe offline, testes e [instruções de uso](V1_SUPERVISED_USAGE.md). A expectativa do teste de migrations de A foi ajustada ao novo manifesto, **sem reexecutar A**.

## Migrations e prontidão de implantação

Somente a migration **0010_supervised_coding.sql** é nova. Ela adiciona a tabela de execução, FKs, unicidade de Run/rascunho e `(Environment, intent_key)`, snapshots e evidências. Nenhuma migration histórica foi reescrita. Os testes clonaram o baseline local já validado em A, aplicaram apenas 0010, provaram reentrada e rollback transacional da nova DDL. Uma verificação adicional confirmou que a fixture também funciona quando o banco QA de origem já contém 0010: somente essa feature é normalizada dentro do clone descartável; o banco de origem e as migrations históricas não são modificados. Não se refez a consolidação histórica.

A preparação é opt-in por grants do host; ausência de migration/grant/sessão não libera execução. `/readyz` exige o novo manifesto; rollback de aplicação deve **preservar** a tabela e seus históricos. Não remover a tabela ao voltar para um executável antigo: ele não conhece a exclusão dos Runs supervisionados no claim genérico. Parar os workers genéricos antes desse rollback e reconciliar/quarentenar os Runs supervisionados; não reativá-los para replay. Remoção de schema após dados reais requer decisão própria do operador.

Nenhum Render/Neon de produção foi alterado; nenhum deploy, merge ou PR foi criado. Um piloto precisa preparar runtime/grants e credencial nesse host separadamente, aplicar a migration pelo guard existente, verificar SHA e readiness reais e executar uma única demonstração curta pela interface. O segredo Codex não cria essa autorização no Render.

## Bloqueio restante e próximo passo

**Dependência externa atual:** uma credencial de provider gratuito já autorizado, pool/billing explicitamente free e cota/rota medida aprovadas no host da demonstração. Não enviar o valor em chat. O caminho executável e os campos necessários estão em [uso/configuração](V1_SUPERVISED_USAGE.md). Ao disponibilizá-los, executar o preflight e **uma** tarefa curta pelo caminho Web real, mantendo os limites e preservando UNKNOWN em caso de resultado incerto.

Depois, validar a versão implantada e Safari/iPhone no piloto autorizado. Até isso acontecer, o resultado permanece **V1_BLOCKED**, não V1_SUPERVISED_PASS nem V1_RC_ONLY. A falta atual é uma prova crítica de inferência real, além da validação implantada.

Continuam excluídos R5–R10, Recovery autônomo longo, reidratação/replay de UNKNOWN, promoção automática, edição arbitrária no Studio e publicação. O supervisor não promete cleanup infalível após SIGKILL/falha total do host. Este fechamento entrega o fluxo offline utilizável e supervisionado sem declarar resolvidos esses limites.

## Histórico preservado de A

A começou em `af79465c4920d269d5823e854cda295c8b7abe93`; o commit validado foi `eeb260334022aed039596ac023bd298d4c3b2107`, seguido do closeout `e1309cacd8c6c95fc8e9ae816e4e23dc4617d36e`. Seus 65 testes, upgrade das 15 migrations então existentes, correções de fixtures Model Intelligence/R4 e preflight anterior continuam em [evidências de A](evidence/v1-supervised/commands.json). Não foram reinterpretados como demonstração real de IA ou validação mobile. Esta continuação implementa as lacunas B/C que aquele relatório identificava, preservando a linhagem e os contratos já aprovados.
