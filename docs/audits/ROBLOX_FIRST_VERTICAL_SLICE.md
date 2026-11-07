# Roblox Cloud + Web Play — primeiro fluxo

**Entregue:** cliente Open Cloud pequeno, demonstração executável e fixture Luau validada localmente. **Execução Roblox remota: LIVE_BLOCKED. Web Play: BLOCKED.** Não há comprovação de jogo visual nem de execução na infraestrutura Roblox.

Branch: [`feat/roblox-first-vertical-slice`](https://github.com/Sllay/Amodex/tree/feat/roblox-first-vertical-slice). Base remota conferida, sem diferenças: `fix/run-lease-fencing-r2a`, `6d2facd63f72c4237bc63f0671df660f791a9b49`. SHA de código/testes validado: `212fc8e984dc923eb3576c4920c2fee6a4cb38ae`; o commit seguinte contém somente este relatório/evidências. Data: 2026-11-07 UTC.

## O que funciona

- `src/amodex/integrations/roblox_cloud.py`: um POST para criar Luau Execution Session Task, GETs limitados para consultar estado, coleta de logs paginados e retorno de resultados/erro. O cliente não publica, cria experiências, usa cookies ou repete POST. Timeout/erro de transporte, 5xx ou recibo inválido ficam ambíguos; 401/403/429 são recusas explícitas. Falha durante polling conserva o caminho conhecido para reconciliação, sem recriar a task.
- Reuso de `ConnectorResourceRecord`/Place selecionado e vinculado ao Environment; `TaskPackage.scope.allowed_operations` exige `roblox.luau.execute` no caminho `submit_scoped`; leitura passa por `ScopedWorkspaceTools.read_file`. Credencial pode vir do `SecretBroker` existente. IDs, ambiente, caminhos retornados e destino HTTPS são validados; redirects são recusados e erros não reproduzem headers/corpos privados.
- O binding usa as capacidades existentes `runtime.execute`, `runtime.console` e `runtime.diagnostics`. O caminho Studio/`DESKTOP_STUDIO`, OAuth, Runner Job, Core e scheduler existentes permanecem inalterados. Este cliente ainda não está exposto como botão na UI nem instalado no laço Director/Worker; não afirma implementar journal de operações ou a guarda de Run da R2A para chamadas independentes.
- `scripts/roblox_luau_smoke.py` executa a fixture `tests/fixtures/roblox/score_test.luau`: três asserções de pontuação/limites, resultado esperado `{ok=true, checks=3, score=42}` e marcador de log. O script é lógica pura, sem serviços Roblox, persistência, rede, compras ou publicação.

Demonstração sem credenciais ou tráfego remoto:

```bash
.venv/bin/python scripts/roblox_luau_smoke.py
# SIMULATED_PASS; remote_executed=false; resultado e logs conferidos
```

Os estados/respostas desse comando vêm de `httpx.MockTransport` e `tests/fixtures/roblox/open_cloud.json`: **não representam execução Luau no servidor Roblox**. Separadamente, o runtime oficial **Luau 0.746** executou a fixture real no Linux: análise estática exit 0; execução exit 0 com `AMODEX_LUAU_OK checks=3 score=42`. Não valida física, DataModel ou serviços Roblox.

## Contrato oficial consultado

Fonte primária acessível: [`Roblox/creator-docs`](https://github.com/Roblox/creator-docs/tree/ccc2b01d9bf8b85dee2905d1aaff3444fcbd01c5), HEAD `ccc2b01d9bf8b85dee2905d1aaff3444fcbd01c5`, de 2026-11-07. Foram lidos OpenAPI, guia de publicação e MCP Studio/Open Cloud, sem auditoria geral do projeto.

- [Luau Execution](https://create.roblox.com/docs/cloud/reference/features/luau-execution): POST `/cloud/v2/universes/{u}/places/{p}[/versions/{v}]/luau-execution-session-tasks`; GET do recurso versionado `.../luau-execution-sessions/{s}/tasks/{t}` e `/logs`. Chave no header `x-api-key`; scopes `universe.place.luau-execution-session:write` e `:read`. Estados QUEUED/PROCESSING/COMPLETE/FAILED/CANCELLED; resultados em `output.results`, logs em `luauExecutionSessionTaskLogs[].messages`.
- Limites oficiais: script até 4 MB/5 minutos, dez tasks incompletas por Place, criação 5/minuto por dono da chave; logs retidos até 450 KB e task retida por 24 h. O cliente adota limites menores: script 64 KB, timeout de script padrão 10 s, polling padrão 2 s/30 s/20 consultas e até cinco páginas de logs. Paginação incompleta produz erro, não falsa coleta completa. Timeout HTTP é por operação/fase de transporte; não cancela a execução remota nem constitui deadline global de tarefa.
- **Não há simulação de física; scripts Server/Local não iniciam automaticamente.** Mudanças de DataModel são locais à task e não persistem, mas código executado pode chamar serviços com efeitos externos, como DataStores. Portanto o teste autorizado aqui se restringe à fixture pura; isso não torna qualquer código gerado seguro para execução remota.
- O cliente exige o recibo canônico versionado com sessão/task correspondente ao alvo. Formatos alternativos/legados são recusados conservadoramente, sem reenvio; compatibilidade real desses aliases não foi verificada.
- [Publicação de Place](https://create.roblox.com/docs/cloud/guides/usage-place-publishing): o guia atual recomenda Assets API, PATCH multipart de `.rbxl`/`.rbxlx`, `published=true` explícito e consulta da operação assíncrona. Publicar altera o lugar e **não foi implementado/executado**.
- [MCP Studio](https://create.roblox.com/docs/studio/mcp) continua dependente de Studio desktop. Seu acesso a Open Cloud não elimina esse requisito para o MCP local. O cliente HTTPS desta entrega é uma capacidade separada, sem duplicar os sistemas fundamentais.

## Bloqueios e conexão de um lugar de teste pelo celular

**LIVE_BLOCKED:** não há alvo descartável nem API key autorizados configurados no ambiente. O comando `--live` foi executado e terminou antes de qualquer request, com exit 2 e `authorized_test_target_and_api_key_required`. Além disso, HTTPS CONNECT recebeu **403 do proxy** para `apis.roblox.com` e `create.roblox.com`. Não se tentou contornar a política ou alterar configuração de rede.

Para uma execução futura autorizada, sem Studio no dispositivo do usuário:

1. Usar **uma experiência/lugar de teste já existente, publicado, pertencente ao usuário e descartável**. Pelo Creator Dashboard no navegador, obter Universe ID, Place ID e, preferencialmente, uma versão conhecida. Não usar jogos de terceiros ou produção. Criar/publicar um novo lugar exige autorização e fica fora desta entrega.
2. Na UI de conexão Roblox existente, conectar a conta, registrar o Place e vinculá-lo ao Environment correto. OAuth de descoberta/seleção não foi ampliado silenciosamente para execução; o cliente usa API key específica de Luau Open Cloud.
3. No Creator Dashboard, configurar chave com acesso somente ao Universe de teste e às permissões Luau Session de leitura/escrita; não conceder publicação/DataStore/compras para este fluxo. Inserir a chave **nos segredos do ambiente**, nunca no chat, Git ou cookie. Nomes usados pela demonstração: `AMODEX_ROBLOX_OPEN_CLOUD_API_KEY`, `AMODEX_ROBLOX_TEST_UNIVERSE_ID`, `AMODEX_ROBLOX_TEST_PLACE_ID`; versão opcional `AMODEX_ROBLOX_TEST_VERSION_ID`.
4. Nas configurações de rede autorizadas do ambiente cloud, permitir `apis.roblox.com`; `create.roblox.com` permite consultar documentação/dashboard. Depois de configurar o alvo e autorizar especificamente esse teste puro, o Codex pode executar `.venv/bin/python scripts/roblox_luau_smoke.py --live`. O usuário não precisa instalar Windows/macOS para esse teste de lógica. Não se promete gratuidade/quota disponível de serviço externo sem conferência.

Um POST com resposta perdida pode ter criado a task. **Não repetir o comando para resolver UNKNOWN_OUTCOME.** Quando houver recibo, consultar o mesmo recurso; sem recibo, é necessária reconciliação/intervenção. O cliente não oferece cancelamento/retry nem persistência nova de intents. Não se deve enviar logs brutos de outros scripts ao relatório: podem conter dados privados.

## Web Play e próximo passo visual

Playwright iniciou **Chromium 156.0.8078.79**, viewport 414×896, em contexto novo, sem login/cookies persistentes. Navegação pública para `https://www.roblox.com/` falhou com **`net::ERR_TUNNEL_CONNECTION_FAILED`**; curl confirmou 403 do proxy. O site oficial de suporte `en.help.roblox.com` também foi recusado. Não foi fornecida sessão Roblox legitimamente autorizada nem experiência para playtest.

Consequência: documentação específica e disponibilidade atual do Web Play, elegibilidade da conta/região e compatibilidade Chromium/Linux **não confirmadas**. Não se descobriu endpoint de lançamento autorizado, não se entrou em jogo e não se construiu bot visual. Nenhuma screenshot de página de erro foi tomada como evidência de gameplay. Para nova avaliação, permitir os domínios oficiais necessários nas configurações e disponibilizar uma sessão legítima de uma experiência de teste autorizada; não fornecer cookies ao agente.

Interface mínima adicionada a `integrations/models.py`: `VisualTestAdapter.observe()` retorna fatos/referências de evidência; `press(control, duration_seconds)` envia input limitado; `wait(seconds)` espera; `verify(expected)` verifica observações. É somente um Protocol, sem implementação Roblox ou FSM. O futuro adaptador deve declarar capacidades de input/screenshot apenas depois de comprová-las.

Próximo marco pequeno: expor o binding Luau ao fluxo de tarefas existente; agente altera um arquivo autorizado via ScopedWorkspaceTools, analisa/executa Luau local e, com alvo autorizado, submete uma única task e apresenta resultado/logs verificados. O passo visual depende primeiro de Web Play acessível e sessão de teste legítima; deve anexar observações e asserções ao Evidence existente. Não implica novo Core/Runner/scheduler, publicação automática ou Recovery.

## Testes e evidências

**52 testes novos passaram; regressão pertinente: 328 aprovados, zero falhas/erros/skips/XFAIL.** Os 52 estão incluídos nos 328. Cobertura: POST único, polling/deadline, permissões, 429/5xx, timeout/conexão, recibo ambíguo, estados de erro/cancelamento, logs/paginação limitada, proteção de destino/redirect, seleção por Environment, grant de TaskPackage, leitura de arquivo e modo live bloqueado. Simulação não é teste remoto real.

```bash
.venv/bin/pytest tests/unit/test_roblox_cloud.py
AMODEX_TEST_DATABASE_URL=postgresql://amodex@127.0.0.1:55432/amodex_qa_task06 \
.venv/bin/pytest tests/unit tests/contract \
  tests/integration/test_stage2_scoped_tools.py \
  tests/integration/test_stage4_engine_integration.py \
  tests/integration/test_postbaseline_connectors.py \
  tests/integration/test_postbaseline_environment_product.py \
  tests/integration/test_postbaseline_environment_storage.py
/workspace/.setup/luau-0.746/luau-analyze tests/fixtures/roblox/score_test.luau
/workspace/.setup/luau-0.746/luau tests/fixtures/roblox/score_test.luau
```

Python 3.13.5, pytest 9.0.2, httpx 0.28.1 e PostgreSQL local 17.11 reutilizados. Apenas o executável oficial [Luau 0.746](https://github.com/luau-lang/luau/releases/tag/0.746) foi acrescentado ao ambiente de ferramentas, fora do Git; hashes registrados. Banco descartável preparado com as 13 migrations existentes, exclusivamente em loopback. Um warning preexistente de depreciação Starlette/httpx permanece. As falhas de Model Intelligence documentadas na R2A não foram reavaliadas: fora desta regressão pertinente.

[Evidências compactas, comandos/resultados, docs e hashes](evidence/task06/results.json); [JUnit dirigido](evidence/task06/roblox.xml). Brutos em `artifacts/qa/task06/`, ignorados pelo Git. Não houve inferência, tarefa Roblox remota, criação/publicação de experiência, leitura de cookies, alteração Render/Neon, merge ou deploy. Branches anteriores preservadas. R2B/R3/R4/Recovery não foram implementados.
