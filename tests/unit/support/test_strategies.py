from hypothesis import given

from tests.support.strategies import (
    QuerySetSpec,
    build_queryset,
    expected_deferred_loading,
    expected_iterable_class,
    projected_specs,
    queryset_specs,
    tie_break_is_present,
)


@given(spec=queryset_specs())
def test_build_queryset_applies_exactly_the_spec_annotations(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert tuple(sorted(qs.query.annotations)) == spec.annotations


@given(spec=queryset_specs())
def test_build_queryset_always_orders_by_pk_last(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert qs.query.order_by[-1] == "pk"


@given(spec=queryset_specs())
def test_build_queryset_applies_the_spec_deferred_loading(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert qs.query.deferred_loading == expected_deferred_loading(spec)


@given(spec=projected_specs())
def test_projected_specs_have_a_projection_and_no_deferral(spec: QuerySetSpec):
    # when
    projection, deferred = spec.projection, spec.deferred
    # then
    assert projection is not None
    assert deferred is None


@given(spec=projected_specs())
def test_build_queryset_applies_the_spec_projection_iterable(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert qs._iterable_class is expected_iterable_class(spec)


@given(spec=projected_specs())
def test_projected_specs_order_a_projected_tag_name_totally(spec: QuerySetSpec):
    # when
    total = tie_break_is_present(spec)
    # then
    assert total
