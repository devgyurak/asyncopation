"""`aq(qs).alist()` matches the sync ORM: values, types, order and exceptions (issue #6, PR #65)."""

from functools import partial

import pytest
from django.db.models import Count
from hypothesis import given

from harness.catalog.models import Author, Book, BookEdition, FieldZoo
from tests.support.alist_cases import (
    all_then_filter_by_peer,
    books_all_of,
    books_of_manager,
    filter_by_peer,
    manager_chain_differential,
    manager_differential,
    related_manager_identity,
    tag_as_loaded_by_aq,
    tag_as_loaded_by_sync,
)
from tests.support.alist_probe import (
    alist_row_count_and_statements,
    alist_statements,
    alist_sync_connection_attempts,
)
from tests.support.backends import XFAIL_UNTIL_ASYNCMY
from tests.support.seeded import books_of, edition_with_key, editions_with_keys
from tests.support.strategies import (
    QuerySetSpec,
    annotated_spec,
    build_queryset,
    deferred_spec,
    expected_error,
    projected_specs,
    queryset_specs,
)

pytestmark = XFAIL_UNTIL_ASYNCMY


def _union():
    return Book.objects.filter(pk__lt=5).union(Book.objects.filter(pk__gt=40))


MODEL_BUILDS = [
    pytest.param(Book.objects.all, id="all"),
    pytest.param(partial(Book.objects.filter, published=True), id="filter"),
    pytest.param(FieldZoo.objects.all, id="field-zoo-from-db-value"),
    pytest.param(lambda: Book.objects.order_by("pk")[:3], id="slice-head"),
    pytest.param(lambda: Book.objects.order_by("pk")[2:5], id="slice-window"),
    pytest.param(
        lambda: Book.objects.filter(tags__isnull=False).distinct().order_by("pk"), id="distinct"
    ),
    pytest.param(
        lambda: Book.objects.extra(select={"double_pages": "pages * 2"}).order_by("pk"),
        id="extra-select",
    ),
    pytest.param(
        lambda: Book.objects.extra(select={"a": "1", "b": "'x'"}).order_by("-pk")[:5],
        id="extra-select-two-columns-sliced",
    ),
    pytest.param(
        lambda: Book.objects.select_related("author__publisher").order_by("pk")[:15],
        id="select-related",
    ),
    pytest.param(
        lambda: Author.objects.select_related("profile", "publisher").order_by("pk"),
        id="select-related-nullable",
    ),
    pytest.param(lambda: Book.objects.only("title").order_by("pk"), id="only"),
    pytest.param(lambda: Book.objects.defer("metadata").order_by("pk"), id="defer"),
    pytest.param(
        lambda: Book.objects.select_related("author").only("title", "author__name").order_by("pk"),
        id="only-select-related",
    ),
    pytest.param(
        partial(build_queryset, annotated_spec(("n_tags",), ("-n_tags", "pk"))), id="annotate"
    ),
    pytest.param(
        partial(build_queryset, deferred_spec("only", ("title",), annotations=("n_tags",))),
        id="only-with-annotation",
    ),
    pytest.param(
        lambda: Book.objects.distinct().order_by("author__name", "pk"),
        id="distinct-order-by-related-column",
    ),
    pytest.param(partial(Book.objects.filter, title="missing"), id="no-rows"),
    pytest.param(BookEdition.objects.all, id="composite-pk-all"),
    pytest.param(
        lambda: BookEdition.objects.select_related("book").order_by("pk")[:20],
        id="composite-pk-select-related",
    ),
    pytest.param(
        lambda: BookEdition.objects.filter(pk__gt=(0, 0)).order_by("pk"), id="composite-pk-gt"
    ),
]

VALUE_BUILDS = [
    pytest.param(lambda: Book.objects.values().order_by("pk"), id="values-all"),
    pytest.param(lambda: Book.objects.values("title", "price").order_by("pk"), id="values"),
    pytest.param(
        lambda: Book.objects.values_list("title", "pages").order_by("pk"), id="values-list"
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", flat=True).order_by("pk"), id="values-list-flat"
    ),
    pytest.param(
        lambda: Book.objects.values_list("metadata", flat=True).order_by("pk"),
        id="values-list-flat-json",
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", "pages", named=True).order_by("pk"),
        id="values-list-named",
    ),
    pytest.param(
        lambda: Book.objects.values_list(named=True).order_by("pk"),
        id="values-list-named-no-fields",
    ),
    pytest.param(
        lambda: Book.objects.values("title").extra(select={"x": "1"}).order_by("pk"),
        id="values-extra-select",
    ),
    pytest.param(
        lambda: Book.objects.values("title").annotate(n=Count("pk")).order_by("title"),
        id="values-annotate-count",
    ),
    pytest.param(
        lambda: Book.objects.extra(select={"x": "pages * 2"}).values().order_by("pk"),
        id="values-all-with-extra-select",
    ),
    pytest.param(
        lambda: Book.objects.annotate(n=Count("tags")).values().order_by("pk"),
        id="values-all-with-annotation",
    ),
    pytest.param(
        lambda: (
            Book.objects.annotate(n=Count("tags"))
            .extra(select={"x": "pages * 2"})
            .values()
            .order_by("pk")
        ),
        id="values-all-with-annotation-and-extra-select",
    ),
    pytest.param(
        lambda: (
            Book.objects.annotate(n=Count("tags"))
            .extra(select={"x": "pages * 2"})
            .values_list(named=True)
            .order_by("pk")
        ),
        id="values-list-named-no-fields-with-annotation-and-extra-select",
    ),
    pytest.param(
        lambda: Book.objects.values("title").distinct().order_by("pages", "pk"),
        id="values-distinct-unselected-order",
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", "pages").distinct().order_by("price", "pk"),
        id="values-list-distinct-unselected-order",
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", flat=True).distinct().order_by("pages", "pk"),
        id="values-list-flat-distinct-unselected-order",
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", named=True).distinct().order_by("pages", "pk"),
        id="values-list-named-distinct-unselected-order",
    ),
    pytest.param(
        lambda: Book.objects.values("title").distinct().order_by("author__name", "pk"),
        id="values-distinct-order-by-related-column",
    ),
    pytest.param(lambda: _union().values("title").order_by("pages", "pk"), id="union-values"),
    pytest.param(
        lambda: _union().values_list("title", "pages").order_by("price", "pk"),
        id="union-values-list",
    ),
    pytest.param(
        lambda: _union().values_list("title", named=True).order_by("pages", "pk"),
        id="union-values-list-named",
    ),
    pytest.param(
        lambda: _union().values_list("title", flat=True).order_by("pages", "pk"),
        id="union-values-list-flat",
    ),
    pytest.param(lambda: Book.objects.dates("published_at", "month"), id="dates"),
    pytest.param(lambda: Book.objects.datetimes("published_at", "day"), id="datetimes"),
    pytest.param(lambda: BookEdition.objects.values().order_by("pk"), id="composite-pk-values"),
    pytest.param(
        lambda: BookEdition.objects.values("pk", "isbn").order_by("pk"),
        id="composite-pk-values-named-pk",
    ),
    pytest.param(
        lambda: BookEdition.objects.values_list("pk", flat=True).order_by("pk"),
        id="composite-pk-values-list-pk",
    ),
    pytest.param(
        lambda: BookEdition.objects.values_list("isbn", "pk").order_by("pk"),
        id="composite-pk-values-list-pk-last",
    ),
    pytest.param(
        lambda: BookEdition.objects.values_list("pk", "released").order_by("pk"),
        id="composite-pk-values-list-pk-released",
    ),
]


@pytest.mark.parametrize("build", MODEL_BUILDS)
async def test_alist_of_model_instances_matches_sync(differential, build):
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize("build", VALUE_BUILDS)
async def test_alist_of_values_rows_matches_sync(differential, build):
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="books"),
        pytest.param(FieldZoo.objects.all, id="field-zoo"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
    ],
)
async def test_alist_returns_rows_in_the_sync_order(differential, build):
    # when
    result = await differential(build)
    # then
    assert result.async_outcome.rows == result.sync_outcome.rows


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="books"),
        pytest.param(FieldZoo.objects.all, id="field-zoo"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
    ],
)
async def test_alist_of_a_seeded_table_returns_rows(differential, build):
    # when
    result = await differential(build)
    # then
    assert len(result.async_outcome.rows) > 0


async def test_alist_of_a_composite_pk_in_lookup_matches_sync_and_finds_rows(
    differential, edition_keys
):
    # given
    build = partial(editions_with_keys, edition_keys)
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert len(result.async_outcome.rows) == len(edition_keys)
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda: BookEdition.objects.filter(pk__gt=(0, 0)).order_by("pk"), id="composite-pk-gt"
        ),
        pytest.param(
            lambda: BookEdition.objects.select_related("book").order_by("pk")[:20],
            id="composite-pk-select-related",
        ),
    ],
)
async def test_alist_of_composite_pk_queries_finds_rows(differential, build):
    # when
    result = await differential(build)
    # then
    assert len(result.async_outcome.rows) > 0
    assert result.async_outcome == result.sync_outcome


async def test_alist_of_a_related_manager_queryset_matches_sync(differential, author_with_books):
    # given
    build = partial(books_of, author_with_books)
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert len(result.async_outcome.rows) > 0
    assert result.async_outcome == result.sync_outcome


async def test_alist_of_a_related_manager_matches_sync(async_env, author_with_books):
    # when
    result = await manager_differential(author_with_books)
    # then
    assert len(result.async_outcome.rows) > 0
    assert result.async_outcome == result.sync_outcome


async def test_alist_of_a_related_manager_caches_the_owner_instance_on_every_row(
    async_env, author_with_books
):
    # when
    outcome = await related_manager_identity(author_with_books)
    # then
    assert outcome.rows > 0
    assert outcome.shared_with_owner == [True] * outcome.rows


async def test_alist_of_a_composite_pk_equality_lookup_matches_sync_and_finds_the_row(
    differential, edition_keys
):
    # given
    build = partial(edition_with_key, edition_keys[0])
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert len(result.async_outcome.rows) == 1
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "chain",
    [
        pytest.param(lambda m: m.all().order_by("pk"), id="all"),
        pytest.param(lambda m: m.filter(published=True).order_by("pk"), id="filter"),
        pytest.param(lambda m: m.exclude(published=True).order_by("pk"), id="exclude"),
        pytest.param(lambda m: m.select_related("author").order_by("pk"), id="select-related"),
        pytest.param(lambda m: m.values("title").order_by("pk"), id="values"),
    ],
)
async def test_alist_of_a_reverse_foreign_key_manager_chain_matches_sync(
    async_env, author_with_books, chain
):
    # given
    manager_of = partial(getattr, author_with_books, "books")
    # when
    result = await manager_chain_differential(manager_of, chain)
    # then
    assert result.sync_outcome.error is None
    assert len(result.async_outcome.rows) > 0
    assert result.async_outcome == result.sync_outcome


async def test_alist_of_a_many_to_many_manager_filter_reuses_the_join_of_the_relation(
    async_env, tag_with_peer
):
    # given: the sticky filter makes `tags__name` a condition on the manager's own join
    tag, peer = tag_with_peer
    manager_of = partial(getattr, tag, "books")
    # when
    result = await manager_chain_differential(
        manager_of, lambda m: m.filter(tags__name=peer).order_by("pk")
    )
    # then
    assert result.sync_outcome.error is None
    assert result.sync_outcome.rows == ()
    assert result.async_outcome == result.sync_outcome


async def test_alist_of_a_many_to_many_manager_filter_on_its_own_tag_matches_sync(
    async_env, tag_with_peer
):
    # given
    tag, _peer = tag_with_peer
    manager_of = partial(getattr, tag, "books")
    # when
    result = await manager_chain_differential(
        manager_of, lambda m: m.filter(tags__name=tag.name).order_by("pk")
    )
    # then
    assert len(result.async_outcome.rows) > 0
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "load", [tag_as_loaded_by_sync, tag_as_loaded_by_aq], ids=["sync-loaded", "aq-loaded"]
)
@pytest.mark.parametrize(
    ("source_of", "chain"),
    [
        pytest.param(books_of_manager, all_then_filter_by_peer, id="manager-all-filter"),
        pytest.param(books_of_manager, filter_by_peer, id="manager-filter"),
        pytest.param(books_all_of, filter_by_peer, id="queryset-filter"),
    ],
)
async def test_alist_of_a_many_to_many_chain_keeps_the_sticky_filter_however_the_owner_was_loaded(
    async_env, tag_with_peer, load, source_of, chain
):
    # given: `tags__name` must stay a condition on the relation's own join, as in Django
    tag, peer = tag_with_peer
    owner = await load(tag)
    # when
    result = await manager_chain_differential(partial(source_of, owner), partial(chain, peer))
    # then
    assert result.sync_outcome.error is None
    assert result.sync_outcome.rows == ()
    assert result.async_outcome == result.sync_outcome


@given(spec=queryset_specs())
async def test_alist_of_random_querysets_matches_sync(differential, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error == expected_error(spec)
    assert result.async_outcome == result.sync_outcome


@given(spec=projected_specs())
async def test_alist_of_random_projections_matches_sync(differential, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="pk-in-empty"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(lambda: Book.objects.filter(pk__in=[])[:5], id="pk-in-empty-sliced"),
        pytest.param(lambda: Book.objects.order_by("pk")[:0], id="empty-window"),
    ],
)
async def test_alist_that_cannot_match_issues_no_statement(async_env, build):
    # when
    statements = await alist_statements(build)
    # then
    assert statements == []


async def test_alist_of_an_empty_filter_returns_an_empty_list(differential):
    # when
    result = await differential(partial(Book.objects.filter, pk__in=[]))
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.rows == ()


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(lambda: Book.objects.values("title"), id="values"),
        pytest.param(lambda: Book.objects.select_related("author"), id="select-related"),
        pytest.param(lambda: Book.objects.order_by("pk")[:3], id="sliced"),
    ],
)
async def test_alist_issues_exactly_one_statement(async_env, build):
    # when
    rows, statements = await alist_row_count_and_statements(build)
    # then
    assert rows > 0
    assert statements == 1


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(FieldZoo.objects.all, id="field-zoo"),
        pytest.param(lambda: Book.objects.select_related("author__publisher"), id="select-related"),
        pytest.param(lambda: Book.objects.values_list("title", flat=True), id="values-list"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="empty"),
    ],
)
async def test_alist_makes_no_sync_connection_attempt(async_env, build):
    # when
    attempts = await alist_sync_connection_attempts(build)
    # then
    assert attempts == 0
