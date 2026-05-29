"""Tests for configuration loading and CLI resolution."""

from __future__ import annotations

import json
from pathlib import Path

from fundus_camera_watchdog.main import _load_config, _resolve

# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for _load_config()."""

    def test_loads_json(self, tmp_path: Path) -> None:
        data = {"watch_dir": "C:\\output", "token": "abc"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(data))
        result = _load_config(config_file)
        assert result["watch_dir"] == "C:\\output"
        assert result["token"] == "abc"

    def test_loads_all_config_keys(self, tmp_path: Path) -> None:
        data = {
            "watch_dir": "/tmp/cam",
            "db_path": "/tmp/cam.db",
            "api_url": "https://edc.example.com",
            "token": "tok123",
            "device_id": "CAM-001",
            "site_id": "40",
            "log_level": "DEBUG",
            "db_patient_table": "Exams",
            "db_patient_subject_id": "patient_code",
            "db_patient_initials": "short_name",
            "db_patient_sex": "gender",
            "db_patient_age": "age_years",
            "db_image_table": "CapturedFiles",
            "db_image_subject_id": "patient_code",
            "db_image_filename": "file_name",
            "db_image_eye": "laterality",
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(data))
        result = _load_config(config_file)
        assert len(result) == len(data)
        for key, value in data.items():
            assert result[key] == value, f"Mismatch for {key}"


# ---------------------------------------------------------------------------
# _resolve
# ---------------------------------------------------------------------------


class TestResolve:
    """Tests for the three-tier _resolve() helper (CLI > config > default)."""

    def test_cli_wins(self) -> None:
        assert _resolve("from_cli", {"key": "from_config"}, "key", "default") == "from_cli"

    def test_config_when_cli_is_none(self) -> None:
        assert _resolve(None, {"key": "from_config"}, "key", "default") == "from_config"

    def test_default_when_both_missing(self) -> None:
        assert _resolve(None, {}, "key", "default") == "default"

    def test_cli_none_config_missing_no_default(self) -> None:
        assert _resolve(None, {}, "key") == ""

    def test_cli_empty_string_still_wins(self) -> None:
        """An explicit empty string from CLI is not None, so it wins."""
        assert _resolve("", {"key": "from_config"}, "key", "default") == ""

    def test_cli_value_wins_over_config_and_default(self) -> None:
        """Any non-None CLI value wins over config and default."""
        assert _resolve("cli", {"key": "config"}, "key", "default") == "cli"
