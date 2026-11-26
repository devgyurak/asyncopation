"""asyncmy driver (the only importer of ``asyncmy``).

Behaves like Django's mysqlclient backend (ADR-0003): ``CLIENT.FOUND_ROWS``, a decoder shim that
matches mysqlclient's values, Django's integrity-code remap, autocommit by default.
"""

from asyncopation.drivers.mysql.driver import MySQLDriver

__all__ = ["MySQLDriver"]
