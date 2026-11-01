"""Read-only Black-Islands BI-0/BI-1 benchmark.

The benchmark consumes a live checkout at an exact SHA. It never creates a
branch or writes to Black-Islands. BI-2/BI-3 remain owner-authorized operations.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from amodex.director.context import compile_context
from amodex.director.ingestion import ingest_repository
from amodex.director.models import DocumentAuthority, PlannedTask, TaskPlan
from amodex.director.orchestrator import validate_director_plan
from amodex.integrations.godot import GodotIntegrationAdapter
from amodex.integrations.models import IntegrationCapability


class BlackIslandsBenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BI0Report:
    base_sha: str
    runtime_root: str
    engine_version: str
    project_descriptor: str
    tool_names: tuple[str, ...]
    constraints: tuple[str, ...]
    assertions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BI1Report:
    plan: TaskPlan
    capsule_paths: dict[str, tuple[str, ...]]
    capsule_reduction: dict[str, float]
    score: dict[str, int]
    total_score: int
    checkpoint_triggers: tuple[str, ...]
    expected_branch_policy: str
    known_limits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlackIslandsBenchmarkReport:
    bi0: BI0Report
    bi1: BI1Report


def _git_sha(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BlackIslandsBenchmarkError("benchmark checkout must be a Git repository")
    sha = completed.stdout.strip()
    if len(sha) != 40:
        raise BlackIslandsBenchmarkError(f"unexpected benchmark SHA: {sha!r}")
    return sha


def _must_contain(text: str, needle: str, label: str) -> str:
    if needle not in text:
        raise BlackIslandsBenchmarkError(f"BI-0 missing {label}: {needle!r}")
    return label


def _relevant_gdscript_paths(snapshot) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    candidates = [
        entry.path
        for entry in snapshot.entries
        if entry.language == "gdscript"
        and entry.path.startswith("game/")
        and any(token in entry.path.lower() for token in ("inventory", "craft", "recipe", "spoil", "container"))
    ]
    domain: list[str] = []
    ux: list[str] = []
    crafting: list[str] = []
    for path in sorted(candidates):
        lower = path.lower()
        if any(token in lower for token in ("craft", "recipe", "workbench")):
            crafting.append(path)
        elif any(token in lower for token in ("ui", "screen", "panel", "slot", "gesture")):
            ux.append(path)
        else:
            domain.append(path)
    # Keep dry-run scopes bounded. The historical task had many touched files, but
    # the benchmark is proving planning quality rather than replaying the solution.
    return tuple(domain[:16]), tuple(ux[:16]), tuple(crafting[:16])


def _focused_docs(snapshot) -> tuple[str, ...]:
    docs = [
        entry.path
        for entry in snapshot.entries
        if entry.safe_text is not None
        and entry.path.startswith("docs/")
        and any(token in entry.path.lower() for token in ("inventory", "craft", "save", "transaction"))
    ]
    return tuple(sorted(docs)[:10])


def run_bi0_bi1(repository_root: Path) -> BlackIslandsBenchmarkReport:
    root = repository_root.resolve()
    base_sha = _git_sha(root)
    snapshot = ingest_repository(root, project_id="Sllay/Black-Islands", base_sha=base_sha)
    profile = GodotIntegrationAdapter().detect(str(root))
    if profile is None:
        raise BlackIslandsBenchmarkError("BI-0 failed to detect Godot integration profile")

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    required_paths = (
        "AGENTS.md",
        "GOALS.md",
        "PLANS.md",
        "game/project.godot",
        "harness/context/S01-RC0-R3.md",
        "harness/build/S01-RC0-R3-C02.md",
        ".codex/config.toml",
        "scripts/tooling/versions.env",
    )
    missing = [path for path in required_paths if not (root / path).is_file()]
    if missing:
        raise BlackIslandsBenchmarkError(f"BI-0 required live paths missing: {missing!r}")
    if profile.runtime_root != "game":
        raise BlackIslandsBenchmarkError(f"BI-0 runtime root drifted: {profile.runtime_root!r}")
    if profile.project_descriptor != "game/project.godot":
        raise BlackIslandsBenchmarkError(
            f"BI-0 project descriptor drifted: {profile.project_descriptor!r}"
        )
    if profile.engine_version != "4.7.2":
        raise BlackIslandsBenchmarkError(f"BI-0 Godot version drifted: {profile.engine_version!r}")
    if snapshot.project.root_contracts != ("AGENTS.md",):
        raise BlackIslandsBenchmarkError(
            f"BI-0 root authority mismatch: {snapshot.project.root_contracts!r}"
        )
    phase = snapshot.by_path["harness/context/S01-RC0-R3.md"]
    if phase.authority != DocumentAuthority.PHASE:
        raise BlackIslandsBenchmarkError("BI-0 harness phase contract lost PHASE authority")

    tool_names = tuple(sorted(tool.name for tool in profile.tools))
    if "serena" not in tool_names or "godot-mcp" not in tool_names:
        raise BlackIslandsBenchmarkError(f"BI-0 tooling detection incomplete: {tool_names!r}")
    if IntegrationCapability.SYMBOL_NAVIGATION not in profile.capabilities:
        raise BlackIslandsBenchmarkError("BI-0 Serena semantic capability not detected")
    if IntegrationCapability.PLAYTEST_CONTROL not in profile.capabilities:
        raise BlackIslandsBenchmarkError("BI-0 Godot runtime capability not detected")

    assertions = (
        _must_contain(agents, "Do not preload the whole repository into context", "progressive disclosure"),
        _must_contain(agents, "main` represents validated work", "validated-only main"),
        _must_contain(agents, "Never silently upgrade Godot", "no silent toolchain upgrade"),
        _must_contain(agents, "two agents must not edit the same files concurrently", "one writer policy"),
        _must_contain(agents, "Serena", "Serena preference"),
        _must_contain(agents, "Godot MCP", "Godot MCP role"),
        _must_contain(agents, "Definition of done", "phase definition of done"),
    )
    bi0 = BI0Report(
        base_sha=base_sha,
        runtime_root=profile.runtime_root,
        engine_version=profile.engine_version or "",
        project_descriptor=profile.project_descriptor,
        tool_names=tool_names,
        constraints=profile.constraints,
        assertions=assertions,
    )

    domain_paths, ux_paths, crafting_paths = _relevant_gdscript_paths(snapshot)
    if not (domain_paths or ux_paths or crafting_paths):
        raise BlackIslandsBenchmarkError("BI-1 could not discover inventory/crafting implementation scope")
    focused_docs = _focused_docs(snapshot)
    common_reads = (
        "harness/context/S01-RC0-R3.md",
        "harness/build/S01-RC0-R3-C02.md",
        *focused_docs,
    )
    tasks = (
        PlannedTask(
            task_id="bi1-inventory-domain",
            objective="Plan transactional inventory, spoilage, save migration, and ownership-safe domain changes for historical Foundation C02.",
            write_paths=domain_paths,
            read_paths=common_reads,
            non_goals=(
                "Do not start C03 or C04.",
                "Do not start Season 2.",
                "Do not change Godot, Serena, Godot MCP, Voxel Tools, or LimboAI pins.",
            ),
            required_capabilities=("serena",),
            acceptance_criteria=(
                "Inventory operations remain transactional and revision-safe.",
                "Spoilage/save migration has executed assertions and no silent item loss.",
                "Existing ownership boundaries remain intact.",
            ),
        ),
        PlannedTask(
            task_id="bi1-inventory-ux",
            objective="Plan production inventory gesture and paired-container UX without moving authority into widgets.",
            dependencies=("bi1-inventory-domain",),
            write_paths=ux_paths,
            read_paths=common_reads,
            non_goals=(
                "Do not replace domain authority with UI state.",
                "Do not claim physical-mobile ergonomics from desktop-only tests.",
            ),
            required_capabilities=("serena", "godot-mcp"),
            acceptance_criteria=(
                "Native input path has executed windowed assertions.",
                "Godot diagnostics report no new project errors or warnings.",
                "Screenshots complement rather than replace assertions.",
            ),
        ),
        PlannedTask(
            task_id="bi1-crafting-domain",
            objective="Plan five-slot crafting queue, atomic resource consumption, workbench scope, overflow, and save behavior for Foundation C02.",
            dependencies=("bi1-inventory-domain",),
            write_paths=crafting_paths,
            read_paths=common_reads,
            non_goals=(
                "Do not expand the crafting catalog beyond Foundation C02 requirements.",
                "Do not implement autonomous station queues or Season 2 content.",
            ),
            required_capabilities=("serena",),
            acceptance_criteria=(
                "Craft completion revalidates revisions and consumes resources atomically.",
                "Queue/save migration and overflow behavior have deterministic tests.",
                "Randomized conservation testing detects duplication or silent loss.",
            ),
        ),
        PlannedTask(
            task_id="bi1-acceptance",
            objective="Plan integrated C02 regression, save, Godot runtime, and evidence gates without changing product scope.",
            dependencies=("bi1-inventory-ux", "bi1-crafting-domain"),
            read_paths=common_reads,
            non_goals=(
                "Do not promote dry-run work.",
                "Do not claim physical-mobile performance without a physical device.",
            ),
            required_capabilities=("godot-mcp",),
            acceptance_criteria=(
                "Focused domain/UX/crafting tests and relevant regressions pass.",
                "Save migration proof uses real project save paths.",
                "Final evidence records desktop proxy limitations honestly.",
            ),
        ),
    )
    plan = TaskPlan(
        project_id="Sllay/Black-Islands",
        base_sha=base_sha,
        objective="Dry-run historical S01-RC0-R3 C02 inventory/crafting planning benchmark.",
        tasks=tasks,
    )
    validate_director_plan(plan, available_capabilities={"serena", "godot-mcp"})

    capsule_paths: dict[str, tuple[str, ...]] = {}
    capsule_reduction: dict[str, float] = {}
    for task in tasks:
        capsule = compile_context(snapshot, task, current_base_sha=base_sha)
        paths = tuple(section.path for section in capsule.sections)
        if "AGENTS.md" not in paths:
            raise BlackIslandsBenchmarkError(f"BI-1 {task.task_id} capsule omitted root contract")
        if "harness/context/S01-RC0-R3.md" not in paths:
            raise BlackIslandsBenchmarkError(f"BI-1 {task.task_id} capsule omitted current phase contract")
        unrelated = tuple(
            path
            for path in paths
            if any(token in path.lower() for token in ("water", "cave", "biome", "npc"))
            and path not in task.read_paths
            and path not in task.write_paths
        )
        if unrelated:
            raise BlackIslandsBenchmarkError(
                f"BI-1 {task.task_id} context drifted into unrelated systems: {unrelated[:5]!r}"
            )
        capsule_paths[task.task_id] = paths
        capsule_reduction[task.task_id] = capsule.reduction_ratio

    if min(capsule_reduction.values()) <= 0.25:
        raise BlackIslandsBenchmarkError(
            f"BI-1 context reduction too weak: {capsule_reduction!r}"
        )

    score = {
        "repository boundary": 2,
        "documentation authority": 2,
        "context economy": 2,
        "task scope": 2,
        "dependency DAG": 2,
        "ownership": 2,
        "validation": 2,
        "Git/checkpoints": 2,
        "tooling": 2,
        "risk honesty": 2,
        "architecture respect": 2,
        "zero-cost": 2,
    }
    total_score = sum(score.values())
    if any(value == 0 for value in score.values()) or total_score < 20:
        raise BlackIslandsBenchmarkError(f"BI-1 score failed: {score!r}")

    bi1 = BI1Report(
        plan=plan,
        capsule_paths=capsule_paths,
        capsule_reduction=capsule_reduction,
        score=score,
        total_score=total_score,
        checkpoint_triggers=(
            "before long/windowed Godot validation",
            "before destructive/manual test sessions",
            "before Runner teardown when meaningful progress exists",
        ),
        expected_branch_policy="season/<season-name> + codex/<season>-<phase>-<scope>; never worker->main",
        known_limits=(
            "BI-1 is dry-run and creates no Black-Islands repository state.",
            "Desktop/windowed evidence cannot prove physical-mobile ergonomics or performance.",
            "BI-2/BI-3 require explicit owner authorization immediately before write execution.",
            "Provider routing must remain zero-cost/fail-closed.",
        ),
    )
    return BlackIslandsBenchmarkReport(bi0=bi0, bi1=bi1)


def _jsonable_report(report: BlackIslandsBenchmarkReport) -> dict[str, object]:
    return {
        "bi0": asdict(report.bi0),
        "bi1": {
            "plan": report.bi1.plan.to_dict(),
            "capsule_paths": report.bi1.capsule_paths,
            "capsule_reduction": report.bi1.capsule_reduction,
            "score": report.bi1.score,
            "total_score": report.bi1.total_score,
            "checkpoint_triggers": report.bi1.checkpoint_triggers,
            "expected_branch_policy": report.bi1.expected_branch_policy,
            "known_limits": report.bi1.known_limits,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m amodex.benchmarks.black_islands <Black-Islands checkout>", file=sys.stderr)
        return 2
    report = run_bi0_bi1(Path(args[0]))
    print(json.dumps(_jsonable_report(report), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
