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
