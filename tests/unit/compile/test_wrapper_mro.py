"""Every Asyncopation class based on Django's real wrapper puts the compile-only mixin first."""

from tests.support.compile_wrappers import (
    default_scan_report,
    default_wrapper_packages,
    diamond_subclass_report,
    indirect_subclass_report,
    lookalike_module_report,
    scanned_package_report,
    skipped_in_this_environment,
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


def test_the_scan_covers_the_compile_and_the_django_boundary_packages():
    # when
    packages = default_wrapper_packages()
    # then
    assert packages == ["asyncopation.compile", "asyncopation._django"]


def test_wrappers_of_any_django_backend_in_any_module_or_subpackage_are_checked(tmp_path):
    # when
    reported, found, _skipped, _raised = scanned_package_report(tmp_path)
    # then
    assert (reported, found) == (
        ["a_wrappers.Between", "sub.deep.Deep", "wrappers.Bare", "wrappers.OnSqlite"],
        [
            "a_wrappers.Between",
            "sub.deep.Deep",
            "wrappers.Bare",
            "wrappers.OnSqlite",
            "wrappers.Safe",
        ],
    )


def test_modules_whose_database_driver_is_missing_are_skipped_and_listed(tmp_path):
    # when
    _reported, _found, skipped, _raised = scanned_package_report(tmp_path)
    # then
    assert skipped == [
        "bad_config",
        "driverless",
        "no_dotted",
        "no_driver",
        "no_psycopg",
        "no_psycopg2",
    ]


def test_an_import_error_for_anything_but_a_driver_library_is_not_hidden(tmp_path):
    # when
    _reported, _found, _skipped, raised = scanned_package_report(tmp_path)
    # then
    assert raised == [
        "RuntimeError",
        "ImportError",
        "ImportError",
        "ModuleNotFoundError",
        "ModuleNotFoundError",
        "ImportError",
        "ImproperlyConfigured",
        "ImportError",
    ]


def test_no_module_of_the_default_packages_is_skipped_in_this_environment():
    # when
    skipped = skipped_in_this_environment()
    # then
    # a future backend module that needs a driver missing from the unit job fails here on purpose
    assert skipped == []


CONNECT_LISTED = [
    "a.a_wrappers.Between",
    "a.sub.deep.Deep",
    "a.wrappers.Bare",
    "a.wrappers.OnSqlite",
    "b.a_wrappers.Between",
    "b.sub.deep.Deep",
    "b.wrappers.Bare",
    "b.wrappers.OnSqlite",
]
FOUND_LISTED = [
    "a.a_wrappers.Between",
    "a.sub.deep.Deep",
    "a.wrappers.Bare",
    "a.wrappers.OnSqlite",
    "a.wrappers.Safe",
    "b.a_wrappers.Between",
    "b.sub.deep.Deep",
    "b.wrappers.Bare",
    "b.wrappers.OnSqlite",
    "b.wrappers.Safe",
]


def test_the_default_scan_of_the_connecting_classes_imports_the_default_packages(tmp_path):
    # when
    before, listed, left = default_scan_report(tmp_path, "connect")
    # then
    assert (before, listed, left) == ([], CONNECT_LISTED, [])


def test_the_default_scan_of_the_found_classes_imports_the_default_packages(tmp_path):
    # when
    before, listed, left = default_scan_report(tmp_path, "found")
    # then
    assert (before, listed, left) == ([], FOUND_LISTED, [])
