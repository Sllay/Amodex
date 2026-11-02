"""Step 4 web augmentation and model benchmark dashboard."""

from __future__ import annotations


_STEP4_CSS = r'''
.mi-ready{border-color:var(--good)!important}.mi-busy{opacity:.62;pointer-events:none}.mi-note{font-size:.76rem;color:var(--muted);line-height:1.4}
'''

_STEP4_SCRIPT = r'''
<script id="amodex-model-intelligence-feature">
const mi={status:null};
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
async function miAutomaticReply(app,ctx){
  const owner=ctx?.owner;if(!owner||!state.chat)return;
  try{
    if(!mi.status)await miBoot();
    if(mi.status?.automatic_chat_inference!=='READY'){
      app.notice('Mensagem salva · resposta automática aguardando benchmark de uma rota gratuita.','warn');
      return;
    }
    const footer=document.querySelector('.composer-wrap');footer?.classList.add('mi-busy');
    await app.api(`/v1/chats/${state.chat.chat_id}/respond`,{method:'POST',body:JSON.stringify({owner_message_id:owner.message_id,profile:null})});
    state.messages=await app.api(`/v1/chats/${state.chat.chat_id}/messages`);
    if(typeof mmRefreshChat==='function')await mmRefreshChat();
    renderMessages();
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
miQueueFeature(app=>app.hooks.register('afterSend',ctx=>miAutomaticReply(app,ctx)));
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
:root{color-scheme:dark;--bg:#0b0f14;--panel:#111821;--panel2:#171f2a;--line:#2b3644;--text:#edf3fa;--muted:#9aa9b8;--good:#55d18b;--warn:#f2c14e;--bad:#ff6b6b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}header{position:sticky;top:0;background:rgba(11,15,20,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:10px;align-items:center;z-index:2}header strong{font-size:1.05rem}header .grow{flex:1}.wrap{max-width:1100px;margin:auto;padding:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}.route{margin:9px 0;padding:10px;border:1px solid var(--line);border-radius:11px;background:var(--panel2)}button,select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px 10px}button{cursor:pointer}.primary{border-color:#5e8cff}.badge{display:inline-block;padding:3px 7px;border:1px solid var(--line);border-radius:999px;font-size:.72rem}.good{color:var(--good);border-color:var(--good)}.warn{color:var(--warn);border-color:var(--warn)}.bad{color:var(--bad);border-color:var(--bad)}.muted{color:var(--muted)}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.busy{opacity:.58;pointer-events:none}code{overflow-wrap:anywhere}@media(max-width:600px){header{padding:10px}.wrap{padding:10px}.grid{grid-template-columns:1fr}}
</style></head><body>
<header><button onclick="location.href='/'">← Workspace</button><strong>Model Intelligence</strong><span id="status" class="badge warn">loading</span><span class="grow"></span><button onclick="reconcile()">Reconciliar UNKNOWN</button></header>
<div class="wrap"><div class="grid"><section class="card"><h2>Rotas</h2><p class="muted">Benchmark é explícito e consome quota gratuita. Chave presente nunca autoriza gasto pago.</p><div id="routes"></div></section><section class="card"><h2>Leaderboard</h2><div class="row"><select id="profile" onchange="loadLeaderboard()"><option value="repository-analyst">Repository analyst</option><option value="research">Research</option><option value="implementation">Implementation</option><option value="correction">Correction</option><option value="reviewer">Reviewer</option><option value="integration-migration">Integration</option><option value="godot-specialist">Godot</option><option value="visual-runtime-validation">Visual/runtime</option></select></div><div id="leaderboard"></div></section></div></div>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}
async function req(path,opt={}){const r=await fetch(path,{headers:{'content-type':'application/json',...(opt.headers||{})},...opt});let b=null;try{b=await r.json()}catch{}if(!r.ok){const d=b?.detail;throw new Error(d?.code||d?.message||r.statusText)}return b}
async function boot(){await Promise.all([loadStatus(),loadRoutes(),loadLeaderboard()])}
async function loadStatus(){try{const s=await req('/v1/model-intelligence/status');const el=document.getElementById('status');const ready=s.automatic_chat_inference==='READY';el.textContent=ready?'AI routed':'benchmark needed';el.className='badge '+(ready?'good':'warn')}catch(e){console.warn(e)}}
async function loadRoutes(){const rows=await req('/v1/model-intelligence/routes');const root=document.getElementById('routes');root.innerHTML='';for(const r of rows){const card=document.createElement('div');card.className='route';const top=document.createElement('div');top.innerHTML=`<strong>${esc(r.provider)} · ${esc(r.model)}</strong><br><code>${esc(r.route_id)}</code><br><span class="badge">${esc(r.lifecycle)}</span> <span class="muted">${esc(r.cost_class)}</span>`;card.appendChild(top);const b=r.latest_benchmark;if(b){const p=document.createElement('p');p.className='muted';p.textContent=`último benchmark: ${b.status} · score ${Number(b.aggregate_score||0).toFixed(3)} · ${b.sample_count||0} amostras`;card.appendChild(p)}const btn=document.createElement('button');btn.textContent='Rodar benchmark';btn.addEventListener('click',()=>runBenchmark(r.route_id));card.appendChild(btn);root.appendChild(card)}}
async function loadLeaderboard(){const root=document.getElementById('leaderboard');root.innerHTML='<p class="muted">Carregando…</p>';try{const profile=document.getElementById('profile').value;const rows=await req('/v1/model-intelligence/leaderboard?profile='+encodeURIComponent(profile));root.innerHTML=rows.length?'':'<p class="muted">Nenhuma rota medida elegível.</p>';rows.forEach((r,i)=>{const d=document.createElement('div');d.className='route';d.innerHTML=`<strong>#${i+1} ${esc(r.provider)} · ${esc(r.model)}</strong><br><span class="badge good">${Number(r.score).toFixed(4)}</span> <span class="muted">${esc(r.lifecycle)}</span><br><code>${esc(r.route_id)}</code>`;root.appendChild(d)})}catch(e){root.innerHTML=`<p class="muted">${esc(e.message)}</p>`}}
async function runBenchmark(routeId){if(!confirm('Executar benchmark real nesta rota? Isso fará chamadas externas e consumirá quota classificada como gratuita.'))return;document.body.classList.add('busy');try{await req('/v1/model-intelligence/benchmarks/run',{method:'POST',body:JSON.stringify({route_id:routeId,force:false})});await Promise.all([loadStatus(),loadRoutes(),loadLeaderboard()])}catch(e){alert(e.message)}finally{document.body.classList.remove('busy')}}
async function reconcile(){document.body.classList.add('busy');try{const r=await req('/v1/model-intelligence/reconcile',{method:'POST',body:JSON.stringify({stale_after_seconds:300})});alert(`Reconciliados: ${r.reconciled}`)}catch(e){alert(e.message)}finally{document.body.classList.remove('busy')}}
boot();
</script></body></html>'''
