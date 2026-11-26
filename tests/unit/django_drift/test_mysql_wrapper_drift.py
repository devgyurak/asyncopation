"""Drift: the ported MySQL wrapper attributes behave like Django's real ``DatabaseWrapper``.

Needs mysqlclient (the ``mysql-sync`` dependency group): Django's ``mysql.base`` imports MySQLdb.
Without it the module skips, except where ``ASYNCOPATION_REQUIRE_MYSQLDB=1`` (CI), where
``test_mysqldb_required.py`` fails instead.
"""

import pytest

from tests.support.mysql_drift import (
    EXPECTED_PORTED,
    SKIP_WITHOUT_MYSQLDB,
    attribute_diffs,
    declared_on_compile_wrapper,
    descriptor_names,
    feature_diffs,
    feature_names,
    kind_diffs,
    ops_diffs,
    ported_names,
    readable_feature_count,
    real_wrapper,
    version_pattern_diff,
)
from tests.support.mysql_wrappers import VERSIONS, fresh_wrapper, scenario_info

pytestmark = SKIP_WITHOUT_MYSQLDB

SCENARIOS = pytest.mark.parametrize("scenario", sorted(VERSIONS))
PORTED = sorted(EXPECTED_PORTED)
PORTED_DESCRIPTORS = descriptor_names(PORTED)


def test_the_ledger_lists_exactly_the_ported_attributes():
    # when
    listed = set(ported_names())
    # then
    assert listed == EXPECTED_PORTED


@pytest.mark.parametrize("name", PORTED)
def test_every_ported_name_exists_on_the_compile_wrapper(name):
    # when
    found = declared_on_compile_wrapper(name)
    # then
    assert found is True


@SCENARIOS
def test_ported_attributes_have_the_same_values_as_django(scenario):
    # given
    info = scenario_info(scenario)
    # when
    diffs = attribute_diffs(real_wrapper(info), fresh_wrapper(info), PORTED)
    # then
    assert diffs == {}


def test_ported_attributes_are_the_same_kind_of_descriptor_as_in_django():
    # when
    diffs = kind_diffs(PORTED_DESCRIPTORS)
    # then
    assert diffs == {}


@SCENARIOS
def test_features_report_the_same_values_as_django(scenario):
    # when
    diffs = feature_diffs(scenario_info(scenario))
    # then
    assert diffs == {}


@SCENARIOS
def test_the_feature_comparison_reads_every_feature(scenario):
    # when
    count = readable_feature_count(scenario_info(scenario))
    # then
    assert count == len(feature_names())


@SCENARIOS
def test_operations_branching_on_the_server_flavour_match_django(scenario):
    # when
    diffs = ops_diffs(scenario_info(scenario))
    # then
    assert diffs == {}


def test_the_version_pattern_is_django_s():
    # when
    patterns = version_pattern_diff()
    # then
    assert patterns[0] == patterns[1]
