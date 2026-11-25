from decimal import Decimal

import pytest

from harness.catalog.models import Book, Publisher
from tests.support.deferred_loads import forbid_deferred_loads
from tests.support.fetch_modes import FETCH_ONE, FETCH_PEERS, FETCH_RAISE
from tests.support.snapshot import (
    annotation_names,
    field_snapshot,
    related_snapshot,
    snapshot,
    snapshot_result,
)
from tests.support.snapshot_graphs import (
    cache_related,
    cached_related,
    make_author,
    make_book,
    make_book_with_deferred_publisher_name,
    make_deferred_author,
    make_deferred_edition,
    make_one_to_one_cycle,
    make_publisher,
)


def test_equal_values_with_different_types_produce_different_snapshots():
    # given
    as_decimal, as_float = [{"price": Decimal("1.5")}], [{"price": 1.5}]
    # when
    left, right = snapshot_result(as_decimal), snapshot_result(as_float)
    # then
    assert left != right


@pytest.mark.parametrize(
    ("left", "right"),
    [([{"a": 1, "b": 2}], [{"b": 2, "a": 1}]), ([b"x"], [b"x"]), (None, None)],
    ids=["dict-key-order", "bytes", "none"],
)
def test_equal_results_produce_equal_snapshots(left, right):
    # when
    result = snapshot_result(left) == snapshot_result(right)
    # then
    assert result


def test_row_order_is_part_of_the_snapshot():
    # when
    result = snapshot_result([1, 2]) == snapshot_result([2, 1])
    # then
    assert result is False


def test_annotation_values_are_part_of_an_instance_snapshot():
    # given
    left = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    right = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(left).update(n_tags=1)
    vars(right).update(n_tags=2)
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_annotation_names_lists_public_non_field_attributes():
    # given
    book = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(book).update(n_tags=3, _private="ignored")
    # when
    names = annotation_names(snapshot(book))
    # then
    assert names == ("n_tags",)


def test_annotation_with_equal_value_but_different_type_differs():
    # given
    left = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    right = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(left).update(total=Decimal("1"))
    vars(right).update(total=1)
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_instance_without_annotations_has_empty_annotation_names():
    # given
    book = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    # when
    names = annotation_names(snapshot(book))
    # then
    assert names == ()


def test_to_attr_list_snapshots_compare_instance_fields():
    # given
    left = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    right = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(left).update(siblings=[Book(pk=2, title="a", pages=1, price=Decimal("1.00"))])
    vars(right).update(siblings=[Book(pk=2, title="b", pages=1, price=Decimal("1.00"))])
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_related_object_field_value_is_part_of_the_snapshot():
    # given
    left = make_book(make_author("x"))
    right = make_book(make_author("y"))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_related_object_annotation_type_difference_is_detected():
    # given
    left_author, right_author = make_author(), make_author()
    vars(left_author).update(n=Decimal("1"))
    vars(right_author).update(n=1)
    left, right = make_book(left_author), make_book(right_author)
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_loaded_relation_differs_from_unloaded_relation():
    # given
    loaded, unloaded = make_book(make_author()), make_book()
    # when
    result = snapshot(loaded) == snapshot(unloaded)
    # then
    assert result is False


@pytest.mark.parametrize(
    ("loaded_as_none", "unloaded"),
    [
        (make_author(publisher=None), make_author()),
        (make_author(profile=None), make_author()),
    ],
    ids=["nullable-fk", "missing-reverse-one-to-one"],
)
def test_relation_loaded_as_none_differs_from_unloaded_relation(loaded_as_none, unloaded):
    # when
    result = snapshot(loaded_as_none) == snapshot(unloaded)
    # then
    assert result is False


def test_nested_relation_difference_is_detected():
    # given
    left = make_book(make_author(publisher=make_publisher("x")))
    right = make_book(make_author(publisher=make_publisher("y")))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_equal_related_graphs_produce_equal_snapshots():
    # given
    left = make_book(make_author(publisher=make_publisher(), profile=None))
    right = make_book(make_author(publisher=make_publisher(), profile=None))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result


def test_related_entries_are_sorted_by_cache_name():
    # given
    author = make_author(publisher=None, profile=None)
    # when
    related = snapshot(author)[3]
    # then
    assert related == (("profile", None), ("publisher", None))


def test_one_to_one_cycle_terminates_with_back_reference():
    # given
    author = make_one_to_one_cycle()
    # when
    related = related_snapshot(snapshot(author), "profile", "author")
    # then
    assert related == ("<ref>", "catalog.Author", ("int", 1))


def test_one_to_one_cycle_snapshots_are_equal_for_equal_graphs():
    # given
    left, right = make_one_to_one_cycle(), make_one_to_one_cycle()
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result


def test_one_to_one_cycle_differs_when_profile_differs():
    # given
    left = make_one_to_one_cycle()
    right = make_one_to_one_cycle()
    cached_related(right, "profile").bio = "other"
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_annotation_names_ignores_related_objects():
    # given
    book = make_book(make_author())
    # when
    names = annotation_names(snapshot(book))
    # then
    assert names == ()


def test_instance_cycle_through_an_annotation_terminates():
    # given
    author = make_author()
    vars(author).update(self_ref=author)
    # when
    related = snapshot(author)[2]
    # then
    assert related == (("self_ref", "Author", ("<ref>", "catalog.Author", ("int", 1))),)


def test_related_object_concrete_field_type_is_part_of_the_snapshot():
    # given
    left = make_book(make_author(publisher=Publisher(pk=1, name="1")))
    right = make_book(make_author(publisher=Publisher(pk=1, name=1)))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_nested_relation_loaded_differs_from_nested_relation_unloaded():
    # given
    loaded = make_book(make_author(publisher=make_publisher()))
    unloaded = make_book(make_author())
    # when
    result = snapshot(loaded) == snapshot(unloaded)
    # then
    assert result is False


def test_related_snapshot_returns_the_cached_relation_snapshot():
    # given
    book = make_book(make_author())
    # when
    author = related_snapshot(snapshot(book), "author")
    # then
    assert author == snapshot(make_author())


def test_related_snapshot_returns_none_for_relation_loaded_as_none():
    # given
    author = make_author(publisher=None)
    # when
    result = related_snapshot(snapshot(author), "publisher")
    # then
    assert result is None


def test_related_snapshot_raises_for_relation_that_was_not_loaded():
    # given
    book = snapshot(make_book())
    # when / then
    with pytest.raises(KeyError):
        related_snapshot(book, "author")


def test_deferred_field_snapshot_differs_from_loaded_field_snapshot():
    # given
    deferred, loaded = make_deferred_author("email"), make_deferred_author()
    # when
    result = field_snapshot(snapshot(deferred), "email") == field_snapshot(
        snapshot(loaded), "email"
    )
    # then
    assert result is False


def test_deferred_field_snapshot_leaves_other_fields_unchanged():
    # given
    deferred, loaded = make_deferred_author("email"), make_deferred_author()
    # when
    result = field_snapshot(snapshot(deferred), "name") == field_snapshot(snapshot(loaded), "name")
    # then
    assert result


def test_deferred_field_differs_from_field_loaded_as_none():
    # given
    deferred, loaded_none = make_deferred_author("publisher_id"), make_deferred_author()
    vars(loaded_none).update(publisher_id=None)
    # when
    result = snapshot(deferred) == snapshot(loaded_none)
    # then
    assert result is False


def test_instances_deferring_different_fields_produce_different_snapshots():
    # given
    left, right = make_deferred_author("email"), make_deferred_author("name")
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


@pytest.mark.parametrize(
    "fetch_mode",
    [FETCH_ONE, FETCH_PEERS, FETCH_RAISE],
    ids=["fetch-one", "fetch-peers", "fetch-raise"],
)
def test_snapshot_never_loads_deferred_fields_in_any_fetch_mode(monkeypatch, fetch_mode):
    # given
    forbid_deferred_loads(monkeypatch)
    author = make_deferred_author("email", "name", fetch_mode=fetch_mode)
    # when
    result = snapshot(author)
    # then
    assert (field_snapshot(result, "email"), field_snapshot(result, "name")) == (
        ("<deferred>", None),
        ("<deferred>", None),
    )


def test_deferred_foreign_key_attname_is_marker_while_cached_relation_is_recorded():
    # given: an artificial state (Django itself refuses to cache a relation whose attname is
    # deferred), used to pin that the marker is decided per attname
    author = make_deferred_author("publisher_id")
    cache_related(author, "publisher", make_publisher())
    # when
    result = snapshot(author)
    # then
    assert field_snapshot(result, "publisher_id") == ("<deferred>", None)


def test_cached_relation_is_still_recorded_when_its_foreign_key_attname_is_deferred():
    # given: the same artificial state as above
    author = make_deferred_author("publisher_id")
    cache_related(author, "publisher", make_publisher())
    # when
    related = related_snapshot(snapshot(author), "publisher")
    # then
    assert related == snapshot(make_publisher())


def test_deferred_field_two_levels_deep_is_recorded_as_marker():
    # given
    book = make_book_with_deferred_publisher_name()
    # when
    recorded = field_snapshot(snapshot(book), "name", "author", "publisher")
    # then
    assert recorded == ("<deferred>", None)


def test_field_snapshot_raises_key_error_for_unknown_field():
    # given
    result = snapshot(make_author())
    # when / then
    with pytest.raises(KeyError):
        field_snapshot(result, "nope")


def test_field_snapshot_raises_lookup_error_for_relation_loaded_as_none():
    # given
    result = snapshot(make_author(publisher=None))
    # when / then
    with pytest.raises(LookupError, match="loaded as None"):
        field_snapshot(result, "name", "publisher")


def test_instances_deferring_the_same_field_produce_equal_snapshots():
    # given
    left, right = make_deferred_author("email"), make_deferred_author("email")
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result


def test_deferred_field_of_a_cached_related_object_is_recorded_as_marker():
    # given
    book = make_book(make_deferred_author("email"))
    # when
    recorded = field_snapshot(snapshot(book), "email", "author")
    # then
    assert recorded == ("<deferred>", None)


def test_field_loaded_with_the_marker_text_differs_from_a_deferred_field():
    # given
    deferred, loaded = make_deferred_author("name"), make_deferred_author()
    loaded.name = "<deferred>"
    # when
    result = snapshot(deferred) == snapshot(loaded)
    # then
    assert result is False


def test_field_snapshot_raises_key_error_for_relation_that_was_not_loaded():
    # given
    result = snapshot(make_author())
    # when / then
    with pytest.raises(KeyError, match="publisher"):
        field_snapshot(result, "name", "publisher")


def test_field_snapshot_raises_lookup_error_when_path_ends_on_a_back_reference():
    # given
    result = snapshot(make_one_to_one_cycle())
    # when / then
    with pytest.raises(LookupError, match="back-reference"):
        field_snapshot(result, "id", "profile", "author")


def test_related_snapshot_raises_lookup_error_when_walking_into_a_back_reference():
    # given
    result = snapshot(make_one_to_one_cycle())
    # when / then
    with pytest.raises(LookupError, match="back-reference"):
        related_snapshot(result, "profile", "author", "profile")


def test_deferred_composite_pk_model_field_is_recorded_as_marker():
    # given
    edition = make_deferred_edition()
    # when
    recorded = field_snapshot(snapshot(edition), "isbn")
    # then
    assert recorded == ("<deferred>", None)


def test_make_deferred_author_rejects_a_name_that_is_not_a_concrete_field():
    # when / then
    with pytest.raises(ValueError, match="emial"):
        make_deferred_author("emial")
