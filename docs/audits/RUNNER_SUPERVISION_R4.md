# R4 — Supervisão do Runner

**R4_PASS.** Branch `fix/runner-supervision-r4`, baseada em `eec30b1ddc62d17ecae6cb64ea911e88aae55c63` de `fix/runner-dispatch-idempotency-r3`. Implementação: `7a740d5`. Checkpoint VALIDATED para este escopo; sem promoção, deploy, merge ou PR.

## CR-05 corrigido

O heartbeat antigo era um subshell desacoplado: sua morte não interrompia o trabalho nem impedia outras etapas. Foi substituído por `amodex.runners.supervisor`, executado em primeiro plano em cada etapa posterior ao claim. O supervisor confirma autoridade antes de lançar um comando, renova durante o trabalho e encerra o grupo de processos quando recebe falha relevante ou cancelamento.

As etapas compartilham um arquivo privado em `RUNNER_TEMP`, vinculado ao Job e ao `github_run_id`. Uma falha bloqueia o próximo supervisor desse Job, inclusive o fallback de report. Não há novo serviço, fila, scheduler, Worker ou sistema de agentes. O servidor R1 já recusa lease expirada, owner divergente e estado incompatível; seus handlers e SQL não precisaram mudar. R2A e R3 também permanecem intactos. **Nenhuma migration foi alterada.**

## Classificação e política de autoridade

`RunnerControlClient.check_authority` devolve `HeartbeatResult` estruturado, com classe, status HTTP e código API sanitizado quando disponíveis. A classificação usa status/códigos, nunca comparação de mensagens.

| Classe | Condição |
| --- | --- |
| HEALTHY | HTTP 200 com mesmo Job, mesmo owner, CLAIMED/RUNNING e lease futura com timezone |
| LEASE_LOST | 409 com códigos R1 conhecidos de lease/owner/estado, recurso inexistente, ou resposta com owner/estado incompatível/lease expirada |
| AUTH_REJECTED | HTTP 401/403 |
| AUTHORITY_UNCONFIRMED | Timeout/rede, JSON ou recibo inconclusivo, status inesperado ou 409 com código desconhecido |
| CONTROL_PLANE_ERROR | HTTP 5xx |

Rede indisponível **não comprova perda definitiva da lease**. Contudo, qualquer classe diferente de HEALTHY interrompe o trabalho e impede novas operações. Não há reconexão infinita, tolerância que autorize continuar sem confirmação, nem renovação automática após bloqueio. O operador recebe diagnóstico com a classe; corpos HTTP, tokens, URLs de controle e mensagens brutas de exceção não são reproduzidos.

O intervalo padrão é 20 segundos. O prazo do heartbeat é limitado pela lease conhecida, com renovação antecipada quando necessário. Cada chamada de controle tem limite total de cinco segundos no supervisor, imposto por temporizador POSIX; não depende somente do timeout por fase HTTP. Há nova checagem de expiração imediatamente antes do spawn, inclusive se uma gravação local consumiu o prazo entre a confirmação e o lançamento. O supervisor roda na main thread POSIX; não deixa threads de heartbeat após concluir.

Cada comando tem limite padrão de 1.800 segundos. A sessão mantém uma única chamada de controle por vez, sem retry. A definição do trabalho continua nas etapas existentes do workflow; nenhum comando fica disponível como ferramenta irrestrita do Hermes.

## Subprocessos, sinais e cleanup

O comando inicia com `start_new_session=True`, sem shell escolhido implicitamente pelo supervisor. O workflow fornece explicitamente seu Bash confiável. O processo principal e seus descendentes normais pertencem ao mesmo grupo. As credenciais e a URL do Control Plane são retiradas do ambiente do trabalho; o cliente no processo supervisor continua autorizado. A credencial de clone, quando concedida, permanece limitada à etapa de clone existente.

Em falha, cancelamento, timeout ou saída normal, o supervisor limpa o grupo. Envia SIGTERM, aguarda até dois segundos e escala para SIGKILL se necessário. No Linux, usa subreaper para adotar descendentes órfãos desse grupo e recolhê-los com `waitpid`; restaura os handlers, timer e configuração anterior ao sair. Também remove filhos remanescentes quando o comando raiz retorna normalmente. Falha de cleanup produz diagnóstico próprio e bloqueia trabalho seguinte.

SIGTERM e SIGINT são tratados como cancelamento controlado. A interrupção é propagada ao grupo; um cancelamento recebido durante o cleanup também fica registrado para a próxima etapa. Os testes exercitam SIGTERM real enviado a outro processo supervisor, filho que ignora SIGTERM, descendentes e cleanup após sucesso. Não há heartbeat rodando em background entre etapas: não há trabalho supervisionado ativo nesse intervalo; a próxima etapa exige confirmação nova antes de operar.

O arquivo de segurança é gravado atomicamente, com conteúdo sincronizado antes de iniciar uma operação e permissões privadas. Um lock local não bloqueante impede dois supervisores simultâneos sobre o mesmo arquivo. Cada operação tem identidade de etapa e passa por STARTED antes do efeito. Uma operação já registrada não é repetida. STARTED deixado por queda de processo é tratado como potencialmente desconhecido e bloqueia trabalho/report automático; não se transforma UNKNOWN em outra intenção. O supervisor não chama dispatch nem providers.

## Reports e cancelamento

- Perda de autoridade conhecida: nenhum report de sucesso ou terminal é enviado. O fallback do workflow lê o bloqueio e termina sem chamar report/heartbeat novamente.
- Falha normal do comando com autoridade válida: uma tentativa de FAILED pode ser feita após heartbeat novo.
- Cancelamento controlado com autoridade ainda válida: permite uma tentativa de CANCELLED após encerrar os processos e confirmar autoridade novamente. Se essa confirmação falhar, não reporta. Não cria Job ou Attempt substituto.
- Antes do request terminal, persiste `terminal_attempted`. Recibo perdido/inconclusivo fica REPORT_UNKNOWN e não admite novo report, nem fallback FAILED sobre um COMPLETED possivelmente aceito. O estado local preserva a incerteza do trabalho interrompido.
- Os checks R1 de owner, estado e expiração continuam sendo a autoridade final no banco. Uma perda concorrente após o heartbeat não autoriza contornar esses checks. Teste integrado prova que um report tardio direto ainda é recusado pelo servidor.

Isso não implementa acknowledgement terminal idempotente. HTTP de report, conclusão do processo e resultado de operações externas continuam fatos distintos; R2B permanece pendente.

## Workflow e arquivos alterados

`.github/workflows/environment-runner.yml` mantém o bootstrap, claim, clone, ferramentas e validações existentes. Remove apenas o heartbeat desacoplado e sua etapa de stop; envolve o processamento do contexto e as operações seguintes no supervisor. `mark-running` e reports são feitos pelo próprio supervisor, sem distribuir o token de controle ao subprocesso. A verificação de credenciais ocorre supervisionada antes do report terminal e não imprime remotes. O helper de askpass tem cleanup de saída. O claim completo fica em arquivo privado; o CLI imprime somente ACK sanitizado.

Arquivos de implementação:

- `src/amodex/runners/supervisor.py`: processo supervisor, estado local, heartbeat, sinais, grupos e report limitado.
- `src/amodex/runners/client.py`: classificação estruturada, erros sanitizados, identidade/lease do heartbeat e redirects recusados.
- `.github/workflows/environment-runner.yml`: wrappers em primeiro plano e reports guardados.

Testes: novos `tests/unit/test_runner_supervision_r4.py` e `tests/integration/test_runner_supervision_ownership_r4.py`; `tests/unit/test_runner_heartbeat_audit.py` deixa de caracterizar a falha e passa a exigir interrupção/bloqueio usando o shell real do workflow com executáveis/API falsos. Relatório e evidências ficam em `docs/audits/evidence/task10/`.

## Testes e evidências

**306 casos distintos aprovados, zero falhas, erros ou skips no resultado final.** Python 3.13.5, PostgreSQL 17.11 em loopback com banco descartável, psycopg 3.3.6, pytest 9.0.2. A bateria conjunta aprovou 299 casos; depois foram repetidos somente os testes de supervisão afetados pelas duas verificações finais e adicionados checks do ACK de running. O JUnit consolida a última execução de cada caso. Há um aviso preexistente de depreciação Starlette/httpx.

| Escopo | Casos aprovados |
| --- | ---: |
| Supervisão R4, workflow e contrato de heartbeat | 64 |
| R1: autenticação, gate e ownership | 65 |
| R2A: lease/fencing de Run | 84 |
| R3: intake, dispatch/claim, crashes e caracterizações corrigidas | 51 |
| Environment e contratos de produto | 7 |
| Tarefa 08: fluxo determinístico, HTTP simulado/análise local | 35 |

Cobertura direta: heartbeat saudável; 409, 401/403, timeout, rede, 500 e resposta inconclusiva; falha com processo ativo e descendentes; cancelamento e SIGTERM; escalonamento forçado; simulação do estado deixado por SIGKILL; inicialização parcial; nenhum novo comando, dispatch ou replay após bloqueio; nenhum report tardio aceito; report CANCELLED limitado; ACK terminal e ACK de running desconhecidos sem repetição; expiração antes do spawn; cancelamento durante cleanup; encerramento normal sem processos/heartbeat restantes. O shell de todas as etapas é validado com `bash -n`, e as falhas de renovação são exercitadas no shell modificado, sem Actions real.

Os testes integrados substituem somente o transporte HTTP e passam pelos middlewares, handlers e ownership/lease reais no PostgreSQL. Os de processos usam comandos locais descartáveis, sem providers. [Resultados e comandos](evidence/task10/results.json), [JUnit consolidado](evidence/task10/tests.xml).

Sem suíte completa, navegador, inferência paga, dispatch GitHub Actions real ou execução Roblox. Nenhum arquivo da integração Roblox/Hermes/Godot foi alterado. A regressão da Tarefa 08 utiliza HTTP simulado; não repete o teste Open Cloud.

## Limites e dependências restantes

Não é possível garantir cleanup após **SIGKILL do supervisor**, falha total do host ou kernel que não consiga terminar um processo. O teste de SIGKILL simula o registro STARTED remanescente e prova o bloqueio de replay; não afirma que um handler roda após SIGKILL. Grupos de processos também não contêm filhos que deliberadamente escapem com outra sessão; isso exigiria contenção de host/cgroups, fora desta tarefa. Comandos supervisionados devem manter seu trabalho em foreground e não daemonizar.

Interrupção local não desfaz efeitos já enviados a APIs, nem garante que serviços externos tenham cancelado o trabalho. Uma chamada pode ter sido aceita antes da interrupção; não é repetida. Não há fencing externo atômico ou garantia de exactly-once.

O arquivo de segurança é local ao runner e não é um journal durável entre hosts. Recovery/reconciliação de STARTED, reports/acknowledgements R2B, vínculo Job/Run/Attempt e cancelamento global pela UI continuam pendentes. Esta entrega supervisiona o caminho Linux Actions existente e sinais locais, com as recusas de autoridade do heartbeat; não cria um protocolo novo de cancelamento de Run nem modifica o runner desktop Roblox.

R1, R2A e R3 permanecem nas autoridades originais, com regressões aprovadas. Não houve merge, PR, deploy, nova migration ou alteração Render/Neon.
