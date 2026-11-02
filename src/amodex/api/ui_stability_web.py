"""Single browser runtime authority for the composed Amodex web workspace.

Feature augmenters may contribute hooks, but they must not replace shared browser
actions such as sendMessage, selectChat, renderMessages or microphone dispatch.
This module is intentionally installed last and owns the public runtime surface.
"""

from __future__ import annotations


_STABILITY_CSS = r'''
.composer-meta.notice{color:var(--muted)}
.composer-meta.warn{color:var(--warn)}
.composer-meta.good{color:var(--good)}
.composer-round.is-busy{opacity:.48;pointer-events:none}
'''


_STABILITY_SCRIPT = r'''
<script id="amodex-ui-runtime">
(function(){
const hookNames=['beforeSend','afterOwnerSaved','afterSend','afterChatSelected','afterRenderMessages','toggleMic'];
const hookMap=Object.fromEntries(hookNames.map(name=>[name,[]]));
const runtimeState={sending:false,lastNotice:'',lastNoticeAt:0};
const AppState={
  data:state,
  patch(values){Object.assign(state,values||{});return state},
  snapshot(){return {...state}},
  get chat(){return state.chat},
  get workspace(){return state.workspace},
  get view(){return state.view},
};

function registerHook(name,handler){
  if(!hookMap[name])throw new Error(`Unknown Amodex hook: ${name}`);
  if(typeof handler!=='function')throw new TypeError(`Hook ${name} must be a function`);
  hookMap[name].push(handler);
  return ()=>{const i=hookMap[name].indexOf(handler);if(i>=0)hookMap[name].splice(i,1)};
}
async function runHooks(name,context){
  for(const handler of [...(hookMap[name]||[])])await handler(context);
}
function runHooksSync(name,context){
  for(const handler of [...(hookMap[name]||[])]){
    try{const result=handler(context);if(result&&typeof result.then==='function')result.catch(console.warn)}catch(e){console.warn(`hook ${name} failed`,e)}
  }
}
function uiNotice(message,kind='notice'){
  const el=document.getElementById('composerMeta');
  if(!el)return;
  el.textContent=message||'';
  el.className='composer-meta '+kind;
}
async function runtimeApi(path,opt={}){
  const r=await fetch(path,{headers:{'content-type':'application/json',...(opt.headers||{})},...opt});
  let body=null;try{body=await r.json()}catch{}
  if(!r.ok){
    const d=body?.detail;
    const message=d?.message||d?.code||(typeof d==='string'?d:null)||r.statusText||`HTTP ${r.status}`;
    const x=new Error(message);x.code=d?.code||body?.code||`HTTP_${r.status}`;x.status=r.status;x.detail=d;x.body=body;throw x;
  }
  return body;
}
function runtimeError(e){
  const code=e?.code||'';
  if(['NO_MEASURED_ROUTE','BENCHMARK_REQUIRED','STT_NOT_CONFIGURED','STT_CONFIGURATION_INCOMPLETE'].includes(code)){
    uiNotice(e?.message||code,'warn');return;
  }
  const text=e?.message||String(e||'Erro inesperado');const now=Date.now();
  if(runtimeState.lastNotice===text&&now-runtimeState.lastNoticeAt<2500)return;
  runtimeState.lastNotice=text;runtimeState.lastNoticeAt=now;
  const x=document.getElementById('error');if(!x)return;
  x.textContent=text;x.style.display='block';clearTimeout(window.__err);window.__err=setTimeout(()=>x.style.display='none',4200);
}

const baseRenderMessages=renderMessages;
function runtimeRenderMessages(){
  baseRenderMessages();
  runHooksSync('afterRenderMessages',{app:window.AmodexApp,state:AppState.data});
}

const baseSelectChat=selectChat;
async function runtimeSelectChat(...args){
  const result=await baseSelectChat(...args);
  await runHooks('afterChatSelected',{app:window.AmodexApp,state:AppState.data,args,result});
  return result;
}

async function runtimeSendMessage(){
  if(runtimeState.sending||!state.chat)return null;
  const input=document.getElementById('composer');
  const send=document.getElementById('uiSend');
  const text=(input?.value||'').trim();
  const ctx={app:null,state:AppState.data,text,content:text,allowEmpty:false,owner:null,featureData:{}};
  runtimeState.sending=true;
  if(send){send.disabled=true;send.classList.add('is-busy');send.textContent='…'}
  if(input)input.readOnly=true;
  uiNotice('Enviando…');
  try{
    ctx.app=window.AmodexApp;
    await runHooks('beforeSend',ctx);
    if(!ctx.content&&!ctx.allowEmpty){uiNotice('');return null}
    const owner=await runtimeApi(`/v1/chats/${state.chat.chat_id}/messages`,{method:'POST',body:JSON.stringify({role:'OWNER',content:ctx.content})});
    ctx.owner=owner;
    const existing=new Set((state.messages||[]).map(m=>m.message_id));
    if(!existing.has(owner.message_id))state.messages=[...(state.messages||[]),owner];
    await runHooks('afterOwnerSaved',ctx);
    if(input)input.value='';
    runtimeRenderMessages();
    uiNotice('Mensagem salva.','good');
    void runHooks('afterSend',ctx).catch(runtimeError);
    return owner;
  }catch(e){runtimeError(e);return null}
  finally{
    runtimeState.sending=false;
    if(send){send.disabled=false;send.classList.remove('is-busy');send.textContent='⏫'}
    if(input){input.readOnly=false;input.focus()}
  }
}

async function runtimeToggleMic(){
  if(!state.chat){uiNotice('Abra um chat antes de usar o microfone.','warn');return null}
  const handlers=[...(hookMap.toggleMic||[])];
  if(!handlers.length){uiNotice('Microfone indisponível nesta instalação.','warn');return null}
  try{return await handlers[0]({app:window.AmodexApp,state:AppState.data})}catch(e){runtimeError(e);return null}
}

const app={
  version:'2.0-front-authority',
  state:AppState,
  hooks:{register:registerHook,run:runHooks,runSync:runHooksSync},
  actions:{sendMessage:runtimeSendMessage,toggleMic:runtimeToggleMic},
  api:runtimeApi,
  error:runtimeError,
  notice:uiNotice,
  renderMessages:runtimeRenderMessages,
  selectChat:runtimeSelectChat,
};
window.AmodexApp=app;

for(const register of window.__amodexFeatureQueue||[]){try{register(app)}catch(e){console.warn('feature registration failed',e)}}
window.__amodexFeatureQueue=[];

// Compatibility bridges for legacy inline handlers. Only this runtime may bind them.
api=runtimeApi;
err=runtimeError;
renderMessages=runtimeRenderMessages;
selectChat=runtimeSelectChat;
sendMessage=runtimeSendMessage;

const sendButton=document.getElementById('uiSend');if(sendButton)sendButton.onclick=()=>app.actions.sendMessage();
const micButton=document.getElementById('mmMic');if(micButton)micButton.onclick=()=>app.actions.toggleMic();
})();
</script>
'''


def augment_ui_stability_html(html: str) -> str:
    """Install the single public browser runtime after all feature scripts."""
    result = html.replace("</style>", _STABILITY_CSS + "</style>", 1)
    return result.replace("</body>", _STABILITY_SCRIPT + "</body>", 1)
