"""Receipt recognition and evidence policy, with local transport only."""
from datetime import datetime, timezone

import httpx
import pytest

from amodex.environments.product import RunnerReportRequest, RunnerTerminalReceipt
from amodex.runners.client import RunnerClientError, RunnerControlClient, build_parser

JOB = "019a3d70-0000-7000-8000-000000000001"
ATTEMPT = "019a3d70-0000-7000-8000-000000000002"


def body(**changes):
    return RunnerReportRequest(github_run_id="owner",attempt_id=ATTEMPT,state="COMPLETED",summary="done",**changes)


def test_digest_is_stable_across_explicit_defaults_and_json_key_order():
    first=body()
    second=body(result_commit=None,evidence={})
    assert first.digest(JOB)==second.digest(JOB)
    a=body(evidence={"checks":3,"score":42})
    b=body(evidence={"score":42,"checks":3})
    assert a.digest(JOB)==b.digest(JOB) and a.digest(JOB)!=first.digest(JOB)


@pytest.mark.parametrize("evidence", [{"authorization":"private"},{"logs":["private"]},
    {"references":["https://user:pw@example.invalid/file"]},{"references":["artifact://bucket/../private"]},
    {"checks":True},{"godot":1}])
def test_evidence_rejects_credentials_raw_logs_and_unsafe_references(evidence):
    with pytest.raises(ValueError,match="REPORT_EVIDENCE_INVALID"):
        body(evidence=evidence).safe_evidence()


@pytest.mark.parametrize("fault", ["no_receipt","different_attempt","different_digest"])
def test_client_never_treats_completed_alone_as_receipt(monkeypatch,fault):
    request=body()
    receipt=RunnerTerminalReceipt(receipt_id=JOB,job_id=JOB,attempt_id=ATTEMPT,github_run_id="owner",
        report_digest=request.digest(JOB),state="COMPLETED",summary="done",result_commit=None,evidence={},
        audit_id=JOB,environment_id=JOB,session_id=JOB,run_id=JOB,committed_at=datetime.now(timezone.utc)).model_dump(mode="json")
    if fault=="no_receipt":receipt={"job_id":JOB,"state":"COMPLETED"}
    elif fault=="different_attempt":receipt['attempt_id']=JOB
    else:receipt['report_digest']='f'*64
    calls=[]
    def transport(method,url,**kwargs):
        calls.append(method)
        return httpx.Response(200,json=receipt)
    monkeypatch.setattr(httpx,'request',transport)
    client=RunnerControlClient('https://unused.invalid','synthetic-token')
    with pytest.raises(RunnerClientError):
        client.report(job_id=JOB,github_run_id='owner',attempt_id=ATTEMPT,state='COMPLETED',summary='done')
    assert calls==['POST']


def test_report_cli_requires_attempt_identity():
    argv=['report',JOB,'--state','COMPLETED','--summary','done','--github-run-id','owner']
    with pytest.raises(SystemExit): build_parser().parse_args(argv)
    assert build_parser().parse_args([*argv,'--attempt-id',ATTEMPT]).attempt_id==ATTEMPT
