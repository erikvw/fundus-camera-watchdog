"""Tests for utility functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from fundus_camera_watchdog.main import (
    REPORT_TYPE_COMBINED,
    REPORT_TYPE_PER_EYE,
    LateralityRequiredForImagesError,
    LateralityRequiredForReportsError,
    UnhandledFileExtensionError,
    determine_api_file_type,
    mime_for_file,
    sha256_file,
)

# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


class TestSha256File:
    """Tests for sha256_file()."""

    def test_known_digest(self, tmp_path: Path) -> None:
        """SHA-256 of known content matches expected value."""
        p = tmp_path / "data.bin"
        p.write_bytes(b"hello world")
        # echo -n 'hello world' | sha256sum
        assert sha256_file(p) == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_empty_file(self, tmp_path: Path) -> None:
        """SHA-256 of empty file is the well-known empty digest."""
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        assert sha256_file(p) == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


# ---------------------------------------------------------------------------
# determine_api_file_type
# ---------------------------------------------------------------------------


class TestDetermineApiFileType:
    """Tests for determine_api_file_type()."""

    def test_jpg_left(self) -> None:
        assert determine_api_file_type("left", ".jpg") == "left"

    def test_jpg_right(self) -> None:
        assert determine_api_file_type("right", ".jpg") == "right"

    def test_jpeg_left(self) -> None:
        assert determine_api_file_type("left", ".jpeg") == "left"

    def test_png_right(self) -> None:
        assert determine_api_file_type("right", ".png") == "right"

    def test_html_left_per_eye(self) -> None:
        assert (
            determine_api_file_type("left", ".html", REPORT_TYPE_PER_EYE)
            == "left_report"
        )

    def test_html_right_per_eye(self) -> None:
        assert (
            determine_api_file_type("right", ".html", REPORT_TYPE_PER_EYE)
            == "right_report"
        )

    def test_htm_left_per_eye(self) -> None:
        assert (
            determine_api_file_type("left", ".htm", REPORT_TYPE_PER_EYE)
            == "left_report"
        )

    def test_html_default_is_combined(self) -> None:
        """Default report_type is combined, so HTML returns 'report'."""
        assert determine_api_file_type("left", ".html") == "report"

    def test_uppercase_jpg(self) -> None:
        """Extension matching is case-insensitive."""
        assert determine_api_file_type("left", ".JPG") == "left"

    def test_no_leading_dot(self) -> None:
        """Works with or without leading dot."""
        assert determine_api_file_type("right", "jpg") == "right"

    def test_unexpected_extension_raises(self) -> None:
        with pytest.raises(UnhandledFileExtensionError):
            determine_api_file_type("left", ".pdf")

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(UnhandledFileExtensionError):
            determine_api_file_type("left", ".xyz")

    def test_image_requires_eye(self) -> None:
        """Image files raise LateralityRequiredForImagesError when eye is None."""
        with pytest.raises(LateralityRequiredForImagesError):
            determine_api_file_type(None, ".jpg")

    def test_per_eye_report_requires_eye(self) -> None:
        """Per-eye reports raise LateralityRequiredForReportsError when eye is None."""
        with pytest.raises(LateralityRequiredForReportsError):
            determine_api_file_type(None, ".html", REPORT_TYPE_PER_EYE)


class TestDetermineApiFileTypeCombined:
    """Tests for determine_api_file_type() in combined report mode."""

    def test_html_combined_returns_report(self) -> None:
        assert (
            determine_api_file_type("left", ".html", REPORT_TYPE_COMBINED) == "report"
        )

    def test_htm_combined_returns_report(self) -> None:
        assert (
            determine_api_file_type("right", ".htm", REPORT_TYPE_COMBINED) == "report"
        )

    def test_combined_ignores_eye_for_html(self) -> None:
        """Eye value is irrelevant in combined mode for reports."""
        assert determine_api_file_type(None, ".html", REPORT_TYPE_COMBINED) == "report"

    def test_combined_images_still_need_eye(self) -> None:
        """Image files still use eye even in combined mode."""
        assert determine_api_file_type("left", ".jpg", REPORT_TYPE_COMBINED) == "left"

    def test_combined_image_without_eye_raises(self) -> None:
        with pytest.raises(LateralityRequiredForImagesError):
            determine_api_file_type(None, ".jpg", REPORT_TYPE_COMBINED)


# ---------------------------------------------------------------------------
# mime_for_file
# ---------------------------------------------------------------------------


class TestMimeForFile:
    """Tests for mime_for_file()."""

    def test_jpg(self) -> None:
        assert mime_for_file(Path("scan.jpg")) == "image/jpeg"

    def test_jpeg(self) -> None:
        assert mime_for_file(Path("scan.jpeg")) == "image/jpeg"

    def test_png(self) -> None:
        assert mime_for_file(Path("scan.png")) == "image/png"

    def test_html(self) -> None:
        assert mime_for_file(Path("report.html")) == "text/html"

    def test_htm(self) -> None:
        assert mime_for_file(Path("report.htm")) == "text/html"

    def test_pdf(self) -> None:
        assert mime_for_file(Path("report.pdf")) == "application/pdf"

    def test_unknown_extension(self) -> None:
        assert mime_for_file(Path("data.bin")) == "application/octet-stream"

    def test_uppercase_extension(self) -> None:
        assert mime_for_file(Path("SCAN.JPG")) == "image/jpeg"
