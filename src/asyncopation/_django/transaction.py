"""Django's error for a broken transaction, the one name of ``django.db.transaction`` we use.

Everything else in ``django.db.transaction`` is Django's *sync* transaction machinery and stays
banned (guards A101/A102). ``asyncopation`` raises this exact type when a pinned connection's
transaction is broken (a statement was cancelled inside it, ADR-0001 Decision 6c), so code that
already catches Django's exception keeps working.
"""

from __future__ import annotations

from django.db.transaction import TransactionManagementError

__all__ = ["TransactionManagementError"]
