"""Simulated environments for asyncmy that need no second server."""

from __future__ import annotations

import pytest

_WRONG_SCRAMBLE = b"\x00" * 32


def _wrong_scramble(password: bytes, nonce: bytes) -> bytes:
    return _WRONG_SCRAMBLE


def force_full_auth_without_cryptography(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `caching_sha2_password` need its RSA full authentication while the `cryptography`
    package looks missing: asyncmy then raises its `RuntimeError` instead of connecting.

    The fast-path scramble is made wrong so that the server asks for full authentication on a
    non-TLS connection (a MySQL 8 user; MariaDB's `mysql_native_password` never gets there).
    """
    import asyncmy.auth

    monkeypatch.setattr(asyncmy.auth, "_have_cryptography", False)
    monkeypatch.setattr(asyncmy.auth, "scramble_caching_sha2", _wrong_scramble)
