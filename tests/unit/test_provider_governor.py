from amodex.providers.governor import AdmissionStatus, ProviderGovernor, ProviderRoute


def test_default_route_is_free_and_admitted() -> None:
    decision = ProviderGovernor.stage1_default().admit()
    assert decision.status == AdmissionStatus.ADMITTED
    assert decision.route is not None
    assert decision.route.cost_class == "free"
    assert decision.route.model == "gemini-3.5-flash-lite"


def test_free_quota_exhaustion_waits_instead_of_falling_back_paid() -> None:
    decision = ProviderGovernor.stage1_default().admit(free_quota_available=False)
    assert decision.status == AdmissionStatus.WAITING_PROVIDER
    assert decision.route is None
    assert "paid fallback forbidden" in decision.reason


def test_paid_only_configuration_fails_closed() -> None:
    governor = ProviderGovernor((ProviderRoute(provider="example", model="paid-model", cost_class="paid"),))
    decision = governor.admit()
    assert decision.status == AdmissionStatus.BLOCKED
    assert decision.route is None
