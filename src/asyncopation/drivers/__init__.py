"""Layer 1 - async database drivers (the only place that may import psycopg / asyncmy).

Defines the ``AsyncDriver`` / ``AsyncDbConnection`` protocols and the frozen ``Capabilities``
(ADR-0003) and one implementation per backend. A driver behaves like Django's sync backend
connection (values, rowcount semantics, session setup) and translates driver exceptions into the
``django.db`` hierarchy. Knows nothing about Django models or QuerySets.
"""
