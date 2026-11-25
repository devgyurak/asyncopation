"""hstore/citext type handlers for ``django.contrib.postgres`` (ADR-0003 Decision 2).

Ported from django/contrib/postgres/signals.py::register_type_handlers, get_type_oids @ 6.1.3
Changes: the OID query is awaited on the driver's own connection; the OIDs are read once per
``PostgresDriver.open()`` (Django caches them per process in ``lru_cache``) and kept in an
immutable ``ContribTypes``; registration happens on the raw connection from the driver's configure
step, not on the ``connection_created`` signal; the vendor / ``NO_DB_ALIAS`` checks are not needed
(the driver only serves real PostgreSQL aliases) and the psycopg2 branch is dropped.

Two steps so that connection configuration stays free of I/O after the first connection:
``fetch_contrib_oids`` (async, probe connection only) and ``register_contrib_types`` (sync).
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.types import TypeInfo, hstore


@dataclass(frozen=True, slots=True)
class ContribTypes:
    """OIDs found in ``pg_type``; empty tuples when the extension is not installed."""

    hstore: tuple[tuple[int, int], ...]
    """``(oid, array_oid)`` for every ``hstore`` type, one per schema that defines it."""
    citext_arrays: tuple[int, ...]
    """Array OIDs of every ``citext`` type (the scalar loads as ``str`` already)."""


async def _type_oids(raw: psycopg.AsyncConnection, type_name: str) -> tuple[tuple[int, int], ...]:
    # Ported from django/contrib/postgres/signals.py::get_type_oids @ 6.1.3
    # Changes: awaited; one tuple of (oid, array oid) pairs instead of two parallel tuples.
    cursor = await raw.execute("SELECT oid, typarray FROM pg_type WHERE typname = %s", (type_name,))
    return tuple((row[0], row[1]) for row in await cursor.fetchall())


async def fetch_contrib_oids(raw: psycopg.AsyncConnection) -> ContribTypes:
    """Read the hstore and citext OIDs (two catalog queries). Not ``TypeInfo.fetch``: that finds
    one type by ``search_path``, Django registers every schema's."""
    return ContribTypes(
        hstore=await _type_oids(raw, "hstore"),
        citext_arrays=tuple(array_oid for _, array_oid in await _type_oids(raw, "citext")),
    )


def register_contrib_types(raw: psycopg.AsyncConnection, types: ContribTypes) -> None:
    """Register the handlers on this connection's own adapters (never on the shared template).

    Synchronous and I/O free: no await, so no cancellation point inside connection configuration.
    """
    # Ported from django/contrib/postgres/signals.py::register_type_handlers @ 6.1.3
    for oid, array_oid in types.hstore:
        hstore.register_hstore(TypeInfo("hstore", oid, array_oid), raw)
    for array_oid in types.citext_arrays:
        # citext is registered by its array type only, so ``citext[]`` loads as ``list[str]``.
        TypeInfo("citext", 0, array_oid).register(raw)
