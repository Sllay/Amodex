"""Desktop runner helpers for engines that require a local GUI application.

Roblox Studio MCP is intentionally modeled as a desktop-only capability. Linux
GitHub Actions must never be treated as a substitute for an open Studio session.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


class DesktopRunnerUnsupported(RuntimeError):
    pass


def roblox_studio_mcp_command(
    *,
    platform_name: str | None = None,
    local_appdata: str | None = None,
) -> Path:
    system = (platform_name or platform.system()).strip().lower()
    if system == "windows":
        base = local_appdata or os.environ.get("LOCALAPPDATA")
        if not base:
            raise DesktopRunnerUnsupported("LOCALAPPDATA is required on Windows")
        return Path(base) / "Roblox" / "mcp.bat"
    if system in {"darwin", "macos", "mac"}:
        return Path("/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP")
    raise DesktopRunnerUnsupported(
        "Roblox Studio MCP requires a Windows or macOS desktop runner with Studio installed"
    )


def validate_roblox_studio_mcp(
    *,
    platform_name: str | None = None,
    local_appdata: str | None = None,
) -> Path:
    command = roblox_studio_mcp_command(
        platform_name=platform_name,
        local_appdata=local_appdata,
    )
    if not command.exists():
        raise DesktopRunnerUnsupported(f"Roblox Studio MCP executable not found: {command}")
    return command
