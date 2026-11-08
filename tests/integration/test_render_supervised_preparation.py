"""Render candidate checks on isolated loopback DB; no live provider or deploy."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from test_v1_supervised_execution import database, h, create, approve, get, HEADERS
from amodex.api.workspace_app import create_workspace_app
from amodex.api.activation_status import activation_snapshot
from amodex.deployment.supervised_runtime import status, verify, expand_grants, ANALYZER_SHA
from amodex.runtime.supervised_guard import SupervisedBlocked


def test_render_production_composition_and_artifact(h,monkeypatch):
    monkeypatch.setenv('AMODEX_ACCESS_PASSCODE','123456')
    monkeypatch.setenv('AMODEX_ACCESS_SESSION_SECRET','test-session-secret-not-used-outside-tests')
    app=create_workspace_app(base_app=FastAPI(),dsn=h.dsn,supervised_service=h.service)
    with TestClient(app,base_url='https://testserver') as c:
        c.post('/auth/passcode',data={'passcode':'123456'},follow_redirects=False)
        ready=c.request('GET','/readyz');assert ready.status_code==200,ready.text
        d=c.post('/v1/supervised/executions',headers=HEADERS,json={'draft_id':h.draft,'project_id':h.project,'path':'score.luau'}).json()
        assert c.post('/v1/supervised/executions/'+d['execution_id']+'/approve',headers=HEADERS,json={'source_sha256':d['source_sha256']}).status_code==202
        result=c.request('GET','/v1/supervised/executions/'+d['execution_id']).json()
        assert result['status']=='COMPLETED' and result['artifact']['source'].startswith('local score = 42')
        diagnostic=c.request('GET','/v1/supervised/host-status')
        assert diagnostic.status_code==200 and not diagnostic.json()['inference_performed']
        assert 'synthetic-test-only-private' not in diagnostic.text
        assert 'secret_ref' not in diagnostic.text


def test_readiness_fails_closed_when_runtime_or_grant_missing(h,monkeypatch,tmp_path):
    monkeypatch.setenv('AMODEX_SUPERVISED_ENABLED','1')
    monkeypatch.setenv('AMODEX_SUPERVISED_RUNTIME_ROOT',str(tmp_path/'absent'))
    result=activation_snapshot(dsn=h.dsn,app_version='isolated-test')
    assert not result['ready'] and result['supervised_runtime']['code']=='SUPERVISED_RUNTIME_INVALID'


def test_verified_assets_and_restartable_grants(h,monkeypatch,tmp_path):
    root=Path(os.environ['AMODEX_TEST_RENDER_ASSETS'])
    monkeypatch.setenv('AMODEX_SUPERVISED_RUNTIME_ROOT',str(root))
    manifest=verify()
    values=h.grant.model_dump(mode='json')
    values.update(repository_root='@runtime/project',base_sha='@release',analyzer='@runtime/luau-analyze',analyzer_sha256='@pinned',hermes_root='@runtime/hermes')
    first=expand_grants([values]);second=expand_grants([values])
    assert first==second and first[0]['base_sha']==manifest['release_sha']
    assert first[0]['analyzer_sha256']==ANALYZER_SHA
    monkeypatch.setenv('AMODEX_RELEASE_SHA','a'*40)
    with pytest.raises(RuntimeError):verify()


def test_one_active_attempt_blocks_another_without_starting(h):
    d=create(h).json()
    assert h.service.approve(d['execution_id'],d['source_sha256'])
    import psycopg
    from amodex.contracts.ids import new_uuid7
    second_draft=new_uuid7()
    with psycopg.connect(h.dsn) as c:
        c.execute("""INSERT INTO amodex_execution_drafts(draft_id,chat_id,environment_id,title,objective,instructions,status)
            SELECT %s,chat_id,environment_id,'Second intent',objective,instructions,'READY'
            FROM amodex_execution_drafts WHERE draft_id=%s""",(second_draft,h.draft))
    second=h.service.create(draft_id=second_draft,project_id=h.project,path='score.luau',key='independent-intent-02')
    with pytest.raises(SupervisedBlocked,match='SUPERVISED_CAPACITY_BUSY'):
        h.service.approve(str(second['execution_id']),second['source_sha256'])
    assert h.service.get(str(second['execution_id']))['status']=='AWAITING_APPROVAL'
    assert not h.requests


def test_registry_web_free_does_not_bind_process_key(h,monkeypatch):
    from amodex.providers.registry import ProviderExecutionRegistry,stage5_credential_pools_from_env
    for name in ('GROQ','GEMINI','NVIDIA','OPENROUTER'):
        monkeypatch.delenv(name+'_API_KEY',raising=False)
        monkeypatch.delenv('AMODEX_'+name+'_BILLING_CLASS',raising=False)
    registry=ProviderExecutionRegistry(stage5_credential_pools_from_env())
    assert all(not p['credential_bound'] and p['billing_class']=='unknown' for p in registry.configuration_metadata())


def test_startup_does_not_migrate_by_default(monkeypatch):
    spec=importlib.util.spec_from_file_location('start_render','scripts/start_render.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'status',lambda:{'enabled':True,'ready':True})
    monkeypatch.delenv('AMODEX_APPLY_MIGRATIONS_ON_START',raising=False)
    calls=[]
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:pytest.fail('unexpected migration'))
    monkeypatch.setattr(module.os,'execv',lambda *a:calls.append(a))
    module.main();assert len(calls)==1 and '--workers' in calls[0][1]


def test_runtime_ready_from_remounted_declarative_grants(h,monkeypatch,tmp_path):
    root=Path(os.environ['AMODEX_TEST_RENDER_ASSETS'])
    monkeypatch.setenv('AMODEX_SUPERVISED_ENABLED','1')
    monkeypatch.setenv('AMODEX_SUPERVISED_RUNTIME_ROOT',str(root))
    config=tmp_path/'render-secret-file.json'
    grant=h.grant.model_dump(mode='json')
    grant.update(repository_root='@runtime/project',base_sha='@release',paths=['tests/fixtures/roblox/score_test.luau'],analyzer='@runtime/luau-analyze',analyzer_sha256='@pinned',hermes_root='@runtime/hermes')
    config.write_text(json.dumps([grant]));monkeypatch.setenv('AMODEX_SUPERVISED_PROJECTS_FILE',str(config))
    assert status()['ready']
    assert activation_snapshot(dsn=h.dsn,app_version='isolated-test')['ready']
    config.unlink()
    assert not status()['ready'] # no invented durable filesystem fallback
    config.write_text(json.dumps([grant]))
    assert status()['ready']
