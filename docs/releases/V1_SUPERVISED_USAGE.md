# Usar o fluxo Luau supervisionado

O caminho de produto é `/supervised`, acessível pelo link **Luau supervisionado** no Workspace. A demonstração com modelo externo ainda depende de `REAL_MODEL_BLOCKED` ser resolvido no host autorizado. Testes com respostas simuladas não autorizam declarar a V1 aprovada.

## Preparação do host pelo operador

Estas são instruções de preparação, não alterações já aplicadas ao Render/Neon.

1. Manter o acesso privado existente (`AMODEX_ACCESS_PASSCODE` e segredo de sessão). Sem sessão de proprietário, os novos endpoints recusam acesso, inclusive se o gate estiver desativado. Credencial de Runner não autoriza estas rotas.
2. Aplicar **somente a nova migration pendente** `postbaseline/0010_supervised_coding.sql`, pelo executor de migrations existente, com guard de endpoint. Nenhuma migration de produção foi executada nesta entrega. `/readyz` exige o manifesto atualizado; `/healthz` continua sendo somente liveness.
3. No ambiente Python 3.13 do serviço, instalar o extra opcional `.[supervised]`. Ele fixa a combinação de dependências validada com o adapter. Preparar separadamente um checkout limpo do Hermes no commit `adecc4024456b387b1518813b90bd60c82b51287`, sem `.env` global. Não há instalação/upgrade automático durante uma tarefa.
4. Preparar um checkout de projeto na revisão autorizada e o executável oficial `luau-analyze` fora desse checkout. Fixar seu SHA-256. Este fluxo copia **um** arquivo rastreado e autorizado para uma área descartável; não escreve no checkout do host nem no jogo.
5. Vincular o projeto existente e seu Workspace ao mesmo Environment ativo. Vincular o recurso `ROBLOX_PLACE` do conector conectado a esse Environment/Workspace. Este caminho V1 admite somente Universe `10769712453` / Place `118200154785968` e recusa o nome protegido. Os IDs ficam nesta política de release, não viram defaults da integração geral Roblox.
6. Configurar `AMODEX_SUPERVISED_PROJECTS_FILE` com o caminho de um JSON privado do **host**. Exemplo de formato, substituindo os identificadores e hashes pelos valores reais já autorizados:

```json
[
  {
    "project_id": "<PROJECT_UUID>",
    "workspace_id": "<WORKSPACE_UUID>",
    "environment_id": "<ENVIRONMENT_UUID>",
    "resource_id": "<ROBLOX_PLACE_RESOURCE_UUID>",
    "repository_root": "/opt/amodex/projects/approved-luau",
    "base_sha": "<SHA_GIT_DE_40_CARACTERES>",
    "paths": ["tests/fixtures/roblox/score_test.luau"],
    "analyzer": "/opt/amodex/bin/luau-analyze",
    "analyzer_sha256": "<SHA256_DO_EXECUTAVEL>",
    "hermes_root": "/opt/amodex/hermes"
  }
]
```

O JSON não contém credenciais. O navegador não pode enviar roots, executáveis, URLs de provider ou novas permissões. Arquivo com symlink, conteúdo divergente do Git, alvo desativado ou grant alterado bloqueia o caminho.

7. Vincular **uma** credencial gratuita autorizada nas configurações seguras desse host. O registry existente exige pool e rota explicitamente `free`; `unknown` permanece bloqueado. Para este adapter, selecionar uma rota **medida e ELIGIBLE** de implementação, com tool-calling/coding e contexto de pelo menos 16.000 tokens, pelos contratos existentes de Model Intelligence. Há suporte aos adapters OpenAI-compatible existentes de Groq/NVIDIA e a um modelo OpenRouter medido com sufixo `:free`; não se usa o roteador aleatório `openrouter/free` nem se cria um adapter Gemini novo.
8. A observação existente de disponibilidade precisa ser `HEALTHY`, `free`, com cota disponível e idade entre zero e cinco minutos. Não fabricar medições nem transformar credencial presente em prova de gratuidade/cota. Se a rota ainda não foi medida/aprovada, preparar essa autorização separadamente, sem benchmarks/inferência pagos. Após a aprovação da tarefa, o processo verifica uma vez a autenticação/conectividade e a presença do modelo com GET `/models`, sem retry. HTTP 200 nesse GET **não** certifica custo ou cota; são gates independentes.

O segredo HTTP do Codex não é disponibilizado automaticamente ao Render, e a credencial Roblox não é uma credencial de modelo. Não copiar valores para chat, Git, prompts ou browser. Preservar o proxy e a CA configurados no host; o adapter mantém essas variáveis no processo isolado do Hermes.

## Operação no celular

1. Entrar no Workspace pelo navegador, selecionar o chat e o Environment autorizado. Criar o rascunho com uma mudança Luau pequena e usar **Marcar pronto**.
2. Abrir **Luau supervisionado**, escolher projeto, rascunho e arquivo. **Preparar revisão** grava Run, TaskPackage e snapshot antes de qualquer inferência.
3. Conferir objetivo, Environment, arquivo original e hash. Marcar a autorização e tocar **Autorizar edição com IA gratuita** uma vez.
4. Acompanhar a fase e as evidências. Em `COMPLETED`, expandir **Código resultante salvo** e usar **Baixar arquivo Luau**. O artefato está persistido no banco; recarregar ou abrir o histórico não inicia trabalho.
5. `REAL_MODEL_BLOCKED`: falta configuração gratuita autorizada/rota/cota. O snapshot e o bloqueio ficam salvos. Resolver a dependência e criar conscientemente um **novo rascunho/intenção**; não há botão que reinicie silenciosamente a tentativa antiga.
6. `UNKNOWN_OUTCOME`: consultar a execução original e reconciliar com o operador. Não criar outra submissão por suposição de falha. Não existe replay ou recuperação automática deste estado.

`COMPLETED` significa edição autorizada produzida e análise Luau estática aprovada, com artefato salvo. **Não** comprova o comportamento solicitado, testes dinâmicos ou gameplay. A aceitação funcional continua supervisionada pelo proprietário. Este fluxo não oferece POST Roblox, publicação, DataStore ou shell ao modelo.

## Contrato mínimo e limites

- `POST /v1/supervised/executions`: `{draft_id, project_id, path}`, `Idempotency-Key` estável por intenção (16–128 caracteres alfanuméricos, `.`, `_`, `:`, `-`). A identidade inclui Environment e o payload canônico. O primeiro pedido congela o conteúdo do rascunho, grant e fonte. Retry do mesmo payload devolve o mesmo Run; payload divergente com a mesma chave retorna 409. Um rascunho só pode originar uma execução.
- `POST /v1/supervised/executions/{id}/approve`: `{source_sha256}`. Sessão privada, Origin igual ao host e `X-AMODEX-Action: supervised-v1` são obrigatórios. A aprovação usa CAS transacional; reenvio consulta a tentativa existente e não inicia outro processo.
- `GET /v1/supervised/options`, `GET /v1/supervised/executions`, `GET /v1/supervised/executions/{id}`: projeções privadas `no-store`. A última permite reconciliar um processo interrompido como UNKNOWN após deadline e expiração da lease; não retoma o Run.
- Até 6 chamadas, 512 tokens de saída por chamada (3.072 no total), 48.000 bytes totais de entrada HTTP, 12 chamadas de ferramenta, 60 segundos de orçamento Hermes e 90 segundos para o processo completo. Cada chamada HTTP tem timeout finito, resposta limitada a 1 MB; arquivo/artefato limitado a 16 KB; eventos limitados a 40. Falha/timeout bloqueia retry do SDK, do Hermes e de novos clientes criados pelo Hermes. Não há fallback pago.
- O processo isolado usa `Stage1Worker.process_scoped` e a lease/fence R2A. Antes de HTTP ou ferramenta, verifica autoridade e controle do Run, validade do TaskPackage, deadline e alvo. Cada intenção HTTP é gravada antes do envio. Nenhum lock SQL fica aberto durante HTTP. Resultados/artefato e transição/evento final de Run são atômicos. O protocolo de Runner Job/R2B permanece separado; nenhum Job terminal conclui este Run por implicação.
- Run de tarefa supervisionada é excluído do claim genérico, inclusive após expiração. Queda após aprovação/início não agenda outro envio. Não é exactly-once externo nem Recovery R5.
- Cancelamento/perda de autoridade bloqueia os próximos efeitos. Uma chamada já em voo pode ter resultado incerto. O launcher aplica timeout e encerra o grupo de processos nos caminhos controláveis; SIGKILL do supervisor/host indisponível não permite prometer cleanup infalível. Em morte do launcher, os limites do filho e os gates de autoridade continuam sendo necessários; a consulta final é conservadora.

Para repetir apenas as provas offline, usar os testes `test_v1_supervised_*`, PostgreSQL descartável em loopback, `AMODEX_TEST_LUAU_ANALYZER` e Chromium. O extra `.[browser-test]` fornece Playwright; `AMODEX_TEST_CHROMIUM` seleciona o executável do host. As fixtures de modelo simulado existem apenas nos testes e no probe explicitamente local `scripts/v1_supervised_hermes_probe.py`. Nenhuma opção da interface ativa simulação em produção.
