from amodex.api.multimodal_web import _MULTIMODAL_SCRIPT
from amodex.api.ui_stability_web import _STABILITY_SCRIPT, augment_ui_stability_html


def test_stability_layer_owns_final_send_and_preserves_structured_error_codes() -> None:
    page = augment_ui_stability_html("<html><head><style></style></head><body></body></html>")
    assert 'id="amodex-ui-runtime"' in page
    assert "window.AmodexApp=app;" in page
    assert "sendMessage=runtimeSendMessage;" in page
    assert "if(runtimeState.sending||!state.chat)return null;" in page
    assert "runtimeState.sending=true;" in page
    assert "send.disabled=true" in page
    assert "send.disabled=false" in page
    assert "x.code=d?.code" in page
    assert "x.status=r.status" in page
    assert "x.detail=d" in page
    assert "x.body=body" in page
    assert "ctx.owner=owner;" in page
    assert "await runHooks('afterOwnerSaved',ctx);" in page
    assert "void runHooks('afterSend',ctx).catch(runtimeError);" in page
    assert _STABILITY_SCRIPT.count("/messages`,{method:'POST'") == 1


def test_stability_layer_delegates_stt_preflight_to_multimodal_hook() -> None:
    assert "app.hooks.register('toggleMic'" in _MULTIMODAL_SCRIPT
    assert "if(!mm.status)await mmBoot();" in _MULTIMODAL_SCRIPT
    assert "if(!mm.status?.stt?.ready)" in _MULTIMODAL_SCRIPT
    assert "STT_NOT_CONFIGURED" in _MULTIMODAL_SCRIPT
    assert "Transcrição indisponível" in _MULTIMODAL_SCRIPT
    assert "Configure o STT gratuito no Render." in _MULTIMODAL_SCRIPT
    assert "await mmNativeToggleMic()" in _MULTIMODAL_SCRIPT
    assert "app.actions.toggleMic()" in _STABILITY_SCRIPT


def test_stability_layer_deduplicates_repeated_error_toasts() -> None:
    page = augment_ui_stability_html("<html><head><style></style></head><body></body></html>")
    assert "runtimeState.lastNotice===text" in page
    assert "now-runtimeState.lastNoticeAt<2500" in page
    assert "runtimeState.lastNotice=text" in page
    assert "runtimeState.lastNoticeAt=now" in page
