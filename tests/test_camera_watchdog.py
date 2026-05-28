"""Tests for CameraWatchDog file detection and scan logic."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fundus_camera_watchdog.camera_watchdog import (
    REPORT_TYPE_COMBINED,
    REPORT_TYPE_PER_EYE,
    CameraDB,
    CameraWatchDog,
    RetinopathyApiClient,
    SubjectFiles,
)

from .constants import (
    COMBINED_HTML_FILE_COUNT,
    PER_EYE_HTML_FILE_COUNT,
    SUBJECT_IDENTIFIER,
)


class _FakeEvent:
    """Mimics a watchdog FileCreatedEvent."""

    def __init__(self, src_path: str, is_directory: bool = False) -> None:
        self.src_path = src_path
        self.is_directory = is_directory


# ---------------------------------------------------------------------------
# Watcher fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def watcher_dirs():
    """Create and yield (tmpdir, processed), cleaning up afterwards."""
    tmpdir = Path(tempfile.mkdtemp())
    processed = tmpdir / "processed"
    processed.mkdir()
    yield tmpdir, processed
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture()
def watcher_factory(watcher_dirs):
    """Return a factory that creates a CameraWatchDog with the given kwargs."""
    tmpdir, processed = watcher_dirs

    def _make(**kwargs):
        defaults = dict(
            api=MagicMock(spec=RetinopathyApiClient),
            camera_db=MagicMock(spec=CameraDB),
            watch_dir=tmpdir,
            processed_dir=processed,
        )
        defaults.update(kwargs)
        return CameraWatchDog(**defaults)

    return _make


@pytest.fixture()
def watcher(watcher_factory):
    """Return a default CameraWatchDog."""
    return watcher_factory()


# ---------------------------------------------------------------------------
# scan_all
# ---------------------------------------------------------------------------


class TestScanAll:
    """Tests for CameraWatchDog.scan_all()."""

    def test_scan_discovers_subject_dirs(self, watcher, watcher_dirs) -> None:
        """scan_all registers files in subject subdirectories."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        (subdir / "aaa.jpg").write_bytes(b"\xff\xd8\xff")
        (subdir / "bbb.jpg").write_bytes(b"\xff\xd8\xff")

        watcher.scan_all()

        assert SUBJECT_IDENTIFIER in watcher._subjects
        sf = watcher._subjects[SUBJECT_IDENTIFIER]
        assert len(sf.files) == 2  # noqa: PLR2004

    def test_scan_skips_processed_dir(self, watcher, watcher_dirs) -> None:
        """The 'processed' subfolder is not treated as a subject."""
        _, processed = watcher_dirs
        (processed / "old.jpg").write_bytes(b"x")
        watcher.scan_all()
        assert "processed" not in watcher._subjects

    def test_scan_multiple_subjects(self, watcher, watcher_dirs) -> None:
        tmpdir, _ = watcher_dirs
        for sid in (SUBJECT_IDENTIFIER, "105-10-0002-3"):
            d = tmpdir / sid
            d.mkdir()
            (d / "a.jpg").write_bytes(b"\xff\xd8\xff")
        watcher.scan_all()
        assert len(watcher._subjects) == 2  # noqa: PLR2004


# ---------------------------------------------------------------------------
# on_created
# ---------------------------------------------------------------------------


class TestOnCreated:
    """Tests for CameraWatchDog.on_created() event handler."""

    def test_directory_event_ignored(self, watcher, watcher_dirs) -> None:
        """Directory creation events don't add files."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        event = _FakeEvent(str(subdir), is_directory=True)
        watcher.on_created(event)
        assert SUBJECT_IDENTIFIER not in watcher._subjects

    @patch("fundus_camera_watchdog.camera_watchdog.wait_for_stable", return_value=True)
    def test_file_event_registers_file(
        self, _mock_stable: MagicMock, watcher, watcher_dirs,
    ) -> None:
        """File creation in a subject folder is tracked."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        img = subdir / "aaa.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        event = _FakeEvent(str(img))
        watcher.on_created(event)

        assert SUBJECT_IDENTIFIER in watcher._subjects
        sf = watcher._subjects[SUBJECT_IDENTIFIER]
        assert "aaa.jpg" in sf.files

    @patch("fundus_camera_watchdog.camera_watchdog.wait_for_stable", return_value=True)
    def test_ignores_non_image_files(
        self, _mock_stable: MagicMock, watcher, watcher_dirs,
    ) -> None:
        """Files with unexpected extensions are ignored."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        txt = subdir / "notes.txt"
        txt.write_bytes(b"some notes")

        event = _FakeEvent(str(txt))
        watcher.on_created(event)

        assert SUBJECT_IDENTIFIER not in watcher._subjects

    @patch("fundus_camera_watchdog.camera_watchdog.wait_for_stable", return_value=True)
    def test_ignores_processed_folder_files(
        self, _mock_stable: MagicMock, watcher, watcher_dirs,
    ) -> None:
        """Files inside 'processed/' are not handled."""
        _, processed = watcher_dirs
        old_dir = processed / "105-10-0001-2_20260101"
        old_dir.mkdir()
        img = old_dir / "a.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        event = _FakeEvent(str(img))
        watcher.on_created(event)

        assert len(watcher._subjects) == 0

    @patch("fundus_camera_watchdog.camera_watchdog.wait_for_stable", return_value=False)
    def test_unstable_file_not_registered(
        self, _mock_stable: MagicMock, watcher, watcher_dirs,
    ) -> None:
        """File that never stabilises is not added."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        img = subdir / "aaa.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        event = _FakeEvent(str(img))
        watcher.on_created(event)

        assert SUBJECT_IDENTIFIER not in watcher._subjects


# ---------------------------------------------------------------------------
# _try_process — per_eye mode
# ---------------------------------------------------------------------------


class TestTryProcessPerEye:
    """Tests for _try_process() in per_eye report mode."""

    @pytest.fixture()
    def per_eye_watcher(self, watcher_factory):
        return watcher_factory(report_type=REPORT_TYPE_PER_EYE)

    @patch.object(CameraWatchDog, "_process_subject")
    def test_not_triggered_when_incomplete(
        self, mock_process: MagicMock, per_eye_watcher, watcher_dirs,
    ) -> None:
        """_process_subject is not called with only 1 JPG."""
        tmpdir, _ = watcher_dirs
        sf = SubjectFiles(SUBJECT_IDENTIFIER, tmpdir, expected_htmls=PER_EYE_HTML_FILE_COUNT)
        sf.add_file(Path("a.jpg"))
        sf.add_file(Path("b.html"))
        sf.add_file(Path("c.html"))
        per_eye_watcher._subjects[SUBJECT_IDENTIFIER] = sf

        per_eye_watcher._try_process(sf)
        mock_process.assert_not_called()
        assert sf.processing is False

    @patch.object(CameraWatchDog, "_process_subject")
    def test_triggered_when_complete(
        self, mock_process: MagicMock, per_eye_watcher, watcher_dirs,
    ) -> None:
        """_process_subject is called when 2 JPGs + 2 HTMLs are present."""
        tmpdir, _ = watcher_dirs
        sf = SubjectFiles(SUBJECT_IDENTIFIER, tmpdir, expected_htmls=PER_EYE_HTML_FILE_COUNT)
        for name in ("a.jpg", "b.jpg", "c.html", "d.html"):
            sf.add_file(Path(name))
        per_eye_watcher._subjects[SUBJECT_IDENTIFIER] = sf

        per_eye_watcher._try_process(sf)
        time.sleep(0.1)
        mock_process.assert_called_once_with(sf)
        assert sf.processing is True

    @patch.object(CameraWatchDog, "_process_subject")
    def test_not_triggered_when_already_processing(
        self, mock_process: MagicMock, per_eye_watcher, watcher_dirs,
    ) -> None:
        """Already-processing subjects are not restarted."""
        tmpdir, _ = watcher_dirs
        sf = SubjectFiles(SUBJECT_IDENTIFIER, tmpdir, expected_htmls=PER_EYE_HTML_FILE_COUNT)
        for name in ("a.jpg", "b.jpg", "c.html", "d.html"):
            sf.add_file(Path(name))
        sf.processing = True
        per_eye_watcher._subjects[SUBJECT_IDENTIFIER] = sf

        per_eye_watcher._try_process(sf)
        mock_process.assert_not_called()


# ---------------------------------------------------------------------------
# _try_process — combined mode
# ---------------------------------------------------------------------------


class TestTryProcessCombined:
    """Tests for _try_process() in combined report mode."""

    @pytest.fixture()
    def combined_watcher(self, watcher_factory):
        return watcher_factory(report_type=REPORT_TYPE_COMBINED)

    @patch.object(CameraWatchDog, "_process_subject")
    def test_triggered_with_one_html(
        self, mock_process: MagicMock, combined_watcher, watcher_dirs,
    ) -> None:
        """Combined mode: 2 JPGs + 1 HTML triggers processing."""
        tmpdir, _ = watcher_dirs
        sf = SubjectFiles(SUBJECT_IDENTIFIER, tmpdir, expected_htmls=COMBINED_HTML_FILE_COUNT)
        for name in ("a.jpg", "b.jpg", "c.html"):
            sf.add_file(Path(name))
        combined_watcher._subjects[SUBJECT_IDENTIFIER] = sf

        combined_watcher._try_process(sf)
        time.sleep(0.1)
        mock_process.assert_called_once_with(sf)

    @patch.object(CameraWatchDog, "_process_subject")
    def test_not_triggered_without_html(
        self, mock_process: MagicMock, combined_watcher, watcher_dirs,
    ) -> None:
        """Combined mode: 2 JPGs + 0 HTMLs does not trigger."""
        tmpdir, _ = watcher_dirs
        sf = SubjectFiles(SUBJECT_IDENTIFIER, tmpdir, expected_htmls=COMBINED_HTML_FILE_COUNT)
        for name in ("a.jpg", "b.jpg"):
            sf.add_file(Path(name))
        combined_watcher._subjects[SUBJECT_IDENTIFIER] = sf

        combined_watcher._try_process(sf)
        mock_process.assert_not_called()

    def test_scan_creates_subjects_with_expected_htmls_1(
        self, combined_watcher, watcher_dirs,
    ) -> None:
        """Combined watcher creates SubjectFiles with expected_htmls=1."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        (subdir / "a.jpg").write_bytes(b"\xff\xd8\xff")

        combined_watcher.scan_all()

        sf = combined_watcher._subjects[SUBJECT_IDENTIFIER]
        assert sf.expected_htmls == COMBINED_HTML_FILE_COUNT


# ---------------------------------------------------------------------------
# report_type attribute
# ---------------------------------------------------------------------------


class TestWatcherReportType:
    """Tests for CameraWatchDog report_type attribute."""

    def test_default_combined(self, watcher_factory) -> None:
        w = watcher_factory()
        assert w.report_type == REPORT_TYPE_COMBINED
        assert w._expected_htmls == 1

    def test_combined(self, watcher_factory) -> None:
        w = watcher_factory(report_type=REPORT_TYPE_COMBINED)
        assert w.report_type == REPORT_TYPE_COMBINED
        assert w._expected_htmls == COMBINED_HTML_FILE_COUNT


# ---------------------------------------------------------------------------
# _move_to_processed
# ---------------------------------------------------------------------------


class TestMoveToProcessed:
    """Tests for CameraWatchDog._move_to_processed()."""

    def test_moves_directory(self, watcher, watcher_dirs) -> None:
        """Subject directory is moved into processed/."""
        tmpdir, processed = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()
        (subdir / "a.jpg").write_bytes(b"x")

        sf = SubjectFiles(SUBJECT_IDENTIFIER, subdir)
        watcher._subjects[SUBJECT_IDENTIFIER] = sf

        watcher._move_to_processed(sf)

        assert not subdir.exists()
        moved = list(processed.iterdir())
        assert len(moved) == 1
        assert moved[0].name.startswith("105-10-0001-2_")
        assert (moved[0] / "a.jpg").exists()

    def test_removes_from_subjects_dict(self, watcher, watcher_dirs) -> None:
        """Subject is removed from tracking after move."""
        tmpdir, _ = watcher_dirs
        subdir = tmpdir / SUBJECT_IDENTIFIER
        subdir.mkdir()

        sf = SubjectFiles(SUBJECT_IDENTIFIER, subdir)
        watcher._subjects[SUBJECT_IDENTIFIER] = sf

        watcher._move_to_processed(sf)

        assert SUBJECT_IDENTIFIER not in watcher._subjects


# ---------------------------------------------------------------------------
# _mark_failed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    """Tests for CameraWatchDog._mark_failed()."""

    def test_clears_processing_flag(self, watcher, watcher_dirs) -> None:
        tmpdir, _ = watcher_dirs
        sf = SubjectFiles(SUBJECT_IDENTIFIER, tmpdir)
        sf.processing = True
        watcher._mark_failed(sf)
        assert sf.processing is False
