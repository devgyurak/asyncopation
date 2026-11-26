"""Throwaway models for the `AsyncObjects` descriptor tests, in a private app registry (no tables,
no effect on the harness catalog)."""

from __future__ import annotations

from typing import Any

from django.apps.registry import Apps
from django.contrib.contenttypes.models import ContentType
from django.db import models

from asyncopation import AsyncObjects

registry = Apps()
LABEL = "aobjects_unit"


class WidgetQuerySet(models.QuerySet):
    def cheap(self) -> Any:
        return self.filter(price__lt=10)


WidgetManager: Any = models.Manager.from_queryset(WidgetQuerySet)


class ActiveManager(WidgetManager):
    def get_queryset(self) -> Any:
        return super().get_queryset().filter(active=True)


class AllOverridingManager(models.Manager):
    """`all()` is overridden to filter; `get_queryset()` is not."""

    def all(self) -> Any:
        return super().get_queryset().filter(active=True)


class Labelled(models.Model):
    """Common base: every model prints its class name."""

    class Meta:
        app_label = LABEL
        apps = registry
        abstract = True

    def __str__(self) -> str:
        return type(self).__name__


class Plain(Labelled):
    aobjects = AsyncObjects()
    name = models.CharField(max_length=10)

    class Meta:
        app_label = LABEL
        apps = registry


class Widget(Labelled):
    """A custom default manager whose QuerySet has its own methods."""

    price = models.IntegerField()
    active = models.BooleanField(default=True)
    objects = ActiveManager()
    aobjects = AsyncObjects()

    class Meta:
        app_label = LABEL
        apps = registry


class Special(Labelled):
    """`default_manager_name` points at a manager that is not called `objects`."""

    active = models.BooleanField(default=True)
    objects = models.Manager()
    special = ActiveManager()
    aobjects = AsyncObjects()

    class Meta:
        app_label = LABEL
        apps = registry
        default_manager_name = "special"


class Overriding(Labelled):
    """A default manager whose `all()` (not `get_queryset()`) adds a filter."""

    active = models.BooleanField(default=True)
    objects = AllOverridingManager()
    aobjects = AsyncObjects()

    class Meta:
        app_label = LABEL
        apps = registry


class Renamed(Labelled):
    """The descriptor under another name."""

    async_objects = AsyncObjects()

    class Meta:
        app_label = LABEL
        apps = registry


class Base(Labelled):
    aobjects = AsyncObjects()

    class Meta:
        app_label = LABEL
        apps = registry
        abstract = True


class Concrete(Base):
    class Meta:
        app_label = LABEL
        apps = registry


class Parent(Labelled):
    aobjects = AsyncObjects()

    class Meta:
        app_label = LABEL
        apps = registry


class Child(Parent):
    extra = models.IntegerField(default=0)

    class Meta:
        app_label = LABEL
        apps = registry


class ProxyPlain(Plain):
    """A proxy of `Plain`: same table, its own class."""

    class Meta:
        app_label = LABEL
        apps = registry
        proxy = True


class Occupied(Labelled):
    """A plain class attribute that `AsyncObjects.attach` is pointed at."""

    marker = "taken"

    class Meta:
        app_label = LABEL
        apps = registry


def manager_names(model: Any) -> list[str]:
    return [manager.name for manager in model._meta.managers]


def default_manager_name(model: Any) -> str:
    return model._default_manager.name


def where_conditions(wrapper: Any) -> list[tuple[str, str]]:
    """`(field name, lookup name)` of each condition in the WHERE of the wrapped QuerySet, in order
    (read from the query tree, so it does not depend on the database's SQL dialect)."""
    return [
        (node.lhs.target.name, node.lookup_name) for node in wrapper.queryset.query.where.children
    ]


def fetch_mode_of(wrapper: Any) -> Any:
    return wrapper.queryset._fetch_mode


def attach_to_content_type(name: str = "aobjects") -> type:
    AsyncObjects.attach(ContentType, name)
    return ContentType


def detach_from_content_type(name: str = "aobjects") -> None:
    delattr(ContentType, name)


def attach_over_marker() -> None:
    AsyncObjects.attach(Occupied, "marker")


def restore_marker() -> None:
    Occupied.marker = "taken"
