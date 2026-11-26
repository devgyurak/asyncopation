"""End-to-end scenarios over HTTP. `/aopt` cases that need `afetch` or writes are strict xfails."""

import pytest

from harness.seed import N_BOOKS
from tests.support.backends import XFAIL_UNTIL_ASYNCMY

PREFIXES = [
    pytest.param("/sync", id="sync"),
    pytest.param("/core", id="core"),
    pytest.param("/aopt", id="asyncopation", marks=XFAIL_UNTIL_ASYNCMY),
]
# `raises=AssertionError`: the status assertion comes first in the test, so only the expected 500
# of the pending scenario is an xfail; a transport error or a malformed body stays a failure.
AFETCH_PENDING = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="needs afetch() (#15)"
)
PREFIXES_NEEDING_AFETCH = [
    pytest.param("/sync", id="sync"),
    pytest.param("/core", id="core"),
    pytest.param("/aopt", id="asyncopation", marks=AFETCH_PENDING),
]
ASYNC_WRITE_PENDING = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="needs acreate() (#20) and async_atomic (#19)"
)
PREFIXES_NEEDING_WRITES = [
    pytest.param("/sync", id="sync"),
    pytest.param("/core", id="core"),
    pytest.param("/aopt", id="asyncopation", marks=ASYNC_WRITE_PENDING),
]


async def test_health_reports_seeded_books(http):
    # when
    response = await http.get("/health/")
    # then
    assert response.json() == {"db": "ok", "books": N_BOOKS}


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_book_detail_returns_the_requested_book(http, book_pk, prefix):
    # when
    response = await http.get(f"{prefix}/book/{book_pk}/")
    # then
    assert response.status_code == 200
    assert response.json()["id"] == book_pk


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_dashboard_aggregates_three_queries(http, readonly_db, prefix):
    # when
    response = await http.get(f"{prefix}/dashboard/")
    # then
    assert response.status_code == 200
    assert response.json() == {"author": "author-0", "books": N_BOOKS, "tag": "tag-0"}


@pytest.mark.parametrize("prefix", PREFIXES_NEEDING_AFETCH)
async def test_books_with_authors_returns_one_page(http, readonly_db, prefix):
    # when
    response = await http.get(f"{prefix}/books-with-authors/")
    # then
    assert response.status_code == 200
    assert len(response.json()["items"]) == 20


@pytest.mark.parametrize("prefix", PREFIXES_NEEDING_WRITES)
async def test_create_order_commits_a_row(http, writable_db, book_pk, prefix):
    # given
    payload = {"book": book_pk, "quantity": 2}
    # when
    response = await http.post(f"{prefix}/orders/", json=payload)
    # then
    assert response.status_code == 201
