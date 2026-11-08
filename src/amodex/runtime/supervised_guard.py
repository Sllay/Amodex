"""One bounded Hermes transport session. No alternate provider or retry path."""
from __future__ import annotations

import json
import time
from threading import RLock
from typing import Callable

import httpx


class SupervisedBlocked(RuntimeError):
    def __init__(self, code: str, *, unknown: bool = False):
        super().__init__(code)
        self.code, self.unknown = code, unknown


class HermesRequestGuard:
    def __init__(self, *, base_url: str, model: str, authorize: Callable,
                 started: Callable, acknowledged: Callable, seconds: float = 60):
        self.url, self.model = base_url.rstrip('/') + '/chat/completions', model
        self.authorize, self.started, self.acknowledged = authorize, started, acknowledged
        self.deadline = time.monotonic() + seconds
        self._lock = RLock()
        self.calls = self.tools = self.input_bytes = 0
        self.pending = False
        self.failure: SupervisedBlocked | None = None

    def deny(self, code, *, unknown=False):
        self.failure = SupervisedBlocked(code, unknown=unknown)
        raise self.failure

    def check(self):
        if self.failure:
            raise self.failure
        if self.pending:
            self.deny('PROVIDER_OUTCOME_UNKNOWN', unknown=True)
        if time.monotonic() >= self.deadline:
            self.deny('TIME_LIMIT')
        self.authorize()

    def tool_step(self):
        with self._lock:
            self.check()
            self.tools += 1
            if self.tools > 12:
                self.deny('TOOL_LIMIT')

    def request(self, request: httpx.Request):
        with self._lock:
            self._request(request)

    def _request(self, request: httpx.Request):
        self.check()
        if str(request.url) != self.url or request.method != 'POST':
            self.deny('PROVIDER_DESTINATION_BLOCKED')
        try:
            payload = json.loads(request.content)
            limit = payload.get('max_tokens', payload.get('max_completion_tokens'))
            if (payload.get('model') != self.model or type(limit) is not int or not 1 <= limit <= 512
                    or payload.get('n', 1) != 1):
                self.deny('PROVIDER_CONTRACT_BLOCKED')
        except (ValueError, TypeError):
            self.deny('PROVIDER_CONTRACT_BLOCKED')
        self.calls += 1
        self.input_bytes += len(request.content)
        # UTF-8 bytes provide a conservative upper bound on byte-tokenizer input.
        if self.calls > 6 or self.input_bytes > 48000:
            self.deny('MODEL_BUDGET_EXCEEDED')
        self.pending = True
        self.started(self.calls)  # Commit intent BEFORE HTTP transport; failure is fail-closed.

    def response(self, response: httpx.Response):
        try:
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 1_000_000 or time.monotonic() >= self.deadline:
                    self.deny('PROVIDER_RESPONSE_UNCONFIRMED', unknown=True)
                chunks.append(chunk)
            body = b''.join(chunks)
            # Cache a completely received body for the SDK, including SSE readers.
            response._content = body
            if response.status_code != 200:
                self.acknowledged(self.calls, response.status_code)
                self.pending = False
                self.deny('PROVIDER_HTTP_ERROR', unknown=response.status_code >= 500)
            if 'text/event-stream' in response.headers.get('content-type', ''):
                if not body.rstrip().endswith(b'data: [DONE]'):
                    self.deny('PROVIDER_STREAM_INCOMPLETE', unknown=True)
                frames = [json.loads(line[5:].strip()) for line in body.decode().splitlines()
                          if line.startswith('data:') and line[5:].strip() != '[DONE]']
                if (not frames or not all(isinstance(f.get('choices'), list) for f in frames)
                        or not any(c.get('finish_reason') for f in frames for c in f['choices'])):
                    self.deny('PROVIDER_RESPONSE_INVALID', unknown=True)
            else:
                choices = json.loads(body).get('choices')
                if (not isinstance(choices, list) or len(choices) != 1
                        or not isinstance(choices[0].get('message'), dict)):
                    self.deny('PROVIDER_RESPONSE_INVALID', unknown=True)
            self.acknowledged(self.calls, 200)
            self.pending = False
        except SupervisedBlocked:
            raise
        except Exception:
            self.deny('PROVIDER_RESPONSE_UNCONFIRMED', unknown=True)

    def configure_client(self, sdk):
        transport = getattr(sdk, '_client', None)
        if not isinstance(transport, httpx.Client):
            self.deny('HERMES_TRANSPORT_UNSUPPORTED')
        sdk.max_retries = 0
        # Pinned Hermes uses one attempt as the minimum, not zero attempts.
        transport.timeout = httpx.Timeout(10, connect=5)
        transport.event_hooks['request'] = [self.request]
        transport.event_hooks['response'] = [self.response]
        return sdk

    def attach(self, agent):
        self.configure_client(getattr(agent, 'client', None))
        factory = getattr(agent, '_create_openai_client', None)
        if callable(factory):
            # Pinned Hermes creates a NEW SDK/HTTPX client for every request.
            # Guarding just agent.client silently leaves that path unprotected.
            def guarded_factory(*args, **kwargs):
                self.check()
                return self.configure_client(factory(*args, **kwargs))
            agent._create_openai_client = guarded_factory
        agent._api_max_retries = 1
        agent.fallback_model = None
