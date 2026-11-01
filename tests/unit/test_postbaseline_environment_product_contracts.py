from __future__ import annotations

from argparse import Namespace

import pytest

from amodex.cli import CliError, execute
from amodex.environments.product import RunnerDispatchRequest, RunnerKind, runner_kind_for_engine
from amodex.runners.desktop import DesktopRunnerUnsupported, roblox_studio_mcp_command


def test_runner_kind_is_engine_specific_and_roblox_never_maps_to_actions() -> None:
    assert runner_kind_for_engine("godot") == RunnerKind.ACTIONS_HEADLESS
    assert runner_kind_for_engine("Godot") == RunnerKind.ACTIONS_HEADLESS
    assert runner_kind_for_engine("roblox") == RunnerKind.DESKTOP_STUDIO
    assert runner_kind_for_engine("Roblox Studio") == RunnerKind.DESKTOP_STUDIO
    assert runner_kind_for_engine(None) == RunnerKind.ACTIONS_HEADLESS


def test_dispatch_request_rejects_path_escape_and_duplicates() -> None:
    with pytest.raises(ValueError):
        RunnerDispatchRequest(
            run_id="019a3d70-0000-7000-8000-000000000001",
            session_id="019a3d70-0000-7000-8000-000000000002",
            write_paths=("../secret",),
        )
    with pytest.raises(ValueError):
        RunnerDispatchRequest(
            run_id="019a3d70-0000-7000-8000-000000000001",
            session_id="019a3d70-0000-7000-8000-000000000002",
            write_paths=("src/player.gd", "src/player.gd"),
        )


def test_roblox_studio_mcp_desktop_paths_are_explicit() -> None:
    assert str(
        roblox_studio_mcp_command(
            platform_name="Windows",
            local_appdata=r"C:\\Users\\BK\\AppData\\Local",
        )
    ).replace("\\", "/").endswith("Roblox/mcp.bat")
    assert str(roblox_studio_mcp_command(platform_name="Darwin")) == (
        "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP"
    )
    with pytest.raises(DesktopRunnerUnsupported, match="Windows or macOS"):
        roblox_studio_mcp_command(platform_name="Linux")


class _NoCallClient:
    def request(self, *_args, **_kwargs):
        raise AssertionError("Control Plane must not be called after local scope validation fails")


def test_cli_rejects_environment_run_without_project_before_creating_any_run() -> None:
    args = Namespace(
        command="run",
        objective="implement inventory",
        project=None,
        environment="019a3d70-0000-7000-8000-000000000001",
        session="019a3d70-0000-7000-8000-000000000002",
    )
    with pytest.raises(CliError, match="require --project"):
        execute(args, _NoCallClient())
