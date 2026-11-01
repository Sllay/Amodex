from __future__ import annotations

import pytest

from amodex.director import (
    CapacityBlocked,
    DagOrchestrator,
    OwnershipConflict,
    OwnershipRegistry,
    PlanRejected,
    PlannedTask,
    TaskPlan,
    TaskState,
    fingerprint_error,
    validate_director_plan,
)


def _task(task_id: str, *, deps=(), writes=(), capability="python", **overrides) -> PlannedTask:
    values = {
        "task_id": task_id,
        "objective": f"Implement {task_id}",
        "dependencies": tuple(deps),
        "write_paths": tuple(writes),
        "non_goals": ("Do not rewrite unrelated components",),
        "required_capabilities": (capability,),
        "acceptance_criteria": (f"{task_id} focused validation passes",),
    }
    values.update(overrides)
    return PlannedTask(**values)


def _plan() -> TaskPlan:
    return TaskPlan(
        project_id="fixture",
        base_sha="d" * 40,
        objective="Implement interface plus two independent consumers and integration",
        tasks=(
            _task("A", writes=("src/contracts.py",)),
            _task("B", deps=("A",), writes=("src/b.py",)),
            _task("C", deps=("A",), writes=("src/c.py",)),
            _task("D", deps=("B", "C"), writes=("src/integration.py",)),
        ),
    )


def test_s3_v3_dag_concurrency_capacity_recovery_and_antiloop() -> None:
    plan = _plan()
    validate_director_plan(plan, available_capabilities={"python"})
    assert TaskPlan.from_dict(plan.to_dict()) == plan

    orchestrator = DagOrchestrator(plan, max_active_tasks=2, equivalent_failure_limit=2)
    assert [task.task_id for task in orchestrator.ready_tasks()] == ["A"]
    orchestrator.start("A")
    orchestrator.succeed("A")

    assert {task.task_id for task in orchestrator.ready_tasks()} == {"B", "C"}
    orchestrator.start("B")
    orchestrator.start("C")
    assert orchestrator.states["D"] == TaskState.PENDING

    snapshot = orchestrator.snapshot()
    restored = DagOrchestrator.restore(plan, snapshot)
    assert restored.states["B"] == TaskState.RUNNING
    assert restored.states["C"] == TaskState.RUNNING

    fingerprint = fingerprint_error(
        validator_id="pytest",
        error_class="AssertionError",
        message="expected 2 but received 3 in stable assertion",
        path="src/b.py",
        failing_test="test_b_contract",
    )
    assert restored.fail("B", fingerprint) == TaskState.CORRECTION_REQUIRED
    assert restored.states["C"] == TaskState.RUNNING
    restored.succeed("C")
    assert restored.states["D"] == TaskState.PENDING

    restored.schedule_correction("B")
    restored.start("B")
    assert restored.fail("B", fingerprint) == TaskState.ESCALATED
    assert restored.states["C"] == TaskState.SUCCEEDED
    assert restored.states["D"] == TaskState.PENDING


def test_s3_v3_resource_cap_serializes_otherwise_ready_tasks() -> None:
    plan = _plan()
    orchestrator = DagOrchestrator(plan, max_active_tasks=1)
    orchestrator.start("A")
    orchestrator.succeed("A")
    orchestrator.start("B")
    with pytest.raises(CapacityBlocked):
        orchestrator.start("C")
    orchestrator.succeed("B")
    orchestrator.start("C")


def test_s3_v3_central_ownership_blocks_overlap_even_if_model_requests_it() -> None:
    registry = OwnershipRegistry()
    left = _task("LEFT", writes=("src/shared/**",))
    right = _task("RIGHT", writes=("src/shared/config.py",))
    registry.acquire(left)
    with pytest.raises(OwnershipConflict):
        registry.acquire(right)


def test_director_rejects_parallel_write_conflict_and_missing_capability() -> None:
    conflicting = TaskPlan(
        project_id="fixture",
        base_sha="e" * 40,
        objective="bad plan",
        tasks=(
            _task("B", writes=("src/shared.py",)),
            _task("C", writes=("src/shared.py",)),
        ),
    )
    with pytest.raises(PlanRejected, match="overlapping write scopes"):
        validate_director_plan(conflicting, available_capabilities={"python"})

    missing_capability = TaskPlan(
        project_id="fixture",
        base_sha="f" * 40,
        objective="bad capability",
        tasks=(_task("G", writes=("src/g.py",), capability="godot"),),
    )
    with pytest.raises(PlanRejected, match="unavailable capabilities"):
        validate_director_plan(missing_capability, available_capabilities={"python"})


def test_director_cannot_bypass_cost_promotion_or_hide_architecture_change() -> None:
    paid = TaskPlan(
        project_id="fixture",
        base_sha="1" * 40,
        objective="paid bypass",
        tasks=(_task("PAID", writes=("src/p.py",), requires_paid_resources=True),),
    )
    with pytest.raises(PlanRejected, match="cannot authorize paid resources"):
        validate_director_plan(paid, available_capabilities={"python"})

    promotion = TaskPlan(
        project_id="fixture",
        base_sha="2" * 40,
        objective="promotion bypass",
        tasks=(_task("PROMOTE", writes=("src/p.py",), requests_promotion=True),),
    )
    with pytest.raises(PlanRejected, match="cannot grant promotion authority"):
        validate_director_plan(promotion, available_capabilities={"python"})

    hidden_architecture = TaskPlan(
        project_id="fixture",
        base_sha="3" * 40,
        objective="hidden architecture change",
        tasks=(_task("ARCH", writes=("src/p.py",), architecture_change=True),),
    )
    with pytest.raises(PlanRejected, match="must be surfaced as a decision request"):
        validate_director_plan(hidden_architecture, available_capabilities={"python"})

    explicit_architecture = TaskPlan(
        project_id="fixture",
        base_sha="4" * 40,
        objective="explicit architecture request",
        tasks=(
            _task(
                "ARCH-OK",
                writes=("src/p.py",),
                architecture_change=True,
                architecture_decision_request="ADR required: replace storage interface",
            ),
        ),
    )
    validate_director_plan(explicit_architecture, available_capabilities={"python"})


def test_planned_task_requires_explicit_non_goals() -> None:
    with pytest.raises(ValueError, match="non_goals are required"):
        PlannedTask(
            task_id="NO-NONGOALS",
            objective="bad incomplete task",
            acceptance_criteria=("must pass",),
        )
