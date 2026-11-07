# AMODEX — Tarefa 02: correções UI/UX e timeout

Validação em **2026-11-07, 12:53–13:05, America/Recife (UTC−3)**.

**Resultado local: 245 testes distintos aprovados, zero falhas finais, zero XFAIL e zero skips.** Os quatro defeitos funcionais/visuais foram corrigidos; a divergência documental de health checks foi esclarecida. Render e WebKit continuam **BLOQUEADOS pela política de rede**. Esta entrega não aprova nem publica uma versão de produção.

## Referências e escopo

- Branch: `fix/ui-ux-timeout-regressions-v1`, criada após verificar que não existia localmente nem no GitHub.
- Base confirmada: `test/browser-mcp-ui-audit`, `b9ed9a66716e32edbd5938a4e614d8992c6984ea`.
- Código e testes finais exercitados: `2ac9fc7b3bb47bad2e80541c69ad5bbc18f67283`. O commit seguinte acrescenta somente este relatório e evidências. O SHA final da entrega é consultável com `git rev-parse fix/ui-ux-timeout-regressions-v1`.
- Contexto lido: `AGENTS.md`, auditoria anterior, implementações reais, testes e workflows existentes.
- Escrita: módulos web e timeout abaixo, testes diretamente relacionados e documentação/evidências. Sem framework novo, dependências adicionais, mudança de lockfile, migration nova, produção, branches protegidas, merge ou deploy.
- Banco local: PostgreSQL 17 em `127.0.0.1:55432`; `amodex_qa_task01` para browser/dados sintéticos e banco novo `amodex_qa_task02_contracts` para integração. O migrador existente foi executado somente com DSNs locais explícitos e guard `127`.
- Ambiente reutilizado: Python 3.13.5, pytest 9.0.2, Playwright 1.64.1, pytest-playwright 0.10.0, Chromium do sistema 156.0.8078.79, Playwright MCP 0.0.84. Nenhum pacote adicional foi necessário nesta tarefa.

## A. Correções

| Achado | Causa confirmada e arquivos | Solução | Validação |
|---|---|---|---|
| QA-01: Contexto cortado | A topbar mantinha grupos flex numa única linha em mobile; título, badges e botão excediam a largura. `src/amodex/api/workspace_v2.py` | Em até 700 px, grupos distribuídos em linhas, encolhimento do título permitido e botões preservados com altura mínima de 40 px. As regras desktop permanecem iguais | Os dois testes de clipping passaram com `--runxfail`, depois sem marcação; navegação e medidas em quatro viewports |
| QA-02: modal inacessível | `openModal`/`closeModal` apenas alternavam classe; forms sem semântica de diálogo/labels associados. `src/amodex/api/workspace_web.py` | Os mesmos helpers agora gerenciam foco, Escape, Tab/Shift+Tab, restauração e `inert` no fundo, preservando estados anteriores e diálogos sobrepostos. Cinco forms têm `role="dialog"`, `aria-modal="true"`, título por `aria-labelledby` e labels associados | Dois testes originais passaram com `--runxfail`, depois sem marcação; dez casos nos cinco diálogos/desktop/mobile, caso de sobreposição e dois casos de foco tardio |
| QA-03: cabeçalho Model Intelligence | Flex sem quebra no cabeçalho a 390 px. `src/amodex/api/model_intelligence_web.py` | Permitir quebra no cabeçalho até 600 px, mantendo título, status, ações e área dos botões. Sem esconder overflow do documento | Teste original passou com `--runxfail`; ampliado para os quatro tamanhos |
| QA-04: timeout de modelo | ID completo normalizado incluía `MISTRALAI_`, mas alias documentado/testado não incluía. `src/amodex/providers/timeouts.py` | ID completo canônico, alias histórico explícito limitado ao par NVIDIA/modelo, precedência por especificidade e validação de números finitos | Suíte inteira de timeout: **38 aprovados**, inclusive o teste originalmente falho |
| QA-05: health checks | Checkpoint histórico citava `/readyz`; configuração e teste atuais exigem `/healthz` | Nota histórica em `docs/checkpoints/P0_ACTIVATION_V1.md` e contrato atual em `docs/DEPLOY_RENDER_NEON_CLOUDINARY_V1.md`; sem mudar `render.yaml` | Dez testes de `test_activation_status.py` aprovados na verificação dirigida e novamente na regressão ampla |

### Corrida de foco descoberta durante a correção

A baseline completa revelou um timer antigo de 20 ms em `workspace_v2.py`: depois do foco síncrono do helper, ele podia redirecionar o foco do segundo campo de volta ao nome. Uma tentativa produziu o título sintético concatenado `E2E-Projeto-…Baseline Playwright`. A execução inicial terminou em **1 falha, 32 aprovados, 1 desmarcado**.

Os timers de projeto e chat foram removidos: o helper central é a única autoridade de foco. Foram acrescentadas duas verificações que mantêm foco no segundo campo após a janela do timer legado. A baseline original e os novos casos passaram na execução final. Não houve supressão da asserção que encontrou a regressão.

### Mock preexistente incompatível

A regressão ampla também encontrou `test_chat_prefers_primary_model_even_when_agent_scores_higher` com um `client_for=lambda candidate: ...` que não aceitava `operation="chat"`, já enviado pelo serviço na base da auditoria.

Isso foi reproduzido executando o teste e o código originais exportados de `b9ed9a6` para `/tmp`, sem checkout/reset de branch protegida: **1 falha**, mesmo `TypeError`. Foi corrigido somente o double em `tests/unit/test_model_route_hardening.py`, exigindo o keyword `operation` e afirmando seu valor `chat`. Todas as asserções anteriores sobre rota primária, alternativa e ausência de replay foram preservadas. Nenhuma lógica de seleção de modelo mudou.

## B. UI/UX e evidências visuais

Navegação real pelo Playwright e inspeção das imagens confirmaram os resultados abaixo. A largura medida é `document.documentElement.scrollWidth`, não apenas um status HTTP.

| Viewport Chromium | Home e reload | Projetos/modal | Modelos | Evidências |
|---|---|---|---|---|
| 1440×900 | 1440 px; desktop preservado | 1440 px; foco inicial e Escape corretos | 1440 px | [Home](evidence/task02/1440x900-home.png), [Modelos](evidence/task02/1440x900-models.png) |
| 768×1024 | 768 px; botão Contexto funciona | 768 px; diálogo correto | 768 px | [Home](evidence/task02/768x1024-home.png), [Modelos](evidence/task02/768x1024-models.png) |
| 414×896 | 414 px; menu e Contexto funcionam | 414 px; diálogo correto | 414 px | [Home](evidence/task02/414x896-home.png), [Modelos](evidence/task02/414x896-models.png) |
| 390×844 | 390 px; menu e Contexto funcionam | 390 px; diálogo correto | 390 px | [Home](evidence/task02/390x844-home.png), [Modelos](evidence/task02/390x844-models.png) |

Comparações com evidências reais da Tarefa 01:

| Estado | Antes | Depois |
|---|---|---|
| Home mobile: Contexto | [Cortado](evidence/task01/mobile-home.png) | [Controles inteiros, em linhas](evidence/task02/390x844-home.png) |
| Modelos mobile | [Cabeçalho cortado](evidence/task01/models-mobile.png) | [Título, badge e ação visíveis](evidence/task02/390x844-models.png) |
| Modal de projeto | [Modal não fechava por Escape](evidence/task01/modal-after-escape.png) | [Diálogo aberto com foco correto](evidence/task02/390x844-project-modal.png); fechamento e retorno de foco demonstrados pelos testes, não apenas pela imagem |

Os cinco diálogos inspecionados foram projeto, chat, memória, execution draft e vínculo de ambiente. Nos testes dos helpers compartilhados, abrir os três últimos não gravou dados nem disparou execução. Houve verificação de foco inicial, retorno ao botão de abertura, ciclo de Tab/Shift+Tab, tentativa de foco no fundo, Escape e Cancelar. O estado `inert` preexistente é preservado, inclusive ao abrir um diálogo sobre outro.

As criações reais de projeto/chat, mensagem owner, duplo envio, upload, cache ETag e restauração após reload continuam cobertas pela baseline existente. Persistir a mensagem owner não é prova de inferência de IA: nenhum modelo real foi chamado.

### Console e rede

- Zero `pageerror` nos quatro percursos monitorados.
- Primeiro GET sem sessão: 401 esperado pelo gate.
- `/v1/model-intelligence/leaderboard`: 503 com código **`NO_MEASURED_ROUTE`**, conferido na resposta e em `ModelIntelligenceService.leaderboard`. É o gate esperado no ambiente sem rota elegível, exibido na página; não foi tratado como sucesso de inferência.
- Warning de Service Worker sob HTTPS autoassinado e favicon 404 continuam limitações da fixture. O cenário offline passou no localhost HTTP confiável, conforme o workflow existente.
- Foi observado `net::ERR_ABORTED` de bootstrap ao navegar/recarregar deliberadamente; registrado nas evidências. Não foi chamado de ausência absoluta de falhas de transporte.
- A primeira versão do coletor visual classificava todo 503 como inesperado e interrompeu no gate acima. A classificação foi refinada apenas após conferir endpoint e código; os 503 continuam registrados. Outros 4xx/5xx inesperados não foram observados nos percursos finais.

O MCP oficial também foi exercitado novamente pelo cliente MCP stdio: navegou no servidor local HTTP, abriu Projetos e o modal, retornou semântica `dialog`, `aria-modal=true`, foco `newWorkspaceName`, screenshot e registros de console/rede. Sem Render MCP ou DevTools MCP separado disponível; inspeção DOM/console/rede feita por Playwright.

Métricas, hashes SHA-256 dos quatro módulos exercitados e eventos sanitizados: [visual.json](evidence/task02/visual.json). Verificação MCP: [mcp.json](evidence/task02/mcp.json).

## C. Política final de timeout

O resolver continua centralizado e preserva esta precedência por componente:

1. Provider + model + operation.
2. Provider + model.
3. Provider + operation.
4. Provider.
5. Global.

Em cada nível com modelo, o ID completo tem prioridade sobre o alias quando ambos contêm valores válidos. Um alias com operação ainda supera um ID completo sem operação, pois a especificidade é superior.

Canônico: `mistralai/mistral-large-4-1t-a52b` → `MISTRALAI_MISTRAL_LARGE_4_1T_A52B`. O alias anterior `MISTRAL_LARGE_4_1T_A52B` é compatível **somente** com esse ID no provider NVIDIA. Não há fallback genérico por basename: outro publisher, sufixo de versão, `:free` ou outro provider não herda esse alias. Modelos sem namespace usam o próprio ID completo.

Valores não numéricos, não finitos ou não positivos são ignorados; positivos finitos são limitados a **1–1800 s** em connect/read/write/pool. Defaults permanecem iguais. Nenhum retry, replay, mudança de elegibilidade, custo ou variável Render foi introduzido.

A normalização legada de ambiente continua convertendo pontuação/case em underscores/maiúsculas. Ela não é um identificador universal sem colisões: `author/model-a` e `author/model_a` colapsam. Isso está explicitamente documentado: não se deve registrar tais IDs distintos sob a mesma grafia nem criar aliases genéricos. Para casos assim, usar orçamento por provider/global até uma convenção distinta ser aprovada. A compatibilidade implementada é uma allowlist de identidade exata; não tenta inferir namespace. Casos de namespaces diferentes com basename igual estão testados e separados.

Testes cobrem IDs completos e sem namespace, prioridade canônico/alias, todos os níveis/componentes, colisões de basename entre publishers, alias não vazando para modelos semelhantes, entradas inválidas, mínimo/máximo e defaults. Detalhes em [PROVIDER_TIMEOUT_POLICY.md](../PROVIDER_TIMEOUT_POLICY.md).

## D. Execuções e resultados

Os testes usam as fixtures, banco local e dependências já existentes. `AMODEX_E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium` reutiliza o navegador instalado. A guarda da Tarefa 01 continua recusando E2E mutável fora de loopback.

| Comando/suíte | Ambiente | Aprovados | Falhas | XFAIL | Skips |
|---|---|---:|---:|---:|---:|
| `pytest tests/unit tests/contract` + quatro integrações listadas abaixo | Banco local novo e migrado | 209 | 0 | 0 | 0 |
| `pytest tests/e2e -k 'not cached_chat_survives_offline_reload' --browser chromium` | HTTPS local com gate sintético | 35 | 0 | 0 | 0 |
| `pytest tests/e2e/test_workspace_baseline.py -k cached_chat_survives_offline_reload --browser chromium` | HTTP localhost, gate desativado no servidor isolado | 1 | 0 | 0 | 0 |
| **Total distinto final** | Local | **245** | **0** | **0** | **0** |
| `pytest tests/unit/test_provider_timeouts.py` | Unitário, já incluído nos 209 | 38 | 0 | 0 | 0 |

O caso offline foi **desmarcado**, não skipped, na execução HTTPS; os oito demais casos da baseline foram desmarcados na execução offline separada. Todos os nove casos da baseline foram executados. Não somar as execuções dirigidas/repetidas ao total distinto.

Sequência de XFAIL: layout **3/3 passaram com `--runxfail`**, marcações removidas, **3/3 passaram normalmente**; modais **2/2 passaram com `--runxfail`**, marcações removidas, testes originais e cobertura adicional passaram. Nenhuma asserção existente foi enfraquecida e nenhum cenário foi convertido em skip.

Comandos finais, executados em `/workspace/Amodex`:

```bash
AMODEX_TEST_DATABASE_URL=postgresql://amodex@127.0.0.1:55432/amodex_qa_task02_contracts \
.venv/bin/pytest tests/unit tests/contract \
  tests/integration/test_activation_readiness.py \
  tests/integration/test_workspace_ui_snapshots.py \
  tests/integration/test_postbaseline_web_workspace.py \
  tests/integration/test_stage6_product_surfaces.py \
  --junitxml=artifacts/qa/task02/regression.xml -ra

AMODEX_E2E_BASE_URL=https://localhost:8765 \
AMODEX_E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
AMODEX_QA_ARTIFACT_DIR=artifacts/qa/task02/audit \
.venv/bin/pytest tests/e2e -k 'not cached_chat_survives_offline_reload' \
  --browser chromium --junitxml=artifacts/qa/task02/browser.xml -ra

AMODEX_E2E_BASE_URL=http://localhost:8766 \
AMODEX_E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
.venv/bin/pytest tests/e2e/test_workspace_baseline.py \
  -k cached_chat_survives_offline_reload --browser chromium \
  --junitxml=artifacts/qa/task02/offline.xml -ra

.venv/bin/pytest tests/unit/test_provider_timeouts.py \
  --junitxml=artifacts/qa/task02/timeouts.xml -ra
.venv/bin/python -m compileall -q src/amodex/api tests/e2e src/amodex/providers/timeouts.py
git diff --check
```

Um warning de depreciação Starlette/httpx permanece; não é falha de teste. A execução ampla inicial terminou em **208 aprovados e 1 falha** por mock preexistente; o rerun após corrigir o double terminou em 209 aprovados. A corrida de foco e a classificação inicial do coletor visual foram investigadas e resolvidas conforme descrito acima, sem ocultar suas tentativas.

Resultados verificáveis: [results.json](evidence/task02/results.json), [regression.xml](evidence/task02/regression.xml), [browser.xml](evidence/task02/browser.xml), [offline.xml](evidence/task02/offline.xml), [timeouts.xml](evidence/task02/timeouts.xml). Logs, provas dirigidas e capturas completas permanecem em `artifacts/qa/task02/` ignorado pelo Git. A seleção versionada contém somente dados sintéticos e metadados, sem cookies, tokens, headers de autenticação ou estado de sessão.

## E. Produção e WebKit

Refs remotas confirmadas na abertura:

| Branch protegida | SHA observado |
|---|---|
| `main` | `fe0dba0bbdc333a8642f78b9deda04225623f3da` |
| `deploy/render-neon-cloudinary-v1` | `9ef70070ddf4b825276160fedcba53f9822fdd9c` |
| `fix/p0-ci-health-readiness` | `065f24818fbe82c14f2df880c9d3dfdc305effec` |
| `feat/durable-runner-leases` | `6eefd66fe00fae54183b71874f9a2bde63737bde` |
| `test/browser-mcp-ui-audit` | `b9ed9a66716e32edbd5938a4e614d8992c6984ea` |

Novas consultas a `amodex.onrender.com`, `cdn.playwright.dev` e `playwright.download.prss.microsoft.com` continuam recebendo **403 do proxy**. O probe público usou apenas GET em `/`, `/healthz` e `/readyz`, além de tentativa de navegação sem credenciais; todos bloqueados antes de alcançar a aplicação. Não há evidência de cold start ou falha do Render. Registro: [production.json](evidence/task02/production.json).

WebKit não foi executado: os domínios de download continuam negados. Chromium gerenciado tampouco foi reinstalado; o binário do sistema permaneceu disponível. Nenhum controle de rede foi contornado.

O rascunho de configuração, revisão 4, já contém exatamente os três domínios acima como adições customizadas, preservando presets. O runtime ainda reporta lista customizada vazia. **Ação necessária do usuário:** abrir as configurações deste ambiente Codex, salvar/aplicar as permissões de saída para esses três domínios e aguardar propagação. Depois, repetir as consultas com conexão nova. Não é uma mudança de credenciais/variáveis Render. Não há ferramenta de aplicação direta da política exposta nesta execução.

**Commit efetivamente implantado: não confirmado.** O SHA `9ef7007…` identifica a branch de deploy observada, não prova o deploy ativo. A readiness local declara `2ac9fc7…`, banco/schema prontos e custo zero fail-closed ([local-readiness.json](evidence/task02/local-readiness.json)); isso vale somente para o ambiente isolado corrigido. Nenhum resultado local foi atribuído à produção.

## Arquivos e commits

Arquivos funcionais alterados: `src/amodex/api/workspace_v2.py`, `src/amodex/api/workspace_web.py`, `src/amodex/api/model_intelligence_web.py`, `src/amodex/providers/timeouts.py`.

Testes: `tests/e2e/test_browser_ui_audit.py`, `tests/unit/test_provider_timeouts.py`, `tests/unit/test_model_route_hardening.py` (somente mock incompatível com o contrato já existente).

Documentação: `docs/PROVIDER_TIMEOUT_POLICY.md`, `docs/DEPLOY_RENDER_NEON_CLOUDINARY_V1.md`, `docs/checkpoints/P0_ACTIVATION_V1.md`, este relatório e `docs/audits/evidence/task02/`. O relatório da auditoria anterior e suas evidências foram preservados.

Commits de implementação/validação: `6165e77` (layout), `a1784b8` (modais), `5b1c976` (timeout), `cd43011` (health docs), `61c4b12` (cobertura e autoridade única de foco) e `2ac9fc7` (double do registry). A divisão foi ajustada para registrar separadamente os achados da revalidação.

## F. Próximos passos

1. Liberar os três domínios no ambiente, executar WebKit e validar a versão pública por `/readyz`/SHA. Essa validação externa permanece aberta.
2. Revisar esta branch; qualquer merge ou deploy requer tarefa própria. As correções não foram publicadas no Render.
3. A base local está adequada para **iniciar a auditoria isolada de Checkpoint + Recovery**, reaproveitando banco, simuladores e navegador existentes. Isso não significa que Checkpoint + Recovery já foi validado ou implementado nesta tarefa.
4. Na próxima auditoria, definir evidências de checkpoint durável, recuperação após crash, fencing/leases, idempotência e resultado ambíguo de provider. Não usar inferência paga nem a produção como bancada de falhas.

Não restam falhas ou XFAIL nas suítes executadas. Compatibilidade Apple, configuração aplicada no Render, autenticação privada remota e inferência real continuam não verificadas. A convenção legada de normalização de IDs de timeout conserva a limitação de pontuação explicitada na política; novos aliases ambíguos não devem ser inferidos automaticamente.
