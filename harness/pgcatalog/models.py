"""PostgreSQL-only models exercising the ``hstore`` and ``citext`` extensions (#43)."""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField, HStoreField
from django.db import models


class CITextField(models.TextField):
    """``citext`` column. Django removed its own ``CIText*`` fields in 5.1; the type is still
    usable through ``db_type``."""

    def db_type(self, connection):
        return "citext"


class PgFieldZoo(models.Model):
    hstore = HStoreField()
    nullable_hstore = HStoreField(null=True)
    hstore_list = ArrayField(HStoreField(), default=list)
    citext = CITextField()
    citext_list = ArrayField(CITextField(), default=list)

    class Meta:
        ordering = ["pk"]
