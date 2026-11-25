"""The test helpers themselves: a marker nobody runs must not be seen as running."""

import uuid

from tests.support.mysql_env import MYSQL_FAMILY, statement_running

pytestmark = MYSQL_FAMILY


async def test_statement_running_does_not_see_its_own_admin_query(django_db_setup):
    # when
    running = await statement_running(f"asyncopation_never_{uuid.uuid4().hex}")
    # then
    assert running is False
