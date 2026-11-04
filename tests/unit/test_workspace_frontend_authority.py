from __future__ import annotations

from amodex.api.model_intelligence_web import _STEP4_SCRIPT
from amodex.api.multimodal_web import _MULTIMODAL_SCRIPT
from amodex.api.ui_stability_web import _STABILITY_SCRIPT
from amodex.api.workspace_v2 import _V2_PREBOOT_SCRIPT


def test_feature_layers_do_not_replace_shared_browser_actions() -> None:
    forbidden = (
        "sendMessage=async function",
        "selectChat=async function",
        "renderMessages=function",
        "const mmBaseSendMessage",
        "const miBaseSendMessage",
        "const mmBaseSelectChat",
        "const mmBaseRenderMessages",
    )
    for token in forbidden:
        assert token not in _MULTIMODAL_SCRIPT
        assert token not in _STEP4_SCRIPT


def test_runtime_is_shared_action_authority() -> None:
    required = (
        'id="amodex-ui-runtime"',
        "window.AmodexApp=app;",
        "state:AppState",
        "sendMessage=runtimeSendMessage;",
        "selectChat=runtimeSelectChat;",
        "renderMessages=runtimeRenderMessages;",
        "app.actions.sendMessage()",
        "app.actions.toggleMic()",
    )
    for token in required:
        assert token in _STABILITY_SCRIPT


def test_feature_layers_register_hooks_instead_of_wrapping_actions() -> None:
    multimodal_hooks = (
        "__amodexFeatureQueue",
        "beforeSend",
        "afterOwnerSaved",
        "afterChatSelected",
        "afterRenderMessages",
        "toggleMic",
    )
    for token in multimodal_hooks:
        assert token in _MULTIMODAL_SCRIPT

    assert "__amodexFeatureQueue" in _STEP4_SCRIPT
    assert "afterSend" in _STEP4_SCRIPT


def test_runtime_send_has_one_write_boundary_and_lock() -> None:
    assert "if(runtimeState.sending||!state.chat)return null;" in _STABILITY_SCRIPT
    assert "runtimeState.sending=true;" in _STABILITY_SCRIPT
    assert _STABILITY_SCRIPT.count("/messages`,{method:'POST'") == 1


def test_v2_shell_does_not_rebind_microphone_after_load() -> None:
    assert "mmToggleMic=async function" not in _V2_PREBOOT_SCRIPT
    assert "window.addEventListener('load'" not in _V2_PREBOOT_SCRIPT


def test_chat_model_provenance_hooks_and_no_model_text_mutation() -> None:
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT, _STEP4_CSS

    assert "app.hooks.register('afterChatSelected'" in _STEP4_SCRIPT
    assert "app.hooks.register('afterRenderMessages'" in _STEP4_SCRIPT
    assert "app.hooks.register('afterSend'" in _STEP4_SCRIPT
    assert "mi-route-footnote" in _STEP4_CSS
    assert "footer.textContent='('+result.label+')'" in _STEP4_SCRIPT
    assert "miDecisionsByChat" in _STEP4_SCRIPT
    assert "byMessage.get(m.message_id)" in _STEP4_SCRIPT
    assert "decision.fallback_used===true||policyFallback" in _STEP4_SCRIPT
    assert "chat_primary_policy=PRIMARY_UNAVAILABLE" in _STEP4_SCRIPT
    assert "miRememberDecisions(chatId,decision)" in _STEP4_SCRIPT
    assert "updated=await app.api" in _STEP4_SCRIPT
    assert "if(state.chat?.chat_id===chatId)" in _STEP4_SCRIPT
    assert "role:'AMODEX',content:" not in _STEP4_SCRIPT


def test_chat_provenance_javascript_parses() -> None:
    import shutil
    import subprocess

    import pytest
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    code = _STEP4_SCRIPT.split("<script", 1)[1].split(">", 1)[1].split("</script>", 1)[0]
    result = subprocess.run([node, "--check"], input=code, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_chat_model_footer_reports_provenance_without_false_legacy_fallback() -> None:
    import json
    import shutil
    import subprocess

    import pytest
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT
    from amodex.providers.catalog import AMODEX_PRIMARY_CHAT_ROUTE_ID

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    helpers = _STEP4_SCRIPT.split("const MI_PROVIDER_NAMES=", 1)[1].split(
        "function miRememberDecisions(", 1
    )[0]
    javascript = (
        "const MI_PRIMARY_ROUTE=" + json.dumps(AMODEX_PRIMARY_CHAT_ROUTE_ID) + ";\n"
        "const MI_PROVIDER_NAMES=" + helpers + "\n"
        "const base={status:'SUCCEEDED',result_message_id:'msg-1',final_route_id:MI_PRIMARY_ROUTE,"
        "selection_facts:[],fallback_used:false};\n"
        "const groq='groq:openai/gpt-oss-20b:groq-free-primary';\n"
        "const primary=miRouteFootnote(base);\n"
        "const historical=miRouteFootnote({...base,final_route_id:groq});\n"
        "const substitution=miRouteFootnote({...base,final_route_id:groq,"
        "selection_facts:['chat_primary_policy=PRIMARY_UNAVAILABLE']});\n"
        "const retry=miRouteFootnote({...base,final_route_id:groq,fallback_used:true});\n"
        "const missing=miRouteFootnote({...base,result_message_id:null});\n"
        "if(primary.label!=='modelo/nvidia-mistral-large-4'||primary.isFallback)throw Error('primary');\n"
        "if(historical.label!=='modelo/groq-gpt-oss-20b'||historical.isFallback)throw Error('legacy');\n"
        "if(substitution.label!=='fallback/groq-gpt-oss-20b'||!substitution.isFallback)throw Error('substitution');\n"
        "if(retry.label!=='fallback/groq-gpt-oss-20b'||!retry.isFallback)throw Error('retry');\n"
        "if(missing!==null)throw Error('missing');\n"
    )
    result = subprocess.run(
        [node, "-e", javascript], capture_output=True, text=True, check=False, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_send_guard_covers_inference_and_rejects_recent_identical_unanswered_turn() -> None:
    from amodex.api.ui_stability_web import _STABILITY_SCRIPT

    assert "await runHooks('afterSend',ctx);" in _STABILITY_SCRIPT
    assert "void runHooks('afterSend',ctx)" not in _STABILITY_SCRIPT
    assert "if(runtimeState.sending||!state.chat)return null;" in _STABILITY_SCRIPT
    assert "previous?.role==='OWNER'" in _STABILITY_SCRIPT
    assert "previousAgeMs<600000" in _STABILITY_SCRIPT
    assert _STABILITY_SCRIPT.count("/messages`,{method:'POST'") == 1


def test_routed_failure_messages_are_visible_without_fake_assistant_reply() -> None:
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT, _STEP4_CSS

    assert "const byOwner=new Map(" in _STEP4_SCRIPT
    assert "['FAILED','UNKNOWN_OUTCOME','STARTED']" in _STEP4_SCRIPT
    assert "decision.error_code||'resultado incerto'" in _STEP4_SCRIPT
    assert "Não reenviar automaticamente." in _STEP4_SCRIPT
    assert "mi-reply-state" in _STEP4_CSS
    assert "e?.code==='PROVIDER_OUTCOME_UNKNOWN'" in _STEP4_SCRIPT
    assert "await miLoadDecisions(chatId,app);" in _STEP4_SCRIPT
    assert "/messages`,{method:'POST'" not in _STEP4_SCRIPT


def test_routed_error_ui_javascript_parses() -> None:
    import shutil
    import subprocess

    import pytest

    from amodex.api.model_intelligence_web import _STEP4_SCRIPT
    from amodex.api.ui_stability_web import _STABILITY_SCRIPT

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable in CI")
    for script in (_STEP4_SCRIPT, _STABILITY_SCRIPT):
        body = script.split(">", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(
            [node, "--check"], input=body, text=True, capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr


def test_stale_chat_decision_has_explicit_safe_recovery_without_provider_retry() -> None:
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT, _STEP4_CSS

    assert "decision.status==='STARTED'&&Number.isFinite(age)&&age>=300000" in _STEP4_SCRIPT
    assert "Reconciliar estado" in _STEP4_SCRIPT
    assert "stale_after_seconds:300" in _STEP4_SCRIPT
    assert "await miLoadDecisions(chatId,window.AmodexApp)" in _STEP4_SCRIPT
    assert "mi-reconcile-btn" in _STEP4_CSS
    assert "model-intelligence/reconcile" in _STEP4_SCRIPT
    assert "Não reenviar automaticamente." in _STEP4_SCRIPT


def test_explicit_manual_recovery_reuses_saved_owner_message_and_never_auto_retries() -> None:
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT, _STEP4_CSS

    assert "Responder com outro modelo gratuito" in _STEP4_SCRIPT
    assert "window.confirm(" in _STEP4_SCRIPT
    assert "/respond/recover" in _STEP4_SCRIPT
    assert "{owner_message_id:m.message_id}" in _STEP4_SCRIPT
    assert "Number(decision.attempt_count)===1" in _STEP4_SCRIPT
    assert "decision.status==='UNKNOWN_OUTCOME'" in _STEP4_SCRIPT
    assert "newerDuplicate" in _STEP4_SCRIPT
    assert "mi-recover-btn" in _STEP4_CSS
    assert "miRememberDecisions(chatId,outcome)" in _STEP4_SCRIPT
    assert "/messages`,{method:'POST'" not in _STEP4_SCRIPT



def test_chat_progress_uses_measured_server_phases_not_invented_reasoning() -> None:
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT, _STEP4_CSS

    assert "miProgressLabel(decision)" in _STEP4_SCRIPT
    assert "progress:provider_wait:" in _STEP4_SCRIPT
    assert "Aguardando resposta do provedor" in _STEP4_SCRIPT
    assert "Aguardando confirmação do servidor" in _STEP4_SCRIPT
    assert "4000" in _STEP4_SCRIPT
    assert "clearInterval(interval)" in _STEP4_SCRIPT
    assert "mi-live-progress" in _STEP4_CSS
    assert "reasoning_content" not in _STEP4_SCRIPT
    assert "stream:true" not in _STEP4_SCRIPT


def test_chat_sse_is_read_only_and_falls_back_to_measured_polling() -> None:
    from amodex.api.model_intelligence_web import _STEP4_SCRIPT

    assert "new EventSource(url)" in _STEP4_SCRIPT
    assert "respond/events?owner_message_id=" in _STEP4_SCRIPT
    assert "feed.onmessage=event=>" in _STEP4_SCRIPT
    assert "feed.addEventListener('done'" in _STEP4_SCRIPT
    assert "feed.onerror=()" in _STEP4_SCRIPT
    assert "if(interval===null)interval=setInterval" in _STEP4_SCRIPT
    assert "feed?.close();" in _STEP4_SCRIPT
    assert "reasoning_content" not in _STEP4_SCRIPT
