"""Tests for DBColumnMap validation and defaults."""

from __future__ import annotations

import pytest

from fundus_camera_watchdog.main import DBColumnMap, InvalidDBColumnError


class TestDBColumnMapDefaults:
    """Tests for DBColumnMap default values."""

    def test_defaults(self) -> None:
        """All defaults are valid SQL identifiers."""
        cm = DBColumnMap()
        assert cm.patient_table == "patients"
        assert cm.patient_subject_id == "subject_identifier"
        assert cm.patient_initials == "initials"
        assert cm.patient_sex == "sex"
        assert cm.patient_age == "age"
        assert cm.image_table == "images"
        assert cm.image_subject_id == "subject_identifier"
        assert cm.image_filename == "filename"
        assert cm.image_eye == "eye"

    def test_custom_values(self) -> None:
        """Custom table/column names are accepted."""
        cm = DBColumnMap(
            patient_table="Exams",
            patient_subject_id="patient_code",
            image_table="CapturedFiles",
            image_eye="laterality",
        )
        assert cm.patient_table == "Exams"
        assert cm.patient_subject_id == "patient_code"
        assert cm.image_table == "CapturedFiles"
        assert cm.image_eye == "laterality"


class TestDBColumnMapValidation:
    """Tests for SQL identifier validation in __post_init__."""

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(patient_table="")

    def test_rejects_sql_injection_semicolon(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(patient_table="patients; DROP TABLE users")

    def test_rejects_spaces(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(patient_table="my table")

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(image_eye="1eye")

    def test_rejects_dash(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(image_table="captured-files")

    def test_rejects_dot(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(patient_table="schema.table")

    def test_rejects_parentheses(self) -> None:
        with pytest.raises(InvalidDBColumnError):
            DBColumnMap(patient_age="age()")

    def test_accepts_underscores(self) -> None:
        cm = DBColumnMap(patient_table="_my_table_2")
        assert cm.patient_table == "_my_table_2"

    def test_accepts_uppercase(self) -> None:
        cm = DBColumnMap(patient_table="PATIENTS")
        assert cm.patient_table == "PATIENTS"

    def test_accepts_mixed_case_with_digits(self) -> None:
        cm = DBColumnMap(image_table="Images2024")
        assert cm.image_table == "Images2024"

    def test_frozen(self) -> None:
        """DBColumnMap is immutable."""
        cm = DBColumnMap()
        with pytest.raises(AttributeError):
            cm.patient_table = "hacked"  # type: ignore[misc]
