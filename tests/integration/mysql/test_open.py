"""`open()` failures surface immediately as Django's exception classes and leave nothing behind."""

import pytest

from asyncopation.drivers.base import PoolStats
from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import compare_open_failure

pytestmark = MYSQL_FAMILY

BAD_SETTINGS = [
    pytest.param({}, {"NAME": "no_such_database_xyz"}, id="unknown-database"),
    pytest.param({}, {"HOST": "127.0.0.1", "PORT": 1}, id="refused-port"),
    pytest.param({}, {"PASSWORD": "definitely-wrong"}, id="bad-credentials"),
    pytest.param(
        {"init_command": "SET @@asyncopation_no_such_variable = 1"}, {}, id="failing-init-command"
    ),
]


@pytest.mark.parametrize(("options", "overrides"), BAD_SETTINGS)
async def test_a_failed_open_raises_djangos_class_at_once(readonly_db, options, overrides):
    # when
    result = await compare_open_failure(options=options, overrides=overrides)
    # then
    assert result.sync_error is not None
    assert type(result.async_failure.error) is type(result.sync_error)
    assert result.async_failure.elapsed < 5


@pytest.mark.parametrize(("options", "overrides"), BAD_SETTINGS)
async def test_a_failed_open_leaves_nothing_behind(readonly_db, options, overrides):
    # when
    failure = (await compare_open_failure(options=options, overrides=overrides)).async_failure
    # then
    assert failure.stats == PoolStats(in_use=0, idle=0, waiting=0)
    assert type(failure.capabilities_error) is RuntimeError
    assert type(failure.server_data_error) is RuntimeError
    assert type(failure.connection_error) is RuntimeError
