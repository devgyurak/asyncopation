"""Layer 0 - compile-only connection: SQL generation with zero I/O.

Provides a ``DatabaseWrapper`` that can never open a connection. Server version / feature data is
fetched asynchronously at startup (by the connection layer) and injected here. Must not contain
``async def`` and must not import drivers, connection or transaction (enforced by import-linter
and tools/guards).
"""
