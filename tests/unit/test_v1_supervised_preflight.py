"""Configuration is never presented as real inference or release acceptance."""
import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location("release_preflight",Path("scripts/v1_supervised_preflight.py"))
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("key,billing,expected", [(False,None,False),(True,None,False),
    (True,"paid",False),(True,"free",True)])
def test_preflight_never_invokes_model_or_declares_quota_and_release(monkeypatch,key,billing,expected):
    for provider in ("GEMINI","GROQ","OPENROUTER","NVIDIA"):
        monkeypatch.delenv(provider+"_API_KEY",raising=False)
        monkeypatch.delenv("AMODEX_"+provider+"_BILLING_CLASS",raising=False)
    if key:monkeypatch.setenv("GROQ_API_KEY","synthetic-private-value")
    if billing:monkeypatch.setenv("AMODEX_GROQ_BILLING_CLASS",billing)
    def prohibited(*a,**k):raise AssertionError("preflight must be offline")
    monkeypatch.setattr("httpx.request",prohibited)
    monkeypatch.setattr("httpx.post",prohibited)
    result=module.preflight()
    assert any(p["configuration_present"] for p in result["pools"])==expected
    assert not result["quota_verified"] and not result["inference_performed"]
    assert not result["paid_fallback_allowed"] and not result["release_gate_passed"]
    assert "synthetic-private-value" not in json.dumps(result)
