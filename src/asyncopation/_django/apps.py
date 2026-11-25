"""Django app-registry reads (ADR-0003 Decision 2): the one place that asks which apps are installed."""

from __future__ import annotations

__all__ = ["is_installed"]


def is_installed(name: str) -> bool:
    """Whether the app ``name`` is in ``INSTALLED_APPS``. Pure: reads the ready app registry."""
    from django.apps import apps

    return apps.is_installed(name)
