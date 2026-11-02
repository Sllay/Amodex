"""HTML augmentation for explicit Step 3 multimodal actions."""

from __future__ import annotations


_MULTIMODAL_CSS = r'''
.mm-tray{display:flex;flex-wrap:wrap;gap:8px;padding:7px 2px 2px}.mm-chip{display:flex;align-items:center;gap:7px;max-width:100%;border:1px solid var(--line);background:var(--panel2);border-radius:10px;padding:7px 9px;font-size:.78rem}.mm-chip span{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.mm-chip button{padding:3px 7px}.mm-media{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px}.mm-card{border:1px solid var(--line);border-radius:12px;background:#0e1319;padding:8px;max-width:360px}.mm-card img{display:block;max-width:100%;max-height:280px;border-radius:8px;margin-bottom:7px}.mm-card audio{width:min(320px,100%);display:block;margin:5px 0}.mm-card .mm-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}.mm-card .mm-actions button{padding:5px 8px;font-size:.75rem}.mm-busy{opacity:.6;pointer-events:none}.mm-recording{border-color:var(--bad)!important;color:var(--bad)}
'''


_MULTIMODAL_SCRIPT = r'''
<script id="amodex-multimodal-feature">
const mm={status:null,pending:[],links:[],operations:[],recorder:null,chunks:[],stream:null};
function mmQueueFeature(register){if(window.AmodexApp)return register(window.AmodexApp);(window.__amodexFeatureQueue||(window.__amodexFeatureQueue=[])).push(register)}
async function mmApi(path,opt={}){return window.AmodexApp?AmodexApp.api(path,opt):api(path,opt)}
function mmReport(e){return window.AmodexApp?AmodexApp.error(e):err(e)}
async function mmBoot(){
  try{
    mm.status=await mmApi('/v1/multimodal/status');
    const b=document.getElementById('mmBadge');
    if(b){const upload=mm.status.binary_upload==='READY';b.textContent=upload?'media ready':'media config';b.className='badge '+(upload?'good':'warn')}
  }catch(e){console.warn('multimodal unavailable',e)}
}
function mmPendingHtml(a){const icon=a.media_type.startsWith('image/')?'🖼️':a.media_type.startsWith('audio/')?'🎙️':'📎';return `<div class="mm-chip">${icon}<span>${esc(a.name)}</span><button onclick="mmRemovePending('${esc(a.attachment_id)}')">✕</button></div>`}
function mmRenderPending(){const tray=document.getElementById('mmTray');if(tray)tray.innerHTML=mm.pending.map(mmPendingHtml).join('')}
function mmRemovePending(id){mm.pending=mm.pending.filter(a=>a.attachment_id!==id);mmRenderPending()}
async function mmChooseFiles(){if(!state.chat)return mmReport(new Error('Selecione um chat'));document.getElementById('mmFileInput').click()}
async function mmFilesSelected(input){for(const file of [...input.files]){try{await mmUploadFile(file)}catch(e){mmReport(e)}}input.value=''}
async function mmUploadFile(file){
  if(!state.chat)throw new Error('Selecione um chat');const max=mm.status?.max_upload_bytes||26214400;if(file.size>max)throw new Error(`Arquivo maior que o limite (${Math.round(max/1024/1024)} MB)`);
  const r=await fetch(`/v1/chats/${state.chat.chat_id}/uploads`,{method:'POST',headers:{'content-type':file.type||'application/octet-stream','x-file-name':encodeURIComponent(file.name||'upload.bin')},body:file});let body=null;try{body=await r.json()}catch{}if(!r.ok){const d=body?.detail;throw new Error(d?.message||d?.code||r.statusText)}mm.pending.push(body);mmRenderPending();return body
}
async function mmRefreshChat(){
  if(!state.chat){mm.links=[];mm.operations=[];return}
  try{[mm.links,mm.operations]=await Promise.all([mmApi(`/v1/chats/${state.chat.chat_id}/attachment-links`),mmApi(`/v1/chats/${state.chat.chat_id}/multimodal-operations`)])}catch(e){mm.links=[];mm.operations=[];console.warn(e)}
}
function mmLinksFor(messageId){return mm.links.filter(x=>x.message_id===messageId).map(x=>x.attachment)}
function mmAttachmentCard(a){const src=`/v1/attachments/${encodeURIComponent(a.attachment_id)}/content`;let preview='';let actions='';if(a.media_type.startsWith('image/')){preview=`<img loading="lazy" src="${src}" alt="${esc(a.name)}">`;actions+=`<button onclick="mmAnalyzeImage('${esc(a.attachment_id)}')">Analisar imagem</button>`}else if(a.media_type.startsWith('audio/')){preview=`<audio controls preload="metadata" src="${src}"></audio>`;actions+=`<button onclick="mmTranscribe('${esc(a.attachment_id)}')">Transcrever</button>`}else{actions+=`<button onclick="window.open('${src}','_blank')">Abrir arquivo</button>`}return `<div class="mm-card">${preview}<div><strong>${esc(a.name)}</strong></div><div class="muted">${esc(a.media_type)} · ${Math.ceil(a.size_bytes/1024)} KB</div><div class="mm-actions">${actions}</div></div>`}
function mmDecorateMessages(){if(!state.messages?.length)return;const nodes=[...messages.querySelectorAll('.message')];state.messages.forEach((m,index)=>{const attachments=mmLinksFor(m.message_id);const node=nodes[index];if(!node)return;const bubble=node.querySelector('.bubble');if(!bubble)return;if(attachments.length){const media=document.createElement('div');media.className='mm-media';media.innerHTML=attachments.map(mmAttachmentCard).join('');bubble.appendChild(media)}if(m.role!=='OWNER'){const actions=document.createElement('div');actions.className='mm-actions';actions.style.marginTop='7px';const speak=document.createElement('button');speak.textContent='🔊 Ouvir';speak.addEventListener('click',()=>mmSpeak(m.content));actions.appendChild(speak);bubble.appendChild(actions)}});messages.scrollTop=messages.scrollHeight}
async function mmAnalyzeImage(id){let prompt=composer.value.trim();if(!prompt)prompt='Descreva e analise esta imagem de forma objetiva, destacando o que for relevante para o projeto.';try{await mmApi(`/v1/attachments/${id}/vision`,{method:'POST',body:JSON.stringify({prompt})});const snap=await mmApi(`/v1/ui/chats/${state.chat.chat_id}`);state.messages=snap.messages||[];await mmRefreshChat();renderMessages()}catch(e){mmReport(e)}}
async function mmTranscribe(id){try{const op=await mmApi(`/v1/attachments/${id}/transcribe`,{method:'POST',body:JSON.stringify({language:'pt'})});if(op.result_text){composer.value=(composer.value?composer.value+'\n':'')+op.result_text;composer.focus()}mmRefreshChat().catch(console.warn)}catch(e){mmReport(e)}}
async function mmResearch(){if(!state.chat)return mmReport(new Error('Selecione um chat'));const query=composer.value.trim();if(!query)return mmReport(new Error('Digite o que deseja pesquisar'));try{await mmApi(`/v1/chats/${state.chat.chat_id}/research`,{method:'POST',body:JSON.stringify({query,max_results:5,language:'pt-BR'})});const snap=await mmApi(`/v1/ui/chats/${state.chat.chat_id}`);state.messages=snap.messages||[];renderMessages();mmRefreshChat().then(()=>renderMessages()).catch(console.warn)}catch(e){mmReport(e)}}
function mmBrowserSpeak(text){if(!('speechSynthesis' in window))return mmReport(new Error('TTS não disponível neste navegador'));speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang='pt-BR';speechSynthesis.speak(u)}
async function mmSpeak(text){if(!state.chat)return;try{if(mm.status?.server_tts?.ready){const op=await mmApi(`/v1/chats/${state.chat.chat_id}/speech`,{method:'POST',body:JSON.stringify({text,voice:null})});if(op.output_attachment_id){const audio=new Audio(`/v1/attachments/${op.output_attachment_id}/content`);await audio.play();return}}mmBrowserSpeak(text)}catch(e){console.warn(e);mmBrowserSpeak(text)}}
function mmBestAudioType(){const choices=['audio/webm;codecs=opus','audio/mp4','audio/webm'];for(const type of choices){if(window.MediaRecorder?.isTypeSupported?.(type))return type}return ''}
async function mmToggleMic(){const btn=document.getElementById('mmMic');if(mm.recorder&&mm.recorder.state==='recording'){mm.recorder.stop();btn?.classList.remove('mm-recording');if(btn)btn.textContent='🎤';return}if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('Gravação não suportada neste navegador');mm.stream=await navigator.mediaDevices.getUserMedia({audio:true});const type=mmBestAudioType();mm.chunks=[];mm.recorder=new MediaRecorder(mm.stream,type?{mimeType:type}:undefined);mm.recorder.ondataavailable=e=>{if(e.data?.size)mm.chunks.push(e.data)};mm.recorder.onstop=async()=>{try{const mime=mm.recorder.mimeType||type||'audio/webm';const ext=mime.includes('mp4')?'m4a':'webm';const blob=new Blob(mm.chunks,{type:mime});const file=new File([blob],`gravacao-${Date.now()}.${ext}`,{type:mime});const attachment=await mmUploadFile(file);if(mm.status?.stt?.ready)await mmTranscribe(attachment.attachment_id)}catch(e){mmReport(e)}finally{mm.stream?.getTracks().forEach(t=>t.stop());mm.stream=null;mm.recorder=null;mm.chunks=[]}};mm.recorder.start();btn?.classList.add('mm-recording');if(btn)btn.textContent='⏹'}
const mmNativeToggleMic=mmToggleMic;

mmQueueFeature(app=>{
  app.hooks.register('beforeSend',async ctx=>{const pending=[...mm.pending];if(!ctx.text&&pending.length){ctx.content='Arquivo anexado.';ctx.allowEmpty=true}ctx.featureData.multimodal={attachmentIds:pending.map(a=>a.attachment_id)}});
  app.hooks.register('afterOwnerSaved',async ctx=>{const ids=ctx.featureData.multimodal?.attachmentIds||[];if(!ids.length)return;await app.api(`/v1/messages/${ctx.owner.message_id}/attachments`,{method:'POST',body:JSON.stringify({attachment_ids:ids})});const used=new Set(ids);mm.pending=mm.pending.filter(a=>!used.has(a.attachment_id));mmRenderPending();await mmRefreshChat()});
  app.hooks.register('afterChatSelected',async()=>{mm.pending=[];mmRenderPending();await mmRefreshChat();renderMessages()});
  app.hooks.register('afterRenderMessages',()=>mmDecorateMessages());
  app.hooks.register('toggleMic',async()=>{if(!state.chat){app.notice('Abra um chat antes de usar o microfone.','warn');return}if(!mm.status)await mmBoot();if(!mm.status?.stt?.ready){const reason=mm.status?.stt?.reason||'STT_NOT_CONFIGURED';app.notice(`🎤 Transcrição indisponível: ${reason}. Configure o STT gratuito no Render.`,'warn');return}const input=document.getElementById('composer');const stopping=Boolean(mm.recorder&&mm.recorder.state==='recording');if(!stopping&&input){input.readOnly=true;input.placeholder='Gravando… toque 🎤 para finalizar';app.notice('Gravando áudio — toque no microfone para finalizar.')}try{await mmNativeToggleMic()}catch(e){app.error(e)}if(stopping&&input){input.readOnly=false;input.placeholder='Digite aqui…';input.focus()}})
});
mmBoot();
</script>
'''


def augment_workspace_html(html: str) -> str:
    """Add Step 3 controls without replacing the stable Step 1 workspace shell."""
    css_marker = "</style>"
    controls_marker = '<div class="left-actions"><button class="subtle" onclick="openMemoryModal()"'
    controls = (
        '<div class="left-actions">'
        '<input id="mmFileInput" type="file" multiple hidden onchange="mmFilesSelected(this)">'
        '<button class="subtle" onclick="mmChooseFiles()" title="Anexar arquivo">📎 Arquivo</button>'
        '<button id="mmMic" class="subtle" onclick="mmToggleMic()" title="Gravar áudio">🎙</button>'
        '<button class="subtle" onclick="mmResearch()" title="Pesquisar na web">🌐 Pesquisar</button>'
        '<button class="subtle" onclick="openMemoryModal()"'
    )
    badge_marker = '<span id="inferenceBadge" class="badge warn">AI gated</span>'
    tray_marker = '<div class="warning">Inferência da Amodex continua bloqueada'

    result = html.replace(css_marker, _MULTIMODAL_CSS + css_marker, 1)
    if controls_marker in result:
        result = result.replace(controls_marker, controls, 1)
    if badge_marker in result:
        result = result.replace(
            badge_marker,
            '<span id="mmBadge" class="badge warn">media</span>' + badge_marker,
            1,
        )
    if tray_marker in result:
        result = result.replace(
            tray_marker,
            '<div id="mmTray" class="mm-tray"></div>' + tray_marker,
            1,
        )
    return result.replace("</body>", _MULTIMODAL_SCRIPT + "</body>", 1)
