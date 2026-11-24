"""Layer 0 - the only package that imports non-public Django APIs (ADR-0007).

Everything else in ``asyncopation`` imports Django internals from here, so a Django upgrade breaks
in one place. Backend-specific internals live in lazily imported submodules
(``_django.postgresql``, ``_django.mysql``) so importing this package never requires both driver
libraries. Every symbol has a ledger entry and is covered by the drift test.
"""
