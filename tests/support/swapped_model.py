"""A swappable model, in the swapped-out state, for the `AsyncObjects` tests (no DB, no tables).

The swapped state is reached through settings only: the model declares
`Meta.swappable = SETTING` and the setting points at another model.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from django.db import models
from django.test.utils import isolate_apps, override_settings

from asyncopation import AsyncObjects

SETTING = "ASYNCOPATION_TEST_SWAP"
REPLACEMENT = "catalog.Book"


@contextmanager
def swapped_out_model() -> Generator[Any]:
    """Yield a model (regular `objects`, `aobjects` attached) whose swappable setting names another."""
    with isolate_apps(), override_settings(**{SETTING: REPLACEMENT}):

        class Swappable(models.Model):
            objects = models.Manager()
            aobjects = AsyncObjects()

            class Meta:
                app_label = "aobjects_swap"
                swappable = SETTING

            def __str__(self) -> str:
                return "Swappable"

        yield Swappable


def read_objects(model: Any) -> Any:
    """`model.objects` (raises on a swapped-out model)."""
    return model.objects
