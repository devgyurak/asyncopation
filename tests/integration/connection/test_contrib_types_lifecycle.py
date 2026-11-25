"""hstore/citext through the driver ``asyncopation`` opens itself (engines wiring, #43)."""

import pytest

from harness.settings import VENDOR
from tests.support.drivers.contrib import zoo_rows_from_the_registered_driver

pytestmark = pytest.mark.skipif(
    VENDOR != "postgresql", reason="hstore/citext exist on PostgreSQL only"
)


async def test_the_registered_driver_reads_contrib_types_as_python_values(spy):
    # when
    rows = await zoo_rows_from_the_registered_driver()
    # then
    assert rows[1] == (
        {"a": "1", "b": None},
        None,
        [{"k": "v"}, {}],
        "MiXeD",
        ["Alpha", "beta"],
    )


async def test_the_registered_driver_reads_empty_containers_as_empty_python_values(spy):
    # when
    rows = await zoo_rows_from_the_registered_driver()
    # then
    assert rows[0] == ({}, None, [], "", [])
