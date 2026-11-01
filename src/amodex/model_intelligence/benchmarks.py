"""Deterministic, machine-verifiable Step 4 benchmark cases.

The suite only measures capabilities the current ProviderClient text interface can
actually exercise. It never fabricates vision/tool/long-context evidence.
"""

from __future__ import annotations

from amodex.model_intelligence.models import BenchmarkCaseSpec
from amodex.providers.capabilities import ModelCandidate, ModelCapability

SUITE_VERSION = "step4-live-v1"


def benchmark_cases(candidate: ModelCandidate) -> tuple[BenchmarkCaseSpec, ...]:
    declared = candidate.capabilities
    cases: list[BenchmarkCaseSpec] = []
    if ModelCapability.TEXT in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="text-exact-v1",
                capability=ModelCapability.TEXT,
                prompt="Return exactly AMODEX_TEXT_OK and nothing else.",
                expected="AMODEX_TEXT_OK",
                weight=1.0,
            )
        )
    if ModelCapability.STRUCTURED_OUTPUT in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="structured-exact-v1",
                capability=ModelCapability.STRUCTURED_OUTPUT,
                prompt=(
                    "This is a schema-following test. Return exactly the token "
                    "AMODEX_STRUCTURED_OK and nothing else."
                ),
                expected="AMODEX_STRUCTURED_OK",
                weight=1.2,
            )
        )
    if ModelCapability.PLANNING in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="planning-order-v1",
                capability=ModelCapability.PLANNING,
                prompt=(
                    "For the required sequence inspect, change, validate, return exactly "
                    "PLAN:inspect>change>validate and nothing else."
                ),
                expected="PLAN:inspect>change>validate",
                weight=1.1,
            )
        )
    if ModelCapability.RESEARCH in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="research-grounding-v1",
                capability=ModelCapability.RESEARCH,
                prompt=(
                    "Use only this supplied evidence: 'The project codename is ORBIT-741.' "
                    "Return exactly ORBIT-741 and do not add facts."
                ),
                expected="ORBIT-741",
                weight=1.0,
            )
        )
    if ModelCapability.CODING in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="coding-exact-v1",
                capability=ModelCapability.CODING,
                prompt=(
                    "Return exactly this single line of Python, with no markdown: "
                    "def add(a,b): return a+b"
                ),
                expected="def add(a,b): return a+b",
                weight=1.3,
            )
        )
    if ModelCapability.CORRECTION in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="correction-exact-v1",
                capability=ModelCapability.CORRECTION,
                prompt=(
                    "Repair the bug in 'def add(a,b): return a-b'. Return exactly the corrected "
                    "single line and no markdown."
                ),
                expected="def add(a,b): return a+b",
                weight=1.2,
            )
        )
    if ModelCapability.REVIEW in declared:
        cases.append(
            BenchmarkCaseSpec(
                case_id="review-detection-v1",
                capability=ModelCapability.REVIEW,
                prompt=(
                    "Review 'def add(a,b): return a-b' against requirement 'return the sum'. "
                    "Return exactly BUG:operator-minus-should-plus"
                ),
                expected="BUG:operator-minus-should-plus",
                weight=1.2,
            )
        )
    return tuple(cases)


def score_case(case: BenchmarkCaseSpec, text: str) -> tuple[float, bool, dict[str, object]]:
    normalized = text.strip().replace("\r\n", "\n")
    expected = case.expected.strip()
    passed = normalized == expected
    return (
        1.0 if passed else 0.0,
        passed,
        {
            "checker": "exact-normalized-v1",
            "expected_sha_material": expected,
            "received_length": len(normalized),
        },
    )
