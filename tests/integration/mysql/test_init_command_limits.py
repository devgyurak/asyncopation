"""Documented limit: a `;` followed by a trailing comment in `init_command` is unsupported."""

from django.db import ProgrammingError

from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import compare_open_failure

pytestmark = MYSQL_FAMILY


async def test_a_semicolon_followed_by_a_comment_fails_open_with_a_syntax_error(readonly_db):
    # when
    result = await compare_open_failure(options={"init_command": "SET @a=1; -- note"})
    error = result.async_failure.error
    # then
    assert type(error) is ProgrammingError
    assert error.args[0] == 1064
