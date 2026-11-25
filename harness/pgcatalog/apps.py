from django.apps import AppConfig


class PgCatalogConfig(AppConfig):
    """PostgreSQL-only models (``contrib.postgres`` fields). Installed by ``harness.settings`` only
    when ``ASYNCOPATION_DB=postgresql``, so MySQL/MariaDB migrations never see them."""

    name = "harness.pgcatalog"
    label = "pgcatalog"
