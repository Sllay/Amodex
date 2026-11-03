"""Provider execution registry for Stage 5.

Credential configs contain only secret *references* (environment variable names).
Secret values are resolved at the final provider execution boundary and are never
stored in model measurements, TaskPackages, or routing decisions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .base import ProviderClient
from .capabilities import BillingClass, ModelCandidate
from .gemini import GeminiProvider
from .groq import GroqProvider
from .nvidia import NvidiaProvider
from .openrouter import OpenRouterProvider


@dataclass(frozen=True, slots=True)
class CredentialPoolConfig:
    pool_id: str
    provider: str
    billing_class: BillingClass
    secret_env: str
    enabled: bool = True


class ProviderExecutionDenied(RuntimeError):
    pass


class ProviderExecutionRegistry:
    def __init__(self, pools: tuple[CredentialPoolConfig, ...]) -> None:
        self._pools = {pool.pool_id: pool for pool in pools}

    def client_for(self, candidate: ModelCandidate) -> ProviderClient:
        pool = self._pools.get(candidate.credential_pool_id)
        if pool is None or not pool.enabled:
            raise ProviderExecutionDenied("credential pool unavailable")
        if pool.provider != candidate.provider:
            raise ProviderExecutionDenied("credential pool/provider mismatch")
        if candidate.cost_class != BillingClass.FREE or pool.billing_class != BillingClass.FREE:
            raise ProviderExecutionDenied("zero-cost execution requires free route and free credential pool")
        api_key = os.environ.get(pool.secret_env, "")
        if not api_key:
            raise ProviderExecutionDenied(f"secret reference {pool.secret_env} is unresolved")
        if pool.provider == "gemini":
            return GeminiProvider(api_key=api_key)
        if pool.provider == "groq":
            return GroqProvider(api_key=api_key)
        if pool.provider == "openrouter":
            return OpenRouterProvider(
                api_key=api_key,
                require_json_object=candidate.model != "openrouter/free",
            )
        if pool.provider == "nvidia":
            return NvidiaProvider(api_key=api_key)
        raise ProviderExecutionDenied(f"unsupported provider adapter: {pool.provider}")


def stage5_credential_pools_from_env() -> tuple[CredentialPoolConfig, ...]:
    return (
        _pool("gemini-free-primary", "gemini", "GEMINI_API_KEY", "AMODEX_GEMINI_BILLING_CLASS"),
        _pool("groq-free-primary", "groq", "GROQ_API_KEY", "AMODEX_GROQ_BILLING_CLASS"),
        _pool(
            "openrouter-free-primary",
            "openrouter",
            "OPENROUTER_API_KEY",
            "AMODEX_OPENROUTER_BILLING_CLASS",
        ),
        _pool("nvidia-free-primary", "nvidia", "NVIDIA_API_KEY", "AMODEX_NVIDIA_BILLING_CLASS"),
    )


def _pool(pool_id: str, provider: str, secret_env: str, billing_env: str) -> CredentialPoolConfig:
    raw = os.environ.get(billing_env, "unknown").strip().lower()
    try:
        billing = BillingClass(raw)
    except ValueError:
        billing = BillingClass.UNKNOWN
    return CredentialPoolConfig(
        pool_id=pool_id,
        provider=provider,
        billing_class=billing,
        secret_env=secret_env,
    )
