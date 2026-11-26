"""repr() of an aq() wrapper never runs a query (Django's QuerySet repr evaluates)."""

from asyncopation import aq
from tests.support.aq_wrapper import fresh_books


def test_repr_runs_no_query(no_sql):
    # given
    wrapper = aq(fresh_books())
    # when
    text = repr(wrapper)
    # then
    assert isinstance(text, str)
    assert "AsyncQuerySet" in text


def test_repr_of_a_chained_wrapper_runs_no_query(no_sql):
    # given
    wrapper = aq(fresh_books()).filter(published=True).values("title")
    # when
    text = repr(wrapper)
    # then
    assert "AsyncQuerySet" in text
