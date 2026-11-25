import pytest

from tests.support.psycopg_impl import expected_implementation

VARIABLE = "ASYNCOPATION_EXPECT_PSYCOPG_IMPL"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        pytest.param({}, None, id="unset"),
        pytest.param({VARIABLE: ""}, None, id="empty"),
        pytest.param({VARIABLE: "Binary"}, "binary", id="capitalised-binary"),
        pytest.param({VARIABLE: "Python"}, "python", id="capitalised-python"),
    ],
)
def test_the_expected_implementation_is_the_lower_cased_variable_or_none(
    monkeypatch, environ, expected
):
    # given
    monkeypatch.setattr("os.environ", environ)
    # when
    implementation = expected_implementation()
    # then
    assert implementation == expected
