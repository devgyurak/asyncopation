"""Self-check of the PostgreSQL-only harness app: the seed creates what the tests expect."""

import pytest
from django.apps import apps

from harness.seed import PG_EXPECTED_COUNTS
from tests.support.drivers import POSTGRES_ONLY

pytestmark = POSTGRES_ONLY


@pytest.mark.parametrize(("model_name", "expected"), list(PG_EXPECTED_COUNTS.items()))
async def test_seed_creates_expected_pg_row_counts(readonly_db, model_name, expected):
    # given
    model = apps.get_model("pgcatalog", model_name)
    # when
    count = await model.objects.acount()
    # then
    assert count == expected
