"""Tests for subject folder pattern matching."""

from __future__ import annotations

import re

import pytest

from fundus_camera_watchdog.main import (
    DEFAULT_SUBJECT_FOLDER_PATTERN,
    InvalidSubjectFolderPatternError,
    compile_subject_folder_pattern,
    extract_subject_identifier,
)


# ---------------------------------------------------------------------------
# compile_subject_folder_pattern
# ---------------------------------------------------------------------------


class TestCompileSubjectFolderPattern:
    def test_default_pattern_compiles(self) -> None:
        p = compile_subject_folder_pattern(DEFAULT_SUBJECT_FOLDER_PATTERN)
        assert isinstance(p, re.Pattern)

    def test_valid_pattern_compiles(self) -> None:
        p = compile_subject_folder_pattern(r"^(\d{3}-\d{2}-\d{4}-\d)_$")
        assert isinstance(p, re.Pattern)

    def test_invalid_regex_raises(self) -> None:
        with pytest.raises(InvalidSubjectFolderPatternError):
            compile_subject_folder_pattern(r"[invalid")


# ---------------------------------------------------------------------------
# extract_subject_identifier
# ---------------------------------------------------------------------------


class TestExtractSubjectIdentifier:
    def test_default_pattern_returns_full_name(self) -> None:
        """Default pattern uses the full folder name."""
        p = compile_subject_folder_pattern(DEFAULT_SUBJECT_FOLDER_PATTERN)
        assert extract_subject_identifier("105-10-0001-2", p) == "105-10-0001-2"

    def test_named_group(self) -> None:
        """Named group 'subject_identifier' is extracted."""
        p = compile_subject_folder_pattern(
            r"^(?P<subject_identifier>\d{3}-\d{2}-\d{4}-\d)_$"
        )
        assert extract_subject_identifier("105-40-1232-0_", p) == "105-40-1232-0"

    def test_unnamed_group(self) -> None:
        """First unnamed capture group is extracted."""
        p = compile_subject_folder_pattern(r"^(\d{3}-\d{2}-\d{4}-\d)_$")
        assert extract_subject_identifier("105-40-1232-0_", p) == "105-40-1232-0"

    def test_no_match_returns_none(self) -> None:
        p = compile_subject_folder_pattern(
            r"^(?P<subject_identifier>\d{3}-\d{2}-\d{4}-\d)_$"
        )
        assert extract_subject_identifier("not-a-match", p) is None

    def test_processed_folder_no_match(self) -> None:
        """'processed' doesn't match a typical subject pattern."""
        p = compile_subject_folder_pattern(
            r"^(?P<subject_identifier>\d{3}-\d{2}-\d{4}-\d)_$"
        )
        assert extract_subject_identifier("processed", p) is None

    def test_trailing_underscore_stripped(self) -> None:
        """Pattern strips trailing underscore from folder name."""
        p = compile_subject_folder_pattern(
            r"^(?P<subject_identifier>\d{3}-\d{2}-\d{4}-\d)_$"
        )
        result = extract_subject_identifier("105-40-1232-0_", p)
        assert result == "105-40-1232-0"
        assert not result.endswith("_")

    def test_no_group_uses_full_match(self) -> None:
        """Pattern with no capture group uses the entire match."""
        p = compile_subject_folder_pattern(r"^\d{3}-\d{2}-\d{4}-\d$")
        assert extract_subject_identifier("105-40-1232-0", p) == "105-40-1232-0"

    def test_partial_match_not_accepted(self) -> None:
        """Pattern must match from the start (re.match semantics)."""
        p = compile_subject_folder_pattern(r"\d{3}-\d{2}-\d{4}-\d_$")
        # Without ^ anchor, re.match still anchors at start
        assert extract_subject_identifier("105-40-1232-0_", p) == "105-40-1232-0_"

    def test_different_site_ids(self) -> None:
        """Same pattern works for different site codes."""
        p = compile_subject_folder_pattern(
            r"^(?P<subject_identifier>\d{3}-\d{2}-\d{4}-\d)_$"
        )
        assert extract_subject_identifier("105-10-0001-2_", p) == "105-10-0001-2"
        assert extract_subject_identifier("200-40-9999-8_", p) == "200-40-9999-8"

    def test_default_pattern_passes_anything(self) -> None:
        """Default pattern accepts any folder name."""
        p = compile_subject_folder_pattern(DEFAULT_SUBJECT_FOLDER_PATTERN)
        assert extract_subject_identifier("anything-goes", p) == "anything-goes"
        assert extract_subject_identifier("105-40-1232-0_", p) == "105-40-1232-0_"
