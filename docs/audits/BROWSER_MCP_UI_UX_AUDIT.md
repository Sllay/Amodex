# AMODEX — Tarefa 01: navegador, UI/UX e Render

Auditoria em **2026-11-07, aproximadamente 12:26–12:36 (America/Recife, UTC−3)**.

**Resultado: ambiente local verificado com ressalvas; produção BLOQUEADA por política de rede. A aplicação não está aprovada por esta auditoria.** Foram confirmados três problemas de UI e uma inconsistência funcional de timeout. Não houve merge, PR, deploy, alteração no Render, migration remota, inferência paga ou teste destrutivo de produção.

## Escopo e revisão testada

- Branch criada: `test/browser-mcp-ui-audit`; não existia localmente nem no remoto na descoberta.
- Base exata exercitada: `6eefd66fe00fae54183b71874f9a2bde63737bde` (`feat/durable-runner-leases`). O commit de entrega acrescenta somente testes e este relatório/evidências, sem mudar a aplicação. Seu SHA é recuperável com `git rev-parse test/browser-mcp-ui-audit`.
- Escrita autorizada nesta tarefa: `tests/e2e/`, `docs/audits/` e artefatos ignorados em `artifacts/qa/`. Instalações e helpers locais em `/workspace/.setup`, fora do checkout.
- Protegidos: branches originais, código de produção, migrations, manifestos e lockfiles. Nenhum deles foi alterado.
- Critérios: executar a infraestrutura existente, reproduzir falhas com evidências, manter testes locais separados de consultas públicas, documentar limitações e entregar a branch sem promoção.

## A. Ambiente e ferramentas

| Recurso | Encontrado/utilizado | Evidência ou limite |
|---|---|---|
| Terminal, Git e proxy GitHub HTTPS | Sim | Consulta de refs, fetch e branch isolada; sem reescrever origens |
| GitHub CLI | Instalado | Operações necessárias feitas por Git; ausência de token explícito não foi interpretada como falta de acesso |
| Python / uv | Python 3.13.5, uv 0.13.5 | `.venv`, `uv sync --frozen --extra dev`; lock intacto |
| Node / npm | 24.22.1 / 11.9.0 | Instalação local do MCP, sem dependência Node adicionada ao produto |
| Playwright Python / pytest-playwright | 1.64.1 / 0.10.0 | Mesmos pins dos workflows existentes; pytest 9.0.2 |
| Chromium | Sistema: 156.0.8078.79, Debian 13 | Navegação real, quatro viewports; binário escolhido explicitamente |
| Chromium gerenciado / WebKit 27.2 (v2370) | Download tentado e bloqueado | HTTP 403 `Domain forbidden` nos CDNs oficiais; WebKit não executado |
| Playwright MCP oficial | 0.0.84, instalado e registrado | Antes: `codex mcp list` vazio; depois: `playwright` habilitado |
| MCP em execução | Sim | Cliente MCP Python já disponível, transporte stdio, handshake e chamadas reais ao servidor oficial; não apenas registro no CLI |
| Browser automation / DevTools | Playwright + eventos de console/rede/DOM | Sem MCP de DevTools separado; não utilizado perfil de navegador pessoal |
| Render MCP | Indisponível | Nenhuma ferramenta Render exposta; não foram consultados logs, métricas ou API administrativa |
| PostgreSQL | 17.11 local, porta 55432 | Banco exclusivo `amodex_qa_task01`; migrations existentes aplicadas somente nesse banco |

A documentação oficial atual de `microsoft/playwright-mcp` foi consultada em `https://github.com/microsoft/playwright-mcp` antes da instalação. Pacote instalado com versão exata em `/workspace/.setup/browser-mcp`. Registro local usa `--headless --isolated --executable-path /usr/bin/chromium`. Os testes pytest reutilizam a biblioteca fixada pelo projeto; a dependência interna alpha do MCP é separada dessa camada.

### Verificação efetiva do MCP

Página sintética local em `http://127.0.0.1:8770/`, sem dados do AMODEX:

1. Browser iniciado e URL navegada por `browser_navigate`.
2. Elementos inspecionados por `browser_snapshot`.
3. `browser_click` em `#safe`; `browser_evaluate` confirmou texto `Clicked`.
4. `browser_take_screenshot` produziu imagem.
5. `browser_console_messages` capturou `QA synthetic console event`.
6. `browser_network_requests` distinguiu HTTP 404 de falha de transporte `net::ERR_UNSAFE_PORT`, ambos induzidos pela fixture.

Todas essas chamadas completaram sem `isError`. Essas falhas sintéticas não são bugs do produto. Resultado resumido em [evidence/task01/mcp-capabilities.json](evidence/task01/mcp-capabilities.json).

## B. GitHub, versão e Render

Refs consultadas antes do trabalho e novamente após os testes:

| Branch | HEAD remoto observado |
|---|---|
| `main` | `fe0dba0bbdc333a8642f78b9deda04225623f3da` |
| `feat/durable-runner-leases` | `6eefd66fe00fae54183b71874f9a2bde63737bde` |
| `deploy/render-neon-cloudinary-v1` | `9ef70070ddf4b825276160fedcba53f9822fdd9c` |
| `fix/p0-ci-health-readiness` | `065f24818fbe82c14f2df880c9d3dfdc305effec` |

O HEAD da branch de deploy é ancestral da base testada. Isso **não comprova o commit implantado**. No código, a composição web de ambas as branches declara `0.7.6-activation-v1`. A base durável acrescenta, entre outras mudanças, leases/heartbeat e bloqueadores de runner expirado e política central de timeout; esses acréscimos não podem ser atribuídos à produção conhecida.

| Consulta pública | Resultado |
|---|---|
| `https://amodex.onrender.com/` | BLOQUEADO: túnel proxy HTTP 403, antes de alcançar o serviço |
| `/healthz` | BLOQUEADO: mesmo erro, sem status HTTP da aplicação |
| `/readyz` | BLOQUEADO: mesmo erro, sem status HTTP da aplicação |
| Navegador com proxy herdado | `net::ERR_TUNNEL_CONNECTION_FAILED` |
| SHA efetivamente implantado / deploy ativo / logs / métricas | NÃO VERIFICADOS |

Evidência: [production-access.json](evidence/task01/production-access.json). As negativas ocorreram em milissegundos: não há evidência de cold start, indisponibilidade ou lentidão do Render. Não houve repetição agressiva nem tentativa de contornar o proxy. O serviço `srv-dbjtv58ae00c73f9aagg` é referência fornecida pelo solicitante, não identidade reconfirmada por API.

`/healthz` é liveness sem dependências. `/readyz` verifica banco, schema/checksums e política de custo zero. No servidor local preparado, ambos responderam corretamente e readiness confirmou o SHA da base. Isso verifica apenas a instância local.

O `render.yaml` atual e seu teste unitário exigem `/healthz` como health check. `docs/checkpoints/P0_ACTIVATION_V1.md` ainda descreve `/readyz`; essa divergência documental não demonstra qual configuração está aplicada no serviço. Nenhuma configuração Render foi alterada.

### Ação externa pendente

Foi salvo no rascunho do ambiente acesso a `amodex.onrender.com`, `cdn.playwright.dev` e `playwright.download.prss.microsoft.com`, preservando os presets existentes. O status de execução ainda informa allowlist customizada vazia. Salvar/aplicar essa alteração nas configurações é necessário para retomar a inspeção pública e o download de WebKit. Não são variáveis nem credenciais do Render. Acesso ao login privado, se necessário depois, deve ser fornecido por configuração segura, nunca pelo relatório/chat.

## C. Resultados funcionais

| Fluxo | Código existente | Teste executado / resultado | Verificado em produção? |
|---|---|---|---|
| Liveness/readiness | Rotas separadas e diagnóstico de release | PASSOU local: banco/schema/custo zero prontos | BLOQUEADO |
| Login | Gate server-side, cookie assinado | PASSOU: login real local, erro de código sintético incorreto, teclado Enter, gate após remoção de sessão | NÃO |
| Proteção de sessão | Assinatura, revogação e rate limiting | PASSOU nos testes unitários existentes de cookie adulterado, mudança de código e limite de tentativas | NÃO |
| Expiração temporal de sessão | TTL no gate | NÃO TESTADO como passagem real do tempo; remoção de cookie não equivale a expiração | NÃO |
| Home/projetos/menu/modal | UI existente | PASSOU navegação em 1440×900, 414×896, 390×844 e 768×1024; FALHOU acessibilidade do modal e clipping mobile | NÃO |
| Projeto + chat + envio + reload | Persistência e frontend existentes | PASSOU baseline: uma mensagem owner persistida, sem duplicação no duplo clique; restauração após reload | NÃO |
| Inferência / token streaming / modelo real | Sistema de providers presente, gate de benchmark | NÃO TESTADO; nenhum provider/credencial de produção usado. Mensagem owner persistida não comprova resposta de IA | NÃO |
| Cache ETag e chat offline | Cache privado/Service Worker existentes | PASSOU: revalidação 304 e recuperação offline em localhost HTTP confiável | NÃO |
| Upload | Pipeline existente | PASSOU local: arquivo sintético permitido >2 MiB chega ao pipeline; sem Cloudinary remoto | NÃO |
| STT indisponível | Feedback existente | PASSOU: feedback explícito com STT desativado; transcrição real NÃO TESTADA | NÃO |
| Conexões e modelos | Páginas/estados existentes | PASSOU carregamento/estados sem configuração; FALHOU layout de modelos em 390 px. OAuth e benchmarks NÃO TESTADOS | NÃO |
| Environments | Controles de vinculação presentes | Visualizados; seleção com ambiente real NÃO TESTADA | NÃO |
| Runs/Runner Jobs/Director/Worker/Validation/Checkpoints | APIs, storage, runners e projeções presentes | Contratos de heartbeat PASSARAM; runner remoto, execução completa, logs de tarefa e recovery NÃO TESTADOS | NÃO |
| Timeout por modelo | Resolver e teste existentes | FALHOU contrato de override, sem chamada externa | NÃO |

Console observado nas capturas locais: nenhuma exceção JavaScript (`pageerror`) nos percursos explorados. HTTP 401 no primeiro acesso é esperado pelo gate. Houve erro/warning de Service Worker com certificado local autoassinado e 404 de favicon; não foram classificados como falha de produção. Não houve evidência de CORS nem streaming real; sua ausência nas capturas não prova esses fluxos.

## D. Problemas confirmados

Nenhum P0/P1 demonstrado. As gravidades abaixo representam impacto observado localmente, sem atribuir ocorrência em produção.

### QA-01 — P2: botão Contexto fora da viewport mobile

- Tela: `/`, logado, 414×896 ou 390×844.
- Reprodução: abrir home em um desses tamanhos; observar botão `Contexto` no topo.
- Esperado: controle inteiramente visível e acionável sem rolagem horizontal.
- Observado: em 414 px o botão termina em aproximadamente x=463; em 390 px termina em x=452. O retângulo excede a viewport; o screenshot de viewport confirma o corte.
- Evidências: [mobile-home.png](evidence/task01/mobile-home.png), métricas em [summary.json](evidence/task01/summary.json), testes `test_context_control_fits_mobile_viewport` (dois casos).
- Suspeita técnica: soma das larguras do título/badges e botão no flex da topbar; não foi isolada uma regra CSS única como causa.
- Correção sugerida: ajustar constraints da topbar existente, permitir encolhimento/quebra seletiva e validar os quatro tamanhos.

### QA-02 — P2: modal de projeto não oferece operação acessível por teclado

- Tela: Projetos → `+ Projeto`, confirmado nos quatro tamanhos.
- Reprodução: abrir modal, pressionar Escape; percorrer controles com Tab.
- Esperado: diálogo nomeado, foco inicial dentro dele, foco contido enquanto aberto e Escape fecha/restaura foco.
- Observado: Escape mantém modal aberto; `role`, `aria-modal` e `aria-labelledby` ausentes; foco permanece inicialmente fora do modal e percorre elementos de fundo. O botão Cancelar funciona.
- Evidências: [modal-after-escape.png](evidence/task01/modal-after-escape.png), métricas em `summary.json`; testes `test_project_modal_closes_with_escape` e `test_project_modal_has_dialog_semantics_and_focus`.
- Causa confirmada no código: `openModal`/`closeModal` de `workspace_web.py` apenas alternam classe; o markup não declara diálogo. Não houve implementação de correção nesta tarefa.
- Correção sugerida: centralizar foco/semântica/Escape nos helpers existentes, associar labels aos campos e evitar duplicar o runtime frontend.

### QA-03 — P2: cabeçalho Model Intelligence extrapola 390 px

- Tela: `/models`, 390×844.
- Reprodução: abrir Modelos com sessão local; observar cabeçalho e largura do documento.
- Esperado: cabeçalho e controles cabem na viewport.
- Observado: documento tem 408 px para viewport de 390 px; ação à direita fica cortada. Nos outros três tamanhos medidos a largura não extrapolou.
- Evidências: [models-mobile.png](evidence/task01/models-mobile.png); teste `test_model_header_fits_small_mobile`.
- Suspeita técnica: layout do cabeçalho sem quebra suficiente para título, status e ação. Não foi isolada a declaração responsável.
- Correção sugerida: adaptar o cabeçalho existente para quebra/empilhamento em telas estreitas.

### QA-04 — P2: override documentado de timeout por modelo é ignorado

- Código: `src/amodex/providers/timeouts.py`; contrato/documentação em `tests/unit/test_provider_timeouts.py` e `docs/PROVIDER_TIMEOUT_POLICY.md`.
- Reprodução: `.venv/bin/pytest tests/unit/test_provider_timeouts.py::test_model_specific_override_wins_over_provider_default -q`.
- Esperado pelo teste: override específico de modelo de 1111 s prevalece sobre provider de 777 s.
- Observado: `read_seconds == 777.0`; falha reproduzida isoladamente.
- Causa confirmada: `_env_key` normaliza o ID completo para `MISTRALAI_MISTRAL_LARGE_4_1T_A52B`; teste e exemplo documentado usam `MISTRAL_LARGE_4_1T_A52B`. Há inconsistência entre contrato público e resolver, não dependência ausente.
- Correção sugerida: decidir a forma canônica/compatível, alinhar resolver, exemplos e testes; manter limites de transporte e proibição de replay automático. Não alterar variáveis Render nesta tarefa.

### QA-05 — P3: checkpoint de ativação descreve health check desatualizado

- Evidência estática: `docs/checkpoints/P0_ACTIVATION_V1.md` descreve `healthCheckPath: /readyz`; `render.yaml` e `tests/unit/test_activation_status.py` exigem `/healthz` na base e na branch de deploy consultadas.
- Esperado: documentação operacional distingue a configuração atual do checkpoint histórico e explica como readiness é avaliada.
- Observado: leitura isolada do documento pode induzir a considerar readiness como health check configurado.
- Correção sugerida: acrescentar nota de supersessão/referência atual sem apagar a evidência histórica. Não trocar o health check de produção como consequência desta auditoria.

## E. Melhorias de UI/UX fundamentadas

1. Tornar `Contexto` totalmente acessível em mobile antes de adicionar novos controles ao topo (QA-01).
2. Aplicar contrato de diálogo/foco aos helpers compartilhados, começando por projeto e estendendo a outros modais somente com testes (QA-02).
3. Reorganizar o cabeçalho Model Intelligence em 390 px preservando status e ações (QA-03).
4. Manter estados `AI gated`, STT indisponível e conexões sem configuração explícitos: foram observados, mas não significam integração pronta.

## F. Cobertura e evidências

Infraestrutura reutilizada: `tests/e2e/conftest.py`, nove testes em `test_workspace_baseline.py`, workflows `p0-web-e2e-baseline`, `p0-cache-local-state`, `p0-performance-structural` e `p0-activation-v1`, scripts `smoke_remote.py`, `migrate_production.py` e `activation_persistence_probe.py` (este último analisado como infraestrutura existente, não executado nesta auditoria). Não havia baseline visual de screenshots nessa suíte; não foram inventados snapshots de referência.

| Execução | Resultado real |
|---|---|
| Baseline HTTPS protegida, excluindo somente offline conforme workflow existente | **8 PASSARAM**, 1 desmarcado |
| Caso offline separado em `http://localhost:8766`, gate desativado no servidor isolado | **1 PASSOU**, 8 desmarcados |
| Contratos selecionados + readiness com banco corretamente preparado | **50 PASSARAM, 1 FALHOU** (QA-04) |
| Novos testes de auditoria | **6 PASSARAM, 5 XFAIL strict** (QA-01/02/03) |
| Novas regressões com `--runxfail` | **5 FALHARAM**, 6 desmarcados; confirma defeitos sem tratar XFAIL como aprovação |
| MCP sintético real | Capacidades de navegador/click/screenshot/console/rede confirmadas |
| Guarda de alvo com `https://qa-safety.invalid` | Recusa esperada em setup (1 erro deliberado em 0,03 s), antes de executar o teste mutável |
| WebKit / produção | BLOQUEADOS; nenhum teste aprovado nessas categorias |

Tentativas iniciais, mantidas nos artefatos: readiness falhou usando o banco antigo `amodex_test` sem o conjunto atual de migrations/checksums; passou no banco exclusivo preparado pelo migrador existente. O caso offline sob HTTPS autoassinado ficou aguardando Service Worker, foi interrompido após aproximadamente 72 s (**7 concluídos, execução incompleta**). Separado para localhost HTTP confiável conforme workflow de cache, passou. As demais execuções completas acima substituem essas tentativas; elas não foram omitidas nem somadas como testes adicionais únicos.

Adições: guarda que recusa E2E mutável fora de loopback, opção de reutilizar Chromium instalado, quatro casos de navegação em viewport, login inválido/teclado, sessão removida e cinco reproduções de defeitos. `XFAIL strict` é dívida conhecida identificada, não baseline aprovada: uma correção inesperada produz XPASS/falha para exigir revisão da marca. Nenhuma asserção dos testes existentes foi desativada.

Artefatos temporários em `artifacts/qa/task01/`: XML JUnit, logs, screenshots, console/rede, JSON de exploração e medidas. Sem HAR, cookies, storage state, headers de autenticação ou corpos privados. Screenshots contêm apenas dados locais sintéticos. Uma seleção pequena foi preservada em `docs/audits/evidence/task01/`; arquivos grandes permanecem ignorados pelo Git.

### Reexecução

Requer PostgreSQL **isolado**, Python 3.13.5 e ambiente virtual do projeto. Os workflows citados já demonstram criação do banco e startup, inclusive as duas origens para gate HTTPS e cache offline. Nunca usar DSN de produção.

```bash
uv sync --python 3.13.5 --extra dev --frozen
uv pip install --python .venv/bin/python playwright==1.64.1 pytest-playwright==0.10.0
# Se a rede permitir os CDNs oficiais:
.venv/bin/python -m playwright install chromium webkit
```

Nesta instância os servidores isolados já estão em `https://localhost:8765` (gate sintético) e `http://localhost:8766` (offline). Certificado autoassinado e signing secret são descartáveis; o secret foi gerado em memória e não está salvo neste relatório.

```bash
export AMODEX_E2E_BASE_URL=https://localhost:8765
export AMODEX_E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium
.venv/bin/pytest tests/e2e/test_workspace_baseline.py --browser chromium \
  -k 'not cached_chat_survives_offline_reload' -ra
.venv/bin/pytest tests/e2e/test_browser_ui_audit.py --browser chromium -ra
# Para exibir as cinco asserções ainda quebradas:
.venv/bin/pytest tests/e2e/test_browser_ui_audit.py --browser chromium --runxfail \
  -k 'context_control or modal_closes or dialog_semantics or model_header' -ra
AMODEX_E2E_BASE_URL=http://localhost:8766 .venv/bin/pytest \
  tests/e2e/test_workspace_baseline.py --browser chromium \
  -k cached_chat_survives_offline_reload -ra
```

Para outros ambientes, seguir o startup dos workflows existentes, aplicar `scripts/migrate_production.py` **somente no banco local dedicado** com as três variáveis de alvo local explícitas e não herdar credenciais de providers. A fixture preserva a política HTTPS existente; `ignore_https_errors` já é exclusivo de testes locais com certificado sintético, não relaxamento da verificação TLS de instalação/produção. Para WebKit, trocar `--browser` após instalar o navegador; `AMODEX_E2E_CHROMIUM_EXECUTABLE` não afeta WebKit.

Não foi criada execução automática de testes contra produção nem um segundo framework E2E. O smoke remoto existente é somente leitura, mas deve ser acionado explicitamente e com credenciais seguras apenas quando necessárias.

## G. Próximas tarefas pequenas

1. Aplicar as permissões de domínio no ambiente; repetir GET público e navegador, conferir `release.git_sha` de `/readyz`, e só então inspecionar o login Render. Nenhum login inválido ou inferência na produção.
2. Resolver QA-01 na topbar compartilhada e QA-03 no cabeçalho de modelos; revalidar quatro viewports e WebKit; retirar XFAIL somente após aprovação real.
3. Resolver QA-02 nos helpers comuns de modal com foco inicial, trap, Escape, retorno de foco e labels; revalidar sem criar frontend paralelo.
4. Alinhar contrato de timeout (QA-04), mantendo os testes existentes e testando IDs com namespace.
5. Esclarecer documento histórico de health/readiness (QA-05), sem modificar Render automaticamente.
6. Depois da liberação de acesso, validar sessão/expiração e UI privada com conta de teste autorizada. Planejar testes determinísticos de timeout/streaming com simuladores existentes; inferência real e runners remotos continuam fora desta aprovação.

Checkpoint desta entrega: **auditoria local com evidências e defeitos abertos**, sem alegação de validação integral, promoção, produção funcional ou implementação de Checkpoint + Recovery.
