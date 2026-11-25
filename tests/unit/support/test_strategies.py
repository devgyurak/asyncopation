from django.db.models.query import (
    FlatValuesListIterable,
    NamedValuesListIterable,
    ValuesIterable,
    ValuesListIterable,
)
from hypothesis import given

from tests.support.strategies import (
    QuerySetSpec,
    build_queryset,
    expected_deferred_loading,
    projected_specs,
    queryset_specs,
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
    # then
    assert spec.projection is not None
    assert spec.deferred is None


@given(spec=projected_specs())
def test_build_queryset_applies_the_spec_projection_iterable(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    kind = (spec.projection or ("values",))[0]
    assert (
        qs._iterable_class
        is {
            "values": ValuesIterable,
            "values_list": ValuesListIterable,
            "flat": FlatValuesListIterable,
            "named": NamedValuesListIterable,
        }[kind]
    )
