"""Builders for in-memory model graphs with ``select_related``-style caches (no database)."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, cast

from django.db.models import DEFERRED, Model  # ty: ignore[missing-module-member]

from harness.catalog.models import Author, AuthorProfile, Book, Publisher


class Unloaded(Enum):
    """Marks a relation that is left out of ``fields_cache`` (``None`` means loaded as None)."""

    RELATION = "unloaded"


UNLOADED = Unloaded.RELATION


def cache_related(obj: Any, name: str, value: Any) -> Any:
    """Store ``value`` (an instance or None) as the loaded relation ``name`` of ``obj``."""
    cast("dict[str, Any]", obj._state.fields_cache)[name] = value
    return obj


def make_publisher(name: str = "pub") -> Publisher:
    return Publisher(pk=1, name=name)


def make_author(
    name: str = "a",
    *,
    publisher: Publisher | Unloaded | None = UNLOADED,
    profile: AuthorProfile | Unloaded | None = UNLOADED,
) -> Author:
    author = Author(pk=1, name=name, email="a@example.com", publisher_id=1)
    if publisher is not UNLOADED:
        cache_related(author, "publisher", publisher)
    if profile is not UNLOADED:
        cache_related(author, "profile", profile)
    return author


def make_book(author: Author | Unloaded | None = UNLOADED) -> Book:
    book = Book(pk=1, title="t", pages=1, price=Decimal("1.00"), author_id=1)
    if author is not UNLOADED:
        cache_related(book, "author", author)
    return book


def make_profile(author: Author | Unloaded | None = UNLOADED) -> AuthorProfile:
    profile = AuthorProfile(pk=1, author_id=1, bio="bio")
    if author is not UNLOADED:
        cache_related(profile, "author", author)
    return profile


def make_one_to_one_cycle() -> Author:
    """author <-> profile, as ``select_related("profile")`` leaves them (remote_setter)."""
    author = make_author()
    profile = make_profile(author)
    return cache_related(author, "profile", profile)


def cached_related(obj: Any, name: str) -> Any:
    """The relation ``name`` cached on ``obj`` (what ``select_related`` loaded)."""
    return cast("dict[str, Any]", obj._state.fields_cache)[name]


def forbid_refresh_from_db(monkeypatch: Any) -> None:
    """Make any ``Model.refresh_from_db`` call (a lazy load of a deferred field) fail the test."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("refresh_from_db was called")

    monkeypatch.setattr(Model, "refresh_from_db", refuse)


def make_book_with_deferred_publisher_name() -> Book:
    """book -> author -> publisher, where the publisher's ``name`` is deferred."""
    publisher = Publisher.from_db(None, ["id"], [1])
    return make_book(make_author(publisher=publisher))


def make_deferred_author(*deferred: str) -> Author:
    """An author as ``only()``/``defer()`` leave it: ``deferred`` attnames are not loaded."""
    author = make_author()
    names = [f.attname for f in Author._meta.concrete_fields]
    return Author.from_db(
        None,
        names,
        [DEFERRED if name in deferred else getattr(author, name) for name in names],
    )
