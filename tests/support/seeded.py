"""Rows read from the committed seed through the sync ORM (setup for async tests, no control flow
in test modules). Keys are read, never hardcoded: the seed's primary keys depend on re-seeding."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async
from django.db import connections
from django.db.models import Count, QuerySet

from harness.catalog.models import Author, Book, BookEdition

EDITION_KEY_COUNT = 6


async def in_sync[T](function: Callable[[], T]) -> T:
    """Run `function` (sync ORM) in a worker thread and release that thread's connection."""

    def run() -> T:
        try:
            return function()
        finally:
            connections.close_all()

    return await sync_to_async(run)()


def _edition_keys() -> list[tuple[int, int]]:
    queryset = BookEdition.objects.order_by("pk").values_list("pk", flat=True)
    return list(queryset[:EDITION_KEY_COUNT])


async def seeded_edition_keys() -> list[tuple[int, int]]:
    """The first composite primary keys `(book_id, number)` of the seeded editions."""
    return await in_sync(_edition_keys)


def editions_with_keys(keys: list[tuple[int, int]]) -> QuerySet[BookEdition]:
    return BookEdition.objects.filter(pk__in=keys).order_by("pk")


def edition_with_key(key: tuple[int, int]) -> QuerySet[BookEdition]:
    """The edition whose composite primary key equals `key` (an equality lookup on `pk`)."""
    return BookEdition.objects.filter(pk=key)


def _author_with_books() -> Any:
    return Author.objects.filter(books__isnull=False).order_by("pk").first()


async def seeded_author_with_books() -> Any:
    """An author that has books (read from the seed)."""
    return await in_sync(_author_with_books)


def books_of(author: Any) -> QuerySet[Book]:
    """The related-manager QuerySet (carries the known related object)."""
    return books_manager_of(author).all()


def books_manager_of(author: Any) -> Any:
    return author.books


def _tag_with_peer() -> tuple[Any, str]:
    """A tag, and the name of another tag on one of its books (so a join on both has rows)."""
    book: Any = Book.objects.annotate(n=Count("tags")).filter(n__gte=2).order_by("pk").first()
    tag, peer = book.tags.order_by("pk")[:2]
    return tag, peer.name


async def seeded_tag_with_peer() -> tuple[Any, str]:
    """A seeded tag and the name of a second tag sharing one of its books (read from the seed)."""
    return await in_sync(_tag_with_peer)


def _book_pk() -> int:
    return Book.objects.order_by("pk").values_list("pk", flat=True)[0]


async def seeded_book_pk() -> int:
    """The primary key of the first seeded book."""
    return await in_sync(_book_pk)
