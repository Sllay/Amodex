"""CR-05 regression: execute checked-in workflow shell with local fake uv/API."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def workflow_shell(step_name: str) -> str:
    workflow = Path(".github/workflows/environment-runner.yml").read_text()
    step = workflow.split(f"      - name: {step_name}\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


@pytest.mark.parametrize("failure", ["LEASE_LOST", "AUTH_REJECTED", "AUTHORITY_UNCONFIRMED", "CONTROL_PLANE_ERROR"])
def test_failed_renewal_now_stops_work_and_blocks_following_workflow_step(tmp_path, failure):
    fake_uv = tmp_path/"fake-uv"
    root = str(Path("src").resolve())
    fake_uv.write_text(f'''#!{sys.executable}
import sys,os
sys.path.insert(0,{root!r})
from datetime import datetime,timedelta,timezone
from pathlib import Path
from amodex.runners.client import RunnerControlClient,HeartbeatResult,AuthorityStatus
from amodex.runners.supervisor import main
counter=Path(os.environ['RUNNER_TEMP'])/'heartbeat-count'
def check(self,**kwargs):
 count=int(counter.read_text())+1 if counter.exists() else 1
 counter.write_text(str(count))
 return HeartbeatResult(AuthorityStatus.HEALTHY if count==1 else AuthorityStatus.{failure},lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=60),attempt_id="019a3d70-0000-7000-8000-000000000002")
RunnerControlClient.check_authority=check
raise SystemExit(main(sys.argv[sys.argv.index('amodex.runners.supervisor')+1:]))
''')
    fake_uv.chmod(0o700)
    project = tmp_path/"project"
    project.mkdir()
    executables = tmp_path/"bin"
    executables.mkdir()
    git = executables/"git"
    git.write_text(f'''#!{sys.executable}
import os,time
from pathlib import Path
assert 'AMODEX_RUNNER_CONTROL_TOKEN' not in os.environ
Path(os.environ['RUNNER_TEMP'],'work-started').touch()
time.sleep(30)
''')
    git.chmod(0o700)
    env = {**os.environ, "PATH": str(executables)+os.pathsep+os.environ["PATH"],
           "RUNNER_TEMP": str(tmp_path), "UV_BIN": str(fake_uv), "TARGET_PROJECT": str(project),
           "AMODEX_JOB_ID": "job", "GITHUB_RUN_ID": "owner",
           "AMODEX_CONTROL_PLANE_URL": "https://unused.invalid", "AMODEX_RUNNER_CONTROL_TOKEN": "synthetic-test-token"}
    shell = workflow_shell("Validate generic repository envelope").replace("--operation validate-generic", "--interval .08 --grace .08 --operation validate-generic")
    result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and failure in result.stdout, result.stderr
    assert (tmp_path/"work-started").exists()
    next_step = shell.replace("--operation validate-generic", "--operation following-work").replace(
        'git -C "$TARGET_PROJECT" fsck --no-progress', 'touch "$RUNNER_TEMP/following-work"')
    repeated = subprocess.run(["bash", "-c", next_step], env=env, capture_output=True, text=True, timeout=5)
    assert repeated.returncode == 2 and not (tmp_path/"following-work").exists()
    assert (tmp_path/"heartbeat-count").read_text() == "2"
    assert "synthetic-test-token" not in result.stdout+result.stderr+repeated.stdout+repeated.stderr


def test_workflow_shell_is_valid_and_all_postclaim_operations_are_supervised():
    workflow = Path(".github/workflows/environment-runner.yml").read_text()
    assert "runner-heartbeat.pid" not in workflow and "while true" not in workflow
    names = [part.splitlines()[0] for part in workflow.split("      - name: ")[1:]]
    after_claim = False
    for name in names:
        if name == "Claim exact durable runner job": after_claim = True
        if name == "Checkout exact Amodex runner implementation": continue
        shell = workflow_shell(name)
        assert subprocess.run(["bash", "-n"], input=shell, text=True, capture_output=True).returncode == 0, name
        if after_claim:
            assert "python -m amodex.runners.supervisor" in shell, name
            assert '--state-file "$RUNNER_TEMP/runner-supervision.json"' in shell, name
            assert '--github-run-id "$GITHUB_RUN_ID"' in shell, name
    assert "--report-state COMPLETED" in workflow_shell("Report successful validation")
    assert "--report-state FAILED" in workflow_shell("Report runner failure")
