"""Comparable, hashable snapshots of ORM results.

Types are part of every snapshot on purpose: async drivers may return a different Python type
for the same column (Decimal vs float, memoryview vs bytes, naive vs aware datetime, decoded vs
raw JSON). Equal values with different types are a mismatch.
"""

from __future__ import annotations

import json
from typing import Any, cast

from django.db.models import Model

type Snapshot = tuple[Any, ...]


def snapshot_value(value: Any, _path: frozenset[int] = frozenset()) -> tuple[str, Any]:
    type_name = type(value).__name__
    if isinstance(value, Model):
        return type_name, _snapshot(value, _path)
    if isinstance(value, memoryview):
        return type_name, value.tobytes()
    if isinstance(value, list) and value and all(isinstance(v, Model) for v in value):
        return type_name, tuple(_snapshot(v, _path) for v in value)  # e.g. Prefetch(to_attr=...)
    if isinstance(value, dict | list):
        return type_name, json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, tuple):
        return type_name, tuple(snapshot_value(v, _path) for v in value)
    return type_name, value


def snapshot(obj: Model) -> Snapshot:
    """Model label, fields, annotations and ``select_related`` objects of one instance.

    Shape: ``(label, fields, annotations, related)`` where ``fields`` and ``annotations`` are
    ``((name, type name, value)...)`` and ``related`` is ``((cache_name, snapshot | None)...)``.

    Annotations (``qs.annotate(...)``) are stored as plain instance attributes: they are the public
    attributes that are not concrete fields (``extra(select=...)`` values and
    ``Prefetch(to_attr=...)`` lists count too). Other private state is ignored, so annotation
    aliases starting with ``_`` are not compared - do not generate them.

    Related objects cached in ``_state.fields_cache`` (``select_related``) are snapshotted
    recursively and sorted by cache name. A relation that was not loaded has no entry; one loaded
    as ``None`` (nullable FK, missing reverse one-to-one) is ``(name, None)``. An object already on
    the recursion path (a one-to-one cycle) becomes ``("<ref>", label, pk snapshot)``.
    The cache state at call time is what is compared (single-valued ``prefetch_related`` fills it
    too), so do not touch relations lazily before snapshotting. ``_prefetched_objects_cache``
    (reverse FK / M2M prefetch) is out of scope (#17).
    """
    return _snapshot(obj, frozenset())


def _snapshot(obj: Model, path: frozenset[int]) -> Snapshot:
    if id(obj) in path:
        return ("<ref>", obj._meta.label, snapshot_value(obj.pk))
    path = path | {id(obj)}
    fields = obj._meta.concrete_fields
    attnames = {f.attname for f in fields}
    annotations = sorted(
        (name, value)
        for name, value in vars(obj).items()
        if not name.startswith("_") and name not in attnames
    )
    return (
        obj._meta.label,
        tuple((f.attname, *snapshot_value(getattr(obj, f.attname))) for f in fields),
        tuple((name, *snapshot_value(value, path)) for name, value in annotations),
        tuple(
            (name, None if related is None else _snapshot(related, path))
            for name, related in sorted(
                cast("dict[str, Model | None]", obj._state.fields_cache).items()
            )
        ),
    )


def annotation_names(instance_snapshot: Snapshot) -> tuple[str, ...]:
    """Names of the annotations captured in a ``snapshot()`` of one instance, sorted."""
    return tuple(name for name, _type, _value in instance_snapshot[2])


def snapshot_row(row: Any) -> Snapshot:
    if isinstance(row, Model):
        return snapshot(row)
    if isinstance(row, dict):
        return ("dict", tuple(sorted((k, *snapshot_value(v)) for k, v in row.items())))
    return ("value", snapshot_value(row))


def snapshot_result(result: Any) -> Snapshot:
    """Snapshot whatever an evaluation returned (list of rows, one instance, scalar, None)."""
    if isinstance(result, list):
        return ("list", tuple(snapshot_row(r) for r in result))
    return ("single", snapshot_row(result))


def nested_related_names(instance_snapshot: Snapshot) -> tuple[str, ...]:
    """Cache names along the first related chain of a ``snapshot()`` (``author``, ``publisher``)."""
    names: list[str] = []
    related = instance_snapshot[3]
    while related:
        name, nested = related[0]
        names.append(name)
        related = nested[3] if nested else ()
    return tuple(names)
