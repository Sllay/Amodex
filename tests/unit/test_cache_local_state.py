from __future__ import annotations

from pathlib import Path

from starlette.requests import Request

from amodex.api.ui_cache_http import private_conditional_json
from amodex.api.workspace_cache_routes import _SERVICE_WORKER
from amodex.api.workspace_cache_web import _CACHE_PREBOOT_SCRIPT, augment_workspace_cache_html


def _request(*, etag: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if etag is not None:
        headers.append((b"if-none-match", etag.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/v1/ui/bootstrap",
            "raw_path": b"/v1/ui/bootstrap",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 443),
        }
    )


def test_private_conditional_json_emits_stable_private_etag_and_304() -> None:
    payload = {"chat": {"chat_id": "chat-1", "title": "A"}, "messages": [{"content": "oi"}]}
    first = private_conditional_json(_request(), payload)
    assert first.status_code == 200
    assert first.headers["etag"].startswith('"')
    assert "private" in first.headers["cache-control"]
    assert "public" not in first.headers["cache-control"]
    assert first.headers["vary"] == "Cookie"
    assert first.headers["x-amodex-cache"] == "private-revalidate"

    second = private_conditional_json(_request(etag=first.headers["etag"]), payload)
    assert second.status_code == 304
    assert second.body == b""
    assert second.headers["etag"] == first.headers["etag"]


def test_local_cache_uses_memory_indexeddb_and_explicit_etag_revalidation() -> None:
    source = _CACHE_PREBOOT_SCRIPT
    assert "const AC_MEMORY=new Map()" in source
    assert "indexedDB.open(AC_DB_NAME" in source
    assert "If-None-Match" in source
    assert "cache:'no-store'" in source
    assert "acSnapshotGet(`chat:${id}`)" in source
    assert "acMetaGet('lastTarget')" in source
    assert "acRestoreTarget(target)" in source
    assert "acSyncTarget(target)" in source
    assert "navigator.serviceWorker.register('/sw.js'" in source
    assert "window.AmodexLocalCache" in source


def test_service_worker_caches_shell_but_never_api_or_auth_payloads() -> None:
    source = _SERVICE_WORKER
    assert "amodex-private-shell-p0-v1" in source
    assert "url.pathname.startsWith('/v1/')" in source
    assert "url.pathname.startsWith('/auth/')" in source
    assert "cache.put('/',response.clone())" in source
    assert "caches.match('/')" in source
    assert "CLEAR_PRIVATE_CACHE" in source
    assert "cache.put(request,response.clone())" in source


def test_cache_augment_runs_before_boot_and_stability_runtime_remains_last() -> None:
    html = "<html><body><script>function boot(){}\nboot();\n</script></body></html>"
    augmented = augment_workspace_cache_html(html)
    assert augmented.index("const AC_DB_NAME") < augmented.index("boot();\n</script>")

    app_source = Path("src/amodex/api/workspace_app.py").read_text(encoding="utf-8")
    assert app_source.index("augment_workspace_v2_html(html)") < app_source.index("augment_workspace_cache_html(html)")
    assert app_source.index("augment_workspace_cache_html(html)") < app_source.index("augment_ui_stability_html(html)")


def test_logout_and_shell_have_explicit_private_cache_policy() -> None:
    source = Path("src/amodex/api/workspace_cache_routes.py").read_text(encoding="utf-8")
    assert 'response.headers["Clear-Site-Data"] = \'"cache", "storage"\'' in source
    assert 'response.headers["Cache-Control"] = "private, no-cache, max-age=0, must-revalidate"' in source
    assert "install_private_cache_boundary(base_app)" in Path("src/amodex/api/workspace_app.py").read_text(encoding="utf-8")
