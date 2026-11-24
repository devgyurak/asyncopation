"""E2E fixtures: a real uvicorn process serving harness.site against the pytest test database."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def live_server_url(seeded_db: None, django_db_blocker, tmp_path_factory) -> Iterator[str]:
    from django.db import connection

    port = _free_port()
    log_path = tmp_path_factory.mktemp("uvicorn") / "server.log"
    env = os.environ | {
        "ASYNCOPATION_DB_NAME": connection.settings_dict["NAME"],
        "PYTHONPATH": str(ROOT),
    }
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "harness.site.asgi:application",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--lifespan", "off"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(url, proc, log_path)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _wait_healthy(
    url: str, proc: subprocess.Popen[bytes], log_path: Path, timeout: float = 20.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early:\n{log_path.read_text()}")
        try:
            if httpx.get(f"{url}/health/", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"uvicorn not healthy after {timeout}s:\n{log_path.read_text()}")


@pytest.fixture
async def http(live_server_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=live_server_url, timeout=10) as client:
        yield client


@pytest.fixture
def book_pk(readonly_db: None) -> int:
    from harness.catalog.models import Book

    return Book.objects.order_by("pk").values_list("pk", flat=True)[0]
