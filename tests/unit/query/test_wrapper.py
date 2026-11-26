"""`aq()` wraps a QuerySet by composition: construction, idempotence, cache and fetch mode."""

import pytest
from django.db.models import FETCH_ONE, FETCH_RAISE, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation import aq
from asyncopation.query import AsyncQuerySet
from harness.catalog.models import Book
from tests.support.aq_wrapper import (
    SHARED_OTHER_MODE,
    NotAQuerySetDuckSource,
    NotAQuerySetManager,
    SharedDuckSource,
    SharedQuerysetManager,
    all_then_alist,
    all_then_filter,
    book_manager,
    class_attribute_of,
    fill_cache,
    foreign_mode_queryset,
    fresh_books,
    prefilled_duck_source,
    prefilled_manager,
    prefilled_queryset,
    raise_mode_books,
    raise_mode_manager,
    sticky_foreign_queryset,
    sticky_tag_books,
    tag_books_manager,
)


def test_aq_of_a_queryset_returns_an_async_queryset():
    # when
    wrapper = aq(fresh_books())
    # then
    assert isinstance(wrapper, AsyncQuerySet)


def test_aq_of_a_manager_wraps_its_queryset():
    # when
    wrapper = aq(book_manager())
    # then
    assert isinstance(wrapper.queryset, QuerySet)
    assert wrapper.queryset.model is Book


def test_aq_of_an_aq_returns_the_same_wrapper():
    # given
    wrapper = aq(fresh_books())
    # when
    again = aq(wrapper)
    # then
    assert again is wrapper


def test_aq_applies_the_raise_fetch_mode_to_the_wrapped_queryset():
    # when
    wrapper = aq(fresh_books())
    # then
    assert wrapper.queryset._fetch_mode is FETCH_RAISE


def test_aq_does_not_mutate_the_queryset_it_was_given():
    # given
    original = fresh_books()
    # when
    aq(original)
    # then
    assert original._fetch_mode is FETCH_ONE


def test_aq_of_an_evaluated_queryset_wraps_it_without_cloning():
    # given
    evaluated = prefilled_queryset([])
    # when
    wrapper = aq(evaluated)
    # then
    assert wrapper.queryset is evaluated
    assert wrapper.queryset._result_cache == []


def test_aq_of_an_unevaluated_queryset_clones_it():
    # given
    original = fresh_books()
    # when
    wrapper = aq(original)
    # then
    assert wrapper.queryset is not original


def test_aq_of_a_queryset_that_is_already_fetch_raise_still_clones_it():
    # given
    original = raise_mode_books()
    # when
    wrapper = aq(original)
    # then
    assert wrapper.queryset is not original


def test_aq_of_a_fetch_raise_queryset_leaves_the_mode_and_cache_of_the_original_alone():
    # given
    original = raise_mode_books()
    # when
    aq(original)
    # then
    assert (original._fetch_mode, original._result_cache) == (FETCH_RAISE, None)


def test_aq_of_a_duck_typed_source_clones_what_its_get_queryset_returns():
    # given
    source = SharedDuckSource()
    # when
    wrapper = aq(source)
    # then
    assert wrapper.queryset is not source.shared


def test_aq_of_a_duck_typed_source_leaves_the_shared_queryset_alone():
    # given
    source = SharedDuckSource()
    # when
    aq(source)
    # then
    assert source.shared._fetch_mode is FETCH_ONE


def test_aq_of_a_duck_typed_source_keeps_the_sticky_filter_of_what_it_returned():
    # given
    source = SharedDuckSource(sticky=True)
    # when
    wrapper = aq(source)
    # then
    assert wrapper.queryset.query.filter_is_sticky is True


def test_aq_of_a_real_manager_switches_its_fresh_queryset_in_place():
    # given
    manager = SharedQuerysetManager()
    # when
    wrapper = aq(manager)
    # then
    assert wrapper.queryset is manager.shared
    assert wrapper.queryset._fetch_mode is FETCH_RAISE


def test_aq_of_a_manager_with_a_filled_cache_leaves_the_fetch_mode_of_that_queryset_alone():
    # given
    manager = prefilled_manager()
    # when
    wrapper = aq(manager)
    # then
    assert wrapper.queryset is manager.shared
    assert wrapper.queryset._fetch_mode is FETCH_ONE


def test_all_of_a_duck_typed_source_wrapper_consumes_the_sticky_filter_as_a_queryset_does():
    # given
    wrapper = aq(SharedDuckSource(sticky=True))
    # when
    again = wrapper.all()
    # then
    assert again.queryset.query.filter_is_sticky is False


def test_aq_of_a_manager_whose_queryset_is_already_fetch_raise_clones_it():
    # given
    manager = raise_mode_manager()
    # when
    wrapper = aq(manager)
    # then
    assert wrapper.queryset is not manager.shared


def test_aq_of_a_manager_whose_queryset_is_already_fetch_raise_leaves_its_cache_alone():
    # given
    manager = raise_mode_manager()
    # when
    aq(manager)
    # then
    assert manager.shared._result_cache is None


def test_aq_of_a_manager_returning_a_non_queryset_raises_type_error():
    # when / then
    with pytest.raises(TypeError, match="QuerySet or a Manager"):
        aq(NotAQuerySetManager())


def test_aq_of_a_duck_typed_source_returning_a_non_queryset_raises_type_error():
    # when / then
    with pytest.raises(TypeError, match="QuerySet or a Manager"):
        aq(NotAQuerySetDuckSource())


def test_aq_of_a_plain_queryset_does_not_invent_a_sticky_filter():
    # when
    wrapper = aq(fresh_books())
    # then
    assert wrapper.queryset.query.filter_is_sticky is False


def test_all_of_a_manager_wrapper_with_a_prefetch_cache_returns_that_queryset():
    # given
    manager = prefilled_manager()
    # when
    again = aq(manager).all()
    # then
    assert again.queryset is manager.shared


def test_all_of_a_manager_wrapper_with_a_prefetch_cache_stays_a_manager_wrapper():
    # given
    manager = prefilled_manager()
    # when
    again = aq(manager).all().all()
    # then
    assert again.queryset is manager.shared


def test_all_of_a_manager_wrapper_whose_cache_alist_filled_gives_a_new_queryset():
    # given
    manager = SharedQuerysetManager()
    wrapper = fill_cache(aq(manager))
    # when
    again = wrapper.all()
    # then
    assert again.queryset is not wrapper.queryset
    assert again.queryset._result_cache is None


def test_all_of_a_manager_wrapper_over_a_fetch_raise_queryset_gives_a_new_queryset():
    # given
    manager = raise_mode_manager()
    wrapper = aq(manager)
    # when
    again = wrapper.all()
    # then
    assert again.queryset is not wrapper.queryset
    assert again.queryset._result_cache is None


async def test_all_of_a_prefetched_manager_wrapper_after_alist_still_reads_the_cache(no_sql):
    # given
    manager = prefilled_manager()
    sentinel = object()
    manager.shared._result_cache = [sentinel]
    wrapper = aq(manager)
    # when
    rows = await all_then_alist(wrapper)
    # then
    assert rows == [sentinel]


async def test_all_of_a_prefetched_manager_wrapper_shares_the_cache_that_was_already_filled(no_sql):
    # given
    manager = prefilled_manager()
    wrapper = aq(manager)
    # when
    again = wrapper.all()
    # then
    assert again.queryset is manager.shared


def test_filter_after_all_of_a_prefetched_manager_wrapper_queries_a_clone():
    # given
    manager = prefilled_manager()
    # when
    filtered = all_then_filter(aq(manager))
    # then
    assert filtered.queryset is not manager.shared
    assert filtered.queryset._result_cache is None


def test_filter_after_all_of_a_prefetched_manager_wrapper_leaves_the_cached_queryset_alone():
    # given
    manager = prefilled_manager()
    # when
    all_then_filter(aq(manager))
    # then
    assert manager.shared._result_cache == []
    assert manager.shared.query.where.children == []


def test_all_of_a_filled_queryset_wrapper_gives_a_clone_without_the_cache():
    # given
    evaluated = prefilled_queryset([])
    # when
    again = aq(evaluated).all()
    # then
    assert again.queryset is not evaluated


def test_all_of_a_filled_duck_typed_source_wrapper_gives_a_clone_without_the_cache():
    # given
    source = prefilled_duck_source()
    # when
    again = aq(source).all()
    # then
    assert again.queryset is not source.shared


def test_aq_of_a_queryset_keeps_the_sticky_filter_of_the_original_on_the_clone():
    # given
    original = sticky_tag_books()
    # when
    wrapper = aq(original)
    # then
    assert wrapper.queryset.query.filter_is_sticky is True


def test_aq_of_a_queryset_leaves_the_sticky_filter_of_the_original_pending():
    # given
    original = sticky_tag_books()
    # when
    aq(original)
    # then
    assert original.query.filter_is_sticky is True


def test_all_of_a_manager_wrapper_keeps_the_pending_sticky_filter():
    # given
    wrapper = aq(tag_books_manager())
    # when
    again = wrapper.all()
    # then
    assert again.queryset.query.filter_is_sticky is True


def test_all_of_a_manager_wrapper_gives_a_new_queryset():
    # given
    wrapper = aq(tag_books_manager())
    # when
    again = wrapper.all()
    # then
    assert again.queryset is not wrapper.queryset


def test_all_of_an_all_consumes_the_sticky_filter_as_a_queryset_does():
    # given
    wrapper = aq(tag_books_manager()).all()
    # when
    again = wrapper.all()
    # then
    assert again.queryset.query.filter_is_sticky is False


def test_all_of_a_queryset_wrapper_does_not_invent_a_sticky_filter():
    # when
    again = aq(fresh_books()).all()
    # then
    assert again.queryset.query.filter_is_sticky is False


def test_a_queryset_of_another_fetch_mode_returned_by_a_proxied_method_is_cloned_into_fetch_raise():
    # given
    wrapper = aq(foreign_mode_queryset())
    # when
    returned = wrapper.shared_other_mode()
    # then
    assert returned.queryset._fetch_mode is FETCH_RAISE


def test_a_queryset_returned_by_a_proxied_method_is_not_mutated_when_it_is_wrapped():
    # given
    wrapper = aq(foreign_mode_queryset())
    # when
    wrapper.shared_other_mode()
    # then
    assert SHARED_OTHER_MODE._fetch_mode is FETCH_ONE


def test_aq_of_a_many_to_many_manager_keeps_the_pending_sticky_filter():
    # when
    wrapper = aq(tag_books_manager())
    # then
    assert wrapper.queryset.query.filter_is_sticky is True
    assert wrapper.queryset._fetch_mode is FETCH_RAISE


def test_a_class_valued_attribute_is_returned_as_it_is():
    # given
    wrapper = aq(fresh_books())
    # when
    model = class_attribute_of(wrapper)
    # then
    assert model is Book


def test_a_queryset_of_another_fetch_mode_returned_by_a_proxied_method_keeps_its_sticky_filter():
    # given
    wrapper = aq(sticky_foreign_queryset())
    # when
    returned = wrapper.sticky_other_mode()
    # then
    assert returned.queryset.query.filter_is_sticky is True
