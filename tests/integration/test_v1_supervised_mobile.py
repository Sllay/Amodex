"""Real Chromium mobile viewport against HTTP product routes; model simulated."""
import os
from pathlib import Path
import socket
import threading
import time

from playwright.sync_api import sync_playwright
import pytest
import uvicorn

from test_v1_supervised_execution import database, h


@pytest.mark.parametrize('blocked',[False,True],ids=['simulated-model','credential-blocked'])
def test_mobile_review_approve_lost_ack_and_durable_result(h,monkeypatch,blocked):
    if blocked:monkeypatch.delenv('AMODEX_TEST_PROVIDER_KEY')
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(h.client.app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
    deadline=time.monotonic()+5
    while not server.started and time.monotonic()<deadline:time.sleep(.01)
    assert server.started
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('AMODEX_TEST_CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            base=f'http://127.0.0.1:{port}'
            page.goto(base+'/auth/passcode');page.locator('#passcode').fill('123456');page.get_by_role('button',name='Entrar',exact=True).click()
            page.add_init_script("document.addEventListener('DOMContentLoaded',()=>{if(location.pathname==='/supervised'){const p=document.createElement('p');p.textContent='TESTE DETERMINÍSTICO · IA simulada; análise Luau real';p.style.color='#ffd69b';document.querySelector('main').prepend(p)}})")
            page.goto(base+'/supervised')
            page.get_by_role('button',name='Preparar revisão').click()
            page.locator('#status').filter(has_text='AWAITING_APPROVAL').wait_for()
            assert not h.requests
            assert len(page.locator('#hash').inner_text())==64
            page.locator('#consent').check()
            approval_posts=[]
            def lost_ack(route):
                approval_posts.append(route.request.method)
                route.fetch()  # Server accepts approval; browser loses only its ACK.
                route.abort()
            page.route('**/approve',lost_ack)
            page.get_by_role('button',name='Autorizar edição com IA gratuita').click()
            expected='BLOCKED' if blocked else 'COMPLETED'
            page.wait_for_function('(s)=>document.getElementById("status").textContent.startsWith(s)',arg=expected,timeout=15000)
            assert page.locator('#history option:checked').inner_text().startswith(expected)
            assert approval_posts==['POST'] and len(h.launches)==1
            assert len(h.requests)==(0 if blocked else 1)
            if blocked:
                assert page.locator('#block').inner_text().startswith('REAL_MODEL_BLOCKED —')
                assert 'Inferência não iniciada.' in page.locator('#block').inner_text()
                assert 'PASS' in page.locator('#evidence').inner_text() # actual local baseline
            else:
                page.locator('#artifact').evaluate('(el)=>el.open=true')
                assert '42' in page.locator('#code').inner_text()
                with page.expect_download() as download:
                    page.get_by_role('button',name='Baixar arquivo Luau').click()
                assert Path(download.value.path()).read_text()=='local score = 42\nreturn score\n'
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            assert page.locator('#prepare').evaluate('(el)=>el.getBoundingClientRect().height')>=44
            assert not errors
            evidence=os.environ.get('AMODEX_TEST_EVIDENCE_DIR')
            if evidence:
                root=Path(evidence);root.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(root/('mobile-blocked.png' if blocked else 'mobile-simulated.png')),full_page=True)
            # Refresh and select the persisted result; no approval/model replay.
            page.reload();page.wait_for_function('(s)=>document.getElementById("status").textContent.startsWith(s)',arg=expected,timeout=10000)
            assert len(h.launches)==1 and len(h.requests)==(0 if blocked else 1)
            browser.close()
    finally:
        server.should_exit=True;thread.join(5);listener.close()
        assert not thread.is_alive()
