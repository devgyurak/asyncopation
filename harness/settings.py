"""Django settings shared by tests, the e2e site and the benchmark site.

The database is selected through environment variables:
    ASYNCOPATION_DB       postgresql (default) | mysql | mariadb
    ASYNCOPATION_DB_NAME  force the database name (e2e/bench server processes point at the test DB)
    ASYNCOPATION_DB_HOST  default 127.0.0.1
    ASYNCOPATION_CONN_MAX_AGE  Django CONN_MAX_AGE (default 0; benchmarks use persistent connections)
"""

from __future__ import annotations

import os
from typing import Any

VENDOR = os.environ.get("ASYNCOPATION_DB", "postgresql")
HOST = os.environ.get("ASYNCOPATION_DB_HOST", "127.0.0.1")

_BACKENDS: dict[str, dict[str, Any]] = {
    "postgresql": {"ENGINE": "django.db.backends.postgresql", "PORT": 25432},
    "mysql": {"ENGINE": "django.db.backends.mysql", "PORT": 23306},
    "mariadb": {"ENGINE": "django.db.backends.mysql", "PORT": 23307},
}

if VENDOR not in _BACKENDS:
    raise RuntimeError(f"ASYNCOPATION_DB must be one of {sorted(_BACKENDS)}, got {VENDOR!r}")

_db: dict[str, Any] = {
    **_BACKENDS[VENDOR],
    "HOST": HOST,
    "NAME": os.environ.get("ASYNCOPATION_DB_NAME", "asyncopation"),
    "USER": "asyncopation",
    "PASSWORD": "asyncopation",
    "CONN_MAX_AGE": int(os.environ.get("ASYNCOPATION_CONN_MAX_AGE", "0")),
    "TEST": {"NAME": "test_asyncopation"},
}
if VENDOR != "postgresql":
    _db["OPTIONS"] = {"charset": "utf8mb4"}
    _db["TEST"] = {
        **_db["TEST"],
        "CHARSET": "utf8mb4",
        "COLLATION": "utf8mb4_0900_ai_ci" if VENDOR == "mysql" else None,
    }

DATABASES = {"default": _db}

SECRET_KEY = "asyncopation-test-only"
DEBUG = False
ALLOWED_HOSTS = ["*"]
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "asyncopation",
    "django.contrib.contenttypes",
    "harness.catalog",
]
MIDDLEWARE: list[str] = []
ROOT_URLCONF = "harness.site.urls"
ASGI_APPLICATION = "harness.site.asgi.application"
WSGI_APPLICATION = "harness.site.wsgi.application"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("ASYNCOPATION_LOG_LEVEL", "WARNING")},
}
