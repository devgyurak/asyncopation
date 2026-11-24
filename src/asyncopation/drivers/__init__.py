"""Layer 0 - async database drivers (the only place that may import psycopg / asyncmy).

Defines the ``AsyncDriver`` interface (connect/pool, execute, fetch*, server-side cursors,
last_insert_id / RETURNING capability, begin/commit/rollback/savepoint) and one implementation
per backend. Knows nothing about Django models or QuerySets.
"""
