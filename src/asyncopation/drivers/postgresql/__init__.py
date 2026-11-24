"""psycopg3 driver (the only importer of ``psycopg`` / ``psycopg_pool``).

Cursor selection mirrors Django's PostgreSQL backend (ADR-0003): ``AsyncClientCursor`` (client-side
binding, Django-compatible SQL) by default, ``AsyncCursor`` with ``OPTIONS["server_side_binding"] =
True``.
"""

from asyncopation.drivers.postgresql.driver import PostgresDriver

__all__ = ["PostgresDriver"]
