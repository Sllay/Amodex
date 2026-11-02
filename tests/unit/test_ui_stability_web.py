from amodex.api.ui_stability_web import augment_ui_stability_html


def test_stability_layer_owns_final_send_and_preserves_structured_error_codes() -> None:
    page = augment_ui_stability_html("<html><head><style></style></head><body></body></html>")
    assert 'id="amodex-ui-stability"' in page
    assert "sendMessage=async function()" in page
    assert "uiStable.sending" in page
    assert "send.disabled=true" in page
    assert "send.disabled=false" in page
    assert "x.code=d?.code" in page
    assert "owner_message_id:owner.message_id" in page
    assert "automatic_chat_inference!=='READY'" in page
    assert "Mensagem salva · resposta automática aguardando benchmark" in page


def test_stability_layer_blocks_unconfigured_stt_before_recording() -> None:
    page = augment_ui_stability_html("<html><head><style></style></head><body></body></html>")
    assert "if(!mm.status?.stt?.ready)" in page
    assert "Transcrição indisponível" in page
    assert "STT_NOT_CONFIGURED" in page
    assert "return await uiNativeMic()" in page


def test_stability_layer_deduplicates_repeated_error_toasts() -> None:
    page = augment_ui_stability_html("<html><head><style></style></head><body></body></html>")
    assert "uiStable.lastNotice===text" in page
    assert "now-uiStable.lastNoticeAt<2500" in page
