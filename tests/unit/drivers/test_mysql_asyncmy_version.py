"""The asyncmy range the driver is written against (pyproject: `>=0.2.18,<0.3`)."""

from tests.support.mysql_env import installed_asyncmy_in_range


def test_the_installed_asyncmy_is_inside_the_supported_range():
    # when
    in_range = installed_asyncmy_in_range()
    # then
    assert in_range is True
