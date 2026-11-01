from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Any, Iterable

from .models import PlannedTask, TaskPlan, TaskState


class PlanRejected(RuntimeError):
    pass


class OwnershipConflict(RuntimeError):
    pass


class TaskNotReady(RuntimeError):
    pass


class CapacityBlocked(RuntimeError):
    pass


_ALLOWED_REASON_CATEGORIES = {
    "BUG",
    "FAILING_TEST",
    "SECURITY_DEFECT",
    "PERFORMANCE_REGRESSION",
    "DEPENDENCY_BREAKAGE",
    "NEW_REQUIREMENT",
    "USER_REQUEST",
    "ARCHITECTURE_APPROVAL",
}


def _normalized(path: str) -> str:
    normalized = PurePosixPath(path).as_posix().lstrip("./")
    while normalized.startswith("../"):
        normalized = normalized[3:]
    return normalized


def _patterns_overlap(left: str, right: str) -> bool:
    left = _normalized(left)
    right = _normalized(right)
    if left == right:
        return True
    left_prefix = left[:-3] if left.endswith("/**") else None
    right_prefix = right[:-3] if right.endswith("/**") else None
    if left_prefix is not None and (right == left_prefix or right.startswith(left_prefix.rstrip("/") + "/")):
        return True
    if right_prefix is not None and (left == right_prefix or left.startswith(right_prefix.rstrip("/") + "/")):
        return True
    if "*" in left and fnmatch(right, left):
        return True
    if "*" in right and fnmatch(left, right):
        return True
    return False


def _tasks_can_run_in_parallel(plan: TaskPlan, left: PlannedTask, right: PlannedTask) -> bool:
    return left.task_id not in plan.ancestors(right.task_id) and right.task_id not in plan.ancestors(left.task_id)


def validate_director_plan(
    plan: TaskPlan,
    *,
    available_capabilities: Iterable[str],
    frozen_paths: Iterable[str] = (),
) -> None:
    capabilities = set(available_capabilities)
    frozen = tuple(frozen_paths)
    by_id = plan.by_id
    for task in plan.tasks:
        missing = set(task.required_capabilities) - capabilities
        if missing:
            raise PlanRejected(f"{task.task_id}: unavailable capabilities {sorted(missing)!r}")
        if task.requires_paid_resources:
            raise PlanRejected(f"{task.task_id}: Director cannot authorize paid resources")
        if task.requests_promotion:
            raise PlanRejected(f"{task.task_id}: Director cannot grant promotion authority")
        if task.architecture_change and not (task.architecture_decision_request or "").strip():
            raise PlanRejected(f"{task.task_id}: architecture change must be surfaced as a decision request")
        if any(_patterns_overlap(write, protected) for write in task.write_paths for protected in frozen):
            if task.reason_category not in _ALLOWED_REASON_CATEGORIES:
                raise PlanRejected(f"{task.task_id}: frozen path change lacks accepted reason category")
    for index, left in enumerate(plan.tasks):
        for right in plan.tasks[index + 1 :]:
            if not _tasks_can_run_in_parallel(plan, left, right):
                continue
            if any(_patterns_overlap(a, b) for a in left.write_paths for b in right.write_paths):
                raise PlanRejected(
                    f"parallel tasks {left.task_id} and {right.task_id} have overlapping write scopes"
                )
    for task_id, task in by_id.items():
        if task_id in task.dependencies:
            raise PlanRejected(f"{task_id}: self dependency")


@dataclass(frozen=True, slots=True)
class WriterLease:
    task_id: str
    paths: tuple[str, ...]
    fencing_token: int


class OwnershipRegistry:
    def __init__(self) -> None:
        self._leases: dict[str, WriterLease] = {}
        self._next_token = 1

    def acquire(self, task: PlannedTask) -> WriterLease:
        for existing in self._leases.values():
            if any(_patterns_overlap(a, b) for a in task.write_paths for b in existing.paths):
                raise OwnershipConflict(
                    f"{task.task_id} overlaps writer {existing.task_id}: {task.write_paths!r} vs {existing.paths!r}"
                )
        lease = WriterLease(task.task_id, tuple(task.write_paths), self._next_token)
        self._next_token += 1
        self._leases[task.task_id] = lease
        return lease

    def release(self, task_id: str) -> None:
        self._leases.pop(task_id, None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "next_token": self._next_token,
            "leases": [asdict(lease) for lease in sorted(self._leases.values(), key=lambda item: item.task_id)],
        }

    @classmethod
    def restore(cls, payload: dict[str, Any]) -> "OwnershipRegistry":
        registry = cls()
        registry._next_token = int(payload["next_token"])
        for raw in payload["leases"]:
            lease = WriterLease(raw["task_id"], tuple(raw["paths"]), int(raw["fencing_token"]))
            registry._leases[lease.task_id] = lease
        return registry


@dataclass(frozen=True, slots=True)
class ErrorFingerprint:
    digest: str
    validator_id: str
    error_class: str
    path: str | None
    failing_test: str | None


def fingerprint_error(
    *,
    validator_id: str,
    error_class: str,
    message: str,
    path: str | None = None,
    failing_test: str | None = None,
) -> ErrorFingerprint:
    stable_tokens = " ".join(
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.:-]*", message)
        if not token.isdigit()
    )[:512]
    material = "|".join((validator_id, error_class, stable_tokens, path or "", failing_test or ""))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return ErrorFingerprint(digest, validator_id, error_class, path, failing_test)


class DagOrchestrator:
    def __init__(
        self,
        plan: TaskPlan,
        *,
        max_active_tasks: int = 2,
        equivalent_failure_limit: int = 2,
        ownership: OwnershipRegistry | None = None,
    ) -> None:
        if max_active_tasks < 1:
            raise ValueError("max_active_tasks must be >= 1")
        if equivalent_failure_limit < 1:
            raise ValueError("equivalent_failure_limit must be >= 1")
        self.plan = plan
        self.max_active_tasks = max_active_tasks
        self.equivalent_failure_limit = equivalent_failure_limit
        self.ownership = ownership or OwnershipRegistry()
        self.states: dict[str, TaskState] = {task.task_id: TaskState.PENDING for task in plan.tasks}
        self.failure_counts: dict[str, dict[str, int]] = {task.task_id: {} for task in plan.tasks}
        self._refresh_ready()

    def _refresh_ready(self) -> None:
        by_id = self.plan.by_id
        for task_id, state in list(self.states.items()):
            if state not in {TaskState.PENDING, TaskState.READY}:
                continue
            task = by_id[task_id]
            if all(self.states[dependency] == TaskState.SUCCEEDED for dependency in task.dependencies):
                self.states[task_id] = TaskState.READY
            else:
                self.states[task_id] = TaskState.PENDING

    @property
    def active_count(self) -> int:
        return sum(state == TaskState.RUNNING for state in self.states.values())

    def ready_tasks(self) -> tuple[PlannedTask, ...]:
        self._refresh_ready()
        return tuple(task for task in self.plan.tasks if self.states[task.task_id] == TaskState.READY)

    def start(self, task_id: str) -> WriterLease:
        self._refresh_ready()
        if self.states[task_id] != TaskState.READY:
            raise TaskNotReady(f"{task_id} is {self.states[task_id]}")
        if self.active_count >= self.max_active_tasks:
            raise CapacityBlocked(f"capacity {self.max_active_tasks} exhausted")
        task = self.plan.by_id[task_id]
        lease = self.ownership.acquire(task)
        self.states[task_id] = TaskState.RUNNING
        return lease

    def succeed(self, task_id: str) -> None:
        if self.states[task_id] != TaskState.RUNNING:
            raise TaskNotReady(f"{task_id} is not running")
        self.ownership.release(task_id)
        self.states[task_id] = TaskState.SUCCEEDED
        self._refresh_ready()

    def fail(self, task_id: str, fingerprint: ErrorFingerprint) -> TaskState:
        if self.states[task_id] != TaskState.RUNNING:
            raise TaskNotReady(f"{task_id} is not running")
        self.ownership.release(task_id)
        counts = self.failure_counts[task_id]
        counts[fingerprint.digest] = counts.get(fingerprint.digest, 0) + 1
        if counts[fingerprint.digest] >= self.equivalent_failure_limit:
            self.states[task_id] = TaskState.ESCALATED
        else:
            self.states[task_id] = TaskState.CORRECTION_REQUIRED
        self._refresh_ready()
        return self.states[task_id]

    def schedule_correction(self, task_id: str) -> None:
        if self.states[task_id] != TaskState.CORRECTION_REQUIRED:
            raise TaskNotReady(f"{task_id} does not require correction")
        self.states[task_id] = TaskState.READY

    def snapshot(self) -> dict[str, Any]:
        return {
            "states": {task_id: state.value for task_id, state in self.states.items()},
            "failure_counts": self.failure_counts,
            "max_active_tasks": self.max_active_tasks,
            "equivalent_failure_limit": self.equivalent_failure_limit,
            "ownership": self.ownership.snapshot(),
        }

    @classmethod
    def restore(cls, plan: TaskPlan, payload: dict[str, Any]) -> "DagOrchestrator":
        orchestrator = cls(
            plan,
            max_active_tasks=int(payload["max_active_tasks"]),
            equivalent_failure_limit=int(payload["equivalent_failure_limit"]),
            ownership=OwnershipRegistry.restore(payload["ownership"]),
        )
        orchestrator.states = {
            task_id: TaskState(value) for task_id, value in payload["states"].items()
        }
        orchestrator.failure_counts = {
            task_id: {digest: int(count) for digest, count in counts.items()}
            for task_id, counts in payload["failure_counts"].items()
        }
        return orchestrator
