"""Tests for RetinopathyApiClient using a mock HTTP server."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fundus_camera_watchdog.main import RetinopathyApiClient

from .conftest import MockHandler
from .constants import SUBJECT_IDENTIFIER

# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------


class TestPing:
    """Tests for RetinopathyApiClient.ping()."""

    def test_ping_success(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/ping/"] = (
            200,
            {"status": "ok"},
        )
        assert api_client.ping() is True

    def test_ping_failure(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/ping/"] = (
            500,
            {"error": "down"},
        )
        assert api_client.ping() is False

    def test_ping_sends_auth_header(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/ping/"] = (
            200,
            {"status": "ok"},
        )
        api_client.ping()
        assert len(MockHandler.received) == 1
        auth = MockHandler.received[0]["headers"].get("Authorization")
        assert auth == "Token test-token-123"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for RetinopathyApiClient.status()."""

    def test_status_success(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/105-10-0001-2/status/"] = (
            200,
            {
                "session_id": "abc-123",
                "uploaded": ["left", "right"],
                "missing": ["left_report", "right_report"],
                "complete": False,
            },
        )
        result = api_client.status(SUBJECT_IDENTIFIER)
        assert result is not None
        assert result["complete"] is False
        assert result["missing"] == ["left_report", "right_report"]

    def test_status_not_found(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/105-10-0001-2/status/"] = (
            404,
            {"code": "no_session"},
        )
        result = api_client.status(SUBJECT_IDENTIFIER)
        assert result is None


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class TestUpload:
    """Tests for RetinopathyApiClient.upload_file()."""

    def test_upload_success(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/105-10-0001-2/left/"] = (
            201,
            {
                "id": "img-1",
                "file_type": "left",
                "stored_filename": "abc.jpg",
            },
        )
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff" + b"\x00" * 100)
            path = Path(f.name)
        try:
            result = api_client.upload_file(
                SUBJECT_IDENTIFIER,
                "left",
                path,
            )
            assert result is not None
            assert result["file_type"] == "left"
        finally:
            path.unlink()

    def test_upload_failure(self, api_client: RetinopathyApiClient) -> None:
        MockHandler.responses["/api/retinopathy/105-10-0001-2/left/"] = (
            400,
            {"code": "invalid_content", "error": "Bad file"},
        )
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff" + b"\x00" * 100)
            path = Path(f.name)
        try:
            result = api_client.upload_file(
                SUBJECT_IDENTIFIER,
                "left",
                path,
            )
            assert result is None
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Base URL normalisation
# ---------------------------------------------------------------------------


class TestBaseUrlNormalisation:
    """Tests for base URL construction."""

    def test_trailing_slash_stripped(self, mock_server: str) -> None:
        client = RetinopathyApiClient(base_url=f"{mock_server}/", token="t")
        assert client.base_url.endswith("/api/retinopathy")
        assert "//api" not in client.base_url

    def test_no_trailing_slash(self, mock_server: str) -> None:
        client = RetinopathyApiClient(base_url=mock_server, token="t")
        assert client.base_url.endswith("/api/retinopathy")
