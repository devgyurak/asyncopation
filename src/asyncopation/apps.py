"""Django app config: ``INSTALLED_APPS = ["asyncopation", ...]`` runs ``configure()`` at ``ready()``.

An entry module above every layer (ADR-0004): nothing imports it, and it reaches
``asyncopation.connection`` only inside ``ready()`` so that importing it stays light.
"""

from django.apps import AppConfig


class AsyncopationConfig(AppConfig):
    name = "asyncopation"

    def ready(self) -> None:
        from asyncopation.connection.lifecycle import configure

        configure()
