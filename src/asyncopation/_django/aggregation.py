"""Query.get_aggregation / exists split around the I/O (ADR-0008 Decision 4, ADR-0007).

Django's ``Query.get_aggregation`` rewrites the query (a subquery for sliced / distinct /
already-aggregated queries), compiles the outer query and runs ``execute_sql(SINGLE)``. The
rewrite and the conversion of the row are pure; this module holds them without the execution, so
the engine's sans-I/O cores can yield a ``Compile`` / ``Execute`` pair in between. ``query`` is
mutated exactly as Django mutates ``self`` (callers clone first, as ``get_count`` and ``exists``
do).
"""

# Ported from django/db/models/sql/query.py::Query.get_aggregation @ 6.1.3
# Changes: `self` is the `query` argument; the function stops before `get_compiler` / `execute_sql`
# (returning the outer query and the empty-result values), and `finish_aggregation` is the code
# after the execution. Also ported (same file): Query.get_count (a Count("*") aggregation on a
# clone) and Query.has_results (Query.exists() then the compiler's has_results). `count_query` and
# `exists_query` clone with `dq.clone_query`, which gives the clone its own copy of the `selected`
# dict (`Query.clone()` shares it), so a later in-place change of `selected` cannot reach the
# caller's query.

from __future__ import annotations

from typing import Any, cast

from django.db.models import Count
from django.db.models.expressions import Ref
from django.db.models.sql.subqueries import AggregateQuery

from asyncopation._django import query as dq

__all__ = ["count_query", "exists_query", "finish_aggregation", "prepare_aggregation"]


def count_query(query: Any) -> tuple[Any, dict[str, Any]]:
    """``(clone, {"__count": Count("*")})``: what ``Query.get_count`` aggregates."""
    return dq.clone_query(query), {"__count": Count("*")}


def exists_query(query: Any) -> Any:
    """The ``LIMIT 1`` query ``Query.has_results`` compiles (``Query.exists``)."""
    return dq.clone_query(query).exists()


def prepare_aggregation(query: Any, aggregate_exprs: dict[str, Any]) -> tuple[Any, bool, list[Any]]:
    """The outer query of ``get_aggregation``, whether to elide empty sets and the empty-set values."""
    # Store annotation mask prior to temporarily adding aggregations for
    # resolving purpose to facilitate their subsequent removal.
    refs_subquery = False
    refs_window = False
    replacements = {}
    annotation_select_mask = query.annotation_select_mask
    for alias, aggregate_expr in aggregate_exprs.items():
        query.check_alias(alias)
        aggregate = aggregate_expr.resolve_expression(
            query, allow_joins=True, reuse=None, summarize=True
        )
        if not aggregate.contains_aggregate:
            raise TypeError(f"{alias} is not an aggregate expression")
        # Temporarily add aggregate to annotations to allow remaining
        # members of `aggregates` to resolve against each others.
        query.append_annotation_mask([alias])
        aggregate_refs = aggregate.get_refs()
        refs_subquery |= any(
            getattr(query.annotations[ref], "contains_subquery", False) for ref in aggregate_refs
        )
        refs_window |= any(
            getattr(query.annotations[ref], "contains_over_clause", True) for ref in aggregate_refs
        )
        aggregate = aggregate.replace_expressions(replacements)
        query.annotations[alias] = aggregate
        replacements[Ref(alias, aggregate)] = aggregate
    # Stash resolved aggregates now that they have been allowed to resolve
    # against each other.
    aggregates = {alias: query.annotations.pop(alias) for alias in aggregate_exprs}
    query.set_annotation_mask(annotation_select_mask)
    # Existing usage of aggregation can be determined by the presence of
    # selected aggregates but also by filters against aliased aggregates.
    _, having, qualify = query.where.split_having_qualify()
    has_existing_aggregation = (
        any(
            getattr(annotation, "contains_aggregate", True)
            for annotation in query.annotations.values()
        )
        or having
    )
    set_returning_annotations = {
        alias
        for alias, annotation in query.annotation_select.items()
        if getattr(annotation, "set_returning", False)
    }
    outer_query: Any
    # Decide if we need to use a subquery.
    #
    # Existing aggregations would cause incorrect results as
    # get_aggregation() must produce just one result and thus must not use
    # GROUP BY.
    #
    # If the query has limit or distinct, or uses set operations, then
    # those operations must be done in a subquery so that the query
    # aggregates on the limit and/or distinct results instead of applying
    # the distinct and limit after the aggregation.
    if (
        isinstance(query.group_by, tuple)
        or query.is_sliced
        or has_existing_aggregation
        or refs_subquery
        or refs_window
        or qualify
        or query.distinct
        or query.combinator
        or set_returning_annotations
    ):
        inner_query = query.clone()
        inner_query.subquery = True
        outer_query = cast("Any", AggregateQuery(query.model, inner_query))
        inner_query.select_for_update = False
        inner_query.select_related = False
        inner_query.set_annotation_mask(query.annotation_select)
        # Queries with distinct_fields need ordering and when a limit is
        # applied we must take the slice from the ordered query. Otherwise
        # no need for ordering.
        if inner_query.orderby_issubset_groupby:
            inner_query.clear_ordering(force=False)
        if not inner_query.distinct:
            # If the inner query uses default select and it has some
            # aggregate annotations, then we must make sure the inner
            # query is grouped by the main model's primary key. However,
            # clearing the select clause can alter results if distinct is
            # used.
            if inner_query.default_cols and has_existing_aggregation:
                inner_query.group_by = (
                    query.model._meta.pk.get_col(inner_query.get_initial_alias()),
                )
            inner_query.default_cols = False
            if not qualify and not query.combinator:
                # Mask existing annotations that are not referenced by
                # aggregates to be pushed to the outer query unless
                # filtering against window functions or if the query is
                # combined as both would require complex realiasing logic.
                annotation_mask = set()
                if isinstance(query.group_by, tuple):
                    for expr in query.group_by:
                        annotation_mask |= expr.get_refs()
                for aggregate in aggregates.values():
                    annotation_mask |= aggregate.get_refs()
                # Avoid eliding expressions that might have an incidence on
                # the implicit grouping logic.
                for annotation_alias, annotation in query.annotation_select.items():
                    if annotation.get_group_by_cols():
                        annotation_mask.add(annotation_alias)
                inner_query.set_annotation_mask(annotation_mask)
                # Annotations that possibly return multiple rows cannot
                # be masked as they might have an incidence on the query.
                annotation_mask |= set_returning_annotations

        # Add aggregates to the outer AggregateQuery. This requires making
        # sure all columns referenced by the aggregates are selected in the
        # inner query. It is achieved by retrieving all column references
        # by the aggregates, explicitly selecting them in the inner query,
        # and making sure the aggregates are repointed to them.
        col_refs = {}
        for alias, aggregate in aggregates.items():
            replacements = {}
            for col in query._gen_cols([aggregate], resolve_refs=False):
                if not (col_ref := col_refs.get(col)):
                    index = len(col_refs) + 1
                    col_alias = f"__col{index}"
                    col_ref = Ref(col_alias, col)
                    col_refs[col] = col_ref
                    inner_query.add_annotation(col, col_alias)
                replacements[col] = col_ref
            outer_query.annotations[alias] = aggregate.replace_expressions(replacements)
        if (
            inner_query.select == ()
            and not inner_query.default_cols
            and not inner_query.annotation_select_mask
        ):
            # In case of Model.objects[0:3].count(), there would be no
            # field selected in the inner query, yet we must use a
            # subquery. So, make sure at least one field is selected.
            inner_query.select = (query.model._meta.pk.get_col(inner_query.get_initial_alias()),)
    else:
        outer_query = query
        query.select = ()
        query.selected = None
        query.default_cols = False
        query.extra = {}
        if query.annotations:
            # Inline reference to existing annotations and mask them as
            # they are unnecessary given only the summarized aggregations
            # are requested.
            replacements = {
                Ref(alias, annotation): annotation
                for alias, annotation in query.annotations.items()
            }
            query.annotations = {
                alias: aggregate.replace_expressions(replacements)
                for alias, aggregate in aggregates.items()
            }
        else:
            query.annotations = aggregates
        query.set_annotation_mask(aggregates)

    empty_set_result = [
        expression.empty_result_set_value for expression in outer_query.annotation_select.values()
    ]
    elide_empty = not any(result is NotImplemented for result in empty_set_result)

    outer_query.clear_ordering(force=True)
    outer_query.clear_limits()
    outer_query.select_for_update = False
    outer_query.select_related = False
    return outer_query, elide_empty, empty_set_result


def finish_aggregation(
    compiler: Any, outer_query: Any, row: Any, empty_set_result: list[Any]
) -> dict[str, Any]:
    """Convert the row of the aggregation (``None`` for an empty set) as Django does."""
    if row is None:
        result = empty_set_result
    else:
        cols = outer_query.annotation_select.values()
        converters = compiler.get_converters(cols)
        rows = compiler.apply_converters((row,), converters)
        if compiler.has_composite_fields(cols):
            rows = compiler.composite_fields_to_tuples(rows, cols)
        result = next(rows)
    return dict(zip(outer_query.annotation_select, result, strict=False))
