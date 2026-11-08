from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from dataclasses import replace

import pytest

from amodex.model_intelligence.service import ModelIntelligenceService
from amodex.providers.capabilities import BillingClass, ProviderHealth


@pytest.mark.parametrize('change,allowed',[
    ({},True),({'billing_class':BillingClass.UNKNOWN},False),
    ({'quota_available':False},False),({'health':ProviderHealth.LIMITED},False),
    ({'age':timedelta(minutes=6)},False),({'age':timedelta(seconds=-1)},False),
    ({'provider':'gemini'},False),({'provider':'openrouter','model':'paid-model'},False),
    ({'provider':'openrouter','model':'measured:free'},True),
])
def test_measured_coding_selection_requires_fresh_explicit_free_allowance(change,allowed):
    now=datetime.now(timezone.utc)
    candidate=SimpleNamespace(provider=change.get('provider','groq'),model=change.get('model','measured'),credential_pool_id='pool')
    observation=SimpleNamespace(provider=candidate.provider,credential_pool_id='pool',billing_class=change.get('billing_class',BillingClass.FREE),
        health=change.get('health',ProviderHealth.HEALTHY),quota_available=change.get('quota_available',True),observed_at=now-change.get('age',timedelta(seconds=1)))
    service=object.__new__(ModelIntelligenceService)
    service._ranked_routes=lambda **kw:(SimpleNamespace(route_id='measured',provider=candidate.provider),)
    service._repository=SimpleNamespace(get_route=lambda id:{'fixture':True})
    service._candidate_from_row=lambda row:candidate
    service._routing_repository=SimpleNamespace(latest_availability=lambda:(observation,))
    service._utcnow=lambda:now
    assert (service.supervised_coding_candidate() is candidate)==allowed


def test_groq_preferred_only_while_eligible_and_quota_available():
    now=datetime.now(timezone.utc)
    candidates={p:SimpleNamespace(provider=p,model='measured',credential_pool_id=p+'-pool') for p in ('groq','nvidia')}
    service=object.__new__(ModelIntelligenceService)
    service._ranked_routes=lambda **kw:tuple(SimpleNamespace(route_id=p,provider=p) for p in ('nvidia','groq'))
    service._repository=SimpleNamespace(get_route=lambda key:key)
    service._candidate_from_row=lambda row:candidates[row]
    observations=[SimpleNamespace(provider=p,credential_pool_id=p+'-pool',billing_class=BillingClass.FREE,health=ProviderHealth.HEALTHY,quota_available=True,observed_at=now) for p in candidates]
    service._routing_repository=SimpleNamespace(latest_availability=lambda:observations)
    service._utcnow=lambda:now
    assert service.supervised_coding_candidate().provider=='groq'
    observations[0].quota_available=False
    assert service.supervised_coding_candidate().provider=='nvidia'
