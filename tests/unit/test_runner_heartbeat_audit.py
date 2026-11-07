"""Run the checked-in workflow's heartbeat shell with local fake executables.

These are characterization tests: a passing test can demonstrate an unsafe gap.
No GitHub workflow, provider or external service is contacted.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


def workflow_shell(step_name: str) -> str:
    workflow = Path(".github/workflows/environment-runner.yml").read_text()
    step = workflow.split(f"      - name: {step_name}\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


@pytest.mark.parametrize("failure", ["RUNNER_LEASE_LOST", "synthetic transport timeout"])
def test_failed_background_renewal_currently_does_not_fail_launch_step_or_stop_following_work(tmp_path, failure):
    executables = tmp_path/"bin"
    executables.mkdir()
    sleep = executables/"sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    uv = executables/"fake-uv"
    uv.write_text(f"#!/bin/sh\nprintf '%s\\n' '{failure}' >&2\nexit 2\n")
    for path in [sleep, uv]:
        path.chmod(0o700)
    script = workflow_shell("Start durable runner heartbeat") + r'''
printf 'LAUNCH_STEP_SUCCEEDED\n'
heartbeat_pid="$(cat "$RUNNER_TEMP/runner-heartbeat.pid")"
if wait "$heartbeat_pid"; then
  printf 'UNEXPECTED_RENEWAL_SUCCESS\n'
else
  printf 'HEARTBEAT_EXIT_%s\n' "$?"
fi
# Instrumentation observes the dead child. The workflow itself has no wait/watchdog.
printf 'work still authorized by shell\n' > "$RUNNER_TEMP/following-work"
''' + workflow_shell("Stop durable runner heartbeat")
    env = {**os.environ, "PATH": str(executables)+os.pathsep+os.environ["PATH"],
           "RUNNER_TEMP": str(tmp_path), "UV_BIN": str(uv),
           "AMODEX_JOB_ID": "synthetic-job", "GITHUB_RUN_ID": "synthetic-workflow"}
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "LAUNCH_STEP_SUCCEEDED" in result.stdout
    assert "HEARTBEAT_EXIT_1" in result.stdout
    assert (tmp_path/"following-work").exists()
    assert failure in (tmp_path/"runner-heartbeat.log").read_text()
    assert not (tmp_path/"runner-heartbeat.pid").exists()
