"""Names through which Django modules re-export psycopg (the driver) or Django's own driver helpers.

``drivers/`` is the only place that may use psycopg (non-negotiable 2, guard A104), so using
these names anywhere else - ``from M import n``, ``M.n`` or ``getattr(M, "n")``, all judged by
``verdict`` - is A104. The table is pinned against the installed Django source by
``tests/unit/django_drift`` (every psycopg name a Django module binds must be listed here).

``DRIVER_REEXPORTS`` maps a Django module to the re-exported names that are forbidden outside
``drivers/``; ``"*"`` means every name except ``ALLOWED_NAMES`` and ``ADAPTER_HELPERS``.
``ADAPTER_HELPERS`` are Django's own functions living in those modules; they may be imported only
by the ``_django`` boundary module ``ADAPTER_BOUNDARY``, which ``drivers/postgresql`` uses
(``adapters_template``). ``FORBIDDEN_EVERYWHERE`` names reached through a Django
module (Django's ``mogrify`` renders through a cursor, so it does I/O) are rejected even in
``drivers/``; a driver cursor's own ``.mogrify`` (client-side, no I/O) is allowed there and driver
API elsewhere.
"""

from __future__ import annotations

PSYCOPG_ANY = "django.db.backends.postgresql.psycopg_any"

DRIVER_REEXPORTS: dict[str, frozenset[str]] = {
    PSYCOPG_ANY: frozenset({"*"}),
    "django.db.backends.postgresql.base": frozenset(
        {
            "ConnectionPool",
            "Database",
            "Format",
            "IsolationLevel",
            "adapters",
            "psycopg",
            "psycopg2",
            "psycopg_pool",
            "sql",
        }
    ),
    "django.db.backends.mysql.base": frozenset(
        {"CLIENT", "Database", "FIELD_TYPE", "MySQLdb", "conversions"}
    ),
    "django.db.backends.mysql.introspection": frozenset({"FIELD_TYPE"}),
    "django.contrib.gis.db.backends.mysql.introspection": frozenset({"FIELD_TYPE"}),
    "django.db.backends.postgresql.operations": frozenset(
        {"Inet", "Jsonb", "errors", "mogrify", "numeric"}
    ),
    "django.db.backends.postgresql.creation": frozenset({"errors"}),
    "django.db.backends.postgresql.schema": frozenset({"sql"}),
    "django.contrib.postgres.apps": frozenset({"RANGE_TYPES"}),
    "django.contrib.postgres.fields.ranges": frozenset(
        {"DateRange", "DateTimeTZRange", "NumericRange", "Range"}
    ),
    "django.contrib.postgres.forms.ranges": frozenset(
        {"DateRange", "DateTimeTZRange", "NumericRange"}
    ),
    "django.contrib.postgres.search": frozenset({"Dumper", "adapt"}),
    "django.contrib.postgres.signals": frozenset(
        {"TypeInfo", "hstore", "psycopg2", "register_hstore"}
    ),
    "django.contrib.gis.db.backends.postgis.adapter": frozenset({"ISQLQuote", "sql"}),
    "django.contrib.gis.db.backends.postgis.base": frozenset(
        {"Dumper", "Format", "TextBinaryLoader", "TextLoader", "TypeInfo"}
    ),
}
ALLOWED_NAMES = frozenset({"is_psycopg3"})
ADAPTER_HELPERS = frozenset({"get_adapters_template", "register_tzloader"})
ADAPTER_BOUNDARY = "src/asyncopation/_django/postgresql_adapters.py"
FORBIDDEN_EVERYWHERE = frozenset({"mogrify"})
# Attributes (of any object: Django backend modules, wrapper classes) that are the driver module.
DRIVER_ATTRIBUTES = frozenset({"Database", "psycopg", "psycopg2", "psycopg_pool", "MySQLdb"})


DRIVER = "driver"
HELPER = "helper"
EVERYWHERE = "everywhere"


def verdict(module: str, name: str, *, attribute: bool = False) -> str | None:
    """What ``name`` of the Django module ``module`` is: ``DRIVER``, ``HELPER`` or ``EVERYWHERE``.

    The one rule behind every form (``from M import n``, ``M.n``, ``getattr(M, "n")``; ``name`` is
    ``"*"`` for a star import or a plain ``import M``). ``psycopg_any`` reached as a name of its
    package counts as the ``psycopg_any`` module itself. ``attribute`` forms also reject the
    driver module names (``.Database``, ...) on any object.
    """
    if name in FORBIDDEN_EVERYWHERE:
        if module.startswith("django."):
            return EVERYWHERE  # Django's own mogrify renders through a cursor
        return DRIVER if attribute else None  # a driver cursor's `mogrify`: drivers/ only
    if module == "django.db.backends.postgresql" and name == "psycopg_any":
        return DRIVER
    if module == PSYCOPG_ANY or module.startswith(PSYCOPG_ANY + "."):
        return HELPER if name in ADAPTER_HELPERS else DRIVER if name not in ALLOWED_NAMES else None
    listed = DRIVER_REEXPORTS.get(module)
    if listed is not None:
        if name in listed or name == "*":
            return DRIVER
        if name in ADAPTER_HELPERS:
            return HELPER
    if attribute and name in DRIVER_ATTRIBUTES:
        return DRIVER
    return None
