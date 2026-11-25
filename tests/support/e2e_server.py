"""A uvicorn process serving `harness.site.asgi:application`, with the ASGI lifespan switched on.

The ASGI app wraps Django in `LifespanApp`, so startup opens the pools and shutdown closes them.
Registry state lives in the server process; tests read it through two harness hooks:
  * `GET /diag/pool` -> `{"entries": ["default"], "server_info": true, "pool": {alias: {...}}}`
  * after the lifespan shutdown completed the server prints one line
    `ASYNCOPATION_DIAG after-shutdown entries=<n> in_use=<n> idle=<n> waiting=<n>`
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DIAG_LINE = re.compile(
    r"ASYNCOPATION_DIAG after-shutdown entries=(\d+) in_use=(\d+) idle=(\d+) waiting=(\d+)"
)


@dataclass
class ServerProcess:
    url: str
    proc: subprocess.Popen[bytes]
    log_path: Path

    def stop(self, timeout: float = 15.0) -> int:
        """SIGTERM (graceful: runs the lifespan shutdown) and wait; returns the exit code."""
        if self.proc.poll() is None:
            self.proc.terminate()
        return self.proc.wait(timeout=timeout)

    def log(self) -> str:
        return self.log_path.read_text()

    def logged(self, *markers: str) -> bool:
        """Whether every marker appears in the server log."""
        text = self.log()
        return all(marker in text for marker in markers)

    def lacks(self, marker: str) -> bool:
        """Whether `marker` does not appear in the server log."""
        return marker not in self.log()

    def after_shutdown(self) -> tuple[int, ...]:
        """(entries, in_use, idle, waiting) printed by the server after shutdown, or ()."""
        found = DIAG_LINE.search(self.log())
        return tuple(int(g) for g in found.groups()) if found else ()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(db_name: str, log_dir: Path, *, timeout: float = 30.0) -> ServerProcess:
    port = _free_port()
    log_path = log_dir / f"server-{port}.log"
    env = os.environ | {"ASYNCOPATION_DB_NAME": db_name, "PYTHONPATH": str(ROOT)}
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "harness.site.asgi:application",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--lifespan", "on"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )  # fmt: skip
    server = ServerProcess(f"http://127.0.0.1:{port}", proc, log_path)
    _wait_healthy(server, timeout)
    return server


def _wait_healthy(server: ServerProcess, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early:\n{server.log()}")
        try:
            if httpx.get(f"{server.url}/health/", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    server.proc.kill()
    raise RuntimeError(f"uvicorn not healthy after {timeout}s:\n{server.log()}")
