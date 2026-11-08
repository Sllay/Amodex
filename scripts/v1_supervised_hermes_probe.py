#!/usr/bin/env python3
"""Pinned real Hermes runtime against deterministic LOOPBACK HTTP, never a model.

No API key from the environment is consumed. This compatibility proof supplements
product-route tests and must never be presented as real inference evidence.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.runtime.hermes_worker import HERMES_COMMIT, HermesScopedWorker
from amodex.runtime.supervised_guard import HermesRequestGuard
from amodex.tools.scoped_workspace import ScopedWorkspaceTools
from stage2_hermes_probe import FakeProviderHandler, ProviderState


class Handler(FakeProviderHandler):
    mode = 'success'

    def do_POST(self):
        if self.mode != 'http500':
            return super().do_POST()
        length = int(self.headers.get('content-length', '0'))
        self.state.capture(json.loads(self.rfile.read(length)))
        self.send_response(500)
        body = b'{"error":{"message":"deterministic test failure"}}'
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _response_message(self, payload):
        results=self._results(payload)
        read=next((r for r in results if r.get('tool')=='amodex_read_file'),None)
        if read is None:return self._tool_call('amodex_read_file',{'path':'score.luau'},1)
        if not any(r.get('tool')=='amodex_patch_file' for r in results):
            return self._tool_call('amodex_patch_file',{'path':'score.luau','expected_sha256':read['sha256'],'old_text':'0','new_text':'42'},2)
        if not any(r.get('tool')=='amodex_luau_validate' for r in results):
            return self._tool_call('amodex_luau_validate',{'path':'score.luau'},3)
        return {'role':'assistant','content':'Deterministic local provider: edit and static analysis complete.'},'stop'


def main():
    p=argparse.ArgumentParser();p.add_argument('--hermes-root',type=Path,required=True);p.add_argument('--analyzer',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--mode',choices=('success','http500'),default='success')
    args=p.parse_args()
    Handler.mode=args.mode
    state=ProviderState();Handler.state=state
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    report={'kind':'DETERMINISTIC_LOOPBACK_PROVIDER','real_model':False,'hermes_commit':HERMES_COMMIT,'result':'FAIL'}
    try:
        now=datetime.now(timezone.utc)
        task=TaskPackage(package_id=new_uuid7(),task_id=new_uuid7(),attempt_id=new_uuid7(),issued_at=now,expires_at=now+timedelta(minutes=3),
            objective=Objective(statement='Set score to 42, then analyze the file.'),
            repository=RepositoryAuthority(repository_id='offline-probe',expected_base_sha='a'*40,task_branch='test/offline-probe'),
            scope=Scope(read=('score.luau',),write=('score.luau',),allowed_operations=('read_file','search_files','patch_file','luau.validate')))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir();(workspace/'score.luau').write_text('local score = 0\nreturn score\n')
            tools=ScopedWorkspaceTools(task=task,workspace=workspace)
            url=f'http://127.0.0.1:{server.server_port}/v1'
            ledger=[];events=[]
            guard=HermesRequestGuard(base_url=url,model='amodex-offline-fixture',authorize=lambda:None,
                started=lambda n:ledger.append({'number':n,'state':'STARTED'}),acknowledged=lambda n,s:ledger.append({'number':n,'state':'ACK','http_status':s}))
            worker=HermesScopedWorker(task=task,tools=tools,hermes_root=args.hermes_root,home_root=root/'home',
                base_url=url,api_key='unused-loopback-fixture',model='amodex-offline-fixture',analyzer=args.analyzer,
                transport_guard=guard,authority_check=lambda:None,event_sink=events.append)
            worker.run(instruction='Read, edit and statically analyze the authorized file.',capsule=tools.build_context_capsule(('score.luau',)))
            source=(workspace/'score.luau').read_text()
            validation=next(e for e in events if e.get('event')=='tool_result' and e.get('tool_name')=='amodex_luau_validate')
            report.update(calls=guard.calls,input_bytes_total=guard.input_bytes,tools=[e['tool_name'] for e in events if e.get('event')=='tool_result'],local_validation=validation['status'],source_matches=source=='local score = 42\nreturn score\n')
            assert validation['status']=='True' and source=='local score = 42\nreturn score\n'
            assert guard.calls==4 and not guard.pending
            report.update(result='PASS',calls=guard.calls,input_bytes_total=guard.input_bytes,
                source_sha256=hashlib.sha256(source.encode()).hexdigest(),local_validation='PASS',
                tools=[e['tool_name'] for e in events if e.get('event')=='tool_result'],ledger=ledger)
    except Exception as exc:
        report['error_code']=getattr(exc,'code',type(exc).__name__)
        if args.mode=='http500' and guard.calls==len(state.requests)==1 and guard.failure and guard.failure.unknown:
            report.update(result='PASS',calls=guard.calls,outcome='UNKNOWN_OUTCOME',retry_requests=0,mode=args.mode)
    finally:
        server.shutdown();server.server_close();thread.join(3)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['result']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
