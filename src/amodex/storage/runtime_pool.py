"""Small per-process PostgreSQL pool for Amodex runtime traffic.

The production database endpoint may already be transaction-pooled upstream, but
opening a brand-new client connection for every repository method still adds
avoidable setup/teardown overhead. This module keeps at most one or two physical
connections per application process and preserves the existing
``with psycopg.connect(...) as conn`` repository contract.

The installer is intentionally runtime-only: migration scripts keep using normal
one-shot psycopg connections in their own process.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from threading import Condition, Lock
from time import monotonic
from typing import Any

import psycopg
from psycopg import pq
from psycopg.rows import tuple_row


class RuntimePoolExhausted(RuntimeError):
    """Raised when every bounded runtime connection stays checked out too long."""


_ORIGINAL_CONNECT = psycopg.connect
_REGISTRY_LOCK = Lock()
_POOLS: dict[str, "RuntimeConnectionPool"] = {}
_INSTALLED = False
_POOL_MAX_SIZE = 2
_POOL_TIMEOUT_SECONDS = 10.0


class RuntimeConnectionPool:
    """Tiny lazy LIFO pool with a strict per-process physical connection cap."""

    def __init__(
        self,
        dsn: str,
        *,
        max_size: int = 2,
        timeout_seconds: float = 10.0,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        if max_size not in {1, 2}:
            raise ValueError("runtime PostgreSQL pool size must be 1 or 2")
        if timeout_seconds <= 0:
            raise ValueError("runtime PostgreSQL pool timeout must be positive")
        self._dsn = dsn
        self._max_size = max_size
        self._timeout_seconds = timeout_seconds
        self._connect = connect_factory or _ORIGINAL_CONNECT
        self._condition = Condition()
        self._available: list[Any] = []
        self._physical_count = 0
        self._closed = False

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def physical_count(self) -> int:
        with self._condition:
            return self._physical_count

    @property
    def available_count(self) -> int:
        with self._condition:
            return len(self._available)

    def connect(self, *, row_factory: Any | None = None) -> "_PooledConnectionHandle":
        return _PooledConnectionHandle(self, row_factory=row_factory)

    def acquire(self, *, row_factory: Any | None = None) -> Any:
        deadline = monotonic() + self._timeout_seconds
        create_new = False
        while True:
            with self._condition:
                if self._closed:
                    raise RuntimeError("runtime PostgreSQL pool is closed")
                while self._available:
                    conn = self._available.pop()
                    if _connection_usable(conn):
                        _set_row_factory(conn, row_factory)
                        return conn
                    self._physical_count -= 1
                    _close_quietly(conn)
                if self._physical_count < self._max_size:
                    self._physical_count += 1
                    create_new = True
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise RuntimePoolExhausted(
                        f"runtime PostgreSQL pool exhausted (max_size={self._max_size})"
                    )
                self._condition.wait(remaining)

        assert create_new
        try:
            conn = self._connect(self._dsn)
            _set_row_factory(conn, row_factory)
            return conn
        except BaseException:
            with self._condition:
                self._physical_count -= 1
                self._condition.notify()
            raise

    def release(self, conn: Any, *, broken: bool = False) -> None:
        reusable = not broken and _connection_usable(conn)
        if reusable:
            try:
                status = conn.info.transaction_status
                if status != pq.TransactionStatus.IDLE:
                    conn.rollback()
                conn.row_factory = tuple_row
            except BaseException:
                reusable = False

        with self._condition:
            if self._closed or not reusable:
                self._physical_count -= 1
                _close_quietly(conn)
            else:
                self._available.append(conn)
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            available = self._available
            self._available = []
            self._physical_count -= len(available)
            self._condition.notify_all()
        for conn in available:
            _close_quietly(conn)


class _PooledConnectionHandle:
    """Compatibility handle for existing ``with psycopg.connect()`` call sites."""

    def __init__(self, pool: RuntimeConnectionPool, *, row_factory: Any | None) -> None:
        self._pool = pool
        self._row_factory = row_factory
        self._conn: Any | None = None

    def _get(self) -> Any:
        if self._conn is None:
            self._conn = self._pool.acquire(row_factory=self._row_factory)
        return self._conn

    def __enter__(self) -> Any:
        return self._get()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        conn = self._conn
        if conn is None:
            return False
        broken = False
        try:
            if exc_type is None:
                conn.commit()
            else:
                conn.rollback()
        except BaseException:
            broken = True
            raise
        finally:
            self._pool.release(conn, broken=broken)
            self._conn = None
        return False

    def close(self) -> None:
        conn = self._conn
        if conn is None:
            return
        self._pool.release(conn)
        self._conn = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)


def runtime_pool_size_from_environment() -> int:
    raw = os.environ.get("AMODEX_DB_POOL_SIZE", "2").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("AMODEX_DB_POOL_SIZE must be an integer") from exc
    if value not in {1, 2}:
        raise ValueError("AMODEX_DB_POOL_SIZE must be 1 or 2")
    return value


def install_runtime_connection_pool(*, max_size: int | None = None) -> None:
    """Redirect ordinary runtime psycopg connects through the bounded pool.

    Only the connection shapes used by Amodex repositories are pooled: a DSN
    plus optional ``row_factory``. Unknown/advanced psycopg options fail open to
    psycopg's original connector rather than silently changing semantics.
    """

    global _INSTALLED, _POOL_MAX_SIZE
    size = max_size if max_size is not None else runtime_pool_size_from_environment()
    if size not in {1, 2}:
        raise ValueError("runtime PostgreSQL pool size must be 1 or 2")
    with _REGISTRY_LOCK:
        _POOL_MAX_SIZE = size
        if _INSTALLED:
            return
        psycopg.connect = _runtime_connect  # type: ignore[assignment]
        _INSTALLED = True


def close_runtime_connection_pools() -> None:
    """Close idle physical connections; future runtime calls lazily recreate pools."""

    with _REGISTRY_LOCK:
        pools = tuple(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


def _runtime_connect(conninfo: str = "", *args: Any, **kwargs: Any) -> Any:
    # Keep advanced psycopg signatures untouched. Runtime repository calls only
    # pass a DSN and, optionally, row_factory.
    allowed = {"row_factory"}
    if args or not isinstance(conninfo, str) or not conninfo or set(kwargs) - allowed:
        return _ORIGINAL_CONNECT(conninfo, *args, **kwargs)
    row_factory = kwargs.get("row_factory")
    with _REGISTRY_LOCK:
        pool = _POOLS.get(conninfo)
        if pool is None:
            pool = RuntimeConnectionPool(
                conninfo,
                max_size=_POOL_MAX_SIZE,
                timeout_seconds=_POOL_TIMEOUT_SECONDS,
                connect_factory=_ORIGINAL_CONNECT,
            )
            _POOLS[conninfo] = pool
    return pool.connect(row_factory=row_factory)


def _set_row_factory(conn: Any, row_factory: Any | None) -> None:
    conn.row_factory = row_factory if row_factory is not None else tuple_row


def _connection_usable(conn: Any) -> bool:
    try:
        return not bool(conn.closed) and conn.info.transaction_status != pq.TransactionStatus.UNKNOWN
    except BaseException:
        return False


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except BaseException:
        pass
