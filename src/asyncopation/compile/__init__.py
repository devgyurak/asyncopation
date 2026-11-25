"""Layer 1 - compile-only connection: SQL generation with zero I/O.

``compile_scope`` (``compile.scope``) makes Django's ``connections[alias]`` resolve to a
compile-only ``DatabaseWrapper`` (``compile.wrapper`` mixin + a per-backend composition such as
``compile.postgresql``, imported lazily because it pulls in the Django backend) for the duration of
one synchronous prepare phase (ADR-0008). Server facts arrive as a frozen ``ServerInfo``
(``compile.server_info``); probing them asynchronously belongs to the connection layer (ADR-0004).
Must not contain ``async def`` and must not import drivers, connection or transaction (enforced by
import-linter and tools/guards).
"""
