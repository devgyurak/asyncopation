"""Every Asyncopation class based on Django's real wrapper puts the compile-only mixin first."""

from tests.support.compile_wrappers import (
    diamond_subclass_report,
    indirect_subclass_report,
    lookalike_module_report,
    throwaway_wrapper_report,
    wrapper_classes_found,
    wrapper_classes_that_can_connect,
)


def test_every_asyncopation_wrapper_class_blocks_connecting_before_djangos_methods():
    # when
    connecting = wrapper_classes_that_can_connect()
    # then
    assert connecting == []


def test_the_compile_only_wrapper_is_among_the_classes_that_are_checked():
    # when
    found = wrapper_classes_found()
    # then
    # a new backend wrapper is added to this list on purpose
    assert found == ["asyncopation.compile.postgresql.PostgreSQLCompileWrapper"]


def test_the_check_reports_direct_and_indirect_subclasses_without_the_mixin_first():
    # when
    report = throwaway_wrapper_report()
    # then
    assert report == ["Indirect", "MixinLast", "NoMixin"]


def test_the_check_is_repeatable_in_one_process():
    # when
    reports = (throwaway_wrapper_report(), throwaway_wrapper_report())
    # then
    assert reports[0] == reports[1]


def test_the_default_check_ignores_modules_that_only_look_like_asyncopation():
    # when
    report = lookalike_module_report()
    # then
    assert report == []


def test_the_found_list_includes_indirect_subclasses():
    # when
    report = indirect_subclass_report()
    # then
    assert report == ["Child"]


def test_a_class_reachable_through_two_parents_is_reported_once():
    # when
    report = diamond_subclass_report()
    # then
    assert report == ["Both", "Left", "Right"]
