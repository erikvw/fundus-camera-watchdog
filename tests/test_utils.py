"""Tests for utility functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from fundus_camera_watchdog.main import (
    DEFAULT_FILENAME_EYE_PATTERN,
    REPORT_TYPE_COMBINED,
    REPORT_TYPE_PER_EYE,
    LateralityRequiredForDicomsError,
    LateralityRequiredForImagesError,
    LateralityRequiredForReportsError,
    UnhandledFileExtensionError,
    acquire_single_instance_lock,
    compile_filename_eye_pattern,
    determine_api_file_type,
    extract_eye_from_filename,
    mime_for_file,
    normalize_eye,
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
# determine_api_file_type — DICOM
# ---------------------------------------------------------------------------


class TestDetermineApiFileTypeDicom:
    """Tests for determine_api_file_type() with DICOM files."""

    def test_dcm_left(self) -> None:
        assert determine_api_file_type("left", ".dcm") == "left_dicom"

    def test_dcm_right(self) -> None:
        assert determine_api_file_type("right", ".dcm") == "right_dicom"

    def test_dcm_uppercase(self) -> None:
        assert determine_api_file_type("left", ".DCM") == "left_dicom"

    def test_dcm_no_leading_dot(self) -> None:
        assert determine_api_file_type("right", "dcm") == "right_dicom"

    def test_dcm_requires_eye(self) -> None:
        with pytest.raises(LateralityRequiredForDicomsError):
            determine_api_file_type(None, ".dcm")

    def test_dcm_combined_still_needs_eye(self) -> None:
        """DICOM files need eye laterality regardless of report_type."""
        with pytest.raises(LateralityRequiredForDicomsError):
            determine_api_file_type(None, ".dcm", REPORT_TYPE_COMBINED)


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

    def test_dcm(self) -> None:
        assert mime_for_file(Path("scan.dcm")) == "application/dicom"

    def test_unknown_extension(self) -> None:
        assert mime_for_file(Path("data.bin")) == "application/octet-stream"

    def test_uppercase_extension(self) -> None:
        assert mime_for_file(Path("SCAN.JPG")) == "image/jpeg"


# ---------------------------------------------------------------------------
# normalize_eye
# ---------------------------------------------------------------------------


class TestNormalizeEye:
    """Tests for normalize_eye()."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("OD", "right"),
            ("OS", "left"),
            ("R", "right"),
            ("L", "left"),
            ("RIGHT", "right"),
            ("LEFT", "left"),
            ("RE", "right"),
            ("LE", "left"),
            ("od", "right"),
            ("os", "left"),
            (" OD ", "right"),
        ],
    )
    def test_known_values(self, raw: str, expected: str) -> None:
        assert normalize_eye(raw) == expected

    def test_unknown_returns_none(self) -> None:
        assert normalize_eye("BOTH") is None

    def test_empty_returns_none(self) -> None:
        assert normalize_eye("") is None


# ---------------------------------------------------------------------------
# extract_eye_from_filename
# ---------------------------------------------------------------------------


class TestExtractEyeFromFilename:
    """Tests for extract_eye_from_filename()."""

    def test_od_in_filename(self) -> None:
        p = compile_filename_eye_pattern(DEFAULT_FILENAME_EYE_PATTERN)
        result = extract_eye_from_filename(
            "105-60-00224-7_Retina_OD_20260602_121802.dcm", p,
        )
        assert result == "right"

    def test_os_in_filename(self) -> None:
        p = compile_filename_eye_pattern(DEFAULT_FILENAME_EYE_PATTERN)
        result = extract_eye_from_filename(
            "105-60-00224-7_Retina_OS_20260602_121802.dcm", p,
        )
        assert result == "left"

    def test_no_match_returns_none(self) -> None:
        p = compile_filename_eye_pattern(DEFAULT_FILENAME_EYE_PATTERN)
        assert extract_eye_from_filename("unknown_file.jpg", p) is None

    def test_jpg_with_od(self) -> None:
        p = compile_filename_eye_pattern(DEFAULT_FILENAME_EYE_PATTERN)
        result = extract_eye_from_filename(
            "105-60-00224-7_Retina_OD_20260602_121802.jpg", p,
        )
        assert result == "right"

    def test_html_with_os(self) -> None:
        p = compile_filename_eye_pattern(DEFAULT_FILENAME_EYE_PATTERN)
        result = extract_eye_from_filename(
            "105-60-00224-7_Report_OS_20260602.html", p,
        )
        assert result == "left"

    def test_custom_pattern(self) -> None:
        """Custom pattern with different group name works."""
        p = compile_filename_eye_pattern(r"_(?P<eye>LEFT|RIGHT)_")
        assert extract_eye_from_filename("scan_LEFT_001.jpg", p) == "left"
        assert extract_eye_from_filename("scan_RIGHT_001.jpg", p) == "right"


# ---------------------------------------------------------------------------
# acquire_single_instance_lock
# ---------------------------------------------------------------------------


class TestAcquireSingleInstanceLock:
    """Tests for acquire_single_instance_lock()."""

    def test_first_acquisition_succeeds(self) -> None:
        """First call binds the port and returns a socket."""
        # Use a high unused port for the test
        sock = acquire_single_instance_lock(port=51743)
        assert sock is not None
        sock.close()

    def test_second_acquisition_fails(self) -> None:
        """Second call to bind the same port returns None."""
        first = acquire_single_instance_lock(port=51744)
        assert first is not None
        try:
            second = acquire_single_instance_lock(port=51744)
            assert second is None
        finally:
            first.close()

    def test_release_allows_reacquisition(self) -> None:
        """After closing the first socket, the port can be re-acquired."""
        first = acquire_single_instance_lock(port=51745)
        assert first is not None
        first.close()
        second = acquire_single_instance_lock(port=51745)
        assert second is not None
        second.close()
