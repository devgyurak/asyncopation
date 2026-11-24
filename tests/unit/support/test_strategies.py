from hypothesis import given

from tests.support.strategies import QuerySetSpec, build_queryset, queryset_specs


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
