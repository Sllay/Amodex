#!/usr/bin/env python3
"""Minimal P0-06 Control Plane fixture for Northflank.

Research-only. Durable truth lives in PostgreSQL; process-local identity is
intentionally disposable so restart/redeploy behavior can be observed.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row

STARTED_AT = time.time()
INSTANCE_ID = str(uuid.uuid4())
DATABASE_URL = os.environ["DATABASE_URL"]
SECRET = os.environ.get("AMODEX_P0_06_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))


def db_connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS p0_06_events (
                    marker TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    writer_instance TEXT NOT NULL
                )
                """
            )


def db_ok() -> bool:
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                return int(cur.fetchone()["ok"]) == 1
    except Exception:
        return False


def provider_egress_ok() -> bool:
    # Synthetic unauthenticated network probe only. HTTP 4xx still proves DNS/TLS/egress.
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/",
        headers={"User-Agent": "amodex-p0-06"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return 200 <= int(response.status) < 500
    except urllib.error.HTTPError as exc:
        return 400 <= int(exc.code) < 500
    except Exception:
        return False


def secret_fingerprint() -> str | None:
    if not SECRET:
        return None
    return hashlib.sha256(SECRET.encode("utf-8")).hexdigest()[:12]


def write_event(marker: str, payload: dict) -> dict:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO p0_06_events(marker, payload, writer_instance)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (marker) DO UPDATE
                SET payload = EXCLUDED.payload,
                    writer_instance = EXCLUDED.writer_instance
                RETURNING marker, payload, created_at, writer_instance
                """,
                (marker, json.dumps(payload), INSTANCE_ID),
            )
            return dict(cur.fetchone())


def read_event(marker: str) -> dict | None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT marker, payload, created_at, writer_instance
                FROM p0_06_events
                WHERE marker = %s
                """,
                (marker,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


class Handler(BaseHTTPRequestHandler):
    server_version = "AmodexP006/1"

    def log_message(self, fmt, *args):
        print(f"http {self.address_string()} {fmt % args}", flush=True)

    def send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            database = db_ok()
            egress = provider_egress_ok()
            ok = database and bool(SECRET) and egress
            self.send_json(
                200 if ok else 503,
                {
                    "ok": ok,
                    "database": database,
                    "secret_injected": bool(SECRET),
                    "secret_fingerprint": secret_fingerprint(),
                    "provider_egress": egress,
                    "instance_id": INSTANCE_ID,
                    "hostname": socket.gethostname(),
                    "started_at": STARTED_AT,
                    "uptime_seconds": round(time.time() - STARTED_AT, 3),
                },
            )
            return
        if parsed.path == "/event":
            marker = (parse_qs(parsed.query).get("marker") or [""])[0]
            if not marker:
                self.send_json(400, {"error": "marker_required"})
                return
            event = read_event(marker)
            self.send_json(200 if event else 404, {"event": event})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/event":
            self.send_json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("content-length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_json"})
            return
        marker = str(body.get("marker") or "").strip()
        payload = body.get("payload")
        if not marker or not isinstance(payload, dict):
            self.send_json(400, {"error": "marker_and_object_payload_required"})
            return
        event = write_event(marker, payload)
        self.send_json(200, {"event": event, "current_instance": INSTANCE_ID})


def main() -> int:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        json.dumps(
            {
                "event": "p0_06_started",
                "instance_id": INSTANCE_ID,
                "port": PORT,
                "secret_injected": bool(SECRET),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
