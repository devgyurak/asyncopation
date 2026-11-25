"""Isolated-interpreter scripts: which driver packages do the MySQL code paths import?"""

from __future__ import annotations

from tests.support.lifecycle_scripts import _HEAD, run_isolated

DRIVER_TOPS = "{'MySQLdb', 'asyncmy', 'psycopg', 'psycopg_pool', 'psycopg_binary'}"

# Defining a model class makes Django ask `connections["default"]` for `ops.max_name_length()`,
# which imports `MySQLdb` when the default ENGINE is MySQL. The scripts therefore define the
# harness models under the dummy engine and give the composed wrapper its own MySQL settings,
# so that what is measured is the composed path and not Django's model loading.
_SETUP = """
from django.conf import settings
settings.configure(
    DATABASES={"default": {"ENGINE": "%s", "NAME": "x"}},
    USE_TZ=True, TIME_ZONE="UTC",
    INSTALLED_APPS=["django.contrib.contenttypes", "harness.catalog"],
)
import django
django.setup()
"""
DUMMY_ENGINE = "django.db.backends.dummy"
MYSQL_ENGINE = "django.db.backends.mysql"
_REPORT = f"print(sorted(m for m in sys.modules if m.split('.')[0] in {DRIVER_TOPS}))"

COMPOSED_WRAPPER_COMPILES = """
from django.db import connections
from asyncopation.compile.mysql import wrapper_for
from asyncopation.compile.scope import compile_scope
from asyncopation.compile.server_info import MySQLServerInfo
from harness.catalog.models import Book
info = MySQLServerInfo(
    version="8.4.11", sql_mode="STRICT_TRANS_TABLES", default_storage_engine="InnoDB",
    sql_auto_is_null=False, lower_case_table_names=False, has_zoneinfo_database=True,
)
mysql_settings = {
    "ENGINE": "django.db.backends.mysql", "NAME": "x", "OPTIONS": {}, "TIME_ZONE": None,
    "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": False, "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False, "USER": "", "PASSWORD": "", "HOST": "", "PORT": "", "TEST": {},
}
wrapper = wrapper_for("default", mysql_settings, info)
with compile_scope("default", wrapper, autocommit=True):
    Book.objects.filter(title="x").query.get_compiler(using="default").as_sql()
"""

REAL_WRAPPER_IS_TOUCHED = """
from django.db import connections
connections["default"]
"""
NOTHING = "pass"


def loaded_driver_modules(statement: str, engine: str = DUMMY_ENGINE) -> tuple[int, str]:
    """(exit code, sorted driver modules loaded) after running `statement` in a clean interpreter
    whose default database uses `engine`."""
    script = _HEAD + _SETUP % engine + statement + "\n" + _REPORT
    ran = run_isolated(script)
    return ran.returncode, ran.stdout
