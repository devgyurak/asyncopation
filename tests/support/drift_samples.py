"""Sample classes the drift helpers are tested against (source is read via inspect)."""

from __future__ import annotations


class Sample:
    class_level = 1
    read_only: int  # annotation only: not a class attribute

    def __init__(self) -> None:
        self.stored = 1

    def read(self) -> int:
        return self.read_only


class Child(Sample):
    def __init__(self) -> None:
        super().__init__()


def method_with_signature(a: int, b: int = 2) -> int:
    return a + b


CONSTANT = 1
