"""Shared pytest fixtures for fundus-camera-watchdog tests."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fundus_camera_watchdog.camera_watchdog import (
    CameraDB,
    CameraWatchDog,
    DBColumnMap,
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
# Camera DB fixtures
# ---------------------------------------------------------------------------


def create_test_db(path: Path, columns: DBColumnMap | None = None) -> None:
    """Create a camera-like SQLite DB with test data."""
    c = columns or DBColumnMap()
    conn = sqlite3.connect(str(path))
    conn.execute(
        f"CREATE TABLE {c.patient_table} ("
        f"  {c.patient_subject_id} TEXT PRIMARY KEY,"
        f"  {c.patient_initials} TEXT,"
        f"  {c.patient_sex} TEXT,"
        f"  {c.patient_age} INTEGER"
        f")",
    )
    conn.execute(
        f"INSERT INTO {c.patient_table} VALUES (?, ?, ?, ?)",
        ("105-10-0001-2", "JD", "M", 35),
    )
    conn.execute(
        f"INSERT INTO {c.patient_table} VALUES (?, ?, ?, ?)",
        ("105-10-0002-3", "AB", "F", 42),
    )
    conn.execute(
        f"CREATE TABLE {c.image_table} ("
        f"  {c.image_subject_id} TEXT,"
        f"  {c.image_filename} TEXT,"
        f"  {c.image_eye} TEXT"
        f")",
    )
    rows = [
        ("105-10-0001-2", "aaa.jpg", "L"),
        ("105-10-0001-2", "bbb.jpg", "R"),
        ("105-10-0001-2", "ccc.html", "L"),
        ("105-10-0001-2", "ddd.html", "R"),
        ("105-10-0002-3", "eee.jpg", "OS"),
        ("105-10-0002-3", "fff.jpg", "OD"),
        ("105-10-0002-3", "ggg.html", "LEFT"),
        ("105-10-0002-3", "hhh.html", "RIGHT"),
    ]
    conn.executemany(
        f"INSERT INTO {c.image_table} VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def camera_db_path() -> Generator[Path, None, None]:
    """Create a temporary SQLite DB with default columns and yield its path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)
    create_test_db(db_path)
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture()
def camera_db(camera_db_path: Path) -> CameraDB:
    """Return a CameraDB instance with default columns."""
    return CameraDB(camera_db_path)


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
    """Return a CameraWatchDog with mocked api and camera_db."""

    watch_dir, processed = watch_dirs
    return CameraWatchDog(
        api=MagicMock(spec=RetinopathyApiClient),
        camera_db=MagicMock(spec=CameraDB),
        watch_dir=watch_dir,
        processed_dir=processed,
    )
