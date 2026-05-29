"""Tests for SubjectFiles tracking class."""

from __future__ import annotations

from pathlib import Path

import pytest

from fundus_camera_watchdog.main import SubjectFiles

from .constants import (
    COMBINED_HTML_FILE_COUNT,
    IMAGE_FILE_COUNT,
    PER_EYE_HTML_FILE_COUNT,
    SUBJECT_IDENTIFIER,
)


class TestSubjectFiles:
    """Tests for SubjectFiles."""

    @pytest.fixture()
    def sf(self, tmp_path: Path) -> SubjectFiles:
        return SubjectFiles(SUBJECT_IDENTIFIER, tmp_path)

    def test_initial_state(self, sf: SubjectFiles, tmp_path: Path) -> None:
        assert sf.subject_identifier == SUBJECT_IDENTIFIER
        assert sf.directory == tmp_path
        assert sf.files == {}
        assert sf.processing is False

    def test_add_file(self, sf: SubjectFiles, tmp_path: Path) -> None:
        p = tmp_path / "aaa.jpg"
        sf.add_file(p)
        assert "aaa.jpg" in sf.files
        assert sf.files["aaa.jpg"] == p

    def test_jpgs_property(self, sf: SubjectFiles, tmp_path: Path) -> None:
        for name in ("a.jpg", "b.jpeg", "c.html", "d.png"):
            sf.add_file(tmp_path / name)
        jpgs = sf.jpgs
        assert len(jpgs) == IMAGE_FILE_COUNT
        names = {p.name for p in jpgs}
        assert names == {"a.jpg", "b.jpeg"}

    def test_htmls_property(self, sf: SubjectFiles, tmp_path: Path) -> None:
        for name in ("a.jpg", "b.html", "c.htm", "d.png"):
            sf.add_file(tmp_path / name)
        htmls = sf.htmls
        assert len(htmls) == PER_EYE_HTML_FILE_COUNT
        names = {p.name for p in htmls}
        assert names == {"b.html", "c.htm"}

    def test_is_ready_false_initially(self, sf: SubjectFiles) -> None:
        assert sf.is_ready is False

    def test_is_ready_false_with_one_jpg(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.html")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is False

    def test_is_ready_false_with_one_html(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is False

    def test_is_ready_true_when_complete(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_is_ready_false_when_processing(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        sf.processing = True
        assert sf.is_ready is False

    def test_is_ready_with_extra_files(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        """More than the minimum file count is still ready."""
        for name in ("a.jpg", "b.jpg", "c.jpg", "d.html", "e.html", "f.htm"):
            sf.add_file(tmp_path / name)
        assert sf.is_ready is True

    def test_png_not_counted_as_jpg(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        """PNG files are not counted toward the JPG requirement."""
        sf.add_file(tmp_path / "a.png")
        sf.add_file(tmp_path / "b.png")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is False


class TestSubjectFilesCombinedReport:
    """Tests for SubjectFiles with expected_htmls=1 (combined report)."""

    @pytest.fixture()
    def sf(self, tmp_path: Path) -> SubjectFiles:
        return SubjectFiles(
            SUBJECT_IDENTIFIER,
            tmp_path,
            expected_htmls=COMBINED_HTML_FILE_COUNT,
        )

    def test_ready_with_one_html(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: 2 JPGs + 1 HTML is ready."""
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is True

    def test_not_ready_without_html(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: 2 JPGs + 0 HTMLs is not ready."""
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        assert sf.is_ready is False

    def test_not_ready_with_one_jpg(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: still requires 2 JPGs."""
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is False

    def test_ready_with_extra_html(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: extra HTMLs don't prevent readiness."""
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_expected_htmls_stored(self, sf: SubjectFiles) -> None:
        assert sf.expected_htmls == 1

    def test_default_expected_htmls(self, tmp_path: Path) -> None:
        """Default expected_htmls is 2 (per_eye mode)."""
        sf = SubjectFiles("S", tmp_path)
        assert sf.expected_htmls == PER_EYE_HTML_FILE_COUNT
