"""Service-worker surface for the private Amodex workspace shell.

The worker may cache the authenticated application shell and same-origin static
assets, but it never caches API or auth responses. Durable private chat/project
payloads live only in IndexedDB through the browser cache layer.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

_SERVICE_WORKER = r'''const SHELL_CACHE='amodex-private-shell-p0-v1';
const STATIC_RE=/\.(?:css|js|svg|png|webp|jpg|jpeg|gif|ico|woff2?)$/i;

self.addEventListener('install',event=>{event.waitUntil(self.skipWaiting())});
self.addEventListener('activate',event=>{
  event.waitUntil((async()=>{
    const names=await caches.keys();
    await Promise.all(names.filter(name=>name.startsWith('amodex-private-shell-')&&name!==SHELL_CACHE).map(name=>caches.delete(name)));
    await self.clients.claim();
  })());
});

async function primeShell(){
  try{
    const response=await fetch(new Request('/',{credentials:'same-origin',cache:'no-store'}));
    if(response.ok&&response.headers.get('content-type')?.includes('text/html')){
      const cache=await caches.open(SHELL_CACHE);
      await cache.put('/',response.clone());
    }
  }catch{}
}

self.addEventListener('message',event=>{
  if(event.data?.type==='PRIME_SHELL')event.waitUntil(primeShell());
  if(event.data?.type==='CLEAR_PRIVATE_CACHE')event.waitUntil(caches.delete(SHELL_CACHE));
});

function offlineHtml(){
  return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Amodex offline</title><style>body{font-family:system-ui;background:#090b10;color:#f5f5f5;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}main{max-width:420px}p{color:#aab3c2;line-height:1.5}</style><main><h1>Amodex</h1><p>O shell ainda não foi preparado para uso offline neste navegador. Reconecte uma vez para inicializar o cache privado.</p></main>',{status:503,headers:{'content-type':'text/html; charset=utf-8','cache-control':'no-store'}});
}

self.addEventListener('fetch',event=>{
  const request=event.request;
  if(request.method!=='GET')return;
  const url=new URL(request.url);
  if(url.origin!==self.location.origin)return;
  if(url.pathname.startsWith('/v1/')||url.pathname.startsWith('/auth/')||url.pathname==='/sw.js')return;

  if(request.mode==='navigate'){
    event.respondWith((async()=>{
      try{
        const response=await fetch(request);
        if(response.ok&&url.pathname==='/'){
          const cache=await caches.open(SHELL_CACHE);
          await cache.put('/',response.clone());
        }
        return response;
      }catch{
        const cached=await caches.match('/');
        return cached||offlineHtml();
      }
    })());
    return;
  }

  if(STATIC_RE.test(url.pathname)){
    event.respondWith((async()=>{
      const cached=await caches.match(request);
      if(cached)return cached;
      const response=await fetch(request);
      if(response.ok){const cache=await caches.open(SHELL_CACHE);await cache.put(request,response.clone())}
      return response;
    })());
  }
});
'''


def build_workspace_cache_router() -> APIRouter:
    router = APIRouter(tags=["workspace-cache"])

    @router.get("/sw.js", include_in_schema=False)
    def service_worker() -> Response:
        return Response(
            content=_SERVICE_WORKER,
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Service-Worker-Allowed": "/",
            },
        )

    return router
