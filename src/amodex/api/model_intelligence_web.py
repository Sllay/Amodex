"""Step 4 web augmentation and model benchmark dashboard."""

from __future__ import annotations


_STEP4_CSS = r'''
.mi-ready{border-color:var(--good)!important}.mi-busy{opacity:.62;pointer-events:none}.mi-note{font-size:.76rem;color:var(--muted);line-height:1.4}
.mi-route-footnote{font-size:.73rem;margin-top:13px;padding-top:8px;border-top:1px solid var(--line);color:var(--muted);overflow-wrap:anywhere}
.mi-route-footnote.is-fallback{color:var(--warn)}
'''

_STEP4_SCRIPT = r'''
<script id="amodex-model-intelligence-feature">
const mi={status:null};
const MI_PRIMARY_ROUTE="nvidia:mistralai/mistral-large-4-1t-a52b:nvidia-free-primary";
const miDecisionsByChat=new Map();
function miQueueFeature(register){if(window.AmodexApp)return register(window.AmodexApp);(window.__amodexFeatureQueue||(window.__amodexFeatureQueue=[])).push(register)}
async function miApi(path,opt={}){return window.AmodexApp?AmodexApp.api(path,opt):api(path,opt)}
async function miBoot(){
  try{
    mi.status=await miApi('/v1/model-intelligence/status');
    const badge=document.getElementById('inferenceBadge');
    if(badge){
      const ready=mi.status.automatic_chat_inference==='READY';
      badge.textContent=ready?'AI routed':'benchmark needed';
      badge.className='badge '+(ready?'good':'warn');
    }
  }catch(e){console.warn('model intelligence unavailable',e)}
}
const MI_PROVIDER_NAMES={
  "nvidia:mistralai/mistral-large-4-1t-a52b:nvidia-free-primary":"nvidia-mistral-large-4",
  "groq:openai/gpt-oss-20b:groq-free-primary":"groq-gpt-oss-20b",
  "gemini:gemini-3.5-flash-lite:gemini-free-primary":"gemini-flash-lite",
  "openrouter:openrouter/free:openrouter-free-primary":"openrouter-free",
  "groq:qwen/qwen3.8-27b:groq-free-primary":"groq-qwen"
};
function miRouteFootnote(decision){
  if(decision?.status!=="SUCCEEDED"||!decision?.result_message_id||!decision?.final_route_id)return null;
  const route=decision.final_route_id;
  const name=MI_PROVIDER_NAMES[route];
  if(!name)return null; // Unknown or untrusted routes must not be mislabelled.
  const facts=Array.isArray(decision.selection_facts)?decision.selection_facts:[];
  const policyFallback=facts.includes('chat_primary_policy=PRIMARY_UNAVAILABLE');
  const isFallback=route!==MI_PRIMARY_ROUTE&&(decision.fallback_used===true||policyFallback);
  return {label:(isFallback?'fallback/':'modelo/')+name,isFallback};
}
function miRememberDecisions(chatId,rows){
  const before=miDecisionsByChat.get(chatId)||[];
  const unique=new Map(before.map(d=>[d.decision_id,d]));
  (Array.isArray(rows)?rows:[rows]).forEach(d=>{if(d?.decision_id)unique.set(d.decision_id,d)});
  miDecisionsByChat.set(chatId,[...unique.values()]);
}
function miDecorateMessages(){
  const chatId=state.chat?.chat_id;
  if(!chatId||!Array.isArray(state.messages))return;
  const stored=miDecisionsByChat.get(chatId)||[];
  const byMessage=new Map(stored.filter(d=>d.result_message_id).map(d=>[d.result_message_id,d]));
  const nodes=[...document.querySelectorAll('#messages .message')];
  state.messages.forEach((m,index)=>{
    if(m.role!=='AMODEX')return;
    const bubble=nodes[index]?.querySelector('.bubble');
    if(!bubble)return;
    const result=miRouteFootnote(byMessage.get(m.message_id));
    let footer=bubble.querySelector('.mi-route-footnote');
    if(!result){footer?.remove();return}
    if(!footer){footer=document.createElement('div');footer.className='mi-route-footnote';bubble.appendChild(footer)}
    footer.classList.toggle('is-fallback',result.isFallback);
    footer.textContent='('+result.label+')'; // Presentation only: never append metadata to saved model text.
  });
}
async function miLoadDecisions(chatId,app){
  if(!chatId)return;
  try{
    const rows=await app.api('/v1/chats/'+encodeURIComponent(chatId)+'/model-decisions?limit=500');
    miRememberDecisions(chatId,rows);
    if(state.chat?.chat_id===chatId)miDecorateMessages();
  }catch(e){console.warn('model decision metadata unavailable',e)}
}
async function miAutomaticReply(app,ctx){
  const owner=ctx?.owner;if(!owner||!state.chat)return;
  const chatId=state.chat.chat_id;
  try{
    if(!mi.status)await miBoot();
    if(mi.status?.automatic_chat_inference!=='READY'){
      app.notice('Mensagem salva · resposta automática aguardando benchmark de uma rota gratuita.','warn');
      return;
    }
    const footer=document.querySelector('.composer-wrap');footer?.classList.add('mi-busy');
    const decision=await app.api(`/v1/chats/${chatId}/respond`,{method:'POST',body:JSON.stringify({owner_message_id:owner.message_id,profile:null})});
    miRememberDecisions(chatId,decision);
    const updated=await app.api(`/v1/chats/${chatId}/messages`);
    if(state.chat?.chat_id===chatId){
      state.messages=updated;
      if(typeof mmRefreshChat==='function')await mmRefreshChat();
      renderMessages();
    }
    app.notice('Resposta concluída.','good');
    await miBoot();
  }catch(e){
    if(e?.code==='NO_MEASURED_ROUTE'||e?.code==='BENCHMARK_REQUIRED'){
      app.notice('Mensagem salva · nenhuma rota gratuita medida está elegível ainda.','warn');
    }else if(e?.code==='DECISION_ALREADY_EXISTS'){
      console.warn('model decision already exists',e);
    }else{
      app.error(e);
    }
    await miBoot();
  }finally{
    document.querySelector('.composer-wrap')?.classList.remove('mi-busy');
  }
}
miQueueFeature(app=>{
  app.hooks.register('afterSend',ctx=>miAutomaticReply(app,ctx));
  app.hooks.register('afterChatSelected',()=>miLoadDecisions(state.chat?.chat_id,app));
  app.hooks.register('afterRenderMessages',()=>miDecorateMessages());
});
miBoot();
</script>
'''


def augment_model_intelligence_html(html: str) -> str:
    """Enable routed chat through the shared browser runtime hook contract."""
    result = html.replace("</style>", _STEP4_CSS + "</style>", 1)
    old_warning = (
        '<div class="warning">Inferência da Amodex continua bloqueada até benchmark/promoção dos modelos. '
        'O chat não executa tarefas sozinho.</div>'
    )
    new_warning = (
        '<div class="warning">A Amodex responde apenas por rotas medidas e classificadas como gratuitas. '
        'Conversar continua sem autoridade de execução: use um Environment autorizado para modificar projetos.</div>'
    )
    result = result.replace(old_warning, new_warning, 1)
    system_marker = '<button class="item" onclick="location.href=\'/docs\'">API / Docs</button>'
    if system_marker in result:
        result = result.replace(
            system_marker,
            '<button class="item" onclick="location.href=\'/models\'">Modelos / Benchmarks</button>' + system_marker,
            1,
        )
    result = result.replace(
        'Passo 4: Model Intelligence',
        'Passo 4: Model Intelligence ativo · execução continua nos Environments',
        1,
    )
    return result.replace("</body>", _STEP4_SCRIPT + "</body>", 1)


MODEL_INTELLIGENCE_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amodex · Model Intelligence</title>
<style>
:root{color-scheme:dark;--bg:#0b0f14;--panel:#111821;--panel2:#171f2a;--line:#2b3644;--text:#edf3fa;--muted:#9aa9b8;--good:#55d18b;--warn:#f2c14e;--bad:#ff6b6b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}header{position:sticky;top:0;background:rgba(11,15,20,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:10px;align-items:center;z-index:2}header strong{font-size:1.05rem}header .grow{flex:1}.wrap{max-width:1100px;margin:auto;padding:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}.route{margin:9px 0;padding:10px;border:1px solid var(--line);border-radius:11px;background:var(--panel2)}button,select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px 10px}button{cursor:pointer}.primary{border-color:#5e8cff}.badge{display:inline-block;padding:3px 7px;border:1px solid var(--line);border-radius:999px;font-size:.72rem}.good{color:var(--good);border-color:var(--good)}.warn{color:var(--warn);border-color:var(--warn)}.bad{color:var(--bad);border-color:var(--bad)}.muted{color:var(--muted)}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.busy{opacity:.58;pointer-events:none}code{overflow-wrap:anywhere}.benchline{margin:7px 0;padding:7px 9px;border:1px solid var(--line);border-radius:9px}.proof{margin-top:12px}.case{margin:8px 0;padding:9px;border:1px solid var(--line);border-radius:9px;background:#0d131a}.case pre{margin:7px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--muted);font-size:.78rem}.hidden{display:none}.validation-routes{margin-top:14px;border-top:1px solid var(--line);padding-top:10px}.validation-routes summary{cursor:pointer;padding:9px 4px;color:var(--muted);font-weight:600}.validation-routes[open] summary{color:var(--text)}@media(max-width:600px){header{padding:10px}.wrap{padding:10px}.grid{grid-template-columns:1fr}}
</style></head><body>
<header><button onclick="location.href='/'">← Workspace</button><strong>Model Intelligence</strong><span id="status" class="badge warn">loading</span><span class="grow"></span><button onclick="reconcile()">Reconciliar UNKNOWN</button></header>
<div class="wrap"><div class="grid"><section class="card"><h2>Rotas</h2><p class="muted">Texto e visão são medições independentes. Falha de provider/rate limit nunca conta como erro visual: suites incompletas ficam BLOCKED e não criam score certificado.</p><div id="routes"></div></section><section class="card"><h2>Leaderboard</h2><div class="row"><select id="profile" onchange="loadLeaderboard()"><option value="repository-analyst">Repository analyst</option><option value="research">Research</option><option value="implementation">Implementation</option><option value="correction">Correction</option><option value="reviewer">Reviewer</option><option value="integration-migration">Integration</option><option value="godot-specialist">Godot</option><option value="visual-runtime-validation">Visual/runtime</option></select></div><div id="leaderboard"></div></section></div><section class="card proof" id="nvidiaDiagnostic"><h2>Diagnóstico NVIDIA · Mistral Large 4</h2><p class="muted">Conexão real com a NVIDIA, separada do benchmark. Primeiro confira a configuração; depois execute um teste de texto e um de imagem. Cada botão faz apenas uma chamada externa. Não altera notas, rotas nem mensagens.</p><div id="nvidiaDiagConfig" class="case">Carregando configuração, sem chamar a NVIDIA…</div><div class="row"><button onclick="loadNvidiaDiagnosticConfig()">Rever configuração</button><button class="primary" onclick="runNvidiaDiagnostic('text')">1 · Testar texto (HTTP)</button><button class="primary" onclick="runNvidiaDiagnostic('vision')">2 · Testar PNG (visão)</button></div><p class="muted">Ambos usam a rota fixa NVIDIA e exigem pool classificado como gratuito. Existe limite de 40 segundos por modalidade para evitar chamadas repetidas. A chave nunca aparece no relatório.</p><div id="nvidiaDiagResults"></div></section><section id="visionEvidence" class="card proof hidden"><h2>Prova visual</h2><div id="visionEvidenceBody"></div></section></div>
<script>
const TEXT_SUITE='step4-live-v1',VISION_SUITE='vision-live-v1';
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}
async function req(path,opt={}){const r=await fetch(path,{headers:{'content-type':'application/json',...(opt.headers||{})},...opt});let b=null;try{b=await r.json()}catch{}if(!r.ok){const d=b?.detail;throw new Error(d?.message||d?.code||r.statusText)}return b}
async function boot(){await Promise.all([loadStatus(),loadRoutes(),loadLeaderboard(),loadNvidiaDiagnosticConfig()])}
async function loadStatus(){try{const s=await req('/v1/model-intelligence/status');const el=document.getElementById('status');const ready=s.automatic_chat_inference==='READY';el.textContent=ready?'AI routed':'benchmark needed';el.className='badge '+(ready?'good':'warn')}catch(e){console.warn(e)}}
function latestFor(runs,routeId,suite){return runs.find(b=>b.route_id===routeId&&b.suite_version===suite)||null}
function benchLine(label,b){
 if(!b)return '<div class="benchline muted"><strong>'+esc(label)+':</strong> ainda não medido</div>';
 const blocked=label==='Visão'&&b.status==='BLOCKED';
 const scored=Number(b.sample_count||0);
 const score=Number(b.aggregate_score||0);
 const failed=b.status==='SUCCEEDED'&&scored>0&&score===0;
 const state=failed?'REPROVADO NO BENCHMARK':b.status;
 return '<div class="benchline"><strong>'+esc(label)+':</strong> '+esc(state)+' · '+(blocked?'score parcial ':'score ')+score.toFixed(3)+' · '+Number(b.passed_count||0)+'/'+scored+' casos pontuados'+(failed?'<br><span class="warn">O transporte respondeu, mas não acertou os casos. Não está validado.</span>':'')+(blocked?'<br><span class="warn">suite incompleta · não certifica visão</span>':'')+'<br><span class="muted">'+esc(b.suite_version)+'</span></div>';
}
function hasMeasuredVisionTransport(r){return (r.provider==='groq'&&r.model==='qwen/qwen3.8-27b')||(r.provider==='nvidia'&&r.model==='mistralai/mistral-large-4-1t-a52b')}
const AMODEX_PRIMARY_ROUTE='nvidia:mistralai/mistral-large-4-1t-a52b:nvidia-free-primary';
function textValidated(r,runs){
 const b=latestFor(runs,r.route_id,TEXT_SUITE);
 return ['ELIGIBLE','ELIGIBLE_LIMITED'].includes(r.lifecycle)&&!!b&&b.status==='SUCCEEDED'&&Number(b.sample_count||0)>=3&&Number(b.aggregate_score||0)>=0.8;
}
function visionValidated(r,runs){
 const b=latestFor(runs,r.route_id,VISION_SUITE);
 return !!b&&b.status==='SUCCEEDED'&&Number(b.sample_count||0)===5&&Number(b.aggregate_score||0)>=0.8;
}
function routeCard(r,runs){
 const card=document.createElement('div');
 card.className='route';
 const caps=Array.isArray(r.capabilities)?r.capabilities:[];
 const vision=caps.includes('vision'),visualTransport=hasMeasuredVisionTransport(r);
 const textRun=latestFor(runs,r.route_id,TEXT_SUITE),visionRun=latestFor(runs,r.route_id,VISION_SUITE);
 const top=document.createElement('div');
 top.innerHTML='<strong>'+esc(r.provider)+' · '+esc(r.model)+'</strong><br><code>'+esc(r.route_id)+'</code><br><span class="badge">'+esc(r.lifecycle)+'</span> <span class="muted">'+esc(r.cost_class)+'</span> '+(vision?'<span class="badge warn">vision declarada</span>':'')+(vision&&!visualTransport?' <span class="badge">transporte visual não medido</span>':'')+benchLine('Texto',textRun)+(vision?benchLine('Visão',visionRun):'');
 card.appendChild(top);
 const actions=document.createElement('div');actions.className='row';
 const btn=document.createElement('button');btn.textContent='Benchmark texto';btn.addEventListener('click',()=>runBenchmark(r.route_id));actions.appendChild(btn);
 if(visualTransport){const vb=document.createElement('button');vb.className='primary';vb.textContent='👁 Benchmark visual';vb.addEventListener('click',()=>runVisionBenchmark(r.route_id));actions.appendChild(vb)}
 if(visionRun){const proof=document.createElement('button');proof.textContent='Ver prova visual';proof.addEventListener('click',()=>loadSavedVisionEvidence(r.route_id,visionRun.benchmark_id));actions.appendChild(proof)}
 card.appendChild(actions);
 return card;
}
async function loadRoutes(){
 const [rows,runs]=await Promise.all([req('/v1/model-intelligence/routes'),req('/v1/model-intelligence/benchmarks?limit=100')]);
 const root=document.getElementById('routes');root.replaceChildren();
 const active=rows.filter(r=>r.enabled!==false);
 const principal=active.find(r=>r.route_id===AMODEX_PRIMARY_ROUTE&&textValidated(r,runs)&&visionValidated(r,runs));
 if(principal){
  const heading=document.createElement('h3');heading.className='good';heading.textContent='Amodex · modelo principal candidato';
  const note=document.createElement('p');note.className='muted';note.textContent='Mistral Large 4: texto e visão medidos. A escolha efetiva no chat continua obedecendo à política de rotas, disponibilidade e custo zero.';
  root.append(heading,note,routeCard(principal,runs));
 }
 const validated=active.filter(r=>r!==principal&&textValidated(r,runs));
 if(validated.length){
  const heading=document.createElement('h3');heading.textContent=principal?'Outros modelos validados':'Modelos validados';
  root.appendChild(heading);
  validated.forEach(r=>root.appendChild(routeCard(r,runs)));
 }
 const pending=active.filter(r=>r!==principal&&!validated.includes(r));
 if(pending.length){
  const details=document.createElement('details');details.className='validation-routes';
  const summary=document.createElement('summary');summary.textContent='Outros modelos · validar ou revisar ('+pending.length+')';
  details.appendChild(summary);
  const note=document.createElement('p');note.className='muted';note.textContent='Rotas não certificadas permanecem acessíveis para testes; falhas anteriores são preservadas como evidência, não apagadas.';
  details.appendChild(note);
  pending.sort((a,b)=>Number(!!latestFor(runs,a.route_id,TEXT_SUITE))-Number(!!latestFor(runs,b.route_id,TEXT_SUITE)));
  pending.forEach(r=>details.appendChild(routeCard(r,runs)));
  root.appendChild(details);
 }
 if(!active.length)root.textContent='Nenhuma rota ativa disponível.';
}
async function loadLeaderboard(){const root=document.getElementById('leaderboard');root.innerHTML='<p class="muted">Carregando…</p>';try{const profile=document.getElementById('profile').value;const rows=await req('/v1/model-intelligence/leaderboard?profile='+encodeURIComponent(profile));root.innerHTML=rows.length?'':'<p class="muted">Nenhuma rota medida elegível.</p>';rows.forEach((r,i)=>{const d=document.createElement('div');d.className='route';d.innerHTML=`<strong>#${i+1} ${esc(r.provider)} · ${esc(r.model)}</strong><br><span class="badge good">${Number(r.score).toFixed(4)}</span> <span class="muted">${esc(r.lifecycle)}</span><br><code>${esc(r.route_id)}</code>`;root.appendChild(d)})}catch(e){root.innerHTML=`<p class="muted">${esc(e.message)}</p>`}}
async function runBenchmark(routeId){if(!confirm('Executar benchmark textual real nesta rota? A medição textual é independente da visual.'))return;document.body.classList.add('busy');try{await req('/v1/model-intelligence/benchmarks/run',{method:'POST',body:JSON.stringify({route_id:routeId,force:false})});await Promise.all([loadStatus(),loadRoutes(),loadLeaderboard()])}catch(e){alert(e.message)}finally{document.body.classList.remove('busy')}}
function caseLabel(id){return ({'vision-position-color-v1':'Cor + posição','vision-ocr-v1':'OCR','vision-count-v1':'Contagem','vision-direction-v1':'Direção','vision-spatial-relation-v1':'Relação espacial'})[id]||id}
function showVisionEvidence(routeId,r){const section=document.getElementById('visionEvidence'),body=document.getElementById('visionEvidenceBody');const cases=Array.isArray(r.cases)?r.cases:[];const complete=r.suite_complete===true;const score=complete?Number(r.vision_success_rate||0):Number(r.partial_score??r.aggregate_score??0);const attempted=Number(r.attempted_case_count??cases.length);const expected=Number(r.expected_case_count||5);const scored=Number(r.scored_case_count??r.sample_count??0);const headline=complete?`Score visual <span class="badge ${r.promotion_candidate?'good':'warn'}">${score.toFixed(3)}</span>`:`<span class="badge warn">${esc(r.status||'BLOCKED')}</span> · score parcial ${score.toFixed(3)}`;body.innerHTML=`<p><strong>${esc(routeId)}</strong></p><p>${headline} · ${Number(r.passed_count||0)}/${scored} pontuados · ${attempted}/${expected} provas executadas · promoção aplicada: <strong>não</strong></p>${complete?'':`<p class="warn"><strong>Suite incompleta:</strong> falha de provider/rate limit não conta como erro de visão e este score parcial não é gravado como vision_success_rate.</p>`}<p class="muted">${r.replayed_from_storage?'Prova carregada do benchmark salvo. Nenhuma chamada nova ao provider foi feita.':'As respostas abaixo vieram das chamadas que receberam os PNGs gerados pelo benchmark. O runner respeita Retry-After quando a espera cabe no limite seguro.'}</p>`+cases.map(c=>{const scoredCase=c.scored!==false;const icon=scoredCase?(c.passed?'✅':'❌'):'⏸️';const scoreText=scoredCase?`score ${Number(c.score||0).toFixed(3)}`:'não pontuado';const retry=c.retry_after_seconds!=null?` · retry-after ${Number(c.retry_after_seconds).toFixed(1)}s`:'';return `<div class="case"><strong>${icon} ${esc(caseLabel(c.case_id))}</strong> · ${scoreText} · ${Math.round(Number(c.latency_ms||0))} ms · tentativa(s) ${Number(c.attempts||1)}${c.provider_failure?`<br><span class="${scoredCase?'bad':'warn'}">${esc(c.provider_failure)}${retry}</span>`:''}${c.field_results?`<br><span class="muted">campos: ${esc(JSON.stringify(c.field_results))}</span>`:''}${c.result_excerpt?`<pre>${esc(c.result_excerpt)}</pre>`:''}</div>`}).join('');section.classList.remove('hidden');section.scrollIntoView({behavior:'smooth',block:'start'})}
async function loadSavedVisionEvidence(routeId,benchmarkId){document.body.classList.add('busy');try{const r=await req('/v1/model-intelligence/benchmarks/vision/'+encodeURIComponent(benchmarkId)+'/evidence');showVisionEvidence(routeId,r)}catch(e){alert(e.message)}finally{document.body.classList.remove('busy')}}
async function runVisionBenchmark(routeId){if(!confirm('Executar até 5 provas visuais reais? PNGs com OCR, cores, contagem, direção e relações espaciais serão enviados ao provider. Rate limit não vira nota zero: se a suite não completar, ela fica BLOCKED e não certifica visão.'))return;document.body.classList.add('busy');try{const r=await req('/v1/model-intelligence/benchmarks/vision/run',{method:'POST',body:JSON.stringify({route_id:routeId,force:false})});showVisionEvidence(routeId,r);await Promise.all([loadStatus(),loadRoutes(),loadLeaderboard()])}catch(e){alert(e.message)}finally{document.body.classList.remove('busy')}}
let nvidiaDiagHistory=[];
function nvidiaDiagSummary(r){
 const code=r.outcome||'UNKNOWN';
 return ({PASS:'A NVIDIA respondeu e acertou o desafio.',ANSWER_MISMATCH:'A NVIDIA respondeu (HTTP 200), mas a resposta não bate com o gabarito.',AUTH_INVALID:'HTTP 401: chave ausente ou inválida na NVIDIA. Confira se o segredo do Render é uma NVIDIA_API_KEY.',AUTH_FORBIDDEN:'HTTP 403: conta ou chave sem autorização.',PAYMENT_REQUIRED_OR_BALANCE:'HTTP 402: créditos insuficientes. Não repetir nem habilitar rota paga.',RATE_LIMITED:'HTTP 429: limite de taxa atingido; respeite Retry-After.',QUOTA_EXHAUSTED_WINDOW:'Cota da conta atingida. Não é um erro de visão.',REQUEST_INVALID:'HTTP 400/422: o payload não foi aceito pelo endpoint.',MODEL_NOT_FOUND:'HTTP 404: modelo não reconhecido pelo provedor.',OUTPUT_TRUNCATED_IN_REASONING:'HTTP 200: o raciocínio consumiu o orçamento antes da resposta final.',EMPTY_FINAL_ANSWER:'HTTP 200 mas sem resposta final. Verifique finish_reason.',RESPONSE_SCHEMA_MISMATCH:'HTTP 200 mas a estrutura de resposta não segue chat completions.',NON_JSON_HTTP_200:'HTTP 200 mas resposta não é JSON.',NETWORK_ERROR:'Falha de rede antes de receber HTTP da NVIDIA.',TIMEOUT:'Tempo esgotado sem resposta completa.',LOCAL_ADMISSION_DENIED:'A política local impediu a chamada à NVIDIA. Veja configuração e segredo.',COOLDOWN:'Aguarde o intervalo de proteção; nenhuma nova chamada foi feita.',BUSY:'Outro diagnóstico está em andamento; nenhuma nova chamada foi feita.'})[code]||'Verifique outcome, HTTP e finish_reason na evidência.'}
async function loadNvidiaDiagnosticConfig(){
 const el=document.getElementById('nvidiaDiagConfig');if(!el)return;
 try{
  const c=await req('/v1/model-intelligence/diagnostics/nvidia',{cache:'no-store'});
  const flags=[
   ['Rota NVIDIA ativa',c.active_route],['Pool ativo',c.pool_enabled],
   ['Billing declarado free',c.pool_billing==='free'],['Key presente no servidor',c.key_present],
   ['Key com prefixo nvapi-',c.key_looks_like_nvidia],['Key contém Bearer indevido',c.key_has_bearer_prefix],
   ['Key contém espaços externos',c.key_has_outer_whitespace]
  ];
  el.innerHTML='<strong>'+esc(c.provider)+' · '+esc(c.model)+'</strong><p><code>'+esc(c.route_id)+'</code></p>'+flags.map(([k,v])=>'<div>'+esc(k)+': <strong class="'+(v===false&&(/ativa|ativo|free|presente|nvapi/.test(k))?'warn':'')+'">'+esc(v===null?'desconhecido':v?'sim':'não')+'</strong></div>').join('')+'<p class="muted">Configuração = '+esc(c.admission_ready?'admitida para teste':'bloqueada')+'. Nenhuma chamada externa foi feita.</p>';
 }catch(e){el.textContent='Configuração indisponível: '+e.message}
}
function drawNvidiaDiag(){
 const root=document.getElementById('nvidiaDiagResults');if(!root)return;
 root.innerHTML=nvidiaDiagHistory.map(r=>{
  const cls=r.outcome==='PASS'?'good':'warn';
  return '<div class="case"><strong class="'+cls+'">'+esc(r.mode==='vision'?'PNG / visão':'Texto / autenticação')+' · '+esc(r.outcome)+'</strong><p>'+esc(nvidiaDiagSummary(r))+'</p><p class="muted">HTTP NVIDIA: '+esc(r.upstream_status??'sem resposta')+' · ID: <code>'+esc(r.diagnostic_id||'')+'</code></p><pre>'+esc(JSON.stringify(r,null,2))+'</pre></div>'
 }).join('');
}
async function runNvidiaDiagnostic(mode){
 if(!confirm('Enviar uma prova '+(mode==='vision'?'PNG OCR':'de texto')+' fixa ao endpoint gratuito da NVIDIA? Uma chamada real será feita sem alterar o benchmark.'))return;
 const root=document.getElementById('nvidiaDiagResults');document.body.classList.add('busy');
 if(root)root.textContent='Executando apenas a prova '+mode+'…';
 try{const result=await req('/v1/model-intelligence/diagnostics/nvidia/run',{method:'POST',body:JSON.stringify({mode})});nvidiaDiagHistory.unshift(result);nvidiaDiagHistory=nvidiaDiagHistory.slice(0,4);drawNvidiaDiag();await loadNvidiaDiagnosticConfig()}
 catch(e){if(root)root.textContent='Falha do diagnóstico: '+e.message}
 finally{document.body.classList.remove('busy')}
}
async function reconcile(){document.body.classList.add('busy');try{const r=await req('/v1/model-intelligence/reconcile',{method:'POST',body:JSON.stringify({stale_after_seconds:300})});alert(`Reconciliados: ${r.reconciled}`)}catch(e){alert(e.message)}finally{document.body.classList.remove('busy')}}
boot();
</script></body></html>'''