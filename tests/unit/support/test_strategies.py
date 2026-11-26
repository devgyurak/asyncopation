import pytest
from hypothesis import given

from tests.support.strategies import (
    QuerySetSpec,
    bare_spec,
    build_queryset,
    combined_read_queryset_specs,
    expected_deferred_loading,
    expected_iterable_class,
    find_spec_with_combinator,
    find_uncombined_spec_ordered_by_extra,
    get_queryset_specs,
    ordered_by_selected_columns,
    projected_specs,
    queryset_specs,
    read_queryset_specs,
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


@given(spec=read_queryset_specs())
def test_build_queryset_applies_the_read_spec_distinct(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert qs.query.distinct is spec.distinct


@given(spec=read_queryset_specs())
def test_build_queryset_applies_the_read_spec_extra_select(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert bool(qs.query.extra) is spec.extra


@given(spec=read_queryset_specs().filter(lambda spec: spec.window is None))
def test_build_queryset_applies_the_read_spec_none(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert qs.query.is_empty() is spec.none


@given(spec=read_queryset_specs())
def test_build_queryset_applies_the_read_spec_projection(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert (qs._fields is not None) is (spec.projection is not None)


@given(spec=queryset_specs())
def test_queryset_specs_leave_the_read_dimensions_off(spec: QuerySetSpec):
    # when
    read_dimensions = (spec.distinct, spec.extra, spec.projection, spec.none, spec.combinator)
    # then
    assert read_dimensions == (False, False, None, False, None)


@given(spec=read_queryset_specs().filter(lambda spec: not spec.none))
def test_build_queryset_applies_the_read_spec_combinator(spec: QuerySetSpec):
    # when
    qs = build_queryset(spec)
    # then
    assert qs.query.combinator == spec.combinator


@given(spec=get_queryset_specs())
def test_get_queryset_specs_have_no_window_and_no_combinator(spec: QuerySetSpec):
    # when
    shape = (spec.window, spec.combinator)
    # then
    assert shape == (None, None)


@pytest.mark.parametrize("combinator", ["union", "intersection", "difference"])
def test_read_queryset_specs_draw_every_combinator(combinator):
    # when
    spec = find_spec_with_combinator(combinator)
    # then
    assert build_queryset(spec).query.combinator == combinator


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        pytest.param(
            bare_spec(("author__name", "title")),
            ("title",),
            id="a-related-column-is-not-selected",
        ),
        pytest.param(
            bare_spec(("extra_pages", "pk")),
            ("pk",),
            id="the-extra-select-is-not-selected",
        ),
        pytest.param(
            bare_spec(("-pages", "-title"), deferred=("only", ("title",))),
            ("-title",),
            id="only-keeps-the-listed-columns",
        ),
        pytest.param(
            bare_spec(("pages", "title", "price"), deferred=("defer", ("pages",))),
            ("title", "price"),
            id="defer-drops-the-listed-columns",
        ),
        pytest.param(
            bare_spec(("pk", "-pages"), deferred=("only", ("title",))),
            ("pk",),
            id="only-always-selects-the-pk",
        ),
        pytest.param(
            bare_spec(("-double_pages", "pages"), annotations=("double_pages",)),
            ("-double_pages", "pages"),
            id="an-annotation-is-selected",
        ),
        pytest.param(
            bare_spec(
                ("-double_pages", "-n_tags"),
                deferred=("only", ("title",)),
                annotations=("double_pages",),
            ),
            ("-double_pages",),
            id="only-keeps-the-annotations-of-the-spec",
        ),
        pytest.param(
            bare_spec(("pages", "title"), projection=("values", ("title",))),
            ("title",),
            id="a-projection-selects-only-its-fields",
        ),
        pytest.param(
            bare_spec(("pk", "pages"), projection=("values", ("title",))),
            ("pk",),
            id="a-projection-gains-the-pk",
        ),
    ],
)
def test_ordered_by_selected_columns_keeps_exactly_the_selected_terms(spec, expected):
    # when
    ordered = ordered_by_selected_columns(spec)
    # then
    assert ordered.order_by == expected


@pytest.mark.parametrize(
    ("projection", "expected"),
    [
        pytest.param(("flat", ("title",)), ("values_list", ("title", "pk")), id="flat"),
        pytest.param(("values", ("title",)), ("values", ("title", "pk")), id="values"),
        pytest.param(
            ("named", ("title", "pages")), ("named", ("title", "pages", "pk")), id="named"
        ),
        pytest.param(("flat", ("pk",)), ("flat", ("pk",)), id="flat-with-the-pk"),
        pytest.param(("values", ("pages", "pk")), ("values", ("pages", "pk")), id="has-the-pk"),
    ],
)
def test_ordered_by_selected_columns_gives_a_projection_the_pk(projection, expected):
    # when
    ordered = ordered_by_selected_columns(bare_spec((), projection=projection))
    # then
    assert ordered.projection == expected


def test_read_queryset_specs_without_a_combinator_keep_every_ordering_term():
    # when
    spec = find_uncombined_spec_ordered_by_extra()
    # then
    assert "extra_pages" in spec.order_by


@given(spec=combined_read_queryset_specs())
def test_read_queryset_specs_output_is_already_ordered_by_selected_columns(spec: QuerySetSpec):
    # when
    ordered = ordered_by_selected_columns(spec)
    # then
    assert ordered == spec
