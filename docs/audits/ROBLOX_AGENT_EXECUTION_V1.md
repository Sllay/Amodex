# Roblox Agent Execution V1 — Tarefa 08

**Resultado: fluxo determinístico aprovado e Open Cloud real LIVE_PASS.**
Branch `feat/roblox-agent-execution-v1`, base `758854741a5d13f61c6e7f6b4ae8ac31461cadd1` de `feat/roblox-first-vertical-slice`. Commit de implementação: `10777b0`. Checkpoint VALIDATED para este escopo; sem merge, PR, promoção, deploy ou publicação. Data: 2026-11-07.

## Fluxo implementado

`HermesScopedWorker` registra `amodex_roblox_luau_test` no plugin/toolset existente `amodex_scoped`. A ferramenta só é anunciada quando o runner fornece a integração e o TaskPackage concede `roblox.luau.execute`. A verificação da superfície exige exatamente as quatro ferramentas anteriores mais essa ferramenta opcional; ferramentas adicionais continuam causando recusa.

O modelo só fornece `path` e `remote` (booleano, padrão falso). Não escolhe Universe, Place, URL, segredo, comando, expectativas ou aprovação. Leitura passa por `ScopedWorkspaceTools.read_file`; edição usa a ferramenta `amodex_patch_file` existente, com escopo, proteção de caminhos, lease de escrita e hash de conteúdo anterior. As ferramentas e contratos existentes não foram relaxados.

O runner injeta `RobloxLuauTestTool` e `SupervisedLuauApproval`, vinculados a package/task/attempt, Environment, resource ID, alvo e SHA256 exato do código revisado como puro. O recurso `ConnectorResourceRecord` é validado por `RobloxCloudTarget.from_resource` contra o Environment e comparado ao alvo aprovado antes de acessar a API. Os IDs desta demonstração não são constantes do produto.

O `luau-analyze` é um executável absoluto configurado pelo runner, fora do workspace; recebe uma cópia temporária do texto que será submetido, sem shell, configurações do projeto ou credenciais herdadas. Há timeout de 10 segundos; só status e returncode são expostos. Runtime ausente ou análise malsucedida bloqueia o POST. Trata-se de análise estática, não execução local de código arbitrário.

Com `remote=true`, a ferramenta exige fonte com hash aprovado, análise PASS e segredo concedido pelo `SecretBroker` da tarefa. Não há fallback para variáveis globais. Reutiliza `RobloxLuauClient` para um único POST, polling limitado do recibo canônico retornado e até duas páginas de logs da mesma task. Preserva a configuração oficial de proxy/CA ao construir o cliente antes da limpeza de ambiente do Hermes.

A resposta ao agente inclui estado, HTTP observado, caminho da execução, hash da fonte, análise local, resultados esperados verificados, comparação dos logs e status. `COMPLETE` isolado não significa aprovação: o resultado JSON deve corresponder às expectativas do supervisor, incluindo tipos, e os logs esperados devem estar presentes. Resultados remotos divergentes não são reproduzidos como texto arbitrário.

Logs entregues ao agente são limitados a 40 linhas de 200 caracteres, removem caracteres de controle e ocultam o segredo injetado, atribuições usuais de credenciais, Bearer e URLs. A resposta informa truncamento; evidência fora desse limite não conta como log aprovado. Diagnósticos locais, corpos HTTP e exceções brutas não entram na resposta. Os logs continuam sendo dados não confiáveis, não instruções.

## Demonstração de edição e teste

`scripts/roblox_agent_demo.py` utiliza um **agente roteirizado determinístico**, sem inferência, chamando os handlers registrados pelo plugin de produção. Não é uma execução do pacote Hermes com um modelo real. Os testes também percorrem `HermesScopedWorker.run`, substituindo somente os módulos do runtime Hermes/modelo e o transporte externo.

O agente recebe a tarefa de corrigir pontuação numa cópia descartável de `tests/fixtures/roblox/score_test.luau`. A cópia começa com `return 0`, é lida e corrigida pela ferramenta de patch para `return math.clamp(coins, 0, 100) * multiplier`, passa pela análise local e solicita o teste remoto pela nova ferramenta. Depois interpreta o retorno estruturado. A fixture original do repositório fica intacta.

| Etapa | Evidência |
| --- | --- |
| Arquivo alterado pelo agente simulado | `score.luau` em workspace temporário |
| Hash antes | `45c12284d7828a8b2a11a3ecaffca0840b470437456dfb3523382a73af67ff76` |
| Hash após edição e submetido | `37440f650821284b231f665bb99927d8bb1712db36ddf4df7c736c9e2ecc4fd4` |
| Ferramentas utilizadas | `amodex_read_file`, `amodex_patch_file`, `amodex_roblox_luau_test` local e remota |
| Análise local real | Luau 0.746: PASS; caso inválido recusado nos testes |
| HTTP simulado | SIMULATED_PASS, MockTransport, sem contato com experiências sintéticas |
| Open Cloud real | LIVE_PASS: um POST e dois GETs, todos HTTP 200 |
| Alvo real | Universe `10769712453`, Place `118200154785968`, versão retornada `1` |
| Resultado remoto | COMPLETE, `{ok: true, checks: 3, score: 42}` |
| Log sanitizado | `AMODEX_LUAU_OK checks=3 score=42` |
| Modelo real / inferência paga | Não utilizados |

O ambiente Codex novo estava ativo com segredo `ready` e política `enforced`. A demonstração cria um registro de recurso e vínculo de Environment efêmeros, exclusivamente para o alvo autorizado; não cria um vínculo no banco do AMODEX Web. Nenhuma outra experiência foi acessada. Nenhuma publicação, SavePlaceAsync, DataStore, compra ou alteração persistente foi executada.

## Segurança, autoridade e limitações

- Uma instância de ferramenta compartilhada por tarefa consome a autorização **antes** do POST. Erro HTTP, timeout, recibo inválido, sucesso ou falha não liberam outra submissão. Chamadas concorrentes são recusadas; não há retry de POST nem redirects. O hash aprovado impede que o agente transforme a fixture revisada em código com serviços ou efeitos externos.
- A autorização de tarefa, validade temporal, alvo e callback do supervisor são verificados antes das chamadas HTTP. `HermesScopedWorker.interrupt` revoga a ferramenta mesmo sem modelo ativo; nenhuma chamada posterior é iniciada após a perda conhecida dessa autorização. Um POST já enviado pode continuar na Roblox: interrupção não equivale a cancelamento remoto.
- O recibo válido é conservado quando uma consulta posterior falha. Resultado ambíguo produz `UNKNOWN_OUTCOME` e `resubmit=false`. Não reiniciar a demonstração nem reconstruir a ferramenta para resolver ambiguidade; é necessária reconciliação supervisionada.
- O bloqueio de nova submissão é **local ao processo/instância**. Não é journal durável, acknowledgement ou garantia de execução exatamente uma vez na API externa; um `idempotency_key` local não foi usado como tal garantia. O host deve compartilhar a instância na tarefa e não recriá-la após submissão.
- Este caminho Hermes não está conectado ao dispatch/Run/lease do Stage1Worker. O callback obrigatório serve à supervisão explícita e deve consultar a autoridade R2A se um host incorporar esse contexto; não oferece fencing externo atômico. A demonstração autoriza uma sessão curta com TaskPackage de cinco minutos, execução Luau de dez segundos e polling limitado. Não declarar este caminho seguro para tarefas autônomas longas.
- R2B (Attempts/reports/acks), R3 (dispatch/claim), R4 (supervisão/interrupção global), Recovery e checkpoints avançados continuam pendentes. As oito falhas preexistentes de Model Intelligence, consolidação e deploy não foram investigadas ou alteradas.
- O Segredo HTTP disponível no **Codex Cloud** não é automaticamente instalado no Render ou em outros Runners. Cada host precisa de binding explícito, domínio autorizado, proxy/CA quando aplicável e concessão pelo `SecretBroker`; o agente não recebe a credencial como argumento nem saída.

## Testes e arquivos alterados

**90 testes aprovados** na suíte dos módulos afetados, sem falhas, erros ou skips; 35 são da integração nova. Após reforçar a redação de logs, os 35 foram repetidos e aprovados. Python 3.13.5, pytest 9.0.2, httpx 0.28.1 e pydantic 2.13.5. O analisador oficial Luau 0.746 foi instalado apenas na área temporária de ferramentas; hashes estão nas evidências.

Cobertura: anúncio com/sem permissão e configuração, escopo de leitura, travessia, escrita protegida e alias, divergências Universe/Place/Environment/recurso/attempt, expiração/revogação, segredo ausente sem fallback, hash não aprovado, flag explícita, falha/ausência de análise local, HTTP 401/403/500, timeout/recibo ambíguo sem repetição, concorrência, interrupção conservando recibo, COMPLETE com resultado errado ou tipo errado, logs limitados/sanitizados e preservação de leitura/busca/patch/pytest anteriores. Shell continua recusado; superfície inesperada continua bloqueada.

Arquivos:

- `src/amodex/tools/roblox_luau.py`: autorização supervisionada, análise, submissão única e resposta estruturada.
- `src/amodex/runtime/hermes_worker.py`: registro opcional, superfície exata, despacho e revogação.
- `src/amodex/integrations/roblox_cloud.py`: callback antes de cada request e último status HTTP, mantendo cliente/protocolo existentes.
- `scripts/roblox_agent_demo.py`: demonstração determinística reproduzível, simulação por padrão e `--live` explícito.
- `tests/integration/test_roblox_agent_execution.py`: testes novos com HTTP simulado e teste do analisador oficial quando configurado.
- Este relatório e `docs/audits/evidence/task08/`: evidências compactas e JUnit, sem credenciais.

Comando dirigido (definir o caminho do executável confiável em `AMODEX_TEST_LUAU_ANALYZER`):

```bash
PYTHONPATH=src python -m pytest tests/integration/test_roblox_agent_execution.py tests/unit/test_roblox_cloud.py tests/integration/test_stage2_scoped_tools.py -ra
```

Nenhuma regressão ampla ou E2E de navegador foi executada. [Resumo e comandos](evidence/task08/results.json), [JUnit dos módulos afetados](evidence/task08/affected-tests.xml), [JUnit final da integração](evidence/task08/agent-tests.xml), [execução Open Cloud real sanitizada](evidence/task08/live.json).

## Próximo passo para AMODEX Web

Conectar a seleção de recurso/Environment do Web a um runner supervisionado que carregue o registro autorizado, conceda o segredo e produza a aprovação do hash após revisão do Luau. Injetar a mesma ferramenta no worker durante toda a tarefa, com validação de autoridade Run/lease quando disponível. Isso exige transporte de aprovação/recibo e reconciliação alinhados ao roadmap; não adicionar um botão que conceda execução irrestrita nem inferir autorização apenas do login OAuth. Não foi criado outro Core, Worker, Director ou scheduler nesta tarefa.
