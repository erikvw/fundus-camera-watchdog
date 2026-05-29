"""Tests for CameraDB against a real in-memory SQLite database."""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from fundus_camera_watchdog.main import CameraDB, DBColumnMap

from .conftest import create_test_db
from .constants import SUBJECT_IDENTIFIER

# ---------------------------------------------------------------------------
# Demographics
# ---------------------------------------------------------------------------


class TestCameraDBDemographics:
    """Tests for CameraDB.get_demographics()."""

    def test_get_demographics(self, camera_db: CameraDB) -> None:
        result = camera_db.get_demographics(SUBJECT_IDENTIFIER)
        assert result is not None
        assert result["initials"] == "JD"
        assert result["sex"] == "M"
        assert result["age"] == 35  # noqa: PLR2004

    def test_get_demographics_second_subject(self, camera_db: CameraDB) -> None:
        result = camera_db.get_demographics("105-10-0002-3")
        assert result is not None
        assert result["initials"] == "AB"
        assert result["sex"] == "F"
        assert result["age"] == 42  # noqa: PLR2004

    def test_get_demographics_unknown_subject(self, camera_db: CameraDB) -> None:
        result = camera_db.get_demographics("999-99-9999-9")
        assert result is None

    def test_get_demographics_handles_none_fields(
        self, camera_db_path: Path,
    ) -> None:
        """Null initials/sex become empty strings."""
        conn = sqlite3.connect(str(camera_db_path))
        conn.execute(
            "INSERT INTO patients VALUES (?, ?, ?, ?)",
            ("105-10-0003-4", None, None, None),
        )
        conn.commit()
        conn.close()
        db = CameraDB(camera_db_path)
        result = db.get_demographics("105-10-0003-4")
        assert result is not None
        assert result["initials"] == ""
        assert result["sex"] == ""
        assert result["age"] is None


# ---------------------------------------------------------------------------
# Eye laterality
# ---------------------------------------------------------------------------


class TestCameraDBEyeLaterality:
    """Tests for CameraDB eye mapping methods."""

    def test_get_eye_for_file_left(self, camera_db: CameraDB) -> None:
        assert camera_db.get_eye_for_file(SUBJECT_IDENTIFIER, "aaa.jpg") == "left"

    def test_get_eye_for_file_right(self, camera_db: CameraDB) -> None:
        assert camera_db.get_eye_for_file(SUBJECT_IDENTIFIER, "bbb.jpg") == "right"

    def test_get_eye_for_file_unknown(self, camera_db: CameraDB) -> None:
        assert camera_db.get_eye_for_file(SUBJECT_IDENTIFIER, "zzz.jpg") is None

    def test_get_eye_normalises_os_to_left(self, camera_db: CameraDB) -> None:
        """OS (Oculus Sinister) maps to 'left'."""
        assert camera_db.get_eye_for_file("105-10-0002-3", "eee.jpg") == "left"

    def test_get_eye_normalises_od_to_right(self, camera_db: CameraDB) -> None:
        """OD (Oculus Dexter) maps to 'right'."""
        assert camera_db.get_eye_for_file("105-10-0002-3", "fff.jpg") == "right"

    def test_get_eye_normalises_left_word(self, camera_db: CameraDB) -> None:
        assert camera_db.get_eye_for_file("105-10-0002-3", "ggg.html") == "left"

    def test_get_eye_normalises_right_word(self, camera_db: CameraDB) -> None:
        assert camera_db.get_eye_for_file("105-10-0002-3", "hhh.html") == "right"

    def test_get_file_map(self, camera_db: CameraDB) -> None:
        result = camera_db.get_file_map(
            SUBJECT_IDENTIFIER,
            ["aaa.jpg", "bbb.jpg", "ccc.html", "ddd.html"],
        )
        assert result == {
            "aaa.jpg": "left",
            "bbb.jpg": "right",
            "ccc.html": "left",
            "ddd.html": "right",
        }

    def test_get_file_map_partial(self, camera_db: CameraDB) -> None:
        """Only known files are returned."""
        result = camera_db.get_file_map(
            SUBJECT_IDENTIFIER,
            ["aaa.jpg", "zzz.jpg"],
        )
        assert result == {"aaa.jpg": "left"}

    def test_get_file_map_empty_list(self, camera_db: CameraDB) -> None:
        result = camera_db.get_file_map(SUBJECT_IDENTIFIER, [])
        assert result == {}


# ---------------------------------------------------------------------------
# Custom columns
# ---------------------------------------------------------------------------


class TestCameraDBCustomColumns:
    """Tests for CameraDB with non-default column names."""

    @pytest.fixture()
    def custom_db(self) -> Generator[CameraDB, None, None]:
        columns = DBColumnMap(
            patient_table="Exams",
            patient_subject_id="patient_code",
            patient_initials="short_name",
            patient_sex="gender",
            patient_age="age_years",
            image_table="CapturedFiles",
            image_subject_id="patient_code",
            image_filename="file_name",
            image_eye="laterality",
        )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = Path(tmp.name)
        create_test_db(db_path, columns=columns)
        yield CameraDB(db_path, columns=columns)
        db_path.unlink(missing_ok=True)

    def test_demographics_with_custom_columns(self, custom_db: CameraDB) -> None:
        result = custom_db.get_demographics(SUBJECT_IDENTIFIER)
        assert result is not None
        assert result["initials"]
        assert result["sex"] == "M"
        assert result["age"] == 35  # noqa: PLR2004

    def test_eye_mapping_with_custom_columns(self, custom_db: CameraDB) -> None:
        assert custom_db.get_eye_for_file(SUBJECT_IDENTIFIER, "aaa.jpg") == "left"

    def test_file_map_with_custom_columns(self, custom_db: CameraDB) -> None:
        result = custom_db.get_file_map(
            SUBJECT_IDENTIFIER, ["aaa.jpg", "bbb.jpg"],
        )
        assert result == {"aaa.jpg": "left", "bbb.jpg": "right"}


# ---------------------------------------------------------------------------
# normalise_eye
# ---------------------------------------------------------------------------


class TestNormaliseEye:
    """Tests for CameraDB._normalise_eye() static method."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("L", "left"),
            ("LE", "left"),
            ("OS", "left"),
            ("LEFT", "left"),
            ("R", "right"),
            ("RE", "right"),
            ("OD", "right"),
            ("RIGHT", "right"),
            ("os", "left"),
            ("Od", "right"),
            ("  L  ", "left"),
        ],
    )
    def test_normalises(self, raw: str, expected: str) -> None:
        assert CameraDB._normalise_eye(raw) == expected

    def test_none_returns_none(self) -> None:
        assert CameraDB._normalise_eye(None) is None

    def test_empty_returns_none(self) -> None:
        assert CameraDB._normalise_eye("") is None

    def test_unknown_returns_none(self) -> None:
        assert CameraDB._normalise_eye("BOTH") is None
