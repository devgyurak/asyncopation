"""Isolated-interpreter scripts for the hooks (run with `lifecycle_scripts.run_isolated`)."""

from __future__ import annotations

from tests.support.lifecycle_scripts import _HEAD

UNCONFIGURED_SETTINGS = (
    _HEAD
    + """
import os
os.environ.pop("DJANGO_SETTINGS_MODULE", None)
import asyncio
from asyncopation.connection import hooks


class Connection:
    async def fetch_all(self, sql, params):
        return [(1,)]


seen = []
with hooks.observe(seen.append):
    subscribers = hooks.active()
    rows = asyncio.run(
        hooks.observed_call(
            Connection(), subscribers, "default", None, "fetch_all", ("SELECT %s", (1,))
        )
    )
from django.conf import settings
print([event.params for event in seen], rows, settings.configured)
"""
)
"""An observed `fetch_all` with parameters, without any Django settings: prints the parameters the
(not opted-in) observer saw, the rows, and whether settings got configured."""
