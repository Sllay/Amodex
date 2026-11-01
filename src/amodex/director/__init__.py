"""Stage 3 Technical Director foundations.

This package deliberately contains no Hermes imports. Project authority, context
selection, DAG state, ownership and anti-loop behavior remain Amodex-owned.
"""

from .context import StaleContextError, compile_context
from .ingestion import IngestionError, ingest_repository
from .models import (
    ContextCapsule,
    DecisionRecord,
    DocumentAuthority,
    IngestionConflict,
    PlannedTask,
    ProjectContract,
    RepositorySnapshot,
    TaskPlan,
    TaskState,
)
from .orchestrator import (
    CapacityBlocked,
    DagOrchestrator,
    ErrorFingerprint,
    OwnershipConflict,
    OwnershipRegistry,
    PlanRejected,
    TaskNotReady,
    fingerprint_error,
    validate_director_plan,
)

__all__ = [
    "CapacityBlocked",
    "ContextCapsule",
    "DagOrchestrator",
    "DecisionRecord",
    "DocumentAuthority",
    "ErrorFingerprint",
    "IngestionConflict",
    "IngestionError",
    "OwnershipConflict",
    "OwnershipRegistry",
    "PlanRejected",
    "PlannedTask",
    "ProjectContract",
    "RepositorySnapshot",
    "StaleContextError",
    "TaskNotReady",
    "TaskPlan",
    "TaskState",
    "compile_context",
    "fingerprint_error",
    "ingest_repository",
    "validate_director_plan",
]
