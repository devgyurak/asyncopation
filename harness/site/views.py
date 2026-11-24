"""Benchmark / e2e endpoints, one variant per execution model.

Each scenario is implemented three times (docs/design/plan.md §5.2):
    /sync/...  WSGI + sync ORM              (served by gunicorn)
    /core/...  ASGI + Django core async ORM (sync_to_async wrappers)
    /aopt/...  ASGI + Asyncopation          (works from Phase 1; until then 500 -> e2e xfail)

Scenarios
    book/<pk>/          single-row lookup
    dashboard/          three independent queries (async variants use asyncio.gather)
    books-with-authors/ N+1 candidate (sync/core: prefetch_related, aopt: afetch peers)
    orders/ (POST)      write inside a transaction
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from harness.catalog.models import Author, Book, Order, Tag

PAGE = 20


def _book(b: Book) -> dict[str, Any]:
    return {"id": b.pk, "title": b.title, "price": str(b.price)}


# ------------------------------------------------------------------ health (harness self-check)
async def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"db": "ok", "books": await Book.objects.acount()})


# ------------------------------------------------------------------ sync
def sync_book(request: HttpRequest, pk: int) -> JsonResponse:
    return JsonResponse(_book(Book.objects.get(pk=pk)))


def sync_dashboard(request: HttpRequest) -> JsonResponse:
    author = Author.objects.get(pk=Author.objects.order_by("pk").values_list("pk", flat=True)[0])
    return JsonResponse(
        {
            "author": author.name,
            "books": Book.objects.count(),
            "tag": Tag.objects.order_by("pk")[0].name,
        }
    )


def sync_books_with_authors(request: HttpRequest) -> JsonResponse:
    books = Book.objects.prefetch_related("author")[:PAGE]
    return JsonResponse({"items": [_book(b) | {"author": b.author.name} for b in books]})


@csrf_exempt
def sync_create_order(request: HttpRequest) -> HttpResponse:
    payload = json.loads(request.body)
    with transaction.atomic():
        order = Order.objects.create(book_id=payload["book"], quantity=payload["quantity"])
    return JsonResponse({"id": order.pk}, status=201)


# ------------------------------------------------------------------ Django core async (sync_to_async)
async def core_book(request: HttpRequest, pk: int) -> JsonResponse:
    return JsonResponse(_book(await Book.objects.aget(pk=pk)))


async def core_dashboard(request: HttpRequest) -> JsonResponse:
    author, count, tag = await asyncio.gather(
        Author.objects.order_by("pk")[:1].aget(),
        Book.objects.acount(),
        Tag.objects.order_by("pk")[:1].aget(),
    )
    return JsonResponse({"author": author.name, "books": count, "tag": tag.name})


async def core_books_with_authors(request: HttpRequest) -> JsonResponse:
    books = [b async for b in Book.objects.prefetch_related("author")[:PAGE]]
    return JsonResponse({"items": [_book(b) | {"author": b.author.name} for b in books]})


@csrf_exempt
async def core_create_order(request: HttpRequest) -> HttpResponse:
    # Core has no async atomic(); a transaction forces you back into a sync function.
    from asgiref.sync import sync_to_async

    payload = json.loads(request.body)

    def create() -> Order:
        with transaction.atomic():
            return Order.objects.create(book_id=payload["book"], quantity=payload["quantity"])

    order = await sync_to_async(create)()
    return JsonResponse({"id": order.pk}, status=201)


# ------------------------------------------------------------------ Asyncopation
# Written against the planned public API (plan §3, §4.5). Starts working once Phase 1
# adds the `aobjects` manager to the catalog models.
async def aopt_book(request: HttpRequest, pk: int) -> JsonResponse:
    return JsonResponse(_book(await Book.aobjects.aget(pk=pk)))  # ty: ignore[missing-attribute]


async def aopt_dashboard(request: HttpRequest) -> JsonResponse:
    author, count, tag = await asyncio.gather(
        Author.aobjects.order_by("pk").afirst(),  # ty: ignore[missing-attribute]
        Book.aobjects.acount(),  # ty: ignore[missing-attribute]
        Tag.aobjects.afirst(),  # ty: ignore[missing-attribute]
    )
    return JsonResponse({"author": author.name, "books": count, "tag": tag.name})


async def aopt_books_with_authors(request: HttpRequest) -> JsonResponse:
    books = await Book.aobjects.all()[:PAGE].alist()  # ty: ignore[missing-attribute]
    await books[0].afetch("author")  # FETCH_PEERS: one query loads the author for all peers
    return JsonResponse({"items": [_book(b) | {"author": b.author.name} for b in books]})


@csrf_exempt
async def aopt_create_order(request: HttpRequest) -> HttpResponse:
    from asyncopation import async_atomic  # ty: ignore[missing-module-member]

    payload = json.loads(request.body)
    async with async_atomic():
        order = await Order.aobjects.acreate(book_id=payload["book"], quantity=payload["quantity"])  # ty: ignore[missing-attribute]
    return JsonResponse({"id": order.pk}, status=201)
