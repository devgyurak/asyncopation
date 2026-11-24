"""End-to-end scenarios over HTTP. `/aopt` cases are strict xfails until Phase 1 lands."""

import pytest

from harness.seed import N_BOOKS

AOPT_PENDING = pytest.mark.xfail(
    strict=True, reason="Phase 1: aobjects manager not implemented yet"
)
PREFIXES = [
    pytest.param("/sync", id="sync"),
    pytest.param("/core", id="core"),
    pytest.param("/aopt", id="asyncopation", marks=AOPT_PENDING),
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


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_books_with_authors_returns_one_page(http, readonly_db, prefix):
    # when
    response = await http.get(f"{prefix}/books-with-authors/")
    # then
    assert response.status_code == 200
    assert len(response.json()["items"]) == 20


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_create_order_commits_a_row(http, writable_db, book_pk, prefix):
    # given
    payload = {"book": book_pk, "quantity": 2}
    # when
    response = await http.post(f"{prefix}/orders/", json=payload)
    # then
    assert response.status_code == 201
