"""`_django.apps.is_installed`: the one place that asks Django's app registry."""

import pytest

from asyncopation._django.apps import is_installed


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("asyncopation", True, id="installed"),
        pytest.param("django.contrib.contenttypes", True, id="django-contrib-installed"),
        pytest.param("no.such.app", False, id="unknown"),
    ],
)
def test_is_installed_reports_the_app_registry(name, expected):
    # when
    result = is_installed(name)
    # then
    assert result is expected
