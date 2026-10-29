"""Start the target API as a subprocess on a free port, for runs without API_BASE_URL."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import httpx


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalServer:
    """``uv run trafficflow serve-api`` in the target project, stopped on exit."""

    def __init__(self, project: Path, *, log_path: Path, startup_timeout_s: float = 90.0):
        if not (project / "pyproject.toml").is_file():
            raise FileNotFoundError(f"target project not found: {project}")
        self.project = project
        self.log_path = log_path
        self.startup_timeout_s = startup_timeout_s
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> LocalServer:
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)  # our venv, not the target's
        env.pop("TRAFFICFLOW_MODEL", None)  # keep /ask on the deterministic mock
        cmd = ["uv", "run", "--project", str(self.project), "--extra", "api", "trafficflow"]
        cmd += ["serve-api", "--host", "127.0.0.1", "--port", str(self.port)]
        # a file, not a pipe: uvicorn logs every request and a full pipe would stall it
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("wb") as log:
            self._proc = subprocess.Popen(
                cmd, cwd=self.project, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        self._wait_ready()
        return self

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if self._proc and self._proc.poll() is not None:
                out = self.log_path.read_text(errors="replace")
                raise RuntimeError(f"target exited with {self._proc.returncode}:\n{out[-2000:]}")
            try:
                if httpx.get(f"{self.base_url}/openapi.json", timeout=2).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        self.stop()
        raise TimeoutError(f"target not ready after {self.startup_timeout_s:.0f}s")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def __enter__(self) -> LocalServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
