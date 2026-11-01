from __future__ import annotations

from pathlib import Path

from amodex.director.context import compile_context
from amodex.director.ingestion import ingest_repository
from amodex.director.models import PlannedTask
from amodex.integrations.godot import GodotIntegrationAdapter
from amodex.integrations.models import IntegrationCapability


def test_godot_integration_profile_is_capability_based(tmp_path: Path) -> None:
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "project.godot").write_text(
        '[application]\nconfig/name="Fixture"\n', encoding="utf-8"
    )
    (tmp_path / "scripts" / "mcp").mkdir(parents=True)
    (tmp_path / "scripts" / "mcp" / "godot_mcp.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    (tmp_path / "scripts" / "mcp" / "serena_mcp.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    (tmp_path / "AGENTS.md").write_text(
        """# Fixture contract
The Godot project root is **`game/`**.
`game/project.godot` is the only project descriptor.
Target engine: Godot 4.7.2.
Do not preload the whole repository into context.
`main` represents validated work.
Never silently upgrade Godot, Godot MCP or Serena.
Use Serena for semantic code work and Godot MCP for runtime inspection.
Two agents must not edit the same files concurrently.
""",
        encoding="utf-8",
    )

    profile = GodotIntegrationAdapter().detect(str(tmp_path))
    assert profile is not None
    assert profile.engine == "Godot"
    assert profile.engine_version == "4.7.2"
    assert profile.runtime_root == "game"
    assert profile.project_descriptor == "game/project.godot"
    assert {tool.name for tool in profile.tools} == {"godot-mcp", "serena"}
    assert IntegrationCapability.SYMBOL_NAVIGATION in profile.capabilities
    assert IntegrationCapability.SCENE_EDIT in profile.capabilities
    assert IntegrationCapability.PLAYTEST_CONTROL in profile.capabilities
    assert IntegrationCapability.DIAGNOSTICS in profile.capabilities


def test_ingestion_recognizes_luau_without_granting_engine_authority(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# Generic project contract\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "PlayerController.luau").write_text(
        """local PlayerController = {}

function PlayerController.move(self, direction: Vector3): ()
    self.position += direction
end

return PlayerController
""",
        encoding="utf-8",
    )

    snapshot = ingest_repository(
        tmp_path,
        project_id="luau-fixture",
        base_sha="a" * 40,
    )
    entry = snapshot.by_path["src/PlayerController.luau"]
    assert entry.language == "luau"
    assert "PlayerController" in entry.symbols
    assert "PlayerController.move" in entry.symbols

    # Language recognition is not equivalent to claiming Roblox/Studio support.
    # A future Roblox adapter must be registered explicitly and prove its tools.
    assert GodotIntegrationAdapter().detect(str(tmp_path)) is None


def test_scope_filename_cannot_expand_implicit_context_topic(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# Contract\nRepository context uses progressive disclosure.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "architecture" / "inventory.md").write_text(
        "# Inventory\nInventory transactions and save migration rules.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "architecture" / "water.md").write_text(
        "# Water\nWater containers also participate in save migration.\n",
        encoding="utf-8",
    )
    (tmp_path / "harness" / "evidence" / "water").mkdir(parents=True)
    (tmp_path / "harness" / "evidence" / "water" / "README.md").write_text(
        "# Water evidence\nHistorical runtime evidence for water simulation.\n",
        encoding="utf-8",
    )
    (tmp_path / "game" / "inventory").mkdir(parents=True)
    (tmp_path / "game" / "inventory" / "water_container.gd").write_text(
        "class_name WaterContainer\n",
        encoding="utf-8",
    )
    (tmp_path / "game" / "water").mkdir(parents=True)
    (tmp_path / "game" / "water" / "water_domain.gd").write_text(
        "class_name WaterDomain\n"
        "func open_save(directory: String) -> int:\n"
        "    return OK\n",
        encoding="utf-8",
    )
    (tmp_path / "game" / "water" / "water_renderer.gd").write_text(
        "class_name WaterRenderer\n",
        encoding="utf-8",
    )

    snapshot = ingest_repository(
        tmp_path,
        project_id="context-theme-fixture",
        base_sha="b" * 40,
    )
    task = PlannedTask(
        task_id="inventory-task",
        objective=(
            "Update transactional inventory save migration behavior and evidence gates"
        ),
        write_paths=("game/inventory/water_container.gd",),
        non_goals=("Do not redesign water systems",),
        acceptance_criteria=("Inventory tests pass",),
    )
    capsule = compile_context(
        snapshot,
        task,
        current_base_sha="b" * 40,
    )
    paths = {section.path for section in capsule.sections}

    assert "AGENTS.md" in paths
    assert "game/inventory/water_container.gd" in paths
    assert "docs/architecture/inventory.md" in paths
    assert "docs/architecture/water.md" not in paths
    assert "harness/evidence/water/README.md" not in paths
    assert "game/water/water_domain.gd" not in paths
    assert "game/water/water_renderer.gd" not in paths
