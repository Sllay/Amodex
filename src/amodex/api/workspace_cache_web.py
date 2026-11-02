"""Local-first browser cache augmentation for Workspace V2.

The durable authority remains PostgreSQL. This layer only projects recent private
UI state into browser memory/IndexedDB, restores it immediately, and revalidates
against Neon in the background with explicit ETags.
"""

from __future__ import annotations


_CACHE_PREBOOT_SCRIPT = r'''
const AC_DB_NAME='amodex-private-ui-v1';
const AC_DB_VERSION=1;
const AC_MEMORY=new Map();
let acDbPromise=null;
let acBootReadyResolve;
const acBootReady=new Promise(resolve=>{acBootReadyResolve=resolve});
let acSwReady=Promise.resolve(null);

function acOpenDb(){
  if(acDbPromise)return acDbPromise;
  acDbPromise=new Promise(resolve=>{
    if(!('indexedDB' in window)){resolve(null);return}
    try{
      const req=indexedDB.open(AC_DB_NAME,AC_DB_VERSION);
      req.onupgradeneeded=()=>{
        const db=req.result;
        if(!db.objectStoreNames.contains('snapshots'))db.createObjectStore('snapshots',{keyPath:'key'});
        if(!db.objectStoreNames.contains('meta'))db.createObjectStore('meta',{keyPath:'key'});
      };
      req.onsuccess=()=>resolve(req.result);
      req.onerror=()=>{console.warn('Amodex IndexedDB unavailable',req.error);resolve(null)};
      req.onblocked=()=>console.warn('Amodex IndexedDB upgrade blocked');
    }catch(e){console.warn('Amodex IndexedDB open failed',e);resolve(null)}
  });
  return acDbPromise;
}

async function acStoreGet(storeName,key){
  const db=await acOpenDb();if(!db)return null;
  return new Promise(resolve=>{try{const tx=db.transaction(storeName,'readonly');const req=tx.objectStore(storeName).get(key);req.onsuccess=()=>resolve(req.result||null);req.onerror=()=>resolve(null)}catch{resolve(null)}})
}
async function acStorePut(storeName,value){
  const db=await acOpenDb();if(!db)return;
  return new Promise(resolve=>{try{const tx=db.transaction(storeName,'readwrite');tx.objectStore(storeName).put(value);tx.oncomplete=()=>resolve();tx.onerror=()=>resolve();tx.onabort=()=>resolve()}catch{resolve()}})
}
async function acSnapshotGet(key){
  if(AC_MEMORY.has(key))return AC_MEMORY.get(key);
  const entry=await acStoreGet('snapshots',key);if(entry)AC_MEMORY.set(key,entry);return entry
}
async function acSnapshotPut(key,payload,etag=null){
  const entry={key,payload,etag:etag||null,savedAt:Date.now()};AC_MEMORY.set(key,entry);await acStorePut('snapshots',entry);return entry
}
async function acMetaGet(key){const entry=await acStoreGet('meta',key);return entry?.value??null}
async function acMetaPut(key,value){await acStorePut('meta',{key,value,savedAt:Date.now()})}
async function acClearPrivateData(){
  AC_MEMORY.clear();
  try{const db=await acOpenDb();db?.close()}catch{}
  acDbPromise=null;
  if('indexedDB' in window){try{indexedDB.deleteDatabase(AC_DB_NAME)}catch{}}
  try{const reg=await navigator.serviceWorker?.ready;reg?.active?.postMessage({type:'CLEAR_PRIVATE_CACHE'})}catch{}
}

function acHttpError(status,body,statusText){
  const d=body?.detail;const message=d?.message||d?.code||(typeof d==='string'?d:null)||statusText||`HTTP ${status}`;
  const e=new Error(message);e.code=d?.code||body?.code||`HTTP_${status}`;e.status=status;e.detail=d;e.body=body;return e
}
async function acRevalidate(key,path){
  const cached=await acSnapshotGet(key);
  const headers={accept:'application/json'};
  if(cached?.etag)headers['If-None-Match']=cached.etag;
  const response=await fetch(path,{method:'GET',credentials:'same-origin',cache:'no-store',headers});
  if(response.status===401){await acClearPrivateData();location.href='/auth/passcode';throw acHttpError(401,null,'Sessão expirada')}
  if(response.status===304&&cached){cached.savedAt=Date.now();AC_MEMORY.set(key,cached);void acStorePut('snapshots',cached);return {payload:cached.payload,changed:false,source:'304',entry:cached}}
  let body=null;try{body=await response.json()}catch{}
  if(!response.ok)throw acHttpError(response.status,body,response.statusText);
  const entry=await acSnapshotPut(key,body,response.headers.get('etag'));
  return {payload:body,changed:true,source:'200',entry}
}

function acBadge(text,kind=''){
  const badge=document.getElementById('statusBadge');if(!badge)return;badge.textContent=text;badge.className='badge'+(kind?' '+kind:'')
}
function acOfflineNotice(){
  acBadge('offline','warn');
  const meta=document.getElementById('composerMeta');if(meta&&state.chat){meta.textContent='Modo offline · usando dados locais';meta.className='composer-meta warn'}
}
function acApplyBootstrap(snap){
  if(!snap)return;state.globalWorkspace=snap.global_workspace||state.globalWorkspace;state.globalChats=snap.global_chats||[];state.workspaces=snap.projects||[];state.allEnvironments=state.allEnvironments||[];renderGlobalChats();
  if(state.view==='projects')renderProjectsHub();
}
function acApplyProject(snap){
  if(!snap?.project)return;state.project=snap.project;state.workspace=snap.project;state.projectChats=snap.chats||[];state.memories=snap.memories||[];state.envBindings=snap.bindings||[];state.allEnvironments=snap.environments||[];state.view='project';state.chat=null;state.chatScope='project';state.messages=[];state.drafts=[];chatTitle.textContent=state.project.name;workspaceTitle.textContent=`Projeto · ${state.projectChats.length} chats`;composer.disabled=true;uiSetComposerVisible(false);renderProjectView();renderContext();renderGlobalChats();toggleSide(false)
}
function acApplyChat(snap,scope){
  if(!snap?.chat||!snap?.workspace)return;state.chat=snap.chat;state.workspace=snap.workspace;state.project=scope==='project'?snap.workspace:null;state.messages=snap.messages||[];state.drafts=snap.drafts||[];state.memories=snap.memories||[];state.envBindings=snap.bindings||[];state.allEnvironments=snap.environments||[];state.chatScope=scope;state.view='chat';chatTitle.textContent=state.chat.title;workspaceTitle.textContent=scope==='global'?'Chat global':state.workspace.name;composer.disabled=false;composer.readOnly=false;uiSetComposerVisible(true);renderGlobalChats();renderMessages();renderContext();fillEnvironmentSelects();composerMeta.textContent=scope==='global'?'Contexto global':'Contexto: '+state.workspace.name;requestAnimationFrame(()=>{messages.scrollTop=messages.scrollHeight});toggleSide(false)
}
function acCurrentProjectPayload(){
  if(!state.project||state.project.__pending)return null;return {project:state.project,chats:state.projectChats||[],memories:state.memories||[],bindings:state.envBindings||[],environments:state.allEnvironments||[]}
}
function acCurrentChatPayload(){
  if(!state.chat||state.chat.__pending||!state.workspace)return null;return {chat:state.chat,workspace:state.workspace,messages:state.messages||[],drafts:state.drafts||[],memories:state.memories||[],bindings:state.envBindings||[],environments:state.allEnvironments||[]}
}
async function acPersistBootstrapLocal(){
  if(!state.globalWorkspace)return;await acSnapshotPut('bootstrap',{ready:true,chat_inference:'GATED_BENCHMARK_PENDING',projects:state.workspaces||[],global_workspace:state.globalWorkspace,global_chats:state.globalChats||[]},null)
}
async function acPersistCurrentProject(){const payload=acCurrentProjectPayload();if(payload)await acSnapshotPut(`project:${payload.project.workspace_id}`,payload,null)}
async function acPersistCurrentChat(){const payload=acCurrentChatPayload();if(payload)await acSnapshotPut(`chat:${payload.chat.chat_id}`,payload,null)}
function acTargetForChat(scope){return {view:'chat',chatId:state.chat?.chat_id||null,scope,workspaceId:state.workspace?.workspace_id||state.project?.workspace_id||null,title:state.chat?.title||''}}

async function acRestoreTarget(target){
  if(!target)return false;
  if(target.view==='projects'){openProjectsHub();return true}
  if(target.view==='project'&&target.workspaceId){const entry=await acSnapshotGet(`project:${target.workspaceId}`);if(entry?.payload){acApplyProject(entry.payload);return true}return false}
  if(target.view==='chat'&&target.chatId){
    if(target.scope==='project'&&target.workspaceId){const projectEntry=await acSnapshotGet(`project:${target.workspaceId}`);if(projectEntry?.payload)acApplyProject(projectEntry.payload)}
    const entry=await acSnapshotGet(`chat:${target.chatId}`);if(entry?.payload){acApplyChat(entry.payload,target.scope||'global');return true}
  }
  return false
}
async function acSyncTarget(target){
  if(!target)return;
  try{
    if(target.view==='project'&&target.workspaceId){const result=await acRevalidate(`project:${target.workspaceId}`,`/v1/ui/projects/${target.workspaceId}`);if(state.view==='project'&&state.project?.workspace_id===target.workspaceId)acApplyProject(result.payload);return}
    if(target.view==='chat'&&target.chatId){const result=await acRevalidate(`chat:${target.chatId}`,`/v1/ui/chats/${target.chatId}`);if(state.view==='chat'&&state.chat?.chat_id===target.chatId){acApplyChat(result.payload,target.scope||'global');if(window.AmodexApp&&result.changed)void AmodexApp.hooks.run('afterChatSelected',{app:AmodexApp,state:AmodexApp.state.data,args:[target.chatId,target.scope],result:result.payload})}return}
  }catch(e){if(!navigator.onLine||e instanceof TypeError){acOfflineNotice();return}console.warn('Amodex background cache sync failed',e)}
}

async function acRegisterServiceWorker(){
  if(!('serviceWorker' in navigator))return null;
  try{const reg=await navigator.serviceWorker.register('/sw.js',{scope:'/'});const ready=await navigator.serviceWorker.ready;ready.active?.postMessage({type:'PRIME_SHELL'});return reg}catch(e){console.warn('Amodex service worker unavailable',e);return null}
}

const acBaseOpenProjectsHub=openProjectsHub;
openProjectsHub=function(){const result=acBaseOpenProjectsHub();void acMetaPut('lastTarget',{view:'projects'});return result};

selectWorkspace=async function(id){
  const local=state.workspaces.find(w=>w.workspace_id===id);if(!local||local.__pending)return;
  state.project=local;state.workspace=local;state.projectChats=[];state.view='project';state.chat=null;state.chatScope='project';state.messages=[];state.drafts=[];chatTitle.textContent=local.name;workspaceTitle.textContent='Projeto';composer.disabled=true;uiSetComposerVisible(false);renderProjectView(true);toggleSide(false);void acMetaPut('lastTarget',{view:'project',workspaceId:id});
  const cached=await acSnapshotGet(`project:${id}`);
  if(cached?.payload){acApplyProject(cached.payload);void acRevalidate(`project:${id}`,`/v1/ui/projects/${id}`).then(r=>{if(state.view==='project'&&state.project?.workspace_id===id)acApplyProject(r.payload);acBadge('ready','good')}).catch(e=>{if(!navigator.onLine||e instanceof TypeError)acOfflineNotice();else err(e)});return cached.payload}
  try{const result=await acRevalidate(`project:${id}`,`/v1/ui/projects/${id}`);acApplyProject(result.payload);acBadge('ready','good');return result.payload}catch(e){renderProjectView();if(!navigator.onLine||e instanceof TypeError)acOfflineNotice();else err(e);return null}
};

selectChat=async function(id,forcedScope=null){
  const globalMatch=state.globalChats.find(c=>c.chat_id===id);const projectMatch=state.projectChats.find(c=>c.chat_id===id);const scope=forcedScope||(globalMatch?'global':'project');const local=scope==='global'?globalMatch:projectMatch;if(!local||local.__pending)return;
  state.chat=local;state.chatScope=scope;uiChatLoading(local.title,scope);renderGlobalChats();const target={view:'chat',chatId:id,scope,workspaceId:scope==='project'?(state.project?.workspace_id||state.workspace?.workspace_id||null):(state.globalWorkspace?.workspace_id||null),title:local.title};void acMetaPut('lastTarget',target);
  const cached=await acSnapshotGet(`chat:${id}`);
  if(cached?.payload){
    acApplyChat(cached.payload,scope);
    void acRevalidate(`chat:${id}`,`/v1/ui/chats/${id}`).then(r=>{if(state.view==='chat'&&state.chat?.chat_id===id){acApplyChat(r.payload,scope);if(window.AmodexApp&&r.changed)void AmodexApp.hooks.run('afterChatSelected',{app:AmodexApp,state:AmodexApp.state.data,args:[id,scope],result:r.payload})}acBadge('ready','good')}).catch(e=>{if(!navigator.onLine||e instanceof TypeError)acOfflineNotice();else err(e)});
    return cached.payload
  }
  try{const result=await acRevalidate(`chat:${id}`,`/v1/ui/chats/${id}`);acApplyChat(result.payload,scope);acBadge('ready','good');return result.payload}catch(e){composer.disabled=true;if(!navigator.onLine||e instanceof TypeError)acOfflineNotice();else err(e);return null}
};

const acBaseCreateWorkspace=createWorkspace;
createWorkspace=async function(e){const result=await acBaseCreateWorkspace(e);await acPersistBootstrapLocal();await acPersistCurrentProject();if(state.project?.workspace_id)await acMetaPut('lastTarget',{view:'project',workspaceId:state.project.workspace_id});return result};
const acBaseCreateChat=createChat;
createChat=async function(e){const result=await acBaseCreateChat(e);if(state.chat&&!state.chat.__pending){await acPersistBootstrapLocal();await acPersistCurrentProject();await acPersistCurrentChat();await acMetaPut('lastTarget',acTargetForChat(state.chatScope||'global'))}return result};

const acBaseBoot=boot;
boot=async function(){
  uiInstallShell();uiHome();acSwReady=acRegisterServiceWorker();
  try{
    const cachedBootstrap=await acSnapshotGet('bootstrap');
    if(cachedBootstrap?.payload){acApplyBootstrap(cachedBootstrap.payload);acBadge('cached','good');const inf=document.getElementById('inferenceBadge');if(inf)inf.textContent=cachedBootstrap.payload.chat_inference==='GATED_BENCHMARK_PENDING'?'AI gated':'AI'}
    const target=await acMetaGet('lastTarget');const restored=await acRestoreTarget(target);
    acBootReadyResolve({cached:Boolean(cachedBootstrap),restored,target});
    void acRevalidate('bootstrap','/v1/ui/bootstrap').then(result=>{acApplyBootstrap(result.payload);acBadge('ready','good');const inf=document.getElementById('inferenceBadge');if(inf)inf.textContent=result.payload.chat_inference==='GATED_BENCHMARK_PENDING'?'AI gated':'AI';if(!restored&&state.view==='home')workspaceTitle.textContent='Escolha um chat ou abra Projetos';void acSyncTarget(target)}).catch(e=>{if(cachedBootstrap||restored){acOfflineNotice();return}err(e)});
  }catch(e){acBootReadyResolve({cached:false,restored:false,target:null,error:String(e)});err(e)}
};

(window.__amodexFeatureQueue||(window.__amodexFeatureQueue=[])).push(app=>{
  app.hooks.register('afterSend',async()=>{await acPersistCurrentChat();await acPersistBootstrapLocal();await acPersistCurrentProject()});
  app.hooks.register('afterOwnerSaved',async()=>{await acPersistCurrentChat()});
  queueMicrotask(()=>{if(state.chat)void app.hooks.run('afterChatSelected',{app,state:app.state.data,args:[state.chat.chat_id,state.chatScope],result:state.chat})});
});

window.AmodexLocalCache={
  version:'p0-cache-local-state-v1',
  ready:()=>acBootReady,
  serviceWorkerReady:()=>acSwReady,
  getSnapshot:acSnapshotGet,
  revalidate:acRevalidate,
  clear:acClearPrivateData,
};
'''


def augment_workspace_cache_html(html: str) -> str:
    """Install local-first hydration before the existing boot call."""

    marker = "boot();\n</script>"
    if marker not in html:
        raise ValueError("workspace cache boot marker not found")
    return html.replace(marker, _CACHE_PREBOOT_SCRIPT + "\nboot();\n</script>", 1)
