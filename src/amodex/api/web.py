"""Small dependency-free Stage 6 web surface.

The page is intentionally a projection of `/v1/dashboard` and action endpoints.
It owns no independent state or authority.
"""

DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amodex</title>
<style>
:root{color-scheme:dark;background:#0d1117;color:#e6edf3;font-family:system-ui,-apple-system,sans-serif}
body{margin:0}.shell{max-width:1480px;margin:auto;padding:20px}.top{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap}.pill{border:1px solid #30363d;border-radius:999px;padding:6px 10px;background:#161b22}.ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}h1{margin:.2rem 0}h2{font-size:1rem;margin:0 0 10px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin-top:16px}.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px;min-width:0}.wide{grid-column:1/-1}table{width:100%;border-collapse:collapse;font-size:.86rem}th,td{text-align:left;padding:7px;border-bottom:1px solid #21262d;vertical-align:top;word-break:break-word}th{color:#8b949e}button,input,select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:7px}button{cursor:pointer}button:hover{border-color:#58a6ff}.row{display:flex;gap:7px;flex-wrap:wrap}.muted{color:#8b949e;font-size:.85rem}.scroll{overflow:auto;max-height:420px}.event{padding:7px 0;border-bottom:1px solid #21262d}.error{background:#3d1117;border:1px solid #f85149;padding:10px;border-radius:8px;display:none}.forms{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.form{border:1px solid #30363d;border-radius:8px;padding:10px}.form input,.form select{width:100%;box-sizing:border-box;margin:4px 0 8px}
</style>
</head>
<body><main class="shell">
<div class="top"><div><h1>Amodex</h1><div class="muted">Stage 6 daily-use control surface · API is authoritative</div></div><div class="row"><span id="release" class="pill">release …</span><span id="cost" class="pill">cost …</span><button onclick="refresh()">Refresh</button></div></div>
<div id="error" class="error"></div>
<div class="grid">
<section class="card wide"><h2>Configuration</h2><div class="forms">
<form class="form" onsubmit="addProject(event)"><strong>Add project</strong><input id="repo" placeholder="https://github.com/owner/repo" required><input id="projectName" placeholder="Name (optional)"><button>Add</button></form>
<form class="form" onsubmit="connectProvider(event)"><strong>Provider connection</strong><input id="provider" placeholder="groq" required><input id="pool" placeholder="groq-free-primary" required><input id="secretRef" placeholder="env://GROQ_API_KEY" required><select id="billing"><option>free</option><option>unknown</option><option>paid</option></select><button>Connect reference</button></form>
</div></section>
<section class="card"><h2>Projects</h2><div id="projects" class="scroll"></div></section>
<section class="card"><h2>Runs</h2><div id="runs" class="scroll"></div></section>
<section class="card"><h2>Task DAG / status</h2><div id="tasks" class="scroll"></div></section>
<section class="card"><h2>Provider availability / quota</h2><div id="providers" class="scroll"></div></section>
<section class="card"><h2>Runner status</h2><div id="runners" class="scroll"></div></section>
<section class="card"><h2>Approvals / blockers</h2><div id="approvals" class="scroll"></div><div id="blockers" class="scroll"></div></section>
<section class="card"><h2>Checkpoints / candidates / promotions</h2><div id="gitstate" class="scroll"></div></section>
<section class="card"><h2>Validation evidence / artifacts</h2><div id="evidence" class="scroll"></div></section>
<section class="card wide"><h2>Event / audit timeline</h2><div id="timeline" class="scroll"></div></section>
</div></main>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const table=(rows,cols)=>!rows.length?'<div class="muted">No records</div>':'<table><thead><tr>'+cols.map(c=>'<th>'+esc(c[0])+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+c[1](r)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
async function api(path,opt){const r=await fetch(path,{headers:{'content-type':'application/json'},...opt});let body={};try{body=await r.json()}catch{}if(!r.ok)throw new Error(body.detail?.message||body.detail||r.statusText);return body}
function showError(e){const x=document.getElementById('error');x.textContent=e.message||e;x.style.display='block'}
async function control(id,action){try{await api('/v1/runs/'+id+'/'+action,{method:'POST',body:JSON.stringify({reason:'web owner action'})});await refresh()}catch(e){showError(e)}}
async function approve(id){try{await api('/v1/approvals/'+id+'/approve',{method:'POST'});await refresh()}catch(e){showError(e)}}
async function addProject(e){e.preventDefault();try{await api('/v1/projects',{method:'POST',body:JSON.stringify({repo_url:repo.value,name:projectName.value||null})});e.target.reset();await refresh()}catch(x){showError(x)}}
async function connectProvider(e){e.preventDefault();try{await api('/v1/provider-connections',{method:'POST',body:JSON.stringify({provider:provider.value,credential_pool_id:pool.value,secret_ref:secretRef.value,billing_class:billing.value,enabled:true})});await refresh()}catch(x){showError(x)}}
async function refresh(){try{document.getElementById('error').style.display='none';const d=await api('/v1/dashboard');release.textContent='release '+d.release;cost.textContent=d.zero_cost_mode?'ZERO COST':'PAID ENABLED';cost.className='pill '+(d.zero_cost_mode?'ok':'bad');
projects.innerHTML=table(d.projects,[['Name',r=>esc(r.name)],['Repo',r=>esc(r.repo_url)],['Status',r=>esc(r.status)]]);
runs.innerHTML=table(d.runs,[['Run',r=>esc(r.run_id)],['State',r=>esc(r.state)],['Provider',r=>esc(r.provider||'—')],['Actions',r=>`<div class="row"><button onclick="control('${esc(r.run_id)}','pause')">Pause</button><button onclick="control('${esc(r.run_id)}','resume')">Resume</button><button onclick="control('${esc(r.run_id)}','cancel')">Cancel</button></div>`]]);
tasks.innerHTML=table(d.tasks,[['Task',r=>esc(r.task_id)],['State',r=>esc(r.state)],['Profile',r=>esc(r.worker_profile||'—')],['Dependencies',r=>esc((r.dependencies||[]).join(', '))]]);
providers.innerHTML=table(d.providers,[['Provider',r=>esc(r.provider)],['Pool',r=>esc(r.credential_pool_id||'—')],['Billing',r=>esc(r.billing_class)],['Health',r=>esc(r.health||r.connection_state||'—')],['Quota',r=>r.quota_available===undefined?'—':esc(r.quota_available)]]);
runners.innerHTML=table(d.runners,[['Runner',r=>esc(r.runner_id)],['Profile',r=>esc(r.profile)],['State',r=>esc(r.state)],['Heartbeat',r=>esc(r.last_heartbeat_at)]]);
approvals.innerHTML=table(d.approvals,[['Action',r=>esc(r.action)],['Impact',r=>esc(r.impact)],['Status',r=>esc(r.status)],['',r=>r.status==='PENDING'?`<button onclick="approve('${esc(r.approval_id)}')">Approve</button>`:'']]);
blockers.innerHTML=table(d.blockers,[['Blocker',r=>esc(r.kind||r.type||'blocker')],['Detail',r=>esc(r.detail||r.reason||r.message||JSON.stringify(r))]]);
gitstate.innerHTML='<strong>Checkpoints</strong>'+table(d.checkpoints,[['ID',r=>esc(r.checkpoint_id)],['Class',r=>esc(r.checkpoint_class)],['SHA',r=>esc(r.commit_sha)]])+'<strong>Candidates</strong>'+table(d.candidates,[['ID',r=>esc(r.candidate_id)],['Status',r=>esc(r.status)],['Source',r=>esc(r.source_sha)]])+'<strong>Promotions</strong>'+table(d.promotions,[['ID',r=>esc(r.promotion_id)],['Status',r=>esc(r.status)],['Target',r=>esc(r.target_post_sha||'—')]]);
evidence.innerHTML=table(d.evidence,[['Validator',r=>esc(r.validator_id)],['Result',r=>esc(r.result)],['Summary',r=>esc(r.summary)],['Artifacts',r=>esc((r.artifact_sha256||[]).join(', '))]]);
timeline.innerHTML=d.timeline.length?d.timeline.map(e=>`<div class="event"><strong>${esc(e.action||e.kind)}</strong> <span class="muted">${esc(e.created_at)}</span><br>${esc(e.subject_type||'')} ${esc(e.subject_id||'')} ${esc(JSON.stringify(e.detail||e.payload||{}))}</div>`).join(''):'<div class="muted">No events</div>';
}catch(e){showError(e)}}
refresh();setInterval(refresh,10000);
</script></body></html>'''
