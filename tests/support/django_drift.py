"""Drift checks against the installed Django.

Ledger checks: docs/django-api-ledger.toml against the installed Django. Pin checks: the
``django`` requirement and ``Framework :: Django :: X.Y`` classifiers in pyproject.toml against
each other and against the installed Django.

Each function returns a human-readable problem (None = no drift) and never raises, so tests stay
free of control flow.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
import re
import textwrap
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

CLASSIFIER_PREFIX = "Framework :: Django :: "


def django_requirement(pyproject: str) -> str | None:
    """The unconditional ``django`` requirement of ``[project].dependencies``, or None.

    Entries with an environment marker are skipped. Several entries are combined, as pip does,
    into one requirement that carries every specifier.
    """
    try:
        project = tomllib.loads(pyproject).get("project")
    except tomllib.TOMLDecodeError:
        return None
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    entries = (
        [d for d in dependencies if isinstance(d, str)] if isinstance(dependencies, list) else []
    )
    found = [
        (entry, req) for entry in entries if (req := _django_requirement_of(entry)) is not None
    ]
    if len(found) <= 1:
        return found[0][0] if found else None
    return "django" + ",".join(sorted({str(spec) for _, req in found for spec in req.specifier}))


def pin_problem(requirement: str | None, installed: str) -> str | None:
    """Why the installed Django violates the pyproject requirement, or None."""
    if requirement is None:
        return "pyproject.toml: no unconditional django requirement in [project].dependencies"
    try:
        allowed = Requirement(requirement).specifier
        version = Version(installed)
    except (InvalidRequirement, InvalidVersion) as exc:
        return f"{requirement!r} / {installed!r}: {exc}"
    if not allowed:
        return f"{requirement!r} does not constrain the Django version"
    if not allowed.contains(version, prereleases=True):
        return f"installed Django {installed} is outside the pyproject pin {requirement!r}"
    return None


def classifier_problem(pyproject: str, installed: str) -> str | None:
    """Why the ``Framework :: Django :: X.Y`` classifiers disagree with the pin, or None.

    The minors the requirement admits are derived from its specifier (see :func:`_admitted_minors`).
    A classifier for a minor outside that set is stale; an admitted minor without a classifier is
    missing. Classifiers that are not ``X.Y`` (the bare ``Framework :: Django``, a major-only
    ``Framework :: Django :: 6``) are ignored. A specifier whose minors cannot be enumerated
    (several majors, no lower or upper bound, a two-part ``~=``) is reported, not guessed.
    """
    requirement = django_requirement(pyproject)
    if (problem := pin_problem(requirement, installed)) is not None:
        return problem
    classifiers = tomllib.loads(pyproject)["project"].get("classifiers", [])
    if not isinstance(classifiers, list):
        return "pyproject.toml: [project].classifiers is not a list"
    admitted = _admitted_minors(Requirement(str(requirement)).specifier)
    if isinstance(admitted, str):
        return f"{requirement!r}: {admitted}"
    written = {
        text
        for c in classifiers
        if isinstance(c, str) and c.startswith(CLASSIFIER_PREFIX)
        if re.fullmatch(r"\d+\.\d+", text := c.removeprefix(CLASSIFIER_PREFIX).strip())
    }
    stale = sorted(written - admitted)
    missing = sorted(admitted - written)
    if stale:
        return f"classifier(s) {stale} are outside the pin {requirement!r}; update or remove them"
    if missing:
        return (
            "no "
            + ", ".join(f"'{CLASSIFIER_PREFIX}{m}'" for m in missing)
            + f" classifier for the pin {requirement!r}"
        )
    return None


def pyproject_text(requirement: str, classifiers: list[str]) -> str:
    """A minimal pyproject.toml with one dependency and the given classifiers (test input)."""
    listed = ", ".join(json.dumps(c) for c in classifiers)
    return f'[project]\nname = "demo"\ndependencies = [{json.dumps(requirement)}]\nclassifiers = [{listed}]\n'


def _admitted_minors(specifier: SpecifierSet) -> set[str] | str:
    """The ``major.minor`` strings the specifier admits, or why they cannot be enumerated.

    Candidates are every bound, the bound with a patch appended, and ``major.minor`` between the
    lowest and highest bound minor; ``contains`` then decides which of them the range admits.
    """
    specs = list(specifier)
    if any(s.operator == "===" for s in specs):
        return "arbitrary equality (===) cannot be enumerated"
    if any(s.operator == "~=" and len(Version(s.version).release) < 3 for s in specs):
        return "a two-part ~= admits several minors; pin the minor explicitly"
    if any(
        s.operator == "==" and s.version.endswith(".*") and "." not in s.version[:-2] for s in specs
    ):
        return "a major-only == wildcard admits several minors; pin the minor explicitly"
    has_lower = any(s.operator in {">=", ">", "~=", "=="} for s in specs)
    has_upper = any(s.operator in {"<", "<=", "~=", "=="} for s in specs)
    if not (has_lower and has_upper):
        return "needs both a lower and an upper bound to enumerate the admitted minors"
    bounds = [Version(s.version.removesuffix(".*")) for s in specs]
    majors = {b.major for b in bounds}
    if len(majors) != 1:
        return f"spans several major versions {sorted(majors)}; minors cannot be enumerated"
    major = majors.pop()
    low, high = min(b.minor for b in bounds), max(b.minor for b in bounds)
    candidates = [Version(f"{major}.{minor}") for minor in range(low, high + 1)]
    candidates += [v for b in bounds for v in (b, Version(f"{b.base_version}.1"))]
    return {f"{v.major}.{v.minor}" for v in candidates if specifier.contains(v, prereleases=True)}


def _django_requirement_of(dependency: str) -> Requirement | None:
    try:
        parsed = Requirement(dependency)
    except InvalidRequirement:
        return None
    if canonicalize_name(parsed.name) == "django" and parsed.marker is None:
        return parsed
    return None


def import_problem(module: str, name: str) -> str | None:
    """Why ``module.name`` cannot be imported, or None. Name ``*`` checks the module only."""
    try:
        target = importlib.import_module(module)
    except Exception as exc:
        return f"{module}: {type(exc).__name__}: {exc}"
    if name == "*" or hasattr(target, name):
        return None
    return f"{module}.{name}: not found"


def signature_problem(module: str, name: str, expected: str) -> str | None:
    """Why the signature of ``module.<dotted name>`` differs from the recorded one, or None."""
    return _signature_of(f"{module}.{name}", expected)


def attribute_signature_problem(owner: str, name: str, expected: str) -> str | None:
    """Same as :func:`signature_problem` for ``owner.name`` (owner is a dotted class path)."""
    return _signature_of(f"{owner}.{name}", expected)


def attribute_problem(owner: str, name: str) -> str | None:
    """Why ``owner.name`` no longer exists, or None.

    A class attribute passes ``hasattr``; an instance attribute is found as a ``self.<name>``
    assignment in the source of the owner or any base class; an attribute installed by a
    metaclass (``Model._meta``) is found as ``add_to_class("name", ...)`` / ``setattr(obj, "name", ...)``
    in the metaclass source, or as a member of the metaclass itself (a property).
    """
    cls = _resolve(owner)
    if isinstance(cls, _Unresolved):
        return f"{owner}: owner not resolvable ({cls.reason})"
    if not inspect.isclass(cls):
        return f"{owner}: owner is not a class"
    if (
        hasattr(cls, name)
        or any(_assigns_self_attribute(base, name) for base in cls.__mro__)
        or any(_installed_by_metaclass(meta, name) for meta in type(cls).__mro__[:-2])
    ):
        return None
    return f"{owner}.{name}: not a class attribute and never assigned as self.{name}"


def ported_problem(module: str, name: str) -> str | None:
    """Why a *ported* (never imported) ``module.<dotted name>`` is gone from the source."""
    try:
        spec = importlib.util.find_spec(module)
        source = Path(spec.origin).read_text(encoding="utf-8") if spec and spec.origin else None
    except Exception as exc:
        return f"{module}: {type(exc).__name__}: {exc}"
    if source is None:
        return f"{module}: no source file"
    if _defines(ast.parse(source).body, name.split(".")):
        return None
    return f"{module}.{name}: not defined in the module source"


class _Unresolved:
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _resolve(dotted: str) -> Any:
    """Import the longest importable module prefix, then walk the rest with ``getattr``."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        except Exception as exc:
            return _Unresolved(f"{type(exc).__name__}: {exc}")
        for attr in parts[cut:]:
            if not hasattr(obj, attr):
                return _Unresolved(f"{attr} not found")
            obj = getattr(obj, attr)
        return obj
    return _Unresolved("not importable")


def _signature_of(dotted: str, expected: str) -> str | None:
    target = _resolve(dotted)
    if isinstance(target, _Unresolved):
        return f"{dotted}: signature unavailable ({target.reason})"
    try:
        actual = str(inspect.signature(target))
    except (TypeError, ValueError) as exc:
        return f"{dotted}: signature unavailable ({exc})"
    if actual != expected:
        return f"{dotted}: signature is {actual}, ledger records {expected}"
    return None


def _defines(body: list[ast.stmt], path: list[str]) -> bool:
    head, rest = path[0], path[1:]
    for node in body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name == head:
                return not rest or (isinstance(node, ast.ClassDef) and _defines(node.body, rest))
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not rest and any(isinstance(t, ast.Name) and t.id == head for t in targets):
                return True
        elif isinstance(node, ast.If | ast.Try):  # e.g. `if is_psycopg3:` around a class
            blocks = [node.body, node.orelse]
            if isinstance(node, ast.Try):
                blocks += [h.body for h in node.handlers]
            if any(_defines(block, path) for block in blocks):
                return True
    return False


def raw_entry_count(ledger_text: str) -> int:
    """Number of (module|owner, name) pairs written in the ledger TOML, before any processing."""
    data = tomllib.loads(ledger_text)
    return sum(
        len(entry["names"]) for table in ("internal", "attribute") for entry in data.get(table, [])
    )


def _installed_by_metaclass(meta: Any, name: str) -> bool:
    if name in vars(meta):
        return True
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(meta)))
    except (OSError, TypeError, SyntaxError):
        return False
    return any(_installs(node, name) for node in ast.walk(tree) if isinstance(node, ast.Call))


def _installs(call: ast.Call, name: str) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "add_to_class":
        position = 0
    elif isinstance(func, ast.Name) and func.id == "setattr":
        position = 1
    else:
        return False
    if len(call.args) <= position:
        return False
    target = call.args[position]
    return isinstance(target, ast.Constant) and target.value == name


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
