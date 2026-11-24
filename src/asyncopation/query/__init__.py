"""Layer 7 - public facade: ``aq()``, the ``aobjects`` descriptor and evaluation methods.

QuerySet building stays Django's (lazy, no I/O). ``aq()`` wraps any QuerySet or Manager by
composition and only adds async evaluation (ADR-0006); execution is delegated to
``asyncopation.engine``.
"""
