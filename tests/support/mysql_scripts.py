"""Isolated-interpreter scripts: MySQL support must work without mysqlclient (`MySQLdb`)."""

from __future__ import annotations

from tests.support.lifecycle_scripts import module_loaded_script, run_isolated

SETUP = """
from django.conf import settings
settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "NAME": "x"}},
    USE_TZ=True, TIME_ZONE="UTC", INSTALLED_APPS=[],
)
import django
django.setup()
"""

BLOCK = """
class Refuse:
    BLOCKED = ("MySQLdb", "django.db.backends.mysql.base", "django.db.backends.mysql.introspection")

    @classmethod
    def find_spec(cls, name, path=None, target=None):
        if name in cls.BLOCKED:
            raise ImportError(f"blocked: {name}")


sys.meta_path.insert(0, Refuse)
"""

USE = """
from asyncopation.compile.mysql import wrapper_for
from tests.support.mysql_wrappers import MYSQL_SETTINGS, mysql_info
w = wrapper_for("a", dict(MYSQL_SETTINGS), mysql_info())
w.features.minimum_database_version
w.features.has_select_for_update_of
w.ops.compiler("SQLCompiler")
w.ops.quote_name("x")
w.ops.explain_query_prefix()
w.data_types
w.data_type_check_constraints
w.sql_mode
w.display_name
w.introspection
w.creation
w.client
"""

STATEMENTS: dict[str, str] = {
    "compile-module": "import asyncopation.compile.mysql",
    "boundary-module": "import asyncopation._django.mysql",
    "wrapper-built-and-used": SETUP + USE,
    "binary-shim": SETUP + USE + "w.Database.Binary(b'x')\n",
}


def mysqldb_loaded(statement: str) -> str:
    """Whether running `statement` put `MySQLdb` into `sys.modules` (`True` / `False`)."""
    ran = run_isolated(module_loaded_script(statement, "MySQLdb"))
    return f"{ran.returncode} {ran.stdout}"


def blocked(statement: str) -> str:
    """`mysqldb_loaded` while importing `MySQLdb` or Django's mysql.base is made impossible."""
    return mysqldb_loaded(BLOCK + statement)
