"""An `AsyncObjects` subclass that is itself named `AsyncObjects` and sets `self.__manager` in
`__init__` (mangled to `_AsyncObjects__manager`, the name a name-mangled binding slot would have),
and a model declaring it, in a private app registry (no tables)."""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import models

from asyncopation import AsyncObjects as BaseAsyncObjects

registry = Apps()


class AsyncObjects(BaseAsyncObjects):
    def __init__(self) -> None:
        super().__init__()
        self.__manager = "helper"

    def helper_value(self) -> str:
        return self.__manager


class SameNameItem(models.Model):
    name = models.CharField(max_length=10)

    aobjects = AsyncObjects()

    class Meta:
        app_label = "aobjects_same_name"
        apps = registry

    def __str__(self) -> str:
        return self.name
