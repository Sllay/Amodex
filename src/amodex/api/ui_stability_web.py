"""Final browser stability layer for the composed Amodex web workspace.

This script runs after Workspace, multimodal and model-intelligence augmentations.
It owns the final send/microphone actions so stacked feature wrappers cannot cause
duplicate message writes or expose expected capability-gating as fatal UI errors.
"""

from __future__ import annotations


_STABILITY_CSS = r'''
.composer-meta.notice{color:var(--muted)}
.composer-meta.warn{color:var(--warn)}
.composer-meta.good{color:var(--good)}
.composer-round.is-busy{opacity:.48;pointer-events:none}
'''


_STABILITY_SCRIPT = r'''
<script id="amodex-ui-stability">
const uiStable={sending:false,lastNotice:'',lastNoticeAt:0};

function uiNotice(message,kind='notice'){
  const el=document.getElementById('composerMeta');
  if(!el)return;
  el.textContent=message||'';
  el.className='composer-meta '+kind;
}

api=async function(path,opt={}){
  const r=await fetch(path,{headers:{'content-type':'application/json',...(opt.headers||{})},...opt});
  let body=null;try{body=await r.json()}catch{}
  if(!r.ok){
    const d=body?.detail;
    const message=d?.message||d?.code||(typeof d==='string'?d:null)||r.statusText||`HTTP ${r.status}`;
    const x=new Error(message);
    x.code=d?.code||body?.code||`HTTP_${r.status}`;
    x.status=r.status;x.detail=d;x.body=body;
    throw x;
  }
  return body;
};

err=function(e){
  const code=e?.code||'';
  if(['NO_MEASURED_ROUTE','BENCHMARK_REQUIRED','STT_NOT_CONFIGURED','STT_CONFIGURATION_INCOMPLETE'].includes(code)){
    uiNotice(e?.message||code,'warn');
    return;
  }
  const text=e?.message||String(e||'Erro inesperado');
  const now=Date.now();
  if(uiStable.lastNotice===text&&now-uiStable.lastNoticeAt<2500)return;
  uiStable.lastNotice=text;uiStable.lastNoticeAt=now;
  const x=document.getElementById('error');if(!x)return;
  x.textContent=text;x.style.display='block';clearTimeout(window.__err);
  window.__err=setTimeout(()=>x.style.display='none',4200);
};

async function uiAutomaticReply(owner){
  if(!owner||!state.chat)return;
  try{
    let status=(typeof mi!=='undefined'&&mi.status)||null;
    if(!status){
      try{status=await api('/v1/model-intelligence/status');if(typeof mi!=='undefined')mi.status=status}catch{}
    }
    if(status?.automatic_chat_inference!=='READY'){
      uiNotice('Mensagem salva · resposta automática aguardando benchmark de uma rota gratuita.','warn');
      return;
    }
    document.querySelector('.composer-wrap')?.classList.add('mi-busy');
    await api(`/v1/chats/${state.chat.chat_id}/respond`,{method:'POST',body:JSON.stringify({owner_message_id:owner.message_id,profile:null})});
    state.messages=await api(`/v1/chats/${state.chat.chat_id}/messages`);
    renderMessages();
    uiNotice('Resposta concluída.','good');
    if(typeof mmRefreshChat==='function')Promise.resolve(mmRefreshChat()).then(()=>renderMessages()).catch(console.warn);
    if(typeof miBoot==='function')Promise.resolve(miBoot()).catch(console.warn);
  }catch(e){
    if(e?.code==='NO_MEASURED_ROUTE'||e?.code==='BENCHMARK_REQUIRED'){
      uiNotice('Mensagem salva · nenhuma rota gratuita medida está elegível ainda.','warn');
    }else if(e?.code==='DECISION_ALREADY_EXISTS'){
      console.warn('model decision already exists',e);
    }else{
      err(e);
    }
  }finally{
    document.querySelector('.composer-wrap')?.classList.remove('mi-busy');
  }
}

sendMessage=async function(){
  if(uiStable.sending||!state.chat)return null;
  const text=(document.getElementById('composer')?.value||'').trim();
  const pending=(typeof mm!=='undefined'&&Array.isArray(mm.pending))?mm.pending:[];
  if(!text&&!pending.length)return null;
  uiStable.sending=true;
  const send=document.getElementById('uiSend');
  const input=document.getElementById('composer');
  if(send){send.disabled=true;send.classList.add('is-busy');send.textContent='…'}
  if(input)input.readOnly=true;
  uiNotice('Enviando…');
  try{
    const content=text||'Arquivo anexado.';
    const owner=await api(`/v1/chats/${state.chat.chat_id}/messages`,{method:'POST',body:JSON.stringify({role:'OWNER',content})});
    if(pending.length){
      await api(`/v1/messages/${owner.message_id}/attachments`,{method:'POST',body:JSON.stringify({attachment_ids:pending.map(a=>a.attachment_id)})});
      mm.pending=[];if(typeof mmRenderPending==='function')mmRenderPending();
    }
    if(input)input.value='';
    const existing=new Set((state.messages||[]).map(m=>m.message_id));
    if(!existing.has(owner.message_id))state.messages=[...(state.messages||[]),owner];
    renderMessages();
    uiNotice('Mensagem salva.','good');
    if(typeof mmRefreshChat==='function')Promise.resolve(mmRefreshChat()).then(()=>renderMessages()).catch(console.warn);
    void uiAutomaticReply(owner);
    return owner;
  }catch(e){
    err(e);return null;
  }finally{
    uiStable.sending=false;
    if(send){send.disabled=false;send.classList.remove('is-busy');send.textContent='⏫'}
    if(input){input.readOnly=false;input.focus()}
  }
};

if(typeof mmToggleMic==='function'){
  const uiNativeMic=mmToggleMic;
  mmToggleMic=async function(){
    if(!state.chat){uiNotice('Abra um chat antes de usar o microfone.','warn');return}
    if(!mm.status&&typeof mmBoot==='function')await mmBoot();
    if(!mm.status?.stt?.ready){
      const reason=mm.status?.stt?.reason||'STT_NOT_CONFIGURED';
      uiNotice(`🎤 Transcrição indisponível: ${reason}. Configure o STT gratuito no Render.`,'warn');
      return;
    }
    try{return await uiNativeMic()}catch(e){err(e)}
  };
}
</script>
'''


def augment_ui_stability_html(html: str) -> str:
    """Install the final browser action authority after all feature scripts."""
    result = html.replace("</style>", _STABILITY_CSS + "</style>", 1)
    return result.replace("</body>", _STABILITY_SCRIPT + "</body>", 1)
