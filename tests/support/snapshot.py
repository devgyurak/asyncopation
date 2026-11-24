"""Comparable, hashable snapshots of ORM results.

Types are part of every snapshot on purpose: async drivers may return a different Python type
for the same column (Decimal vs float, memoryview vs bytes, naive vs aware datetime, decoded vs
raw JSON). Equal values with different types are a mismatch.
"""

from __future__ import annotations

import json
from typing import Any

from django.db.models import Model

type Snapshot = tuple[Any, ...]


def snapshot_value(value: Any) -> tuple[str, Any]:
    type_name = type(value).__name__
    if isinstance(value, Model):
        return type_name, snapshot(value)
    if isinstance(value, memoryview):
        return type_name, value.tobytes()
    if isinstance(value, list) and value and all(isinstance(v, Model) for v in value):
        return type_name, tuple(snapshot(v) for v in value)  # e.g. Prefetch(to_attr=...)
    if isinstance(value, dict | list):
        return type_name, json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, tuple):
        return type_name, tuple(snapshot_value(v) for v in value)
    return type_name, value


def snapshot(obj: Model) -> Snapshot:
    """Model label, every concrete field and every annotation as (name, type name, value).

    Annotations (``qs.annotate(...)``) are stored as plain instance attributes: they are the public
    attributes that are not concrete fields (``extra(select=...)`` values and
    ``Prefetch(to_attr=...)`` lists count too). Private state (``_state``, caches) is ignored, so
    annotation aliases starting with ``_`` are not compared - do not generate them.
    """
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
        tuple((name, *snapshot_value(value)) for name, value in annotations),
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
