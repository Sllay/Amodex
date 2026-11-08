"""Offline B/C product-path proofs: real PostgreSQL/Luau, simulated model only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import psycopg
from psycopg import sql
import pytest

from amodex.api.access_gate import AccessGateConfig, install_access_gate
from amodex.api.supervised_routes import build_supervised_router
from amodex.contracts.ids import new_uuid7
from amodex.deployment.migrations import apply_production_migrations, database_endpoint_identity
from amodex.providers.capabilities import BillingClass, ModelCandidate, ModelCapability, ModelLifecycle
from amodex.providers.registry import CredentialPoolConfig, ProviderExecutionRegistry
from amodex.runtime.hermes_worker import HermesScopedWorker, register_hermes_plugin
from amodex.runtime.supervised_execution import ProjectGrant, SupervisedExecutionService

HEADERS={'Origin':'https://testserver','X-AMODEX-Action':'supervised-v1','Idempotency-Key':'supervised-intent-0001'}


@pytest.fixture(scope='module')
def database():
    source=os.environ['AMODEX_TEST_DATABASE_URL']
    p=urlparse(source)
    assert p.hostname in {'127.0.0.1','localhost'} and p.path.startswith('/amodex_qa_')
    name='amodex_qa_supervised_'+uuid4().hex[:12]
    admin=p._replace(path='/postgres').geturl()
    # Reuse checkpoint A's validated local baseline; only the NEW migration runs.
    with psycopg.connect(admin,autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(name),sql.Identifier(p.path[1:])))
    dsn=p._replace(path='/'+name).geturl()
    try:
        # Rehearse rollback of only the new additive DDL before committing it.
        with psycopg.connect(dsn) as c:
            c.execute(Path('migrations/postbaseline/0010_supervised_coding.sql').read_text())
            assert c.execute("SELECT to_regclass('amodex_supervised_executions')").fetchone()[0]
            c.rollback()
            assert c.execute("SELECT to_regclass('amodex_supervised_executions')").fetchone()[0] is None
        result=apply_production_migrations(runtime_dsn=dsn,migration_dsn=dsn,endpoint_guard=database_endpoint_identity(dsn),migrations_root='migrations')
        assert result.applied==('postbaseline/0010_supervised_coding.sql',)
        assert apply_production_migrations(runtime_dsn=dsn,migration_dsn=dsn,endpoint_guard=database_endpoint_identity(dsn),migrations_root='migrations').applied==()
        yield dsn
    finally:
        with psycopg.connect(admin,autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture
def h(database,tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    for args in (['init','-q'],['config','user.name','Local fixture'],['config','user.email','fixture@invalid.local']):
        subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
    (repo/'score.luau').write_text('local score = 0\nreturn score\n')
    subprocess.run(['git','-C',str(repo),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'commit','-qm','fixture'],check=True,capture_output=True)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    ids={k:new_uuid7() for k in ('project','workspace','environment','resource','connector','chat','draft')}
    with psycopg.connect(database) as c:
        c.execute("INSERT INTO amodex_projects(project_id,name,repo_url,status) VALUES (%s,'Fixture',%s,'ACTIVE')",(ids['project'],'https://github.com/qa-local/'+ids['project']))
        c.execute("INSERT INTO amodex_environments(environment_id,name,engine,status) VALUES (%s,'Authorized fixture','roblox','ACTIVE')",(ids['environment'],))
        c.execute('INSERT INTO amodex_environment_projects(environment_id,project_id) VALUES (%s,%s)',(ids['environment'],ids['project']))
        c.execute("INSERT INTO amodex_workspace_projects(workspace_id,name) VALUES (%s,'Supervised fixture')",(ids['workspace'],))
        c.execute('INSERT INTO amodex_workspace_environments(workspace_id,environment_id) VALUES (%s,%s)',(ids['workspace'],ids['environment']))
        c.execute("INSERT INTO amodex_workspace_chats(chat_id,workspace_id,title) VALUES (%s,%s,'Small change')",(ids['chat'],ids['workspace']))
        c.execute("INSERT INTO amodex_execution_drafts(draft_id,chat_id,environment_id,title,objective,instructions,status) VALUES (%s,%s,%s,'Score','Set score to 42','Edit the authorized file and analyze it','READY')",(ids['draft'],ids['chat'],ids['environment']))
        c.execute("INSERT INTO amodex_connector_accounts(connector_id,workspace_id,provider,state) VALUES (%s,%s,'ROBLOX','CONNECTED')",(ids['connector'],ids['workspace']))
        c.execute("INSERT INTO amodex_connector_resources(resource_id,connector_id,resource_type,external_id,name,metadata,bound_environment_id) VALUES (%s,%s,'ROBLOX_PLACE','118200154785968','Authorized fixture','{\"universe_id\":\"10769712453\"}',%s)",(ids['resource'],ids['connector'],ids['environment']))
    analyzer=Path(os.environ['AMODEX_TEST_LUAU_ANALYZER'])
    grant=ProjectGrant(project_id=ids['project'],workspace_id=ids['workspace'],environment_id=ids['environment'],resource_id=ids['resource'],repository_root=repo,base_sha=sha,paths=('score.luau',),analyzer=analyzer,analyzer_sha256=hashlib.sha256(analyzer.read_bytes()).hexdigest(),hermes_root=tmp_path)
    candidate=ModelCandidate('groq','fixture-model','fixture-pool',BillingClass.FREE,frozenset({ModelCapability.CODING,ModelCapability.TOOL_CALLING}),32000,ModelLifecycle.ELIGIBLE,'deterministic test')
    monkeypatch.setenv('AMODEX_TEST_PROVIDER_KEY','synthetic-test-only-private')
    registry=ProviderExecutionRegistry((CredentialPoolConfig('fixture-pool','groq',BillingClass.FREE,'AMODEX_TEST_PROVIDER_KEY'),))
    intel=SimpleNamespace(supervised_coding_candidate=lambda:candidate)
    service=SupervisedExecutionService(dsn=database,grants=(grant,),intelligence=intel,registry=registry)
    # Preflight HTTP is simulated; validation uses the actual official analyzer.
    preflights=[]
    def preflight(self,url,**kwargs):
        preflights.append(url)
        return httpx.Response(200,json={'data':[{'id':'fixture-model'}]})
    monkeypatch.setattr(httpx.Client,'get',preflight)
    handlers,definitions,requests={ },[],[]
    def register(**kw):
        handlers[kw['name']]=kw['handler'];definitions.append({'function':kw['schema']})
    def discover(**kw):
        handlers.clear();definitions.clear()
        register_hermes_plugin(SimpleNamespace(register_hook=lambda *a:None,register_tool=register))
    mode={'name':'success'}
    class Agent:
        def __init__(self,**kw):
            assert kw['max_iterations']==6 and kw['max_tokens']==512 and kw['run_budget_seconds']==60 and kw['fallback_model'] is None
            self.kw=kw
            def respond(req):
                requests.append(req)
                if mode['name']=='timeout': raise httpx.ReadTimeout('synthetic private detail')
                if mode['name']=='http500': return httpx.Response(500,json={'error':'synthetic private detail'})
                return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'test'}}]})
            self.client=SimpleNamespace(_client=httpx.Client(transport=httpx.MockTransport(respond)),max_retries=5)
        def run_conversation(self,**kw):
            self.client._client.post('https://api.groq.com/openai/v1/chat/completions',json={'model':'fixture-model','max_tokens':512,'messages':[]})
            read=json.loads(handlers['amodex_read_file']({'path':'score.luau'}));assert read['ok']
            denied=json.loads(handlers['amodex_patch_file']({'path':'../outside.luau','expected_sha256':read['sha256'],'old_text':'0','new_text':'42'}));assert not denied['ok']
            assert not json.loads(handlers['amodex_run_tests']({'argv':['sh','-c','false']}))['ok']
            replacement='broken !!!' if mode['name']=='invalid' else '42'
            patched=json.loads(handlers['amodex_patch_file']({'path':'score.luau','expected_sha256':read['sha256'],'old_text':'0','new_text':replacement}));assert patched['ok']
            local=json.loads(handlers['amodex_luau_validate']({'path':'score.luau'}))
            assert local['ok']==(mode['name']!='invalid')
            if mode['name']=='lease_lost':
                with psycopg.connect(database) as c:
                    c.execute("UPDATE amodex_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE lease_owner LIKE 'supervised-%%'")
                handlers['amodex_read_file']({'path':'score.luau'})
            return {'final_response':'Simulation only, never real inference evidence'}
        def close(self): self.client._client.close()
    monkeypatch.setattr(HermesScopedWorker,'_verify_hermes',lambda self:None)
    monkeypatch.setitem(sys.modules,'model_tools',SimpleNamespace(get_tool_definitions=lambda **kw:definitions))
    monkeypatch.setitem(sys.modules,'hermes_cli',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'hermes_cli.plugins',SimpleNamespace(discover_plugins=discover))
    monkeypatch.setitem(sys.modules,'run_agent',SimpleNamespace(AIAgent=Agent))
    app=FastAPI();app.include_router(build_supervised_router(service));install_access_gate(app,AccessGateConfig('123456','test-session-secret-not-used-outside-tests'))
    launches=[]
    monkeypatch.setattr(service,'launch',lambda id:(launches.append(id),service.execute(id)))
    client=TestClient(app,base_url='https://testserver')
    # The class-level GET monkeypatch is for HTTPX preflight only; TestClient
    # requests use its own implementation, leaving actual routes/auth intact.
    client.post('/auth/passcode',data={'passcode':'123456'},follow_redirects=False)
    yield SimpleNamespace(**ids,dsn=database,service=service,grant=grant,candidate=candidate,intel=intel,registry=registry,client=client,launches=launches,requests=requests,preflights=preflights,mode=mode,repo=repo)
    client.close()


def create(h,**changes):
    return h.client.post('/v1/supervised/executions',headers=HEADERS,json={'draft_id':h.draft,'project_id':h.project,'path':'score.luau',**changes})


def approve(h,d):
    return h.client.post('/v1/supervised/executions/'+d['execution_id']+'/approve',headers=HEADERS,json={'source_sha256':d['source_sha256']})


def get(h,d):
    # request rather than GET avoids patching the simulated provider preflight.
    return h.client.request('GET','/v1/supervised/executions/'+d['execution_id']).json()


def test_web_approval_hermes_edit_real_local_validation_and_durable_artifact(h):
    created=create(h);assert created.status_code==201,created.text
    d=created.json();assert d['status']=='AWAITING_APPROVAL' and not h.requests
    generic = h.service.runs.claim_next(worker_id='generic')
    assert generic is None or generic.run_id != d['run_id']
    assert approve(h,d).status_code==202
    result=get(h,d);assert result['status']=='COMPLETED',result
    assert result['local_validation']=={'status':'PASS','returncode':0}
    assert result['artifact']['source']=='local score = 42\nreturn score\n'
    assert h.repo.joinpath('score.luau').read_text()=='local score = 0\nreturn score\n'
    assert [e['tool'] for e in result['events']]==['amodex_read_file','amodex_patch_file','amodex_luau_validate']
    assert len(h.requests)==len(h.launches)==1
    assert h.service.runs.get_run(d['run_id']).state.value=='COMPLETED'
    assert 'synthetic-test-only-private' not in json.dumps(result)
    # Lost HTTP acknowledgements and process restart never dispatch/model twice.
    assert approve(h,d).status_code==202 and len(h.launches)==1
    assert create(h).json()['execution_id']==d['execution_id']
    restarted=SupervisedExecutionService(dsn=h.dsn)
    assert restarted.get(d['execution_id'])['artifact']==result['artifact']
    h.service.execute(d['execution_id']);assert len(h.requests)==1


def test_concurrent_identical_intake_and_conflicting_payload(h):
    def submit(): return h.service.create(draft_id=h.draft,project_id=h.project,path='score.luau',key='concurrent-intent-01')
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda _:submit(),range(2)))
    assert results[0]['execution_id']==results[1]['execution_id']
    conflict=h.client.post('/v1/supervised/executions',headers={**HEADERS,'Idempotency-Key':'concurrent-intent-01'},json={'draft_id':h.draft,'project_id':h.project,'path':'other.luau'})
    assert conflict.status_code==409 and not h.requests


@pytest.mark.parametrize('what',['environment','universe','place','protected_name','path','source_changed','project_disabled'])
def test_wrong_target_scope_or_source_is_denied(h,what):
    if what=='path':
        assert create(h,path='../outside.luau').status_code==403;return
    if what=='source_changed':
        h.repo.joinpath('score.luau').write_text('return 100\n')
    else:
        with psycopg.connect(h.dsn) as c:
            if what=='project_disabled':c.execute("UPDATE amodex_projects SET status='DISABLED' WHERE project_id=%s",(h.project,))
            if what=='environment':c.execute('UPDATE amodex_connector_resources SET bound_environment_id=NULL WHERE resource_id=%s',(h.resource,))
            if what=='universe':c.execute("UPDATE amodex_connector_resources SET metadata='{\"universe_id\":\"2\"}' WHERE resource_id=%s",(h.resource,))
            if what=='place':c.execute("UPDATE amodex_connector_resources SET external_id='2' WHERE resource_id=%s",(h.resource,))
            if what=='protected_name':c.execute('UPDATE amodex_connector_resources SET name=%s WHERE resource_id=%s',('Teste - Não entre',h.resource))
    assert create(h).status_code==403 and not h.requests


@pytest.mark.parametrize('mode,expected',[('timeout','UNKNOWN_OUTCOME'),('http500','UNKNOWN_OUTCOME'),('invalid','FAILED'),('lease_lost','UNKNOWN_OUTCOME')])
def test_failure_unknown_and_authority_loss_never_replay(h,mode,expected):
    h.mode['name']=mode;d=create(h).json();approve(h,d)
    result=get(h,d);assert result['status']==expected,result
    approve(h,d);h.service.execute(d['execution_id']);assert len(h.requests)==len(h.launches)==1
    assert 'synthetic private detail' not in json.dumps(result)
    if mode=='lease_lost': assert h.service.runs.get_run(d['run_id']).state.value!='COMPLETED'


def test_missing_credential_blocks_before_http_and_is_persisted(h,monkeypatch):
    monkeypatch.delenv('AMODEX_TEST_PROVIDER_KEY')
    d=create(h).json();approve(h,d)
    result=get(h,d)
    assert result['status']=='BLOCKED' and result['error_code']=='REAL_MODEL_BLOCKED'
    assert not h.requests and not h.preflights
    assert h.service.runs.get_run(d['run_id']).state.value=='BLOCKED'


def test_unknown_cost_is_not_free(h):
    h.intel.supervised_coding_candidate=lambda:replace(h.candidate,cost_class=BillingClass.UNKNOWN)
    d=create(h).json();approve(h,d)
    assert get(h,d)['error_code']=='REAL_MODEL_BLOCKED' and not h.preflights


def test_private_owner_session_and_origin_required(h):
    d=create(h).json()
    foreign=h.client.post('/v1/supervised/executions/'+d['execution_id']+'/approve',headers={**HEADERS,'Origin':'https://evil.invalid'},json={'source_sha256':d['source_sha256']})
    assert foreign.status_code==403
    h.client.cookies.clear()
    assert create(h).status_code==401 and not h.launches


def test_crash_after_start_reconciles_without_work(h):
    d=create(h).json();assert h.service.approve(d['execution_id'],d['source_sha256'])
    with psycopg.connect(h.dsn) as c:
        c.execute("UPDATE amodex_supervised_executions SET deadline=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(d['execution_id'],))
    result=get(h,d);assert result['status']=='UNKNOWN_OUTCOME'
    approve(h,d);h.service.execute(d['execution_id']);assert not h.requests and not h.launches


def test_web_composition_exposes_private_mobile_entry(h,monkeypatch):
    from amodex.api.workspace_app import create_workspace_app
    monkeypatch.setenv('AMODEX_ACCESS_PASSCODE','123456')
    monkeypatch.setenv('AMODEX_ACCESS_SESSION_SECRET','test-session-secret-not-used-outside-tests')
    app=create_workspace_app(base_app=FastAPI(),dsn=h.dsn,supervised_service=h.service)
    with TestClient(app,base_url='https://testserver') as client:
        client.post('/auth/passcode',data={'passcode':'123456'},follow_redirects=False)
        root=client.request('GET','/');assert root.status_code==200 and 'href="/supervised"' in root.text
        page=client.request('GET','/supervised');assert page.status_code==200
        assert 'viewport-fit=cover' in page.text and 'no-store' in page.headers['cache-control']
        options=client.request('GET','/v1/supervised/options');assert options.json()['ready']
        assert options.headers['cache-control']=='private, no-store'


def test_bad_initial_luau_blocks_before_provider(h):
    # Pin the intentionally invalid source, so this tests the analyzer rather
    # than the independent Git/source-drift guard.
    h.repo.joinpath('score.luau').write_text('local = invalid !!!\n')
    subprocess.run(['git','-C',str(h.repo),'commit','-am','invalid fixture','-q'],check=True)
    sha=subprocess.check_output(['git','-C',str(h.repo),'rev-parse','HEAD'],text=True).strip()
    h.service.grants=(h.grant.model_copy(update={'base_sha':sha}),)
    d=create(h).json();approve(h,d)
    result=get(h,d)
    assert result['status']=='BLOCKED' and result['error_code']=='LOCAL_BASELINE_FAILED'
    assert result['local_validation']['status']=='FAIL' and not h.preflights and not h.requests


def test_terminal_artifact_and_run_event_roll_back_together(h,monkeypatch):
    d=create(h).json()
    original=h.service.runs._insert_event
    def fail(cur,**kw):
        if kw['kind'].value=='RUN_COMPLETED':raise RuntimeError('synthetic audit failure')
        return original(cur,**kw)
    monkeypatch.setattr(h.service.runs,'_insert_event',fail)
    approve(h,d)
    result=get(h,d)
    assert result['status']=='UNKNOWN_OUTCOME' and result['artifact'] is None
    assert h.service.runs.get_run(d['run_id']).state.value!='COMPLETED'
    assert len(h.requests)==1


def test_actual_isolated_launcher_persists_missing_real_model_block(h,monkeypatch,tmp_path):
    # No simulated agent/provider is installed in the child process. Deliberately
    # absent supported credentials prove the production CLI/launcher denial path.
    for provider in ('GEMINI','GROQ','OPENROUTER','NVIDIA'):
        monkeypatch.delenv(provider+'_API_KEY',raising=False)
        monkeypatch.setenv('AMODEX_'+provider+'_BILLING_CLASS','unknown')
    config=tmp_path/'host-grants.json'
    config.write_text(json.dumps([h.grant.model_dump(mode='json')]))
    monkeypatch.setenv('AMODEX_SUPERVISED_PROJECTS_FILE',str(config))
    d=create(h).json()
    assert h.service.approve(d['execution_id'],d['source_sha256'])
    SupervisedExecutionService.launch(h.service,d['execution_id'])
    result=get(h,d)
    assert result['status']=='BLOCKED' and result['error_code']=='REAL_MODEL_BLOCKED',result
    assert result['local_validation']['status']=='PASS'
    assert not result['calls'] and not h.requests
    assert h.service.runs.get_run(d['run_id']).state.value=='BLOCKED'
