"""Comparisons between `configured_databases()` and Django's own configured settings."""

from __future__ import annotations

from typing import Any

from django.db import connections


def django_databases() -> dict[str, dict[str, Any]]:
    return connections.settings


def mutation_leaks_into_django() -> bool:
    """Whether mutating the returned copy (top level and nested) changes Django's settings."""
    from asyncopation._django.connections import configured_databases

    copy_ = configured_databases()
    copy_["default"]["NAME"] = "mutated-by-test"
    copy_["default"]["OPTIONS"]["mutated"] = True
    copy_["default"]["TEST"]["mutated"] = True
    live = django_databases()["default"]
    return (
        live["NAME"] == "mutated-by-test"
        or "mutated" in live["OPTIONS"]
        or "mutated" in live["TEST"]
    )


def nested_dicts_are_shared() -> bool:
    from asyncopation._django.connections import configured_databases

    copy_ = configured_databases()["default"]
    live = django_databases()["default"]
    return copy_["OPTIONS"] is live["OPTIONS"] or copy_["TEST"] is live["TEST"]
