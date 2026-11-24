"""Server facts a compile-only wrapper needs, probed asynchronously elsewhere (ADR-0004/0008)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Base of the per-backend server info; backends add their own fields."""


@dataclass(frozen=True, slots=True)
class PostgreSQLServerInfo(ServerInfo):
    """``pg_version`` as psycopg reports it, e.g. ``170002`` for 17.2."""

    pg_version: int
