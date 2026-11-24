"""Layer 1 - compile-only connection: SQL generation with zero I/O.

Provides a ``DatabaseWrapper`` that can never open a connection. Server version / feature data is
probed asynchronously once per alias (by the connection layer, ADR-0004) and injected here; how
Django's compilers reach this wrapper is ADR-0008. Must not contain
``async def`` and must not import drivers, connection or transaction (enforced by import-linter
and tools/guards).
"""
