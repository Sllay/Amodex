"""Deterministic Godot project integration profile discovery."""

from __future__ import annotations

import re
from pathlib import Path

from .models import EngineIntegrationProfile, IntegrationCapability, ToolBinding


_ENGINE_RE = re.compile(r"Target engine:\s*Godot\s+([0-9]+(?:\.[0-9]+){1,2})", re.IGNORECASE)
_RUNTIME_RE = re.compile(r"project root is \*\*`([^`]+)`\*\*", re.IGNORECASE)
_DESCRIPTOR_RE = re.compile(r"`([^`]*project\.godot)` is the only project descriptor", re.IGNORECASE)


class GodotIntegrationAdapter:
    adapter_id = "godot"

    def detect(self, repository_root: str) -> EngineIntegrationProfile | None:
        root = Path(repository_root).resolve()
        agents = root / "AGENTS.md"
        if not agents.is_file():
            return None
        text = agents.read_text(encoding="utf-8")
        runtime_match = _RUNTIME_RE.search(text)
        descriptor_match = _DESCRIPTOR_RE.search(text)
        if runtime_match is None and not (root / "project.godot").is_file():
            return None
        runtime_root = runtime_match.group(1).rstrip("/") if runtime_match else "."
        descriptor = (
            descriptor_match.group(1)
            if descriptor_match
            else ("project.godot" if runtime_root == "." else f"{runtime_root}/project.godot")
        )
        if not (root / descriptor).is_file():
            return None

        version_match = _ENGINE_RE.search(text)
        version = version_match.group(1) if version_match else None
        tools: list[ToolBinding] = []
        provenance = ["AGENTS.md", descriptor]

        godot_mcp = root / "scripts" / "mcp" / "godot_mcp.sh"
        if godot_mcp.is_file() or "Godot MCP" in text:
            source_path = "scripts/mcp/godot_mcp.sh" if godot_mcp.is_file() else "AGENTS.md"
            tools.append(
                ToolBinding(
                    name="godot-mcp",
                    transport="mcp",
                    capabilities=(
                        IntegrationCapability.SCENE_READ,
                        IntegrationCapability.SCENE_EDIT,
                        IntegrationCapability.RUNTIME_EXECUTE,
                        IntegrationCapability.PLAYTEST_CONTROL,
                        IntegrationCapability.DIAGNOSTICS,
                        IntegrationCapability.CONSOLE,
                        IntegrationCapability.SCREENSHOT,
                        IntegrationCapability.INPUT,
                    ),
                    source_path=source_path,
                    command=(source_path,) if godot_mcp.is_file() else (),
                )
            )
            if godot_mcp.is_file():
                provenance.append(source_path)

        serena = root / "scripts" / "mcp" / "serena_mcp.sh"
        if serena.is_file() or "Serena" in text:
            source_path = "scripts/mcp/serena_mcp.sh" if serena.is_file() else "AGENTS.md"
            tools.append(
                ToolBinding(
                    name="serena",
                    transport="mcp",
                    capabilities=(
                        IntegrationCapability.CODE_READ,
                        IntegrationCapability.CODE_EDIT,
                        IntegrationCapability.SYMBOL_NAVIGATION,
                        IntegrationCapability.DIAGNOSTICS,
                    ),
                    source_path=source_path,
                    command=(source_path,) if serena.is_file() else (),
                )
            )
            if serena.is_file():
                provenance.append(source_path)

        constraints: list[str] = []
        if "Do not preload the whole repository into context" in text:
            constraints.append("progressive-disclosure")
        if "Never silently upgrade Godot" in text:
            constraints.append("no-silent-toolchain-upgrades")
        if "main` represents validated work" in text:
            constraints.append("main-validated-only")
        if "two agents must not edit the same files concurrently" in text.lower():
            constraints.append("one-writer-per-path")

        return EngineIntegrationProfile(
            adapter_id=self.adapter_id,
            engine="Godot",
            engine_version=version,
            languages=("GDScript",),
            runtime_root=runtime_root,
            project_descriptor=descriptor,
            tools=tuple(tools),
            constraints=tuple(sorted(set(constraints))),
            provenance_paths=tuple(dict.fromkeys(provenance)),
        )
