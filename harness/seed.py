"""Deterministic seed dataset shared by integration tests, e2e and benchmarks.

The same SEED always produces the same rows. Changing any size changes test expectations,
so keep `EXPECTED_COUNTS` in sync.

CLI (prepares a standalone database for bench/e2e):
    uv run python -m harness.seed --create-database --create   # database + tables + seed
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import random
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import connection, models, transaction

if TYPE_CHECKING:
    from harness.catalog.models import FieldZoo

SEED = 20261124
N_PUBLISHERS = 5
N_AUTHORS = 20
N_BOOKS = 100
N_TAGS = 10
# Book i gets 1 + i % 3 editions: 34 * 1 + 33 * 2 + 33 * 3. A literal on purpose, so the row-count
# test checks the seed against an independent number instead of its own formula.
N_EDITIONS = 199

EXPECTED_COUNTS = {
    "Publisher": N_PUBLISHERS,
    "Author": N_AUTHORS,
    "AuthorProfile": N_AUTHORS // 2,
    "Tag": N_TAGS,
    "Book": N_BOOKS,
    "BookEdition": N_EDITIONS,
    "Comment": N_BOOKS + N_AUTHORS,
    "FieldZoo": 4,
}

_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _bulk_create[M: models.Model](model: type[M], objs: Iterable[M]) -> list[M]:
    """bulk_create that always returns saved rows with primary keys.

    MySQL has no RETURNING, so bulk_create() leaves pks unset there (MariaDB and PostgreSQL
    return them). Seeding always starts from empty tables, so re-reading in pk order yields the
    rows in insertion order.
    """
    created = model._default_manager.bulk_create(objs)
    if connection.features.can_return_rows_from_bulk_insert:
        return created
    return list(model._default_manager.order_by("pk"))


def seed() -> None:
    """Insert the dataset. Caller must ensure tables are empty."""
    from django.contrib.contenttypes.models import ContentType

    from harness.catalog.models import (
        Author,
        AuthorProfile,
        Book,
        BookEdition,
        Comment,
        FieldZoo,
        Publisher,
        Tag,
    )

    rnd = random.Random(SEED)
    with transaction.atomic():
        publishers = _bulk_create(
            Publisher,
            [
                Publisher(name=f"publisher-{i}", country=rnd.choice(["KR", "US", "FR", "JP"]))
                for i in range(N_PUBLISHERS)
            ],
        )
        authors = _bulk_create(
            Author,
            [
                Author(
                    name=f"author-{i}",
                    email=f"author-{i}@example.com",
                    birth_date=None if i % 7 == 0 else dt.date(1950 + i, 1 + i % 12, 1 + i % 28),
                    publisher=None if i % 9 == 0 else publishers[i % N_PUBLISHERS],
                )
                for i in range(N_AUTHORS)
            ],
        )
        AuthorProfile.objects.bulk_create(
            AuthorProfile(author=a, bio=f"bio of {a.name} ✍️", website=f"https://{a.name}.example")
            for a in authors[::2]
        )
        tags = _bulk_create(Tag, (Tag(name=f"tag-{i}") for i in range(N_TAGS)))
        books = _bulk_create(
            Book,
            [
                Book(
                    title=f"book-{i}",
                    author=authors[rnd.randrange(N_AUTHORS)],
                    price=Decimal(rnd.randrange(100, 100_000)) / 100,
                    pages=rnd.randrange(50, 1200),
                    published=i % 3 != 0,
                    published_at=None
                    if i % 3 == 0
                    else _EPOCH + dt.timedelta(hours=rnd.randrange(10_000)),
                    metadata={"edition": 1 + i % 4, "langs": ["ko", "en"][: 1 + i % 2]},
                )
                for i in range(N_BOOKS)
            ],
        )
        Book.tags.through.objects.bulk_create(
            Book.tags.through(book=b, tag=t)
            for b in books
            for t in rnd.sample(tags, k=rnd.randrange(0, 4))
        )
        book_ct = ContentType.objects.get_for_model(Book)
        author_ct = ContentType.objects.get_for_model(Author)
        Comment.objects.bulk_create(
            [
                *(
                    Comment(
                        content_type=book_ct,
                        object_id=b.pk,
                        body=f"on {b.title}",
                        created_at=_EPOCH,
                    )
                    for b in books
                ),
                *(
                    Comment(
                        content_type=author_ct,
                        object_id=a.pk,
                        body=f"on {a.name}",
                        created_at=_EPOCH,
                    )
                    for a in authors
                ),
            ]
        )
        FieldZoo.objects.bulk_create(_field_zoo_rows())
        # Deterministic without `rnd`: drawing from it here would shift later seed data.
        BookEdition.objects.bulk_create(
            BookEdition(
                book=book,
                number=number,
                isbn=f"978{i:08d}{number:02d}",
                released=None if i % 5 == 0 else _EPOCH.date() + dt.timedelta(days=i * 7 + number),
            )
            for i, book in enumerate(books)
            for number in range(1, 2 + i % 3)
        )


def _field_zoo_rows() -> list[FieldZoo]:
    from harness.catalog.models import FieldZoo

    base = {
        "small": 0, "integer": 0, "big": 0, "positive": 0, "floating": 0.0,
        "decimal": Decimal("0"), "boolean": False, "nullable_boolean": None,
        "date": dt.date(2026, 1, 1), "datetime": _EPOCH, "time": dt.time(0, 0),
        "duration": dt.timedelta(0), "json": None, "binary": b"", "slug": "zero",
        "ip": "127.0.0.1", "nullable_char": None,
    }  # fmt: skip
    return [
        FieldZoo(char="zero", text="", uuid=uuid.UUID(int=0), **base),
        FieldZoo(
            char="max", text="x" * 5000, uuid=uuid.UUID(int=2**128 - 1),
            **base | {
                "small": 32767, "integer": 2**31 - 1, "big": 2**63 - 1, "positive": 2**31 - 1,
                "floating": 1.7976931348623157e308, "decimal": Decimal("99999999999999.999999"),
                "boolean": True, "nullable_boolean": True,
                "datetime": dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),
                "time": dt.time(23, 59, 59, 999999), "duration": dt.timedelta(days=999_999),
                "json": {"nested": {"list": [1, 2.5, None, True, "s"]}}, "binary": bytes(range(256)),
                "ip": "2001:db8::1", "nullable_char": "set",
            },
        ),
        FieldZoo(
            char="negative", text="음수 ✓", uuid=uuid.UUID(int=1),
            **base | {
                "small": -32768, "integer": -(2**31), "big": -(2**63), "floating": -0.5,
                "decimal": Decimal("-0.000001"), "nullable_boolean": False,
                "duration": dt.timedelta(microseconds=-1), "json": [], "binary": b"\x00",
            },
        ),
        FieldZoo(
            char="json-scalars", text="\n\t", uuid=uuid.UUID(int=2),
            **base | {"json": "string", "time": dt.time(12, 0, 0, 1)},
        ),
    ]  # fmt: skip


def reset() -> None:
    """Delete everything and seed again (used after tests that write)."""
    from harness.catalog.models import (
        Author,
        BookEdition,
        Comment,
        FieldZoo,
        Order,
        Publisher,
        Tag,
    )

    with transaction.atomic():
        Order.objects.all().delete()
        BookEdition.objects.all().delete()
        Author.objects.all().delete()  # cascades to books, profiles, through rows
        Publisher.objects.all().delete()
        Tag.objects.all().delete()
        FieldZoo.objects.all().delete()
        Comment.objects.all().delete()
    seed()


def create_database() -> None:
    """Create the configured database if it does not exist (bench/e2e standalone runs)."""
    from django.db import DatabaseError, connection

    name = connection.ops.quote_name(connection.settings_dict["NAME"])
    # DatabaseError here means the database already exists.
    with connection._nodb_cursor() as cursor, contextlib.suppress(DatabaseError):
        cursor.execute(f"CREATE DATABASE {name}")


def main() -> None:
    import django

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-database", action="store_true", help="CREATE DATABASE if missing")
    parser.add_argument("--create", action="store_true", help="create tables (migrate)")
    args = parser.parse_args()
    django.setup()
    if args.create_database:
        create_database()
    if args.create:
        from django.core.management import call_command

        call_command("migrate", verbosity=0)
    reset()


if __name__ == "__main__":
    main()
