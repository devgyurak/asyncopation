"""Layer 4 - evaluation engine: pipeline contracts, materializer and the sans-I/O runner.

Defines ``CompiledQuery`` and builds it with the compile-only wrapper, adapts executor output and
materializes it through Django's ``results_iter(results=...)`` (ADR-0002). Drives the sans-I/O
generator cores of ported orchestration against the current executor (ADR-0007).

Stages: ``compiled`` (QuerySet -> ``CompiledQuery``), ``execute`` (rows from an executor),
``materialize`` (rows -> instances, ported post-execute iterable code), ``evaluate`` (the three in
order). The sans-I/O runner arrives with the first ported core (``acount``, #6, PR #67).
"""
