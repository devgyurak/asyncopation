"""Layer 3 - evaluation: the ``aobjects`` manager, ``aq()`` adapter and async iterables.

QuerySet building stays Django's (lazy, no I/O). This layer intervenes only at evaluation time:
compile -> execute through the connection layer -> apply converters -> ``Model.from_db``.
"""
