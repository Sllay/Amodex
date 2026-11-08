# Piloto supervisionado no Render — preparação, não ativação

Serviço informado pelo proprietário: **My Workspace / Amodex**, produção na branch `deploy/render-neon-cloudinary-v1`, SHA `9ef70070ddf4b825276160fedcba53f9822fdd9c`. Não consultamos configurações/credenciais de produção nem alteramos esse serviço. Este procedimento só pode ser aplicado após autorização explícita. Não criar outro serviço pago nem conectar um Blueprint novo por engano ao serviço existente.

## Build e startup candidatos

- `sh scripts/build_render.sh`: instala **`'.[supervised]'`**, prepara Hermes `adecc4024456b387b1518813b90bd60c82b51287` e checkout de fonte na revisão de build. O wheel/extras já eram sintaticamente válidos; o defeito concreto era o build antigo `pip install .`, sem SDK/dependências opcionais nem assets. A instalação foi comprovada em virtualenv limpo, sem PYTHONPATH do repositório.
- Luau oficial Linux x86_64 **0.746**, URL fixa `https://github.com/luau-lang/luau/releases/download/0.746/luau-ubuntu.zip`. SHA-256 do zip: `30f11c9cde8be2dc0e3066d210705532aac3726a2db89a6c5078080b6faa6cf8`; executável: `151d8490740c7afc10638ef6e396fd50ab6f4b02a4ddf3b924abdea30f0e9820`. Ambos verificados antes do uso, sem extrair caminhos arbitrários. Nenhum download durante uma tarefa.
- Assets reconstruíveis em `.amodex-runtime/` dentro do artefato de build; `AMODEX_SUPERVISED_RUNTIME_ROOT` permite um diretório alternativo. Manifesto contém somente revisões/hashes. Não é armazenamento de autoridade nem de resultado. Não usar instalação manual no filesystem em execução como requisito para reinício.
- `python scripts/start_render.py`: valida assets e grants se `AMODEX_SUPERVISED_ENABLED=1`, depois executa um Uvicorn worker. **Não migra por padrão.** `AMODEX_APPLY_MIGRATIONS_ON_START=1` só após aprovação explícita de migrations; delega ao runner e guard Neon existentes. Recomenda-se migrar separadamente na janela autorizada e manter esse flag em `0`.
- `render.yaml` é apenas proposta versionada: build/start novos, autoDeploy false, plano Free preservado. Nenhuma configuração foi aplicada ao serviço atual. A branch do serviço deve ser escolhida pelo operador na ativação autorizada, nunca automaticamente por este trabalho.
- Build requer Git, Python 3.13, Linux x86_64, saída HTTPS para GitHub/assets e índice de pacotes. Falha de download/hash/runtime bloqueia build/start. Nada converte isso em uso pago.

## Grants remontáveis e fonte

Preparar no **próprio Render**, após autorização, um Secret File `/etc/secrets/amodex-supervised-projects.json` e apontar `AMODEX_SUPERVISED_PROJECTS_FILE` para ele. Trata-se de configuração do host, sem chaves de provider. Substituir os quatro UUIDs abaixo pelos vínculos existentes aprovados no banco; não inventar recursos ou copiar credenciais para Codex:

```json
[
  {
    "project_id": "<PROJECT_UUID>",
    "workspace_id": "<WORKSPACE_UUID>",
    "environment_id": "<ENVIRONMENT_UUID>",
    "resource_id": "<ROBLOX_PLACE_RESOURCE_UUID>",
    "repository_root": "@runtime/project",
    "base_sha": "@release",
    "paths": ["tests/fixtures/roblox/score_test.luau"],
    "analyzer": "@runtime/luau-analyze",
    "analyzer_sha256": "@pinned",
    "hermes_root": "@runtime/hermes"
  }
]
```

Os marcadores públicos acima são tokens de configuração, **não** marcadores de credenciais. São resolvidos somente pelo servidor contra assets verificados. O grant continua restrito ao projeto/Workspace/Environment ativo e recurso Place autorizado, Universe **10769712453** / Place **118200154785968**. Não editar jogo/publicar. Não acessar a experiência protegida. Se o piloto usar outro arquivo Luau no mesmo projeto autorizado, adicioná-lo explicitamente ao grant e fixar sua revisão antes da aprovação; nenhum caminho vem livremente do modelo.

O Secret File deve ser remontado no boot e os assets devem fazer parte do artefato de build. Resultado, Run, TaskPackage, fonte e evidências continuam no PostgreSQL existente. A perda do diretório temporário de trabalho pode perder edição ainda não confirmada, não autoriza replay. Resultado já commitado continua consultável. Não adicionar persistent disk pago.

## Diagnóstico de providers no host

Após login privado, consultar **GET `/v1/supervised/host-status`** pelo próprio navegador do host. Não enviar a resposta com informações privadas desnecessárias para o chat. A rota não chama providers: informa apenas presença da variável, billing/enabled do processo, classificação das conexões Web e rota medida elegível; não retorna chave, headers, valores ou referências de segredo. Não equivale a preflight live nem prova de cota consumível.

Web `free` é registro no banco, não vínculo de credencial. O registry resolve exclusivamente os nomes de processo já existentes: `GROQ_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, com os respectivos `AMODEX_<PROVIDER>_BILLING_CLASS`. Não ler/copiar seus valores do Render. Se o processo não tiver a referência correta, corrigir a vinculação **no Render**, mediante autorização própria. Não inferir billing a partir do banco, não mudar unknown artificialmente e não criar fallback de Vault/global/provider alternativo silencioso.

A seleção supervisionada prioriza **Groq entre rotas que já passaram os gates**. Mantém elegibilidade medida de implementação/tool-calling/coding, contexto mínimo e disponibilidade HEALTHY/free/cota recente. Depois, ProviderGovernor e ProviderExecutionRegistry ainda precisam admitir o pool. Nenhum ranking Web concede permissão. Rota Gemini nativa existente não é um adapter Hermes OpenAI-compatible; este fluxo suporta Groq/NVIDIA/OpenRouter medido `:free`, sem roteador aleatório. Indisponibilidade é bloqueio, não permissão para pagar.

Credencial presente não prova autenticação, billing ou cota. O único preflight GET `/models` acontece no host após aprovação da tarefa e admissão local, sem retry. Exige resposta válida e modelo selecionado presente. A cota precisa de observação autorizada recente no mecanismo existente; não fabricar observação nem medir novamente modelos automaticamente nesta preparação. Se não existir uma rota já medida elegível, registrar essa dependência antes do piloto.

## Plano Free e limites de hospedagem

Não houve teste em container Free real do Render nem verificação de suas configurações atuais. Tentamos ler a documentação oficial `https://render.com/docs/free`, `/docs/deploys` e `/docs/configure-environment-variables`; este executor recebeu **HTTP 403**. Portanto regras atuais de suspensão, orçamento mensal, CPU/RAM, retenção de build e Secret Files precisam ser confirmadas no painel/documentação pelo operador. Não apresentamos essa consulta bloqueada como validação oficial concluída.

O desenho considera filesystem efêmero, suspensão/reinício, processo Web e filho compartilhando recursos e ausência de SLA de trabalho em background. Uma tarefa dura até 90s; Hermes tem 60s e limites de chamadas/tokens já existentes. Apenas **uma tentativa supervisionada ativa por instalação**, adquirida em transação PostgreSQL antes do processo; outro pedido recebe `SUPERVISED_CAPACITY_BUSY`/409. Nenhum lock SQL fica aberto durante inferência. Não se criou background worker, scheduler ou serviço pago.

Medição local do Hermes/SDK com provider loopback: pico RSS do processo Python **165.604 KiB (~162 MiB)**; assets Hermes ~418 MiB, checkout ~11 MiB, analisador ~8 MiB, virtualenv ~79 MiB. Isso é evidência local, não medição do Render, não inclui pico combinado Web+filho e não garante caber no limite real do serviço. CPU reduzida/cold start/OOM/redeploy podem causar timeout ou UNKNOWN. O piloto precisa observar memória e prazo no host. Não aumentar tier automaticamente.

Manter a página acompanhando a tarefa ajuda a observar o progresso, sem garantir continuidade do host. SIGKILL/OOM/falha do host não permite prometer cleanup infalível. Depois de reinício, consultar o mesmo ID; deadline e lease expirados tornam a projeção UNKNOWN. Não reexecutar um efeito incerto para “tentar novamente”. Uma tentativa STARTED/RUNNING conservadora ocupa o slot até conclusão/reconciliação; isso é bloqueio seguro, não solicitação de novo Job.

## Janela de piloto, após autorização

1. Registrar SHA/branch/configuração atuais (baseline informado acima). Autorizar separadamente mudança de branch/build/start/grants e migrations. Sem isso, parar na preparação versionada.
2. Preparar assets/Secret File e presença das credenciais **no próprio Render**. Confirmar ambiente e recurso pelos UUIDs já existentes. Não habilitar cobrança ou tiers adicionais.
3. Migrations: usar manifesto/runner/guard Neon já existentes, apenas endpoint novo **Amodex**, pooled/direct correspondentes. Comparar pendências a partir da versão realmente implantada, não presumir que só 0010 falta frente a `9ef7007`. Não resetar Neon nem acessar projeto antigo. Nenhuma migration nova foi criada nesta tarefa.
4. Só na janela autorizada, ativar o SHA candidato. Confirmar `/healthz`, `/readyz` e SHA anunciado pelo serviço. Runtime habilitado sem assets/grants falha; schema/checksum ausentes mantém `/readyz` unready. Liveness não aprova release.
5. Consultar diagnóstico privado: Groq configurado/explicitamente free, rota medida elegível e cota observada. Em caso de divergência, registrar bloqueio específico no host, sem exportar chaves. Não realizar inferência diagnóstica paga.
6. Criar **um** rascunho READY pequeno: por exemplo alterar o marcador de saída da fixture pura para incluir uma descrição, preservando seus asserts. Revisar fonte/hash/escopo e aprovar uma vez. Máximo 6 chamadas, 512 tokens de saída/chamada, 48 KB acumulados de entrada, 12 ferramentas, 90s. O código resultante é artefato, não publicação Roblox.
7. Coletar status, modelo/custo, validação, hashes e artefato pelo iPhone. Não reportar modelo real aprovado enquanto apenas os testes simulados tiverem passado. Não executar Open Cloud neste piloto de preparação.
8. Falha/timeout/UNKNOWN: consultar somente o ID original, preservar recibos/evidências; não reenviar trabalho, não criar FAILED sobre sucesso possivelmente aceito. Encerrar ao obter sucesso ou bloqueio.

## Rollback

Antes de rollback, parar novas aprovações e reconciliar/quarentenar Runs supervisionados. Voltar para `9ef70070ddf4b825276160fedcba53f9822fdd9c`/branch anterior exige cuidado: o Worker antigo não conhece a exclusão dos Runs supervisionados e pode fazer claim indevido. **Não reativar workers genéricos antigos com esses Runs reclamáveis.** Preservar tabela 0010, receipts e evidências; não executar down migrations destrutivas. Restaurar configurações previamente registradas somente na mesma janela autorizada. Chaves permanecem no host. Desabilitar a proposta de auto-migration; verificar comandos antigos antes de reativar startup, pois o comando anterior executava migrations automaticamente.

A aprovação de deploy, mudança de produção e migrations não está implícita neste documento.
