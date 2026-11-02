from __future__ import annotations

from amodex.api.model_intelligence_web import _STEP4_SCRIPT
from amodex.api.multimodal_web import _MULTIMODAL_SCRIPT
from amodex.api.ui_stability_web import _STABILITY_SCRIPT


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
