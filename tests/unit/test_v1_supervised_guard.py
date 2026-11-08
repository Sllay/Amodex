"""Transport and authority bounds; every HTTP transport is simulated."""
import json
from types import SimpleNamespace

import httpx
import pytest

from amodex.runtime.supervised_guard import HermesRequestGuard, SupervisedBlocked

URL='https://provider.invalid/v1'


def harness(responder=None):
    effects=[]
    guard=HermesRequestGuard(base_url=URL,model='approved-free',authorize=lambda:None,
        started=lambda n:effects.append(('STARTED',n)),acknowledged=lambda n,s:effects.append(('ACK',n,s)))
    requests=[]
    def respond(req):
        requests.append(req)
        return responder(req) if responder else httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
    client=httpx.Client(transport=httpx.MockTransport(respond))
    agent=SimpleNamespace(client=SimpleNamespace(_client=client,max_retries=3))
    guard.attach(agent)
    return guard,client,requests,effects,agent


def call(client,**changes):
    return client.post(URL+'/chat/completions',json={'model':'approved-free','max_tokens':512,**changes})


def test_exact_route_budget_and_disabled_retry():
    g,c,requests,effects,a=harness()
    try:
        for _ in range(6):assert call(c).status_code==200
        with pytest.raises(SupervisedBlocked,match='BUDGET'):call(c)
        assert len(requests)==6 and len(effects)==12 and a.client.max_retries==0 and a._api_max_retries==1
        assert a.fallback_model is None
    finally:c.close()


@pytest.mark.parametrize('change',[{'model':'paid-model'},{'max_tokens':513},{'max_tokens':None},{'n':2}])
def test_model_or_token_escalation_blocked_before_transport(change):
    g,c,requests,effects,_=harness()
    try:
        with pytest.raises(SupervisedBlocked):call(c,**change)
        assert not requests and not effects
    finally:c.close()


def test_destination_fallback_or_helper_call_denied():
    g,c,requests,effects,_=harness()
    try:
        with pytest.raises(SupervisedBlocked):c.post('https://other.invalid/chat/completions',json={})
        assert not requests
    finally:c.close()


def test_timeout_latches_pending_and_blocks_sdk_retry_and_tools():
    def fail(req):raise httpx.ReadTimeout('must not appear in operator evidence')
    g,c,requests,effects,_=harness(fail)
    try:
        with pytest.raises(httpx.ReadTimeout):call(c)
        with pytest.raises(SupervisedBlocked) as exc:call(c)
        assert exc.value.unknown and len(requests)==1 and effects==[('STARTED',1)]
        with pytest.raises(SupervisedBlocked):g.tool_step()
    finally:c.close()


@pytest.mark.parametrize('status',[401,429,500])
def test_http_failure_latches_and_never_retries(status):
    g,c,requests,effects,_=harness(lambda req:httpx.Response(status,json={'error':'private provider body'}))
    try:
        with pytest.raises(SupervisedBlocked) as exc:call(c)
        assert exc.value.unknown==(status>=500)
        with pytest.raises(SupervisedBlocked):call(c)
        assert len(requests)==1 and effects==[('STARTED',1),('ACK',1,status)]
    finally:c.close()


def test_authority_loss_blocks_http_and_tool_before_effect():
    g,c,requests,effects,_=harness()
    def no():raise SupervisedBlocked('AUTHORITY_UNCONFIRMED')
    g.authorize=no
    try:
        with pytest.raises(SupervisedBlocked):call(c)
        with pytest.raises(SupervisedBlocked):g.tool_step()
        assert not requests and not effects
    finally:c.close()


def test_sse_is_fully_received_before_ack_and_incomplete_body_is_unknown():
    for body,complete in [(b'data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',True),(b'data: partial\n',False)]:
        g,c,requests,effects,_=harness(lambda req:httpx.Response(200,content=body,headers={'content-type':'text/event-stream'}))
        try:
            if complete:assert call(c).content==body and not g.pending
            else:
                with pytest.raises(SupervisedBlocked) as exc:call(c)
                assert exc.value.unknown and g.pending
        finally:c.close()


def test_input_output_size_and_tool_limits():
    g,c,requests,effects,_=harness()
    try:
        with pytest.raises(SupervisedBlocked):call(c,messages=['x'*48001])
        assert not requests
    finally:c.close()
    g,c,requests,effects,_=harness(lambda req:httpx.Response(200,content=b'x'*1000001))
    try:
        with pytest.raises(SupervisedBlocked) as exc:call(c)
        assert exc.value.unknown
    finally:c.close()
    g,c,*_=harness()
    try:
        for _ in range(12):g.tool_step()
        with pytest.raises(SupervisedBlocked):g.tool_step()
    finally:c.close()


def test_unsupported_sdk_fails_closed_without_call():
    g,c,requests,effects,_=harness()
    try:
        with pytest.raises(SupervisedBlocked):g.attach(SimpleNamespace(client=object()))
        assert not requests
    finally:c.close()


def test_pinned_hermes_per_request_client_factory_is_guarded():
    g,c,requests,effects,a=harness()
    made=[]
    def fresh(*args,**kw):
        sdk=SimpleNamespace(_client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]}))),max_retries=4)
        made.append(sdk);return sdk
    a._create_openai_client=fresh
    g.attach(a)
    try:
        sdk=a._create_openai_client({},reason='chat_completion_request',shared=False)
        assert sdk.max_retries==0
        assert call(sdk._client).status_code==200
        assert effects==[('STARTED',1),('ACK',1,200)]
        with pytest.raises(SupervisedBlocked):call(a._create_openai_client({})._client,model='paid')
    finally:
        c.close()
        for sdk in made:sdk._client.close()
