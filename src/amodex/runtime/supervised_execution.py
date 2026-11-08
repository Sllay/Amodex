"""Opt-in Web handoff to the existing scoped Hermes/Run runtime.

One approved attempt, disposable files, durable artifacts, no recovery scheduler.
The CLI runs in its own process: Hermes' environment/cwd changes never affect Web.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Authority, Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.providers.governor import AdmissionStatus, ProviderGovernor, ProviderRoute
from amodex.providers.openai_compatible import OpenAICompatibleProvider
from amodex.providers.registry import ProviderExecutionDenied
from amodex.runtime.hermes_worker import HermesScopedWorker
from amodex.runtime.models import EventKind, RunState
from amodex.runtime.supervised_guard import HermesRequestGuard, SupervisedBlocked
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository, StaleLeaseError
from amodex.tools.roblox_luau import analyze_luau
from amodex.tools.scoped_workspace import ScopedWorkspaceTools

TERMINAL = {'COMPLETED', 'BLOCKED', 'FAILED', 'CANCELLED', 'UNKNOWN_OUTCOME'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class ProjectGrant(BaseModel):
    """Trusted host configuration, never accepted from browser/model arguments."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    project_id: str
    workspace_id: str
    environment_id: str
    resource_id: str
    repository_root: Path
    base_sha: str = Field(pattern='^[a-f0-9]{40}$')
    paths: tuple[str, ...]
    analyzer: Path
    analyzer_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    hermes_root: Path

    def validate_host(self):
        root = self.repository_root.resolve(strict=True)
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=3)
        if head.returncode or head.stdout.strip() != self.base_sha:
            raise SupervisedBlocked('SOURCE_REVISION_CHANGED')
        analyzer = self.analyzer.resolve(strict=True)
        if (not self.analyzer.is_absolute() or not analyzer.is_file() or not os.access(analyzer, os.X_OK)
                or analyzer == root or root in analyzer.parents
                or hashlib.sha256(analyzer.read_bytes()).hexdigest() != self.analyzer_sha256):
            raise SupervisedBlocked('ANALYZER_NOT_AUTHORIZED')
        return root, analyzer

    def source(self, path):
        if path not in self.paths or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or not path.endswith('.luau'):
            raise SupervisedBlocked('PATH_NOT_AUTHORIZED')
        root, _ = self.validate_host()
        file = root / path
        if any(part.is_symlink() for part in (file, *file.parents)) or root not in file.resolve(strict=True).parents:
            raise SupervisedBlocked('PATH_NOT_AUTHORIZED')
        if file.stat().st_size > 16000:
            raise SupervisedBlocked('SOURCE_TOO_LARGE')
        source = file.read_text(encoding='utf-8')
        # An exact tracked revision, not an uncommitted source or secret file.
        tracked = subprocess.run(['git', '-C', str(root), 'show', f'{self.base_sha}:{path}'], capture_output=True, timeout=3)
        if tracked.returncode or tracked.stdout != source.encode():
            raise SupervisedBlocked('SOURCE_REVISION_CHANGED')
        return source


def grants_from_environment():
    config = os.environ.get('AMODEX_SUPERVISED_PROJECTS_FILE', '')
    if not config:
        return ()
    try:
        return tuple(ProjectGrant.model_validate(item) for item in json.loads(Path(config).read_text()))
    except Exception:
        raise SupervisedBlocked('HOST_GRANTS_INVALID') from None


class SupervisedExecutionService:
    def __init__(self, *, dsn, grants=(), intelligence=None, registry=None):
        self.dsn, self.grants = dsn, tuple(grants)
        self.intelligence, self.registry = intelligence, registry
        self.runs = PostgresRunRepository(dsn)

    def connection(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def schema_ready(self):
        with self.connection() as conn:
            return bool(conn.execute("SELECT to_regclass('public.amodex_supervised_executions') AS t").fetchone()['t'])

    def grant_for(self, project_id):
        matches = [g for g in self.grants if g.project_id == project_id]
        if len(matches) != 1:
            raise SupervisedBlocked('PROJECT_NOT_AUTHORIZED')
        return matches[0]

    def authorize_target(self, conn, grant):
        row = conn.execute('''SELECT e.status, e.name, project.name AS project_name, r.external_id, r.name AS resource_name,
            r.metadata, r.resource_type, c.state AS connector_state
            FROM amodex_environments e
            JOIN amodex_environment_projects p ON p.environment_id=e.environment_id AND p.project_id=%s
            JOIN amodex_projects project ON project.project_id=p.project_id AND project.status='ACTIVE'
            JOIN amodex_workspace_environments w ON w.environment_id=e.environment_id AND w.workspace_id=%s
            JOIN amodex_workspace_projects wp ON wp.workspace_id=w.workspace_id AND wp.status='ACTIVE'
            JOIN amodex_connector_resources r ON r.bound_environment_id=e.environment_id AND r.resource_id=%s
            JOIN amodex_connector_accounts c ON c.connector_id=r.connector_id AND c.workspace_id=w.workspace_id
            WHERE e.environment_id=%s''', (grant.project_id, grant.workspace_id, grant.resource_id, grant.environment_id)).fetchone()
        # V1 release allowlist is deliberately confined to this opt-in product path.
        # General Roblox integration retains per-resource IDs, not these constants.
        if (not row or row['status'] != 'ACTIVE' or row['connector_state'] != 'CONNECTED'
                or row['resource_type'] != 'ROBLOX_PLACE' or row['external_id'] != '118200154785968'
                or str(row['metadata'].get('universe_id')) != '10769712453'
                or any('teste - não entre' in row[k].casefold() for k in ('name', 'resource_name'))):
            raise SupervisedBlocked('ENVIRONMENT_RESOURCE_BLOCKED')
        return row

    def options(self):
        if not self.schema_ready():
            return {'ready': False, 'code': 'SUPERVISED_MIGRATION_REQUIRED', 'projects': [], 'drafts': []}
        projects, drafts = [], []
        with self.connection() as conn:
            for g in self.grants:
                try:
                    target = self.authorize_target(conn, g)
                    g.validate_host()
                except (SupervisedBlocked, OSError):
                    continue
                projects.append({'project_id': g.project_id, 'project_name': target['project_name'],
                                 'environment_name': target['name'], 'environment_id': g.environment_id,
                                 'paths': g.paths, 'base_sha': g.base_sha,
                                 'universe_id': '10769712453', 'place_id': '118200154785968'})
                rows = conn.execute('''SELECT d.draft_id,d.title,d.objective,d.environment_id
                    FROM amodex_execution_drafts d JOIN amodex_workspace_chats c USING(chat_id)
                    WHERE c.workspace_id=%s AND c.status='ACTIVE' AND d.environment_id=%s
                      AND d.status='READY' AND d.run_id IS NULL ORDER BY d.created_at DESC LIMIT 30''',
                    (g.workspace_id, g.environment_id)).fetchall()
                drafts.extend({**r, 'project_id': g.project_id} for r in rows)
        return {'ready': bool(projects), 'code': None if projects else 'HOST_PROJECT_GRANT_REQUIRED',
                'projects': projects, 'drafts': drafts, 'policy': {'cost': 'free_only', 'max_calls': 6,
                'max_output_tokens_per_call': 512, 'max_input_bytes_total': 48000, 'max_seconds': 90,
                'roblox_remote': False}}

    def create(self, *, draft_id, project_id, path, key):
        if not re.fullmatch(r'[A-Za-z0-9._:-]{16,128}', key):
            raise SupervisedBlocked('IDEMPOTENCY_KEY_INVALID')
        grant = self.grant_for(project_id)
        content_digest = digest({'draft_id': draft_id, 'project_id': project_id, 'path': path})
        with self.connection() as conn:
            # Serializes only this intent; no SQL lock spans any external call.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (grant.environment_id + ':' + key,))
            existing = conn.execute('SELECT * FROM amodex_supervised_executions WHERE environment_id=%s AND intent_key=%s', (grant.environment_id, key)).fetchone()
            if existing:
                if existing['intent_digest'] != content_digest:
                    raise SupervisedBlocked('IDEMPOTENCY_CONFLICT')
                return self.project(existing)
            self.authorize_target(conn, grant)
            draft = conn.execute('''SELECT d.*,c.workspace_id,c.status AS chat_status FROM amodex_execution_drafts d
                JOIN amodex_workspace_chats c USING(chat_id) WHERE d.draft_id=%s FOR UPDATE OF d''', (draft_id,)).fetchone()
            if (not draft or str(draft['workspace_id']) != grant.workspace_id or draft['chat_status'] != 'ACTIVE'
                    or str(draft['environment_id']) != grant.environment_id or draft['status'] != 'READY' or draft['run_id']):
                raise SupervisedBlocked('DRAFT_NOT_READY_OR_BOUND')
            instruction = draft['objective'] + '\n' + draft['instructions']
            if len(instruction.encode()) > 3000:
                raise SupervisedBlocked('TASK_TOO_LARGE')
            source = grant.source(path)
            now = datetime.now(timezone.utc)
            task = TaskPackage(package_id=new_uuid7(), task_id=new_uuid7(), attempt_id=new_uuid7(),
                issued_at=now, expires_at=now+timedelta(minutes=15), objective=Objective(statement=instruction),
                repository=RepositoryAuthority(repository_id=project_id, expected_base_sha=grant.base_sha, task_branch='supervised/artifact'),
                scope=Scope(read=(path,), write=(path,), allowed_operations=('read_file', 'search_files', 'patch_file', 'luau.validate')),
                authority=Authority(may_commit_checkpoint=False, may_push_task_branch=False, may_request_scope_expansion=False))
            run = self.runs.create_run(prompt=instruction, project_id=project_id, connection=conn)
            row = conn.execute('''INSERT INTO amodex_supervised_executions
                (execution_id,run_id,draft_id,environment_id,project_id,resource_id,intent_key,intent_digest,
                 task_package,grant_digest,path,source,source_sha256,status,phase)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'AWAITING_APPROVAL','REVIEW') RETURNING *''',
                (new_uuid7(), run.run_id, draft_id, grant.environment_id, project_id, grant.resource_id, key, content_digest,
                 Jsonb(task.model_dump(mode='json')), digest(grant.model_dump(mode='json')), path, source, hashlib.sha256(source.encode()).hexdigest())).fetchone()
            conn.execute('UPDATE amodex_execution_drafts SET run_id=%s,updated_at=NOW() WHERE draft_id=%s', (run.run_id, draft_id))
        return self.project(row)

    @staticmethod
    def project(row):
        # Explicit projection: no host paths, SDK text, key references or HTTP headers.
        fields = ('execution_id','run_id','draft_id','environment_id','project_id','path','source','source_sha256',
                  'status','phase','error_code','route','calls','events','artifact','local_validation','created_at','updated_at')
        data = {k: row[k] for k in fields}
        data['task_id'] = row['task_package']['task_id']
        data['objective'] = row['task_package']['objective']['statement']
        data['cost_policy'] = 'free_only'
        data['roblox_remote'] = False
        return data

    def get(self, execution_id):
        with self.connection() as conn:
            # Read reconciliation changes only the projection, never reclaims or replays a Run.
            conn.execute('''UPDATE amodex_supervised_executions s SET status='UNKNOWN_OUTCOME',phase='RECONCILIATION_REQUIRED',
                error_code='WORKER_STOPPED_UNCONFIRMED',updated_at=NOW() FROM amodex_runs r
                WHERE s.execution_id=%s AND s.run_id=r.run_id AND s.status IN ('STARTED','RUNNING')
                AND s.deadline < clock_timestamp() AND (r.lease_expires_at IS NULL OR r.lease_expires_at < clock_timestamp())''', (execution_id,))
            row = conn.execute('SELECT * FROM amodex_supervised_executions WHERE execution_id=%s', (execution_id,)).fetchone()
        return self.project(row) if row else None

    def recent(self):
        with self.connection() as conn:
            ids = conn.execute('SELECT execution_id FROM amodex_supervised_executions ORDER BY created_at DESC LIMIT 20').fetchall()
        return [self.get(str(r['execution_id'])) for r in ids]

    def approve(self, execution_id, source_sha256):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM amodex_supervised_executions WHERE execution_id=%s FOR UPDATE', (execution_id,)).fetchone()
            if not row:
                raise SupervisedBlocked('EXECUTION_NOT_FOUND')
            if row['source_sha256'] != source_sha256:
                raise SupervisedBlocked('APPROVAL_HASH_MISMATCH')
            if row['status'] != 'AWAITING_APPROVAL':
                return False  # Same approval after lost ACK only reads the original attempt.
            grant = self.grant_for(str(row['project_id']))
            self.authorize_target(conn, grant)
            if digest(grant.model_dump(mode='json')) != row['grant_digest'] or grant.source(row['path']) != row['source']:
                raise SupervisedBlocked('APPROVAL_SOURCE_CHANGED')
            if datetime.now(timezone.utc) >= TaskPackage.model_validate(row['task_package']).expires_at:
                raise SupervisedBlocked('APPROVAL_EXPIRED')
            conn.execute("UPDATE amodex_supervised_executions SET status='STARTED',phase='PREFLIGHT',approved_at=NOW(),started_at=NOW(),deadline=clock_timestamp()+interval '90 seconds',updated_at=NOW() WHERE execution_id=%s", (execution_id,))
        return True

    def launch(self, execution_id):
        """One bounded isolated runtime process, not a new worker/scheduler service."""
        env = os.environ.copy()
        env['AMODEX_DATABASE_URL'] = self.dsn
        process = None
        try:
            process = subprocess.Popen([sys.executable, '-m', 'amodex.runtime.supervised_execution', execution_id],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            process.wait(timeout=90)
        except Exception:
            pass  # Sanitized durable projection below; never log process environment.
        finally:
            if process is not None:
                # Same process-group cleanup policy as R4; no subprocess can outlive
                # a controllable timeout, even if the runtime's analyzer is active.
                from amodex.runners.supervisor import RunnerSupervisor
                RunnerSupervisor._signal_group(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    RunnerSupervisor._signal_group(process.pid, signal.SIGKILL)
                    process.wait(timeout=1)
                RunnerSupervisor._signal_group(process.pid, signal.SIGKILL)
            with self.connection() as conn:
                conn.execute("UPDATE amodex_supervised_executions SET status='UNKNOWN_OUTCOME',phase='RECONCILIATION_REQUIRED',error_code='WORKER_STOPPED_UNCONFIRMED',updated_at=NOW() WHERE execution_id=%s AND status IN ('STARTED','RUNNING')", (execution_id,))

    def execute(self, execution_id, *, agent_factory=HermesScopedWorker):
        # One child acquires local execution permission by CAS before claim. A
        # crash after this mark is UNKNOWN, never grounds for another model call.
        with self.connection() as conn:
            row = conn.execute("UPDATE amodex_supervised_executions SET status='RUNNING',phase='CLAIMING',updated_at=NOW() WHERE execution_id=%s AND status='STARTED' AND deadline>clock_timestamp() RETURNING *", (execution_id,)).fetchone()
        if not row:
            return
        worker = Stage1Worker(repository=self.runs, provider=None,
            governor=ProviderGovernor((ProviderRoute('none','none','unknown'),)), worker_id='supervised-' + str(row['execution_id']))
        return worker.process_scoped(run_id=str(row['run_id']), operation=lambda run, renewal: self._perform(row, run, renewal, worker, agent_factory))

    def _perform(self, row, run, renewal, worker, agent_factory):
        guard = None
        route = None
        artifact = validation = None
        status, code = 'FAILED', 'EXECUTION_FAILED'
        task = TaskPackage.model_validate(row['task_package'])
        grant = self.grant_for(str(row['project_id']))

        def authorize():
            renewal.check()
            self.runs.renew_lease(run_id=run.run_id, worker_id=worker.worker_id,
                fencing_token=run.fencing_token, lease_seconds=worker.lease_policy.lease_seconds)
            if (datetime.now(timezone.utc) >= task.expires_at or self.runs.get_run_control_state(run.run_id) != 'ACTIVE'):
                raise SupervisedBlocked('OWNER_CONTROL_BLOCKED')
            with self.connection() as conn:
                self.authorize_target(conn, grant)
                live = conn.execute('SELECT status,deadline FROM amodex_supervised_executions WHERE execution_id=%s', (row['execution_id'],)).fetchone()
                if live['status'] != 'RUNNING' or datetime.now(timezone.utc) >= live['deadline']:
                    raise SupervisedBlocked('EXECUTION_AUTHORITY_BLOCKED')

        def update(**values):
            authorize()
            with self.connection() as conn:
                # Task updates are fenced in the SAME transaction, too.
                self.runs._lock_owned_run(conn.cursor(), run_id=run.run_id, worker_id=worker.worker_id, fencing_token=run.fencing_token)
                from psycopg import sql
                conn.execute(sql.SQL('UPDATE amodex_supervised_executions SET {},updated_at=NOW() WHERE execution_id=%s').format(
                    sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in values)),
                    tuple(Jsonb(v) if isinstance(v,(list,dict)) else v for v in values.values()) + (row['execution_id'],))

        calls, events = [], []
        def started(number):
            calls.append({'number': number, 'state': 'STARTED'})
            update(calls=list(calls), phase='MODEL')
        def acknowledged(number, http_status):
            calls[number-1] = {'number': number, 'state': 'ACKNOWLEDGED', 'http_status': http_status}
            update(calls=list(calls))
        def event(item):
            # Closed vocabulary only. Raw prompts, model text, SDK/HTTP errors
            # and arguments are deliberately never projected as logs.
            if len(events) >= 40:
                return
            if item.get('event') == 'tool_result' and item.get('tool_name') in {'amodex_read_file','amodex_patch_file','amodex_search_files','amodex_luau_validate'}:
                events.append({'event':'tool_result', 'tool':item['tool_name'], 'ok':item.get('status') == 'True'})
                update(events=list(events))

        try:
            authorize()
            if digest(grant.model_dump(mode='json')) != row['grant_digest']:
                raise SupervisedBlocked('HOST_GRANT_CHANGED')
            _, analyzer = grant.validate_host()
            validation = analyze_luau(row['source'], analyzer)
            if validation['status'] != 'PASS':
                raise SupervisedBlocked('LOCAL_BASELINE_FAILED')
            update(local_validation=validation)
            if self.intelligence is None or self.registry is None:
                raise SupervisedBlocked('REAL_MODEL_BLOCKED')
            candidate = self.intelligence.supervised_coding_candidate()
            if candidate is None:
                raise SupervisedBlocked('REAL_MODEL_BLOCKED')
            admission = ProviderGovernor((ProviderRoute(candidate.provider,candidate.model,candidate.cost_class.value),)).admit(free_quota_available=True)
            if admission.status != AdmissionStatus.ADMITTED:
                raise SupervisedBlocked('REAL_MODEL_BLOCKED')
            client = self.registry.client_for(candidate, operation='chat')
            if not isinstance(client, OpenAICompatibleProvider):
                raise SupervisedBlocked('HERMES_PROVIDER_UNSUPPORTED')
            base_url, api_key = client.hermes_settings()
            route = {'provider':candidate.provider, 'model':candidate.model, 'cost_class':candidate.cost_class.value,
                     'quota':'fresh_explicit_observation', 'real_inference':False}
            update(route=route, phase='PREFLIGHT')
            # Read-only connectivity/authentication check. A 200 does NOT itself
            # prove free billing/quota; those were independently required above.
            authorize()
            with httpx.Client(timeout=5, follow_redirects=False) as http:
                response = http.get(base_url+'/models', headers={'authorization':'Bearer '+api_key})
                if response.status_code != 200:
                    raise SupervisedBlocked('REAL_MODEL_PREFLIGHT_BLOCKED')
                try:
                    model_ids = {item['id'] for item in response.json()['data']}
                except (ValueError, KeyError, TypeError):
                    raise SupervisedBlocked('REAL_MODEL_PREFLIGHT_BLOCKED') from None
                if candidate.model not in model_ids:
                    raise SupervisedBlocked('REAL_MODEL_NOT_AVAILABLE')
            guard = HermesRequestGuard(base_url=base_url, model=candidate.model, authorize=authorize,
                                       started=started, acknowledged=acknowledged)
            with tempfile.TemporaryDirectory(prefix='amodex-supervised-') as directory:
                workspace = Path(directory)/'workspace'
                file = workspace/row['path']
                file.parent.mkdir(parents=True)
                file.write_text(row['source'])
                tools = ScopedWorkspaceTools(task=task, workspace=workspace)
                agent = agent_factory(task=task, tools=tools, hermes_root=grant.hermes_root,
                    home_root=Path(directory)/'home', base_url=base_url, api_key=api_key, model=candidate.model,
                    worker_id=worker.worker_id, event_sink=event, analyzer=analyzer, authority_check=authorize, transport_guard=guard)
                capsule = tools.build_context_capsule((row['path'],))
                agent.run(instruction='Read the file, implement only the requested small change, then use amodex_luau_validate. Do not claim runtime test results; analysis is static only.', capsule=capsule)
                guard.check()
                authorize()
                changed = tools.read_file(row['path'], max_bytes=16000)
                validation = analyze_luau(changed, analyzer)
                artifact = {'path':row['path'], 'source':changed, 'sha256':hashlib.sha256(changed.encode()).hexdigest(),
                            'base_sha':grant.base_sha, 'changed': changed != row['source']}
                if not guard.calls:
                    raise SupervisedBlocked('NO_MODEL_REQUEST')
                route['real_inference'] = True
                if validation['status'] != 'PASS':
                    status, code = 'FAILED', 'LOCAL_VALIDATION_FAILED'
                elif not artifact['changed'] or not any(e['tool']=='amodex_patch_file' and e['ok'] for e in events):
                    status, code = 'FAILED', 'NO_AUTHORIZED_EDIT'
                else:
                    status, code = 'COMPLETED', None
        except (SupervisedBlocked, ProviderExecutionDenied) as exc:
            code = exc.code if isinstance(exc, SupervisedBlocked) else 'REAL_MODEL_BLOCKED'
            status = 'UNKNOWN_OUTCOME' if getattr(exc,'unknown',False) else 'BLOCKED'
        except StaleLeaseError:
            status, code = 'UNKNOWN_OUTCOME', 'RUN_AUTHORITY_LOST'
        except Exception:
            status, code = ('UNKNOWN_OUTCOME','PROVIDER_OUTCOME_UNKNOWN') if guard and guard.pending else ('FAILED','EXECUTION_FAILED')
        if guard and (guard.pending or (guard.failure and guard.failure.unknown)):
            status, code = 'UNKNOWN_OUTCOME','PROVIDER_OUTCOME_UNKNOWN'
        try:
            renewal.check()
            with self.connection() as conn:
                state = RunState(status)
                renewal.call(self.runs.transition, run_id=run.run_id, worker_id=worker.worker_id, fencing_token=run.fencing_token,
                    state=state, kind=EventKind.RUN_COMPLETED if state==RunState.COMPLETED else EventKind.RUN_FAILED,
                    payload={'execution_id':str(row['execution_id']), 'local_validation':validation, 'code':code},
                    result_text=json.dumps({'status':status,'artifact_sha256':artifact['sha256'] if artifact else None}),
                    provider=route['provider'] if route else None, model=route['model'] if route else None,
                    last_error_code=code, release_lease=True, connection=conn)
                conn.execute('''UPDATE amodex_supervised_executions SET status=%s,phase='FINISHED',error_code=%s,
                    artifact=%s,local_validation=%s,route=%s,updated_at=NOW() WHERE execution_id=%s''',
                    (status,code,Jsonb(artifact),Jsonb(validation),Jsonb(route),row['execution_id']))
                if status=='COMPLETED':
                    conn.execute("UPDATE amodex_execution_drafts SET status='EXECUTED',updated_at=NOW() WHERE draft_id=%s", (row['draft_id'],))
        except Exception:
            # Never circumvent a stale fence to report success or renew authority.
            with self.connection() as conn:
                conn.execute("UPDATE amodex_supervised_executions SET status='UNKNOWN_OUTCOME',phase='RECONCILIATION_REQUIRED',error_code='TERMINAL_AUTHORITY_UNCONFIRMED',updated_at=NOW() WHERE execution_id=%s AND status='RUNNING'", (row['execution_id'],))
        return self.get(str(row['execution_id']))


def runtime_service():
    # Use the same measured routing service and credential registry as Web, in
    # the isolated execution process. No key fallback or discovery inference.
    from amodex.model_intelligence.service import ModelIntelligenceService
    from amodex.providers.registry import ProviderExecutionRegistry, stage5_credential_pools_from_env
    from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository
    from amodex.storage.routing import PostgresRoutingRepository
    from amodex.storage.workspace import PostgresWorkspaceRepository
    dsn = os.environ['AMODEX_DATABASE_URL']
    registry = ProviderExecutionRegistry(stage5_credential_pools_from_env())
    intelligence = ModelIntelligenceService(repository=PostgresModelIntelligenceRepository(dsn),
        routing_repository=PostgresRoutingRepository(dsn), workspace_repository=PostgresWorkspaceRepository(dsn), execution_registry=registry)
    return SupervisedExecutionService(dsn=dsn, grants=grants_from_environment(), intelligence=intelligence, registry=registry)


if __name__ == '__main__':
    runtime_service().execute(sys.argv[1])
