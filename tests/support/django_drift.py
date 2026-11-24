"""Drift checks for docs/django-api-ledger.toml against the installed Django.

Each function returns human-readable problems (empty list = no drift) so tests stay free of
control flow.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from typing import Any

PINNED_DJANGO = (6, 1)


def import_problem(module: str, name: str) -> str | None:
    """Why ``module.name`` cannot be imported, or None. Name ``*`` checks the module only."""
    try:
        target = importlib.import_module(module)
    except ImportError as exc:
        return f"{module}: {exc}"
    if name == "*" or hasattr(target, name):
        return None
    try:
        importlib.import_module(f"{module}.{name}")
    except ImportError:
        return f"{module}.{name}: not found"
    return None


def signature_problem(module: str, name: str, expected: str) -> str | None:
    """Why the live signature differs from the recorded one, or None."""
    try:
        actual = str(inspect.signature(getattr(importlib.import_module(module), name)))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        return f"{module}.{name}: signature unavailable ({exc})"
    if actual != expected:
        return f"{module}.{name}: signature is {actual}, ledger records {expected}"
    return None


def attribute_problem(owner: str, name: str) -> str | None:
    """Why ``owner.name`` no longer exists, or None.

    A class attribute passes ``hasattr``; an instance attribute is found as a ``self.<name>``
    assignment in the source of the owner or any base class.
    """
    module, _, class_name = owner.rpartition(".")
    try:
        cls = getattr(importlib.import_module(module), class_name)
    except (ImportError, AttributeError) as exc:
        return f"{owner}: owner not importable ({exc})"
    if not inspect.isclass(cls):
        return f"{owner}: owner is not a class"
    if hasattr(cls, name) or any(_assigns_self_attribute(base, name) for base in cls.__mro__):
        return None
    return f"{owner}.{name}: not a class attribute and never assigned as self.{name}"


def _assigns_self_attribute(cls: Any, name: str) -> bool:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    except (OSError, TypeError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        for node in ast.walk(tree)
    )
