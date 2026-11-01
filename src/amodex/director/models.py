from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum, StrEnum
from typing import Any, Iterable


class DocumentAuthority(IntEnum):
    UNCLASSIFIED = 0
    SOURCE = 10
    HISTORICAL = 20
    OVERVIEW = 40
    PHASE = 60
    FOCUSED_SPEC = 80
    ADR = 90
    ROOT_CONTRACT = 100


class FileKind(StrEnum):
    CONTRACT = "contract"
    DECISION = "decision"
    DOCUMENTATION = "documentation"
    SOURCE = "source"
    TEST = "test"
    INFRA = "infra"
    GENERATED = "generated"
    SECRET = "secret"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Provenance:
    path: str
    sha256: str
    start_line: int
    end_line: int
    authority: DocumentAuthority
    kind: FileKind


@dataclass(frozen=True, slots=True)
class RepositoryEntry:
    path: str
    sha256: str
    size_bytes: int
    line_count: int
    kind: FileKind
    authority: DocumentAuthority
    language: str | None = None
    symbols: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    safe_text: str | None = None


@dataclass(frozen=True, slots=True)
class RuleObservation:
    key: str
    value: str
    source: Provenance


@dataclass(frozen=True, slots=True)
class IngestionConflict:
    key: str
    competing_values: tuple[str, ...]
    sources: tuple[Provenance, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    title: str
    status: str
    source: Provenance
    supersedes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectContract:
    project_id: str
    base_sha: str
    root_contracts: tuple[str, ...]
    effective_rules: dict[str, str]
    conflicts: tuple[IngestionConflict, ...]


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    project: ProjectContract
    entries: tuple[RepositoryEntry, ...]
    decisions: tuple[DecisionRecord, ...]

    @property
    def by_path(self) -> dict[str, RepositoryEntry]:
        return {entry.path: entry for entry in self.entries}

    @property
    def total_context_chars(self) -> int:
        return sum(len(entry.safe_text or "") for entry in self.entries)


@dataclass(frozen=True, slots=True)
class ContextSection:
    path: str
    reason: str
    content: str
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ContextCapsule:
    project_id: str
    base_sha: str
    task_id: str
    sections: tuple[ContextSection, ...]
    excluded_paths: tuple[str, ...]
    selected_chars: int
    naive_full_chars: int

    @property
    def reduction_ratio(self) -> float:
        if self.naive_full_chars <= 0:
            return 0.0
        return 1.0 - (self.selected_chars / self.naive_full_chars)


class TaskState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


@dataclass(frozen=True, slots=True)
class PlannedTask:
    task_id: str
    objective: str
    dependencies: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    worker_profile: str | None = None
    reason_category: str | None = None
    architecture_change: bool = False
    architecture_decision_request: str | None = None
    requires_paid_resources: bool = False
    requests_promotion: bool = False

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must be non-empty")
        if not self.objective.strip():
            raise ValueError(f"{self.task_id}: objective must be non-empty")
        if self.worker_profile is not None and not self.worker_profile.strip():
            raise ValueError(f"{self.task_id}: worker_profile cannot be blank")
        if not self.non_goals:
            raise ValueError(f"{self.task_id}: non_goals are required")
        if not self.acceptance_criteria:
            raise ValueError(f"{self.task_id}: acceptance_criteria are required")
        for field_name in (
            "dependencies",
            "write_paths",
            "read_paths",
            "non_goals",
            "required_capabilities",
            "acceptance_criteria",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{self.task_id}: duplicate {field_name} entries")


@dataclass(frozen=True, slots=True)
class TaskPlan:
    project_id: str
    base_sha: str
    objective: str
    tasks: tuple[PlannedTask, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must be non-empty")
        if not self.objective.strip():
            raise ValueError("plan objective must be non-empty")
        if not self.tasks:
            raise ValueError("task plan must contain at least one task")
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        known = set(ids)
        for task in self.tasks:
            unknown = set(task.dependencies) - known
            if unknown:
                raise ValueError(f"{task.task_id}: unknown dependencies {sorted(unknown)!r}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        deps = {task.task_id: set(task.dependencies) for task in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError(f"task DAG contains a cycle at {task_id}")
            visiting.add(task_id)
            for dependency in deps[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in deps:
            visit(task_id)

    @property
    def by_id(self) -> dict[str, PlannedTask]:
        return {task.task_id: task for task in self.tasks}

    def ancestors(self, task_id: str) -> set[str]:
        result: set[str] = set()
        by_id = self.by_id

        def walk(current: str) -> None:
            for dependency in by_id[current].dependencies:
                if dependency not in result:
                    result.add(dependency)
                    walk(dependency)

        walk(task_id)
        return result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskPlan":
        tasks = tuple(PlannedTask(**task) for task in payload["tasks"])
        return cls(
            project_id=payload["project_id"],
            base_sha=payload["base_sha"],
            objective=payload["objective"],
            tasks=tasks,
            schema_version=int(payload.get("schema_version", 1)),
        )


def tupled(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(values)
