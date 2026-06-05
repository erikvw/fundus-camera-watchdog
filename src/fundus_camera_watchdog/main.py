#!/usr/bin/env python
"""Watch a folder for retinopathy camera output and upload to the API.

Folder structure
----------------
The camera organises output into one subfolder per subject::

    watch_dir/
        105-10-0989-3/
            a1b2c3d4.jpg        <- left or right eye (determined by DB)
            e5f6a7b8.jpg        <- left or right eye (determined by DB)
            a1b2c3d4.dcm        <- left or right DICOM (determined by DB)
            e5f6a7b8.dcm        <- left or right DICOM (determined by DB)
            c9d0e1f2.html       <- left or right eye report
            f3a4b5c6.html       <- left or right eye report
        105-10-0001-2/
            ...

File names are random UUIDs assigned by the camera.  The camera's SQLite
database maps each filename to its eye laterality (left / right).

SQLite database
---------------
The ``CameraDB`` class queries the camera's SQLite database for:

* **Demographics** — initials, sex, age (used by ``/api/retinopathy/resolve/``).
* **Eye laterality** — which eye a given file belongs to.

Table and column names are fully configurable via a JSON config file
and/or CLI flags so that no source edits are needed.

Configuration
-------------
Create a JSON config file (e.g. ``camera_config.json``)::

    {
        "watch_dir": "C:\\\\RetCamOutput",
        "db_path": "C:\\\\RetCamOutput\\\\camera.db",
        "api_url": "https://edc.example.com",
        "token": "abc123",
        "device_id": "RET-CAM-001",
        "site_id": "",
        "db_patient_table": "patients",
        "db_patient_subject_id": "subject_identifier",
        "db_patient_initials": "initials",
        "db_patient_sex": "sex",
        "db_patient_age": "age",
        "db_image_table": "images",
        "db_image_subject_id": "subject_identifier",
        "db_image_filename": "filename",
        "db_image_eye": "eye"
    }

CLI flags override anything in the config file.

Report type
-----------
Set ``report_type`` in the config file or via ``--report-type``:

* ``combined`` *(default)* — the camera produces a single HTML report
  covering both eyes.  Expects 1 HTML file per subject, uploaded as
  ``report``.
* ``per_eye`` — the camera produces one HTML report per eye.
  Expects 2 HTML files per subject, uploaded as ``left_report`` and
  ``right_report``.

Processing
----------
Once a subject folder contains the expected files (2 JPGs + the number
of HTMLs dictated by ``report_type``):

1. Query the camera DB for demographics and eye mapping.
2. POST ``/api/retinopathy/resolve/`` — validate subject, create session.
3. Upload each file with the correct API file type
   (``left``, ``right``, ``left_report`` / ``right_report`` or ``report``).
4. GET ``/api/retinopathy/<sid>/status/`` — verify completeness.
5. Move the subject folder to ``<watch-dir>/processed/<sid>_<timestamp>/``.

A background sweep re-checks every 60 s for subjects that became ready
after a failed attempt or a missed filesystem event.

Usage (Windows)::

    python watch_camera.py --config camera_config.json

    python watch_camera.py ^
        --watch-dir C:\\RetCamOutput ^
        --db-path C:\\RetCamOutput\\camera.db ^
        --api-url https://edc.example.com ^
        --token abc123 ^
        --device-id RET-CAM-001

Requirements::

    pip install watchdog requests
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("watch_camera")

STATUS_CODE_OK = 200
STATUS_CODE_CREATED = 201

# ---------------------------------------------------------------------------
# Report type: per-eye (left_report + right_report) or combined (single report)
# ---------------------------------------------------------------------------
REPORT_TYPE_PER_EYE = "per_eye"
REPORT_TYPE_COMBINED = "combined"
VALID_REPORT_TYPES = frozenset({REPORT_TYPE_PER_EYE, REPORT_TYPE_COMBINED})
DEFAULT_CONFIG_FILENAME = "fundus_camera_watchdog.json"
ENV_TOKEN = "FUNDUS_CAMERA_WATCHDOG_TOKEN"

# ---------------------------------------------------------------------------
# Expected file counts per subject folder
# ---------------------------------------------------------------------------
EXPECTED_IMAGES = 2  # JPEGs or DICOMs per subject

# ---------------------------------------------------------------------------
# API retry settings
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
SWEEP_INTERVAL_SECONDS = 60
FILE_STABLE_TIMEOUT = 30.0
FILE_STABLE_POLL = 1.0

# ---------------------------------------------------------------------------
# Eye laterality values used by camera vendors
# ---------------------------------------------------------------------------
EYE_LEFT_VALUES = frozenset({"L", "OS", "LEFT", "LE"})
EYE_RIGHT_VALUES = frozenset({"R", "OD", "RIGHT", "RE"})


# ===================================================================
# Utilities
# ===================================================================


class InvalidSubjectFolderPatternError(Exception):
    def __init__(self, pattern: str, reason: str) -> None:
        super().__init__(
            f"Invalid subject_folder_pattern {pattern!r}: {reason}"
        )


class LateralityRequiredForImagesError(Exception):
    def __init__(self) -> None:
        super().__init__("Eye laterality is required for image files.")


class LateralityRequiredForDicomsError(Exception):
    def __init__(self) -> None:
        super().__init__("Eye laterality is required for DICOM files.")


class LateralityRequiredForReportsError(Exception):
    def __init__(self) -> None:
        super().__init__("Eye laterality is required for per-eye report files.")


class UnhandledFileExtensionError(Exception):
    def __init__(self, extension: str) -> None:
        super().__init__(f"Unexpected extension. Got {extension}.")


DEFAULT_SUBJECT_FOLDER_PATTERN = r"^(?P<subject_identifier>.+)$"


def compile_subject_folder_pattern(pattern: str) -> re.Pattern:
    """Compile and validate a subject folder regex pattern.

    The pattern must contain a named group ``subject_identifier``
    (or exactly one unnamed group).  When no group is present the
    entire match is used as the subject identifier.
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise InvalidSubjectFolderPatternError(pattern, str(exc)) from exc
    return compiled


def extract_subject_identifier(
    folder_name: str,
    pattern: re.Pattern,
) -> str | None:
    """Return the subject_identifier from *folder_name*, or None if no match.

    Tries (in order):
    1. Named group ``subject_identifier``
    2. First unnamed capture group
    3. The entire match
    """
    m = pattern.match(folder_name)
    if not m:
        return None
    try:
        return m.group("subject_identifier")
    except IndexError:
        pass
    if m.lastindex:
        return m.group(1)
    return m.group(0)


DEFAULT_FILENAME_EYE_PATTERN = r"(?P<eye>OD|OS)"


def compile_filename_eye_pattern(pattern: str) -> re.Pattern:
    """Compile and validate a filename eye-laterality regex pattern."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise InvalidSubjectFolderPatternError(pattern, str(exc)) from exc


def normalize_eye(raw: str) -> str | None:
    """Map a raw eye value (e.g. OD, OS, L, R) to 'left' or 'right'."""
    val = raw.strip().upper()
    if val in EYE_LEFT_VALUES:
        return "left"
    if val in EYE_RIGHT_VALUES:
        return "right"
    logger.warning("Unknown eye value: %r", raw)
    return None


def extract_eye_from_filename(
    filename: str,
    pattern: re.Pattern,
) -> str | None:
    """Return 'left' or 'right' from *filename*, or None if no match.

    Searches for a named group ``eye`` first, then first capture group,
    then the full match.  The raw value is normalized via
    :func:`normalize_eye`.
    """
    m = pattern.search(filename)
    if not m:
        return None
    try:
        raw = m.group("eye")
    except IndexError:
        raw = m.group(1) if m.lastindex else m.group(0)
    return normalize_eye(raw)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_for_stable(
    path: Path,
    timeout: float = FILE_STABLE_TIMEOUT,
    interval: float = FILE_STABLE_POLL,
) -> bool:
    """Wait until a file's size stops changing (camera finished writing)."""
    deadline = time.monotonic() + timeout
    prev_size = -1
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
            if size > 0 and size == prev_size:
                return True
            prev_size = size
        except OSError:
            pass
        time.sleep(interval)
    return prev_size > 0


def determine_api_file_type(
    eye: str | None,
    extension: str,
    report_type: str = REPORT_TYPE_COMBINED,
) -> str:
    """Map eye laterality + file extension to an API file_type.

    For images, returns ``left`` or ``right`` (eye is required).

    For DICOM files, returns ``left_dicom`` or ``right_dicom``
    (eye is required).

    For reports (HTML/HTM), behaviour depends on *report_type*:

    * ``per_eye`` — returns ``left_report`` or ``right_report``
      (eye is required).
    * ``combined`` — returns ``report`` (eye is ignored).
    """
    ext = extension.lower().lstrip(".")
    if ext in ("jpg", "jpeg", "png"):
        if not eye:
            raise LateralityRequiredForImagesError()
        return eye  # "left" or "right"
    if ext == "dcm":
        if not eye:
            raise LateralityRequiredForDicomsError()
        return f"{eye}_dicom"  # "left_dicom" or "right_dicom"
    if ext in ("html", "htm"):
        if report_type == REPORT_TYPE_COMBINED:
            return "report"
        if not eye:
            raise LateralityRequiredForReportsError()
        return f"{eye}_report"  # "left_report" or "right_report"
    raise UnhandledFileExtensionError(extension)


def mime_for_file(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".dcm": "application/dicom",
        ".html": "text/html",
        ".htm": "text/html",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


# ===================================================================
# API client
# ===================================================================


class RetinopathyApiClient:
    """Thin wrapper around the edc-retinopathy REST API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        device_id: str = "",
        site_id: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/api/retinopathy"
        self.device_id = device_id
        self.site_id = site_id
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Token {token}"

    def ping(self) -> bool:
        r = self._get(f"{self.base_url}/ping/", label="ping")
        return r is not None and r.status_code == STATUS_CODE_OK

    def resolve(self, subject_identifier: str) -> dict | None:
        """Confirm a CameraSession exists for *subject_identifier*.

        Returns the response dict on success, or None on failure.
        """
        payload: dict = {"subject_identifier": subject_identifier}
        if self.device_id:
            payload["device_id"] = self.device_id

        r = self._post_json(
            f"{self.base_url}/resolve/",
            payload,
            label=f"resolve({subject_identifier})",
        )
        if r is not None and r.status_code == STATUS_CODE_OK:
            return r.json()
        if r is not None:
            logger.error(
                "resolve %s: %d %s",
                subject_identifier,
                r.status_code,
                r.text[:300],
            )
        return None

    def upload_file(
        self,
        subject_identifier: str,
        file_type: str,
        file_path: Path,
    ) -> dict | None:
        checksum = sha256_file(file_path)
        capture_dt = datetime.now(UTC).isoformat()
        url = f"{self.base_url}/{subject_identifier}/{file_type}/"
        file_bytes = file_path.read_bytes()
        mime = mime_for_file(file_path)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self._session.post(
                    url,
                    data={"capture_datetime": capture_dt, "checksum": checksum},
                    files={"file": (file_path.name, file_bytes, mime)},
                    timeout=60,
                )
                if r.status_code in (STATUS_CODE_OK, STATUS_CODE_CREATED):
                    return r.json()
                logger.warning(
                    "upload %s/%s attempt %d: %d %s",
                    subject_identifier,
                    file_type,
                    attempt,
                    r.status_code,
                    r.text[:300],
                )
            except requests.RequestException as exc:
                logger.warning(
                    "upload %s/%s attempt %d error: %s",
                    subject_identifier,
                    file_type,
                    attempt,
                    exc,
                )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
        return None

    def status(self, subject_identifier: str) -> dict | None:
        r = self._get(
            f"{self.base_url}/{subject_identifier}/status/",
            label=f"status({subject_identifier})",
        )
        if r is not None and r.status_code == STATUS_CODE_OK:
            return r.json()
        return None

    # -- internal helpers -----------------------------------------------

    def _get(self, url: str, label: str = "") -> requests.Response | None:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self._session.get(url, timeout=15)
            except requests.RequestException as exc:
                logger.warning("%s attempt %d error: %s", label, attempt, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
        return None

    def _post_json(
        self,
        url: str,
        payload: dict,
        label: str = "",
    ) -> requests.Response | None:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self._session.post(url, json=payload, timeout=15)
            except requests.RequestException as exc:
                logger.warning("%s attempt %d error: %s", label, attempt, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
        return None


# ===================================================================
# Subject tracking
# ===================================================================


class SubjectFiles:
    """Track files detected for a single subject folder."""

    def __init__(
        self,
        subject_identifier: str,
        directory: Path,
        expected_htmls: int = 2,
        include_jpgs: bool = False,
    ) -> None:
        self.subject_identifier = subject_identifier
        self.directory = directory
        self.expected_htmls = expected_htmls
        self.include_jpgs = include_jpgs
        self.files: dict[str, Path] = {}  # filename -> Path
        self.processing = False

    def add_file(self, path: Path) -> None:
        self.files[path.name] = path

    @property
    def jpgs(self) -> list[Path]:
        return [p for p in self.files.values() if p.suffix.lower() in (".jpg", ".jpeg")]

    @property
    def dcms(self) -> list[Path]:
        return [p for p in self.files.values() if p.suffix.lower() == ".dcm"]

    @property
    def htmls(self) -> list[Path]:
        return [p for p in self.files.values() if p.suffix.lower() in (".html", ".htm")]

    @property
    def is_ready(self) -> bool:
        if self.include_jpgs:
            has_enough_images = (
                len(self.jpgs) >= EXPECTED_IMAGES
                or len(self.dcms) >= EXPECTED_IMAGES
            )
        else:
            has_enough_images = len(self.dcms) >= EXPECTED_IMAGES
        return (
            not self.processing
            and has_enough_images
            and len(self.htmls) >= self.expected_htmls
        )


# ===================================================================
# Watchdog handler
# ===================================================================


class CameraWatchDog(FileSystemEventHandler):
    """Detect camera output, resolve metadata via SQLite, upload to API."""

    def __init__(
        self,
        api: RetinopathyApiClient,
        watch_dir: Path,
        processed_dir: Path,
        report_type: str = REPORT_TYPE_COMBINED,
        require_html: bool = True,
        include_jpgs: bool = False,
        subject_folder_pattern: re.Pattern | None = None,
        filename_eye_pattern: re.Pattern | None = None,
    ) -> None:
        self.api = api
        self.watch_dir = watch_dir
        self.processed_dir = processed_dir
        self.report_type = report_type
        self._subject_folder_pattern = subject_folder_pattern or re.compile(
            DEFAULT_SUBJECT_FOLDER_PATTERN,
        )
        self._filename_eye_pattern = filename_eye_pattern or re.compile(
            DEFAULT_FILENAME_EYE_PATTERN,
        )
        self._require_html = require_html
        self._include_jpgs = include_jpgs
        if not require_html:
            self._expected_htmls = 0
        elif report_type == REPORT_TYPE_COMBINED:
            self._expected_htmls = 1
        else:
            self._expected_htmls = 2
        self._subjects: dict[str, SubjectFiles] = {}
        self._lock = threading.Lock()

    # -- folder matching -------------------------------------------------

    def _extract_subject_id(self, folder_name: str) -> str | None:
        """Return subject_identifier if *folder_name* matches the pattern."""
        if folder_name == "processed":
            return None
        return extract_subject_identifier(folder_name, self._subject_folder_pattern)

    # -- startup & periodic sweep --------------------------------------

    def scan_all(self) -> None:
        """Scan every subject directory. Called at startup and by the sweep."""
        for entry in sorted(self.watch_dir.iterdir()):
            if not entry.is_dir():
                continue
            subject_id = self._extract_subject_id(entry.name)
            if subject_id is not None:
                self._scan_subject_dir(entry, subject_id)

    def run_sweep_loop(self) -> None:
        """Background loop that retries failed / late-arriving subjects."""
        while True:
            time.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                self.scan_all()
            except OSError:
                logger.exception("Sweep error")

    # -- watchdog events -----------------------------------------------

    def on_created(self, event) -> None:
        path = Path(event.src_path)

        if event.is_directory:
            # New subject folder appeared
            if path.parent == self.watch_dir:
                subject_id = self._extract_subject_id(path.name)
                if subject_id is not None:
                    logger.info("New subject folder: %s (id=%s)", path.name, subject_id)
            return

        # Only handle files one level below watch_dir (inside subject folders)
        if path.parent.parent != self.watch_dir:
            return
        subject_id = self._extract_subject_id(path.parent.name)
        if subject_id is None:
            return

        self._handle_file(subject_id, path)

    # -- file handling -------------------------------------------------

    def _scan_subject_dir(self, subject_dir: Path, subject_id: str) -> None:
        with self._lock:
            sf = self._subjects.setdefault(
                subject_id,
                SubjectFiles(
                    subject_id,
                    subject_dir,
                    self._expected_htmls,
                    include_jpgs=self._include_jpgs,
                ),
            )
            for p in sorted(subject_dir.iterdir()):
                if p.is_file() and p.name not in sf.files:
                    sf.add_file(p)
            self._try_process(sf)

    def _handle_file(self, subject_id: str, path: Path) -> None:
        ext = path.suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".dcm", ".html", ".htm"):
            logger.debug("Ignoring: %s", path.name)
            return

        if not wait_for_stable(path):
            logger.warning("Timed out waiting for file: %s", path)
            return

        with self._lock:
            sf = self._subjects.setdefault(
                subject_id,
                SubjectFiles(
                    subject_id,
                    path.parent,
                    self._expected_htmls,
                    include_jpgs=self._include_jpgs,
                ),
            )
            sf.add_file(path)
            logger.info(
                "Detected %s/%s  (jpgs=%d, dcms=%d, htmls=%d)",
                subject_id,
                path.name,
                len(sf.jpgs),
                len(sf.dcms),
                len(sf.htmls),
            )
            self._try_process(sf)

    # -- processing ----------------------------------------------------

    def _try_process(self, sf: SubjectFiles) -> None:
        """Start upload if the subject folder is complete.  Caller holds _lock."""
        if not sf.is_ready:
            return
        sf.processing = True
        threading.Thread(
            target=self._process_subject,
            args=(sf,),
            daemon=True,
            name=f"upload-{sf.subject_identifier}",
        ).start()

    def _process_subject(self, sf: SubjectFiles) -> None:
        sid = sf.subject_identifier
        logger.info("=== Processing %s ===", sid)

        # 1. Build upload plan from filename patterns
        upload_plan: list[tuple[str, Path]] = []
        for filename, path in sf.files.items():
            if not self._include_jpgs and path.suffix.lower() in (
                ".jpg",
                ".jpeg",
                ".png",
            ):
                continue
            eye = extract_eye_from_filename(filename, self._filename_eye_pattern)
            try:
                api_type = determine_api_file_type(
                    eye, path.suffix, self.report_type,
                )
            except (
                LateralityRequiredForImagesError,
                LateralityRequiredForDicomsError,
                LateralityRequiredForReportsError,
                UnhandledFileExtensionError,
            ) as exc:
                logger.warning("Skipping %s: %s", filename, exc)
                continue
            upload_plan.append((api_type, path))

        if not upload_plan:
            logger.error("No uploadable files for %s.", sid)
            self._mark_failed(sf)
            return

        logger.info(
            "Upload plan for %s: %s",
            sid,
            [(t, p.name) for t, p in upload_plan],
        )

        # 2. Ping
        if not self.api.ping():
            logger.error("Server unreachable. Will retry %s later.", sid)
            self._mark_failed(sf)
            return

        # 3. Resolve — confirm a CameraSession exists on the server
        resolve_result = self.api.resolve(sid)
        if not resolve_result:
            logger.error("No camera session on server for %s.", sid)
            self._mark_failed(sf)
            return

        logger.info(
            "Session %s confirmed (uploaded=%s)",
            resolve_result["camera_session_id"],
            resolve_result.get("uploaded", []),
        )

        # 4. Upload each file
        for api_type, path in upload_plan:
            if not path.exists():
                logger.error("File disappeared: %s", path)
                self._mark_failed(sf)
                return
            size = path.stat().st_size
            logger.info("Uploading %s  %s (%d bytes) ...", api_type, path.name, size)
            upload_result = self.api.upload_file(sid, api_type, path)
            if upload_result:
                logger.info("  -> stored as %s", upload_result.get("stored_filename"))
            else:
                logger.error("Upload failed: %s/%s", sid, api_type)
                self._mark_failed(sf)
                return

        # 4. Check status
        status_data = self.api.status(sid)
        if status_data:
            logger.info(
                "Status: uploaded=%s  missing=%s  complete=%s",
                status_data.get("uploaded"),
                status_data.get("missing"),
                status_data.get("complete"),
            )

        # 5. Move to processed
        self._move_to_processed(sf)
        logger.info("=== Done: %s ===", sid)

    # -- post-processing -----------------------------------------------

    def _move_to_processed(self, sf: SubjectFiles) -> None:
        ts = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y%m%d_%H%M%S")
        dest = self.processed_dir / f"{sf.subject_identifier}_{ts}"
        try:
            shutil.move(str(sf.directory), str(dest))
        except OSError:
            logger.exception("Failed to move %s", sf.directory)
        else:
            logger.info("Moved %s -> processed/", sf.directory.name)

        with self._lock:
            self._subjects.pop(sf.subject_identifier, None)

    def _mark_failed(self, sf: SubjectFiles) -> None:
        with self._lock:
            sf.processing = False


# ===================================================================
# CLI
# ===================================================================

# Keys accepted in the JSON config file.  The ``db_*`` keys map 1-to-1
# onto ``DBColumnMap`` fields (strip the ``db_`` prefix, keep the rest).
_CONFIG_KEYS_REQUIRED = ("watch_dir", "api_url", "token")
_CONFIG_KEYS_OPTIONAL = (
    "device_id",
    "site_id",
    "log_level",
    "report_type",
    "include_jpgs",
    "require_html",
    "subject_folder_pattern",
    "filename_eye_pattern",
)


def _create_sample_config(watch_dir: Path) -> Path:
    """Write a sample JSON config file with defaults into *watch_dir*.

    Returns the path to the created file.
    """
    sample: dict[str, str] = {
        "watch_dir": str(watch_dir),
        "api_url": "https://edc.example.com",
        "device_id": "",
        "site_id": "",
        "log_level": "INFO",
        "report_type": REPORT_TYPE_COMBINED,
        "subject_folder_pattern": DEFAULT_SUBJECT_FOLDER_PATTERN,
        "filename_eye_pattern": DEFAULT_FILENAME_EYE_PATTERN,
    }
    dest = watch_dir / DEFAULT_CONFIG_FILENAME
    dest.write_text(json.dumps(sample, indent=4) + "\n", encoding="utf-8")
    return dest


def _load_config(path: Path) -> dict:
    """Load a JSON config file."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _resolve(
    cli_value: str | None,
    config: dict,
    key: str,
    default: str = "",
) -> str:
    """CLI flag wins, then config file, then built-in default."""
    if cli_value is not None:
        return cli_value
    return config.get(key, default)


def main() -> None:  # noqa: PLR0915
    # ---- first pass: extract --config/--watch-dir so we can set defaults -
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre.add_argument("--watch-dir", default=None)
    pre_args, _ = pre.parse_known_args()

    config: dict = {}
    if pre_args.config:
        if not pre_args.config.is_file():
            sys.stdout.write(f"Config file not found: {pre_args.config}\n")
            sys.exit(1)
        config = _load_config(pre_args.config)
    else:
        # Auto-discover config in watch-dir (or cwd if --watch-dir omitted).
        watch_base = Path(pre_args.watch_dir or ".").resolve()
        auto_config = watch_base / DEFAULT_CONFIG_FILENAME
        if auto_config.is_file():
            config = _load_config(auto_config)

    # ---- main parser --------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Watch a folder for retinopathy camera files and upload them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Folder layout:\n"
            "  watch-dir/\n"
            "      <subject_identifier>/      subfolder per subject\n"
            "          <uuid>.jpg              left or right eye image\n"
            "          <uuid>.jpg              left or right eye image\n"
            "          <uuid>.html             left or right eye report\n"
            "          <uuid>.html             left or right eye report\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('fundus-camera-watchdog')}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON config file. CLI flags override values from the file.",
    )
    parser.add_argument(
        "--watch-dir",
        type=Path,
        default=None,
        help="Folder the camera writes subject sub-folders to.",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Base URL of the EDC server (e.g. https://edc.example.com).",
    )
    parser.add_argument("--token", default=None, help="DRF authentication token.")
    parser.add_argument("--device-id", default=None, help="Camera device identifier.")
    parser.add_argument("--site-id", default=None, help="Study site identifier.")
    parser.add_argument(
        "--create-config",
        action="store_true",
        default=False,
        help=(
            "Write a sample camera_config.json with defaults into the "
            "--watch-dir folder and exit. Only --watch-dir may be used "
            "with this flag."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--report-type",
        default=None,
        choices=[REPORT_TYPE_PER_EYE, REPORT_TYPE_COMBINED],
        help=(
            "How the camera produces report files. "
            f"'{REPORT_TYPE_PER_EYE}' expects one HTML per eye "
            f"(left_report + right_report); "
            f"'{REPORT_TYPE_COMBINED}' expects a single HTML for both eyes "
            f"(default: {REPORT_TYPE_COMBINED})."
        ),
    )

    parser.add_argument(
        "--include-jpgs",
        action="store_true",
        default=False,
        help=(
            "Include JPEG files in uploads. By default only DICOM "
            "files (and HTML reports) are uploaded."
        ),
    )
    parser.add_argument(
        "--no-require-html",
        action="store_true",
        default=False,
        help=(
            "Do not wait for HTML report files before uploading. "
            "When set, the watchdog triggers on JPEG files alone."
        ),
    )

    parser.add_argument(
        "--subject-folder-pattern",
        default=None,
        help=(
            "Regex to match subject folder names. Use a named group "
            "'subject_identifier' to extract the ID "
            "(e.g. '^(?P<subject_identifier>\\d{3}-\\d{2}-\\d{4}-\\d)_$'). "
            "Folders that don't match are ignored."
        ),
    )
    parser.add_argument(
        "--filename-eye-pattern",
        default=None,
        help=(
            "Regex to extract eye laterality from filenames. Use a named "
            "group 'eye' (e.g. '(?P<eye>OD|OS)'). The extracted value is "
            "normalized: OD/R/RIGHT/RE → right, OS/L/LEFT/LE → left. "
            f"Default: '{DEFAULT_FILENAME_EYE_PATTERN}'."
        ),
    )

    args = parser.parse_args()

    # ---- --create-config mode -----------------------------------------
    if args.create_config:
        # Reject any other flags that were explicitly provided.
        disallowed = {
            "--config": args.config,
            "--api-url": args.api_url,
            "--token": args.token,
            "--device-id": args.device_id,
            "--site-id": args.site_id,
            "--log-level": args.log_level,
            "--report-type": args.report_type,
            "--include-jpgs": args.include_jpgs or None,
            "--no-require-html": args.no_require_html or None,
            "--subject-folder-pattern": args.subject_folder_pattern,
            "--filename-eye-pattern": args.filename_eye_pattern,
        }
        extra = [flag for flag, val in disallowed.items() if val is not None]
        if extra:
            parser.error(
                f"--create-config only accepts --watch-dir. "
                f"Remove: {', '.join(extra)}."
            )
        watch_dir = Path(args.watch_dir or ".").resolve()
        watch_dir.mkdir(parents=True, exist_ok=True)
        dest = _create_sample_config(watch_dir)
        sys.stdout.write(f"Sample config written to {dest}\n")
        sys.exit(0)

    # ---- merge: CLI > config > built-in defaults ----------------------
    log_level = _resolve(args.log_level, config, "log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    watch_dir_raw = _resolve(args.watch_dir, config, "watch_dir", ".")
    api_url = _resolve(args.api_url, config, "api_url")
    token = _resolve(args.token, config, "token") or os.environ.get(ENV_TOKEN, "")

    missing = [
        name
        for name, val in [
            ("--watch-dir", watch_dir_raw),
            ("--api-url", api_url),
            ("--token", token),
        ]
        if not val
    ]
    if missing:
        parser.error(
            f"Missing required settings: {', '.join(missing)}. "
            f"Provide via CLI flags, --config JSON file"
            f", or {ENV_TOKEN} env var (token only).",
        )

    watch_dir = Path(watch_dir_raw).resolve()
    if not watch_dir.is_dir():
        logger.error("Watch directory does not exist: %s", watch_dir)
        sys.exit(1)

    device_id = _resolve(args.device_id, config, "device_id", "") or ""
    site_id = _resolve(args.site_id, config, "site_id", "") or ""
    report_type = _resolve(
        args.report_type,
        config,
        "report_type",
        REPORT_TYPE_COMBINED,
    )
    if report_type not in VALID_REPORT_TYPES:
        parser.error(
            f"Invalid report_type: {report_type!r}. "
            f"Must be one of: {', '.join(sorted(VALID_REPORT_TYPES))}.",
        )

    # CLI flag --include-jpgs wins, then config, then default (False)
    include_jpgs = args.include_jpgs or config.get("include_jpgs", False)

    # CLI flag --no-require-html wins, then config, then default (True)
    require_html = not args.no_require_html
    if require_html and not config.get("require_html", True):
        require_html = False

    subject_folder_pattern_raw = _resolve(
        args.subject_folder_pattern,
        config,
        "subject_folder_pattern",
        DEFAULT_SUBJECT_FOLDER_PATTERN,
    )
    try:
        subject_folder_pattern = compile_subject_folder_pattern(
            subject_folder_pattern_raw,
        )
    except InvalidSubjectFolderPatternError:
        logger.exception("")
        sys.exit(1)

    filename_eye_pattern_raw = _resolve(
        args.filename_eye_pattern,
        config,
        "filename_eye_pattern",
        DEFAULT_FILENAME_EYE_PATTERN,
    )
    try:
        filename_eye_pattern = compile_filename_eye_pattern(
            filename_eye_pattern_raw,
        )
    except InvalidSubjectFolderPatternError:
        logger.exception("")
        sys.exit(1)

    processed_dir = watch_dir / "processed"
    processed_dir.mkdir(exist_ok=True)

    api = RetinopathyApiClient(
        base_url=api_url,
        token=token,
        device_id=device_id,
        site_id=site_id,
    )

    logger.info("Pinging %s ...", api_url)
    if api.ping():
        logger.info("Server OK.")
    else:
        logger.warning("Initial ping failed — continuing anyway.")

    logger.info("Report type: %s", report_type)
    if include_jpgs:
        logger.info("JPEG files: included")
    if not require_html:
        logger.info("HTML reports: not required")
    if subject_folder_pattern_raw != DEFAULT_SUBJECT_FOLDER_PATTERN:
        logger.info("Subject folder pattern: %s", subject_folder_pattern_raw)
    if filename_eye_pattern_raw != DEFAULT_FILENAME_EYE_PATTERN:
        logger.info("Filename eye pattern: %s", filename_eye_pattern_raw)
    handler = CameraWatchDog(
        api,
        watch_dir,
        processed_dir,
        report_type,
        require_html=require_html,
        include_jpgs=include_jpgs,
        subject_folder_pattern=subject_folder_pattern,
        filename_eye_pattern=filename_eye_pattern,
    )
    handler.scan_all()

    sweep = threading.Thread(target=handler.run_sweep_loop, daemon=True, name="sweep")
    sweep.start()

    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()
    logger.info("Watching %s  (Ctrl+C to stop)", watch_dir)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down ...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
