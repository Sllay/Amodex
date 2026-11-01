"""Provider adapters, capability registry and deterministic routing."""

from .capabilities import (
    BillingClass,
    ModelCandidate,
    ModelCapability,
    ModelLifecycle,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
    RoutingDecision,
    RoutingRequest,
    RoutingUnavailable,
    WorkerProfile,
    WorkerProfileKind,
    stage5_worker_profiles,
)
from .catalog import stage5_declared_candidates
from .groq import GroqProvider
from .nvidia import NvidiaProvider
from .openrouter import OpenRouterProvider
from .registry import (
    CredentialPoolConfig,
    ProviderExecutionDenied,
    ProviderExecutionRegistry,
    stage5_credential_pools_from_env,
)
from .routing import CapabilityRouter

__all__ = [
    "BillingClass",
    "CapabilityRouter",
    "CredentialPoolConfig",
    "GroqProvider",
    "ModelCandidate",
    "ModelCapability",
    "ModelLifecycle",
    "ModelMeasurement",
    "NvidiaProvider",
    "OpenRouterProvider",
    "ProviderAvailability",
    "ProviderExecutionDenied",
    "ProviderExecutionRegistry",
    "ProviderHealth",
    "RoutingDecision",
    "RoutingRequest",
    "RoutingUnavailable",
    "WorkerProfile",
    "WorkerProfileKind",
    "stage5_credential_pools_from_env",
    "stage5_declared_candidates",
    "stage5_worker_profiles",
]
