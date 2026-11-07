"""Engine/tool integration descriptors.

The integration layer is intentionally capability-based. Amodex core policy does
not know whether a capability comes from Godot MCP, Roblox Studio MCP, an LSP,
or another editor/runtime bridge. Future adapters register a profile and expose
only the capabilities granted to a TaskPackage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class IntegrationCapability(StrEnum):
    CODE_READ = "code.read"
    CODE_EDIT = "code.edit"
    SYMBOL_NAVIGATION = "code.symbol_navigation"
    SCENE_READ = "scene.read"
    SCENE_EDIT = "scene.edit"
    RUNTIME_EXECUTE = "runtime.execute"
    PLAYTEST_CONTROL = "runtime.playtest_control"
    DIAGNOSTICS = "runtime.diagnostics"
    CONSOLE = "runtime.console"
    SCREENSHOT = "vision.screenshot"
    INPUT = "runtime.input"
    ASSET_INSERT = "assets.insert"
    ASSET_GENERATE = "assets.generate"


@dataclass(frozen=True, slots=True)
class ToolBinding:
    name: str
    transport: str
    capabilities: tuple[IntegrationCapability, ...]
    source_path: str | None = None
    command: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EngineIntegrationProfile:
    adapter_id: str
    engine: str
    engine_version: str | None
    languages: tuple[str, ...]
    runtime_root: str
    project_descriptor: str
    tools: tuple[ToolBinding, ...]
    constraints: tuple[str, ...]
    provenance_paths: tuple[str, ...]

    @property
    def capabilities(self) -> frozenset[IntegrationCapability]:
        return frozenset(cap for tool in self.tools for cap in tool.capabilities)


class EngineIntegrationAdapter(Protocol):
    adapter_id: str

    def detect(self, repository_root: str) -> EngineIntegrationProfile | None:
        """Return a deterministic profile when the repository matches this adapter."""
        ...


class VisualTestAdapter(Protocol):
    """Future authorized visual session; observations include evidence references.

    This interface grants no platform access and has no Roblox implementation yet.
    """

    def observe(self) -> dict[str, object]:
        """Return observed facts and screenshot/evidence references."""
        ...

    def press(self, control: str, *, duration_seconds: float = 0.1) -> None:
        """Press a bounded input in the explicitly authorized test session."""
        ...

    def wait(self, seconds: float) -> None:
        """Wait a bounded duration without generating additional input."""
        ...

    def verify(self, expected: dict[str, object]) -> bool:
        """Check expectations against observations; no proof from HTTP status alone."""
        ...
