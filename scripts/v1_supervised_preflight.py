#!/usr/bin/env python3
"""Offline host configuration check; never inference, quota verification or PASS.

Use on the host that would actually run Hermes. Codex and Render grants differ.
Only metadata leaves this command; no credential values or HTTP requests.
"""
import json
import os

from amodex.providers.capabilities import BillingClass
from amodex.providers.registry import stage5_credential_pools_from_env


def preflight() -> dict:
    pools=[]
    for pool in stage5_credential_pools_from_env():
        bound=bool(os.environ.get(pool.secret_env))
        pools.append({"provider":pool.provider,"pool_id":pool.pool_id,
            "billing_class":pool.billing_class.value,"credential_bound":bound,
            "configuration_present":pool.enabled and pool.billing_class==BillingClass.FREE and bound})
    present=any(pool["configuration_present"] for pool in pools)
    return {"status":"CONFIGURED_REQUIRES_LIVE_VALIDATION" if present else "BLOCKED_NO_APPROVED_FREE_PROVIDER",
            "pools":pools,"quota_verified":False,"inference_performed":False,
            "paid_fallback_allowed":False,"release_gate_passed":False}


if __name__=="__main__":
    result=preflight()
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(0 if result["status"]=="CONFIGURED_REQUIRES_LIVE_VALIDATION" else 2)
