"""`build_config` for MySQL/MariaDB: Django parity of the connection params, validation, pool."""

from dataclasses import FrozenInstanceError

import pytest
from django.core.exceptions import ImproperlyConfigured

from tests.support.drivers import set_attribute
from tests.support.mysql_driver_drift import (
    compare_conn_kwargs,
    django_config_error,
    my_config,
    my_config_error,
    pool_error,
    pool_values,
)

OPTIONS_CASES = [
    pytest.param({}, id="no-options"),
    pytest.param({"connect_timeout": 5}, id="connect-timeout"),
    pytest.param({"charset": "latin1"}, id="charset-override"),
    pytest.param({"sql_mode": "TRADITIONAL"}, id="sql-mode"),
    pytest.param({"isolation_level": "serializable"}, id="isolation-level"),
    pytest.param({"isolation_level": None}, id="no-isolation-level"),
    pytest.param({"read_timeout": 3, "local_infile": True}, id="timeouts-and-infile"),
]

SETTINGS_CASES = [
    pytest.param({}, id="defaults"),
    pytest.param({"USER": "", "PASSWORD": "", "HOST": "", "PORT": "", "NAME": ""}, id="blank"),
    pytest.param({"HOST": "/var/run/mysqld/mysqld.sock"}, id="unix-socket"),
    pytest.param({"PORT": "3307"}, id="port-as-string"),
    pytest.param({"HOST": "127.0.0.1", "PORT": 23306}, id="ip-and-port"),
]


@pytest.mark.parametrize("options", OPTIONS_CASES)
def test_connection_params_equal_djangos_apart_from_the_documented_differences(options):
    # when
    comparison = compare_conn_kwargs(options)
    # then
    assert comparison.differences() == {}


@pytest.mark.parametrize("extra", SETTINGS_CASES)
def test_connection_params_equal_djangos_for_every_settings_shape(extra):
    # when
    comparison = compare_conn_kwargs(None, **extra)
    # then
    assert comparison.differences() == {}


def test_unix_socket_host_becomes_unix_socket_not_host():
    # when
    kwargs = my_config(None, HOST="/var/run/mysqld/mysqld.sock").conn_kwargs
    # then
    assert kwargs["unix_socket"] == "/var/run/mysqld/mysqld.sock"
    assert "host" not in kwargs


def test_connections_are_opened_in_autocommit_mode():
    # when
    kwargs = my_config().conn_kwargs
    # then
    assert kwargs["autocommit"] is True


def test_the_found_rows_client_flag_is_set_like_django():
    # when
    comparison = compare_conn_kwargs()
    # then
    assert comparison.ours["client_flag"] == comparison.reference["client_flag"]


def test_the_connection_charset_is_utf8mb4_like_django():
    # when
    kwargs = my_config().conn_kwargs
    # then
    assert kwargs["charset"] == "utf8mb4"


def test_the_conversions_are_the_decoder_shim_not_djangos_mysqlclient_table():
    # when
    comparison = compare_conn_kwargs()
    # then
    assert comparison.ours["conv"] is not comparison.reference["conv"]
    assert isinstance(comparison.ours["conv"], dict)


def test_the_isolation_level_option_does_not_reach_the_connect_arguments():
    # when
    kwargs = my_config({"isolation_level": "serializable"}).conn_kwargs
    # then
    assert "isolation_level" not in kwargs


def test_the_default_isolation_level_is_read_committed_like_django():
    # when
    comparison = compare_conn_kwargs()
    # then
    assert comparison.our_isolation == comparison.reference_isolation == "read committed"


@pytest.mark.parametrize("level", ["serializable", "REPEATABLE READ", "Read Uncommitted", None])
def test_the_isolation_level_is_normalised_like_django(level):
    # when
    comparison = compare_conn_kwargs({"isolation_level": level})
    # then
    assert comparison.our_isolation == comparison.reference_isolation


def test_the_default_isolation_is_applied_by_the_init_command():
    # when
    init_command = my_config().conn_kwargs["init_command"]
    # then
    assert "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED" in init_command


def test_the_isolation_level_in_the_init_command_is_upper_case():
    # when
    init_command = my_config({"isolation_level": "repeatable read"}).conn_kwargs["init_command"]
    # then
    assert "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ" in init_command


def test_no_isolation_level_means_no_isolation_statement():
    # when
    init_command = my_config({"isolation_level": None}).conn_kwargs.get("init_command") or ""
    # then
    assert "ISOLATION LEVEL" not in init_command


@pytest.mark.parametrize(
    "command",
    [
        "SET sql_mode='ANSI'",
        "SET sql_mode='ANSI';",
        "SET sql_mode='ANSI' ;  ",
        "SET sql_mode='ANSI';\n",
    ],
    ids=["plain", "semicolon", "semicolon-and-spaces", "semicolon-and-newline"],
)
def test_the_user_init_command_comes_first_followed_by_a_newline_and_the_assignments(command):
    # when
    init_command = my_config({"init_command": command}).conn_kwargs["init_command"]
    # then
    assert init_command.startswith("SET sql_mode='ANSI'\n; ")
    assert ";;" not in init_command
    assert "ISOLATION LEVEL READ COMMITTED" in init_command


@pytest.mark.parametrize(
    "command",
    ["SET @a = 1 -- note", "SET @a = 1 # note", "SET @a = 1 -- note;", "SET @a = 1 # note ;  "],
    ids=["dash-comment", "hash-comment", "dash-comment-semicolon", "hash-comment-semicolon"],
)
def test_a_trailing_comment_of_the_user_command_ends_before_the_assignments(command):
    # when
    init_command = my_config({"init_command": command}).conn_kwargs["init_command"]
    # then
    assert init_command.splitlines()[0].startswith("SET @a = 1")
    assert init_command.splitlines()[1].startswith("; SET")


def test_an_invalid_isolation_level_is_improperly_configured_like_django():
    # when
    ours = my_config_error({"isolation_level": "chaos"})
    reference = django_config_error({"isolation_level": "chaos"})
    # then
    assert isinstance(ours, ImproperlyConfigured)
    assert type(ours) is type(reference)


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"ssl_mode": "REQUIRED"}, id="mysqlclient-only-ssl-mode"),
        pytest.param({"no_such_option": 1}, id="unknown"),
        pytest.param({"pool": {"min_size": 1}}, id="pool-is-not-an-option"),
        pytest.param({"pool": True}, id="pool-true"),
        pytest.param({"stmt_cache_size": 10}, id="prepared-statements"),
        pytest.param({"binary_prefix": True}, id="binary-prefix-has-no-effect"),
    ],
)
def test_options_asyncmy_cannot_take_are_improperly_configured(options):
    # when
    error = my_config_error(options)
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_a_disabled_binary_prefix_is_accepted():
    # when
    error = my_config_error({"binary_prefix": False})
    # then
    assert error is None


def test_a_disabled_statement_cache_is_accepted():
    # when
    error = my_config_error({"stmt_cache_size": 0})
    # then
    assert error is None


def test_server_side_cursors_are_on_by_default():
    # when
    config = my_config()
    # then
    assert config.server_side_cursors is True


def test_disable_server_side_cursors_turns_them_off():
    # when
    config = my_config(None, DISABLE_SERVER_SIDE_CURSORS=True)
    # then
    assert config.server_side_cursors is False


def test_the_config_carries_its_alias():
    # when
    config = my_config()
    # then
    assert config.alias == "default"


def test_the_config_is_frozen():
    # given
    config = my_config()
    # when / then
    with pytest.raises(FrozenInstanceError):
        set_attribute(config, "alias", "other")


def test_pool_options_default_to_the_provisional_sizes_and_a_30_second_timeout():
    # when
    kwargs = pool_values()
    # then
    assert (kwargs["min_size"], kwargs["max_size"], kwargs["timeout"]) == (1, 10, 30.0)


def test_a_min_size_above_the_default_max_size_raises_the_max_size_with_it():
    # when
    kwargs = pool_values({"min_size": 20})
    # then
    assert (kwargs["min_size"], kwargs["max_size"]) == (20, 20)


def test_pool_options_are_read_from_the_asyncopation_key():
    # when
    kwargs = pool_values(
        {"min_size": 2, "max_size": 8, "timeout": 5, "max_waiting": 3, "max_idle": 600}
    )
    # then
    assert (
        kwargs["min_size"],
        kwargs["max_size"],
        kwargs["timeout"],
        kwargs["max_waiting"],
        kwargs["max_idle"],
    ) == (2, 8, 5, 3, 600)


def test_the_pool_options_do_not_leak_into_the_connection_params():
    # when
    kwargs = my_config(None, ASYNCOPATION={"pool": {"min_size": 2}}).conn_kwargs
    # then
    assert "ASYNCOPATION" not in kwargs
    assert "min_size" not in kwargs


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param({"min_sise": 2}, id="misspelt-key"),
        pytest.param({"pool_recycle": 5}, id="asyncmy-name"),
        pytest.param({"minsize": 5}, id="asyncmy-minsize"),
        pytest.param({"min_size": "5"}, id="min-str"),
        pytest.param({"min_size": 2.5}, id="min-float"),
        pytest.param({"min_size": True}, id="min-bool"),
        pytest.param({"max_size": None}, id="max-none"),
        pytest.param({"max_size": "7"}, id="max-str"),
        pytest.param({"timeout": "5"}, id="timeout-str"),
        pytest.param({"timeout": True}, id="timeout-bool"),
        pytest.param({"max_waiting": 2.5}, id="max-waiting-float"),
        pytest.param({"max_waiting": True}, id="max-waiting-bool"),
        pytest.param({"max_lifetime": 60}, id="old-name-max-lifetime"),
        pytest.param({"max_idle": "1h"}, id="max-idle-str"),
        pytest.param({"max_idle": 60.5}, id="max-idle-float"),
        pytest.param({"max_idle": 0}, id="max-idle-zero"),
        pytest.param({"max_idle": True}, id="max-idle-bool"),
        pytest.param({"min_size": 5, "max_size": 2}, id="min-above-max"),
        pytest.param("min_size=2", id="not-a-mapping"),
        pytest.param([1], id="list"),
    ],
)
def test_invalid_pool_options_are_improperly_configured_before_any_connection(pool):
    # when
    error = pool_error(pool)
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param({"min_size": 0}, id="min-zero"),
        pytest.param({"min_size": 3, "max_size": 3}, id="equal"),
        pytest.param({"timeout": 2.5}, id="float-timeout"),
        pytest.param({"max_waiting": 0}, id="max-waiting-zero"),
        pytest.param({"max_idle": 1}, id="smallest-idle"),
    ],
)
def test_valid_pool_options_are_accepted(pool):
    # when
    error = pool_error(pool)
    # then
    assert error is None


def test_the_sql_mode_option_is_quoted_into_the_init_command():
    # when
    kwargs = my_config({"sql_mode": "TRADITIONAL,NO_BACKSLASH_ESCAPES"}).conn_kwargs
    # then
    assert kwargs["init_command"].startswith("SET sql_mode='TRADITIONAL,NO_BACKSLASH_ESCAPES'\n; ")
    assert "sql_mode" not in kwargs


def test_the_sql_mode_option_runs_after_the_users_init_command_like_mysqlclient():
    # when
    kwargs = my_config({"sql_mode": "ANSI", "init_command": "SET @a = 1;"}).conn_kwargs
    # then
    assert kwargs["init_command"].startswith("SET @a = 1\n; SET sql_mode='ANSI'\n; ")


@pytest.mark.parametrize(
    "options",
    [{"sql_mode": ""}, {"sql_mode": "", "init_command": "SET @a = 1"}],
    ids=["alone", "with-command"],
)
def test_an_empty_sql_mode_option_adds_nothing(options):
    # when
    init_command = my_config(options).conn_kwargs.get("init_command") or ""
    # then
    assert "sql_mode" not in init_command


@pytest.mark.parametrize(
    "mode", ["A'B", "A\\B", 5, ["ANSI"]], ids=["quote", "backslash", "int", "list"]
)
def test_an_unusable_sql_mode_option_is_improperly_configured(mode):
    # when
    error = my_config_error({"sql_mode": mode})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_pooled_kwargs_add_set_sql_auto_is_null_when_the_server_has_it_enabled():
    # when
    init_command = my_config().pooled_kwargs(sql_auto_is_null=True)["init_command"]
    # then
    assert "SET SQL_AUTO_IS_NULL = 0" in init_command


def test_pooled_kwargs_leave_it_out_when_the_server_has_it_disabled():
    # when
    init_command = my_config().pooled_kwargs(sql_auto_is_null=False)["init_command"]
    # then
    assert "SQL_AUTO_IS_NULL" not in init_command


def test_pooled_kwargs_keep_the_users_command_first_and_the_isolation_level():
    # when
    init_command = my_config({"init_command": "SET @a = 1"}).pooled_kwargs(sql_auto_is_null=True)[
        "init_command"
    ]
    # then
    assert init_command.startswith("SET @a = 1\n; ")
    assert "ISOLATION LEVEL READ COMMITTED" in init_command
