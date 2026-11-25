"""A uvicorn process serving `harness.site.asgi:application`, with the ASGI lifespan switched on.

The ASGI app wraps Django in `LifespanApp`, so startup opens the pools and shutdown closes them.
Registry state lives in the server process; tests read it through two harness hooks:
  * `GET /diag/pool` -> `{"entries": ["default"], "server_info": true, "pool": {alias: {...}}}`
  * the process tags its database sessions with `ASYNCOPATION_APP_NAME` and, around the lifespan
    shutdown, prints one line
    `ASYNCOPATION_DIAG sessions before-close=<n> after-shutdown=<n>`
    counting its pg_stat_activity sessions before `close()` and after `shutdown.complete`
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
DIAG_LINE = re.compile(r"ASYNCOPATION_DIAG sessions before-close=(-?\d+) after-shutdown=(-?\d+)")


@dataclass
class ServerProcess:
    url: str
    proc: subprocess.Popen[bytes]
    log_path: Path

    def stop(self, timeout: float = 15.0) -> int:
        """SIGTERM (graceful: runs the lifespan shutdown) and wait; kills the process when it does
        not exit in time. Returns the exit code."""
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=5)

    def log(self) -> str:
        return self.log_path.read_text()

    def logged(self, *markers: str) -> bool:
        """Whether every marker appears in the server log."""
        text = self.log()
        return all(marker in text for marker in markers)

    def lacks(self, marker: str) -> bool:
        """Whether `marker` does not appear in the server log."""
        return marker not in self.log()

    def sessions_around_shutdown(self) -> tuple[int, ...]:
        """(sessions before close, sessions after shutdown) the server printed, or ()."""
        found = DIAG_LINE.search(self.log())
        assert found is not None, (
            f"no ASYNCOPATION_DIAG line in the server log:\n{self.log()[-2000:]}"
        )
        return tuple(int(g) for g in found.groups())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(
    db_name: str, log_dir: Path, app_name: str, *, lifespan: str = "on", timeout: float = 30.0
) -> ServerProcess:
    port = _free_port()
    log_path = log_dir / f"server-{port}.log"
    env = os.environ | {
        "ASYNCOPATION_DB_NAME": db_name,
        "ASYNCOPATION_APP_NAME": app_name,
        "PYTHONPATH": str(ROOT),
    }
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "harness.site.asgi:application",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--lifespan", lifespan],
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
