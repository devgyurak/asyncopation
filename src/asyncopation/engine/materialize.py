"""The materialize stage: rows become model instances, dicts or tuples (ADR-0002 Decision 4).

Synchronous and free of I/O. Django's ``compiler.results_iter(results=[rows])`` applies converters
(``from_db_value``) and composite-pk handling exactly as in sync evaluation; what is ported here is
the code that follows the ``execute_sql`` call in Django's iterables.
"""

# Ported from django/db/models/query.py::ModelIterable.__iter__ @ 6.1.3
# Changes: execute_sql() is replaced by the rows the execute stage fetched (results_iter(results=[rows]));
# the generator is a list builder; Django-internal reads go through asyncopation._django.query.
# Also ported (same file): ValuesIterable / ValuesListIterable / NamedValuesListIterable /
# FlatValuesListIterable, with the same changes.

from __future__ import annotations

import operator
from typing import Any
from weakref import ref as weak_ref

from asyncopation._django import query as dq
from asyncopation.engine.compiled import CompiledQuery

__all__ = ["materialize"]


def materialize(queryset: Any, compiled: CompiledQuery, rows: list[tuple[Any, ...]]) -> list[Any]:
    """Build the result list of ``queryset`` from the rows of ``compiled``."""
    kind = dq.iterable_kind(queryset)
    compiler = compiled.compiler
    if kind is dq.IterableKind.MODEL:
        return _models(queryset, compiler, rows)
    if kind is dq.IterableKind.VALUES:
        names = dq.values_names(queryset)
        indexes = range(len(names))
        return [
            {names[i]: row[i] for i in indexes} for row in compiler.results_iter(results=[rows])
        ]
    tuples = compiler.results_iter(results=[rows], tuple_expected=True)
    if kind is dq.IterableKind.VALUES_LIST:
        return list(tuples)
    if kind is dq.IterableKind.FLAT:
        return [row[0] for row in compiler.results_iter(results=[rows])]
    names = dq.queryset_fields(queryset) or dq.values_names(queryset)
    tuple_class = dq.namedtuple_class(*names)
    new = tuple.__new__
    return [new(tuple_class, row) for row in tuples]


def _models(queryset: Any, compiler: Any, rows: list[tuple[Any, ...]]) -> list[Any]:
    db = queryset.db
    fetch_mode = dq.queryset_fetch_mode(queryset)
    select = dq.select(compiler)
    klass_info = dq.klass_info(compiler)
    annotation_col_map = dq.annotation_col_map(compiler)
    model_cls = klass_info["model"]
    select_fields = klass_info["select_fields"]
    model_fields_start, model_fields_end = select_fields[0], select_fields[-1] + 1
    init_list = [f[0].target.attname for f in select[model_fields_start:model_fields_end]]
    from_db = dq.from_db_callable(model_cls, fetch_mode)
    related_populators = dq.related_populators(klass_info, select, db, fetch_mode)
    known_related_objects = [
        (
            field,
            related_objs,
            attnames := [
                (
                    field.attname
                    if from_field == "self"
                    else queryset.model._meta.get_field(from_field).attname
                )
                for from_field in field.from_fields
            ],
            operator.attrgetter(*attnames),
        )
        for field, related_objs in dq.known_related_objects(queryset).items()
    ]
    peers: list[Any] = []
    result = []
    for row in compiler.results_iter(results=[rows]):
        obj = from_db(db, init_list, row[model_fields_start:model_fields_end])
        if fetch_mode.track_peers:
            peers.append(weak_ref(obj))
            dq.set_peers(obj, peers)
        for rel_populator in related_populators:
            rel_populator.populate(row, obj)
        if annotation_col_map:
            for attr_name, col_pos in annotation_col_map.items():
                setattr(obj, attr_name, row[col_pos])

        # Add the known related objects to the model.
        for field, rel_objs, rel_attnames, rel_getter in known_related_objects:
            # Avoid overwriting objects loaded by, e.g., select_related().
            if field.is_cached(obj):
                continue
            # Avoid fetching potentially deferred attributes that would
            # result in unexpected queries.
            if any(attname not in obj.__dict__ for attname in rel_attnames):
                continue
            rel_obj_id = rel_getter(obj)
            try:
                rel_obj = rel_objs[rel_obj_id]
            except KeyError:
                pass  # May happen in qs1 | qs2 scenarios.
            else:
                setattr(obj, field.name, rel_obj)

        result.append(obj)
    return result
