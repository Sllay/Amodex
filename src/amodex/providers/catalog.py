"""Conservative Stage 5 provider/model catalog.

Declared capabilities come from provider documentation snapshots. Every route is
BENCHMARK_PENDING by default: catalog discovery alone never makes a model eligible.
"""

from __future__ import annotations

from .capabilities import (
    BillingClass,
    ModelCandidate,
    ModelCapability,
    ModelLifecycle,
)


def stage5_declared_candidates() -> tuple[ModelCandidate, ...]:
    return (
        ModelCandidate(
            provider="gemini",
            model="gemini-3.5-flash-lite",
            credential_pool_id="gemini-free-primary",
            cost_class=BillingClass.FREE,
            capabilities=frozenset(
                {
                    ModelCapability.TEXT,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.PLANNING,
                    ModelCapability.RESEARCH,
                }
            ),
            context_capacity_tokens=100_000,
            lifecycle=ModelLifecycle.BENCHMARK_PENDING,
            provenance="Amodex Stage 1 validated route; re-benchmark required for Stage 5 roles",
        ),
        ModelCandidate(
            provider="groq",
            model="openai/gpt-oss-20b",
            credential_pool_id="groq-free-primary",
            cost_class=BillingClass.FREE,
            capabilities=frozenset(
                {
                    ModelCapability.TEXT,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.CODING,
                    ModelCapability.CORRECTION,
                    ModelCapability.PLANNING,
                    ModelCapability.RESEARCH,
                }
            ),
            context_capacity_tokens=131_072,
            lifecycle=ModelLifecycle.BENCHMARK_PENDING,
            provenance="Groq official docs snapshot 2026-11-01; eligibility still requires measured evidence and free credential classification",
        ),
        ModelCandidate(
            provider="openrouter",
            model="openrouter/free",
            credential_pool_id="openrouter-free-primary",
            cost_class=BillingClass.FREE,
            capabilities=frozenset(
                {
                    ModelCapability.TEXT,
                    ModelCapability.RESEARCH,
                    ModelCapability.PLANNING,
                }
            ),
            context_capacity_tokens=32_768,
            lifecycle=ModelLifecycle.BENCHMARK_PENDING,
            provenance="OpenRouter free router snapshot 2026-11-01; capabilities intentionally conservative because routed backend may vary",
        ),
        ModelCandidate(
            provider="nvidia",
            model="mistralai/mistral-large-4-1t-a52b",
            credential_pool_id="nvidia-free-primary",
            cost_class=BillingClass.FREE,
            capabilities=frozenset(
                {
                    ModelCapability.TEXT,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.CODING,
                    ModelCapability.CORRECTION,
                    ModelCapability.PLANNING,
                    ModelCapability.RESEARCH,
                    ModelCapability.REVIEW,
                    ModelCapability.VISION,
                    ModelCapability.LONG_CONTEXT,
                }
            ),
            context_capacity_tokens=1_000_000,
            lifecycle=ModelLifecycle.BENCHMARK_PENDING,
            provenance="NVIDIA NIM Mistral Large 4 official docs snapshot 2026-11-01; Build.NVIDIA labels a free endpoint, but Governor still requires free credential/quota evidence",
        ),
    )
