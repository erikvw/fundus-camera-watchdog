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
    """Tests for SubjectFiles (default: DCMs required, JPEGs ignored)."""

    @pytest.fixture()
    def sf(self, tmp_path: Path) -> SubjectFiles:
        return SubjectFiles(SUBJECT_IDENTIFIER, tmp_path)

    def test_initial_state(self, sf: SubjectFiles, tmp_path: Path) -> None:
        assert sf.subject_identifier == SUBJECT_IDENTIFIER
        assert sf.directory == tmp_path
        assert sf.files == {}
        assert sf.processing is False
        assert sf.include_jpgs is False

    def test_add_file(self, sf: SubjectFiles, tmp_path: Path) -> None:
        p = tmp_path / "aaa.dcm"
        sf.add_file(p)
        assert "aaa.dcm" in sf.files
        assert sf.files["aaa.dcm"] == p

    def test_jpgs_property(self, sf: SubjectFiles, tmp_path: Path) -> None:
        for name in ("a.jpg", "b.jpeg", "c.html", "d.png"):
            sf.add_file(tmp_path / name)
        jpgs = sf.jpgs
        assert len(jpgs) == IMAGE_FILE_COUNT
        names = {p.name for p in jpgs}
        assert names == {"a.jpg", "b.jpeg"}

    def test_htmls_property(self, sf: SubjectFiles, tmp_path: Path) -> None:
        for name in ("a.dcm", "b.html", "c.htm", "d.png"):
            sf.add_file(tmp_path / name)
        htmls = sf.htmls
        assert len(htmls) == PER_EYE_HTML_FILE_COUNT
        names = {p.name for p in htmls}
        assert names == {"b.html", "c.htm"}

    def test_is_ready_false_initially(self, sf: SubjectFiles) -> None:
        assert sf.is_ready is False

    def test_is_ready_false_with_one_dcm(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.html")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is False

    def test_is_ready_false_with_one_html(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is False

    def test_is_ready_true_when_complete(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_is_ready_false_when_processing(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
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
        for name in ("a.dcm", "b.dcm", "c.dcm", "d.html", "e.html", "f.htm"):
            sf.add_file(tmp_path / name)
        assert sf.is_ready is True

    def test_jpgs_alone_do_not_trigger_readiness(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        """JPEGs are ignored for readiness by default."""
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is False

    def test_png_not_counted(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.png")
        sf.add_file(tmp_path / "b.png")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is False


class TestSubjectFilesIncludeJpgs:
    """Tests for SubjectFiles with include_jpgs=True."""

    @pytest.fixture()
    def sf(self, tmp_path: Path) -> SubjectFiles:
        return SubjectFiles(SUBJECT_IDENTIFIER, tmp_path, include_jpgs=True)

    def test_jpgs_trigger_readiness(self, sf: SubjectFiles, tmp_path: Path) -> None:
        sf.add_file(tmp_path / "a.jpg")
        sf.add_file(tmp_path / "b.jpg")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_dcms_also_trigger_readiness(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_not_ready_without_images(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
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
        """Combined mode: 2 DCMs + 1 HTML is ready."""
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is True

    def test_not_ready_without_html(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: 2 DCMs + 0 HTMLs is not ready."""
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        assert sf.is_ready is False

    def test_not_ready_with_one_dcm(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: still requires 2 DCMs."""
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "c.html")
        assert sf.is_ready is False

    def test_ready_with_extra_html(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """Combined mode: extra HTMLs don't prevent readiness."""
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_expected_htmls_stored(self, sf: SubjectFiles) -> None:
        assert sf.expected_htmls == 1

    def test_default_expected_htmls(self, tmp_path: Path) -> None:
        """Default expected_htmls is 2 (per_eye mode)."""
        sf = SubjectFiles("S", tmp_path)
        assert sf.expected_htmls == PER_EYE_HTML_FILE_COUNT


class TestSubjectFilesNoRequireHtml:
    """Tests for SubjectFiles with expected_htmls=0."""

    @pytest.fixture()
    def sf(self, tmp_path: Path) -> SubjectFiles:
        return SubjectFiles(SUBJECT_IDENTIFIER, tmp_path, expected_htmls=0)

    def test_ready_with_dcms_only(self, sf: SubjectFiles, tmp_path: Path) -> None:
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        assert sf.is_ready is True

    def test_not_ready_with_one_dcm(self, sf: SubjectFiles, tmp_path: Path) -> None:
        sf.add_file(tmp_path / "a.dcm")
        assert sf.is_ready is False


class TestSubjectFilesDicom:
    """Tests for SubjectFiles DICOM tracking."""

    @pytest.fixture()
    def sf(self, tmp_path: Path) -> SubjectFiles:
        return SubjectFiles(SUBJECT_IDENTIFIER, tmp_path)

    def test_dcms_property(self, sf: SubjectFiles, tmp_path: Path) -> None:
        for name in ("a.jpg", "b.dcm", "c.dcm", "d.html"):
            sf.add_file(tmp_path / name)
        dcms = sf.dcms
        assert len(dcms) == 2
        names = {p.name for p in dcms}
        assert names == {"b.dcm", "c.dcm"}

    def test_dcms_empty_when_none(self, sf: SubjectFiles, tmp_path: Path) -> None:
        sf.add_file(tmp_path / "a.jpg")
        assert sf.dcms == []

    def test_dcms_trigger_readiness(
        self,
        sf: SubjectFiles,
        tmp_path: Path,
    ) -> None:
        """DCM files are the primary trigger for readiness."""
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        sf.add_file(tmp_path / "c.html")
        sf.add_file(tmp_path / "d.html")
        assert sf.is_ready is True

    def test_dcms_alone_not_ready(self, sf: SubjectFiles, tmp_path: Path) -> None:
        """DCM files alone don't satisfy readiness (HTMLs still needed)."""
        sf.add_file(tmp_path / "a.dcm")
        sf.add_file(tmp_path / "b.dcm")
        assert sf.is_ready is False
