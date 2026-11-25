"""The e2e server helpers: the diagnostic-line parser and the unhealthy-start cleanup."""

import pytest

from tests.support.e2e_server import server_with_log, unhealthy_server_outcome


def test_sessions_around_shutdown_reads_the_diag_line(tmp_path):
    # given
    log = "x\nASYNCOPATION_DIAG sessions before-close=3 after-shutdown=0\ny\n"
    server = server_with_log(tmp_path, log)
    # when
    sessions = server.sessions_around_shutdown()
    # then
    assert sessions == (3, 0)


def test_sessions_around_shutdown_fails_when_the_server_printed_no_diag_line(tmp_path):
    # given
    server = server_with_log(tmp_path, "Application startup complete\n")
    # when / then
    with pytest.raises(AssertionError, match="no ASYNCOPATION_DIAG line"):
        server.sessions_around_shutdown()


def test_sessions_around_shutdown_accepts_a_failed_count_of_minus_one(tmp_path):
    # given
    log = "ASYNCOPATION_DIAG sessions before-close=-1 after-shutdown=-1"
    server = server_with_log(tmp_path, log)
    # when
    sessions = server.sessions_around_shutdown()
    # then
    assert sessions == (-1, -1)


def test_a_server_that_never_gets_healthy_is_killed_and_waited_for(tmp_path):
    # when
    proc = unhealthy_server_outcome(tmp_path)
    # then
    assert proc.killed is True
    assert proc.waited is True
