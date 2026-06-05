"""Shared pytest fixtures for fundus-camera-watchdog tests."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fundus_camera_watchdog.main import (
    CameraWatchDog,
    RetinopathyApiClient,
)

# ---------------------------------------------------------------------------
# Temp directory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_path_cleanup() -> Generator[Path, None, None]:
    """Temporary directory that is cleaned up after the test."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def watch_dirs(tmp_path_cleanup: Path) -> tuple[Path, Path]:
    """Return (watch_dir, processed_dir) inside a temporary directory."""
    processed = tmp_path_cleanup / "processed"
    processed.mkdir()
    return tmp_path_cleanup, processed


# ---------------------------------------------------------------------------
# Mock HTTP server fixtures
# ---------------------------------------------------------------------------


class MockHandler(BaseHTTPRequestHandler):
    """Minimal handler that records requests and returns canned responses."""

    responses: dict[str, tuple[int, dict]] = {}  # noqa: RUF012
    received: list[dict] = []  # noqa: RUF012

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._handle(body)

    def _handle(self, body: bytes = b"") -> None:
        MockHandler.received.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            },
        )
        status_code, payload = MockHandler.responses.get(
            self.path.split("?")[0],
            (404, {"error": "not found"}),
        )
        response_body = json.dumps(payload).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silence request logging during tests


@pytest.fixture(scope="session")
def mock_server() -> Generator[str, None, None]:
    """Start a mock HTTP server for the session and return its base URL."""
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def api_client(mock_server: str) -> Generator[RetinopathyApiClient, None, None]:
    """Return an API client pointed at the mock server, clearing state each test."""
    MockHandler.received.clear()
    MockHandler.responses.clear()
    yield RetinopathyApiClient(
        base_url=mock_server,
        token="test-token-123",
        device_id="CAM-001",
        site_id="SITE-A",
    )


# ---------------------------------------------------------------------------
# Watcher fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def watcher(watch_dirs: tuple[Path, Path]) -> CameraWatchDog:
    """Return a CameraWatchDog with mocked api."""

    watch_dir, processed = watch_dirs
    return CameraWatchDog(
        api=MagicMock(spec=RetinopathyApiClient),
        watch_dir=watch_dir,
        processed_dir=processed,
    )
