"""Drift checks against the installed Django.

Ledger checks: docs/django-api-ledger.toml against the installed Django. Pin checks: the
``django`` requirement and ``Framework :: Django :: X.Y`` classifiers in pyproject.toml against
each other and against the installed Django. Dependency checks: a ``[[dependency]]`` ledger row
(a third-party package such as asgiref) against the installed version and the pyproject requirement.

The pin checks and ledger-entry checks return a human-readable problem (None = no drift) instead
of raising, so tests stay free of control flow (``raw_entry_count`` is a plain count and may raise
on malformed TOML).
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
from packaging.version import Version

CLASSIFIER_PREFIX = "Framework :: Django :: "
_MAX_MINOR_SPAN = 50
_LOWER = frozenset({">=", ">", "~=", "=="})
_UPPER = frozenset({"<", "<=", "~=", "=="})


def django_requirement(pyproject: str) -> str | None:
    """The unconditional ``django`` requirement of ``[project].dependencies``, or None."""
    return package_requirement(pyproject, "django")


def package_requirement(pyproject: str, package: str) -> str | None:
    """The unconditional requirement on ``package`` in ``[project].dependencies``, or None.

    Entries with an environment marker are skipped. Several entries are combined, as pip does,
    into one requirement that carries every specifier.
    """
    try:
        project = tomllib.loads(pyproject).get("project")
    except (tomllib.TOMLDecodeError, RecursionError):
        return None
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    entries = (
        [d for d in dependencies if isinstance(d, str)] if isinstance(dependencies, list) else []
    )
    found = [
        (entry, req) for entry in entries if (req := _requirement_named(entry, package)) is not None
    ]
    if len(found) <= 1:
        return found[0][0] if found else None
    return package + ",".join(sorted({str(spec) for _, req in found for spec in req.specifier}))


def pin_problem(
    requirement: str | None, installed: str, package: str = "django", subject: str | None = None
) -> str | None:
    """Why the installed ``package`` violates the pyproject requirement, or None.

    ``subject`` replaces the "installed <package> <version>" wording of the out-of-range message.
    """
    label = "Django" if package == "django" else package
    if requirement is None:
        return (
            f"pyproject.toml: no unconditional {package} requirement "
            "(valid, without marker) in [project].dependencies"
        )
    try:
        allowed = Requirement(requirement).specifier
        version = Version(installed)
        inside = allowed.contains(version, prereleases=True)
    except ValueError as exc:  # InvalidRequirement and InvalidVersion are ValueErrors
        return f"{requirement!r} / {installed!r}: {exc}"
    if not allowed:
        return f"{requirement!r} does not constrain the {label} version"
    if not inside:
        who = subject or f"installed {label} {installed}"
        return f"{who} is outside the pyproject pin {requirement!r}"
    return None


def ledger_version_problem(
    versions: frozenset[tuple[str, str]], package: str, installed: str
) -> str | None:
    """Why the installed ``package`` is not the version its ``[[dependency]]`` row verified."""
    verified = dict(versions).get(package)
    if verified is None:
        return f"no [[dependency]] row for {package} in the ledger"
    try:
        same = Version(installed) == Version(verified)
    except ValueError as exc:  # InvalidVersion is a ValueError
        return f"{installed!r} / {verified!r}: {exc}"
    if not same:
        return (
            f"installed {package} {installed} differs from the ledger's {verified}: re-read the "
            "pins in tests/unit/django_drift (test_asgiref_local_drift.py and "
            "test_connection_handler_drift.py), then update verified_against in the "
            f"[[dependency]] row of docs/django-api-ledger.toml for {package}"
        )
    return None


def ledger_pin_problem(
    pyproject: str, versions: frozenset[tuple[str, str]], package: str
) -> str | None:
    """Why the ledger's verified ``package`` version is outside the pyproject requirement."""
    verified = dict(versions).get(package)
    if verified is None:
        return f"no [[dependency]] row for {package} in the ledger"
    return pin_problem(
        package_requirement(pyproject, package),
        verified,
        package,
        subject=f"the ledger's {package} {verified}",
    )


def packages_of(versions: frozenset[tuple[str, str]]) -> list[str]:
    """The sorted package names of ledger ``(package, version)`` pairs."""
    return sorted(package for package, _ in versions)


def used_by_problem(root: Path, path: str) -> str | None:
    """Why a ``used_by`` path of a ``[[dependency]]`` row does not exist under ``root``."""
    if (root / path).is_file():
        return None
    return f"{path}: file not found (update used_by in the [[dependency]] row)"


def dependency_name_problem(package: str, name: str) -> str | None:
    """Why ``package.<dotted name>`` (a ``[[dependency]]`` name) no longer resolves, or None."""
    target = _resolve(f"{package}.{name}")
    if isinstance(target, _Unresolved):
        return f"{package}.{name}: {target.reason}"
    return None


def classifier_problem(pyproject: str, installed: str) -> str | None:
    """Why the ``Framework :: Django :: X.Y`` classifiers disagree with the pin, or None.

    The minors the requirement admits are derived from its specifier (see :func:`_admitted_minors`).
    A classifier for a minor outside that set is stale; an admitted minor without a classifier is
    missing. Classifiers that are not ``X.Y`` (the bare ``Framework :: Django``, a major-only
    ``Framework :: Django :: 6``, a three-part ``6.1.2``) are ignored. A specifier whose minors
    cannot be enumerated (``===``, ``==N.*``, a two-part ``~=``, a missing bound, several majors,
    more than 50 minors apart, a non-zero epoch) is reported, not guessed.
    """
    requirement = django_requirement(pyproject)
    if (problem := pin_problem(requirement, installed)) is not None:
        return problem
    classifiers = tomllib.loads(pyproject)["project"].get("classifiers", [])
    if not isinstance(classifiers, list):
        return "pyproject.toml: [project].classifiers is not a list"
    admitted = _admitted_minors(Requirement(str(requirement)).specifier, Version(installed))
    if isinstance(admitted, str):
        return f"{requirement!r}: {admitted}"
    written = {
        text
        for c in classifiers
        if isinstance(c, str) and c.startswith(CLASSIFIER_PREFIX)
        if re.fullmatch(r"[0-9]+\.[0-9]+", text := c.removeprefix(CLASSIFIER_PREFIX))
    }
    stale = sorted(written - admitted, key=_minor_key)
    missing = sorted(admitted - written, key=_minor_key)
    if stale:
        noun, verb, pronoun = (
            ("classifier", "is", "it") if len(stale) == 1 else ("classifiers", "are", "them")
        )
        return f"{noun} {stale} {verb} outside the pin {requirement!r}; update or remove {pronoun}"
    if missing:
        names = ", ".join(f"'{CLASSIFIER_PREFIX}{m}'" for m in missing)
        noun = "classifier" if len(missing) == 1 else "classifiers"
        return f"no {noun} {names} for the pin {requirement!r}"
    return None


def pyproject_text(requirement: str | list[str], classifiers: list[str]) -> str:
    """A minimal pyproject.toml with the given dependencies and classifiers (test input)."""
    requirements = [requirement] if isinstance(requirement, str) else requirement
    dependencies = ", ".join(json.dumps(r) for r in requirements)
    listed = ", ".join(json.dumps(c) for c in classifiers)
    return f'[project]\nname = "demo"\ndependencies = [{dependencies}]\nclassifiers = [{listed}]\n'


def _minor_key(minor: str) -> tuple[tuple[int, str], ...]:
    return tuple((len(part), part) for part in minor.split("."))  # numeric order, no int()


def _admitted_minors(specifier: SpecifierSet, installed: Version) -> set[str] | str:
    """The ``major.minor`` strings the specifier admits, or why they cannot be enumerated.

    Only the bounds that define the range count: ``lo`` is the greatest lower bound (``>=``,
    ``>``, ``~=``, ``==``), ``hi`` the smallest upper bound (``<``, ``<=``, ``~=``, ``==``);
    ``!=`` clauses never widen the search. Candidates are ``lo``, ``hi``, their release parts,
    ``M.m.0`` to ``M.m.3`` for every minor from ``lo.minor`` to ``hi.minor`` (at most
    ``_MAX_MINOR_SPAN`` apart), each also with a ``.1`` successor, and the installed version
    (which already satisfies the pin); ``contains`` decides which the range admits.
    Pre-release candidates are dropped: a minor is admitted only through a final release.
    Not enumerable, so reported instead of guessed: ``===``, a major-only ``==N.*`` or a
    two-part ``~=``, a missing lower or upper bound, bounds in several major versions, a span
    wider than ``_MAX_MINOR_SPAN`` minors and a non-zero epoch in a bound. Limit: a minor
    admitted only through patches no candidate reaches (for example ``!=M.m.0.*`` to
    ``!=M.m.3.*`` all excluded, with a different patch installed) is not found.
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
    versions = [Version(s.version.removesuffix(".*")) for s in specs]
    lowers = [v for s, v in zip(specs, versions, strict=True) if s.operator in _LOWER]
    uppers = [v for s, v in zip(specs, versions, strict=True) if s.operator in _UPPER]
    if any(v.epoch != 0 for v in (*lowers, *uppers)):
        return "a non-zero epoch cannot be enumerated"
    if not (lowers and uppers):
        return "needs both a lower and an upper bound to enumerate the admitted minors"
    lo, hi = max(lowers), min(uppers)
    if lo.major != hi.major:
        return (
            f"spans several major versions ({lo.major} to {hi.major}); minors cannot be enumerated"
        )
    if hi.minor - lo.minor > _MAX_MINOR_SPAN:
        return f"spans more than {_MAX_MINOR_SPAN} minors; minors cannot be enumerated"
    minors = range(lo.minor, hi.minor + 1)
    candidates = [lo, hi, Version(lo.base_version), Version(hi.base_version), installed]
    candidates += [Version(f"{lo.major}.{m}.{p}") for m in minors for p in range(4)]
    candidates += [Version(f"{v.base_version}.1") for v in list(candidates)]
    return {
        f"{v.major}.{v.minor}"
        for v in candidates
        if not v.is_prerelease and specifier.contains(v, prereleases=True)
    }


def _requirement_named(dependency: str, package: str) -> Requirement | None:
    try:
        parsed = Requirement(dependency)
    except InvalidRequirement:
        return None
    if canonicalize_name(parsed.name) == canonicalize_name(package) and parsed.marker is None:
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
        len(entry["names"])
        for table in ("internal", "attribute", "dependency")
        for entry in data.get(table, [])
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
