#!/usr/bin/env python
"""Watch a folder for retinopathy camera output and upload to the API.

Folder structure
----------------
The camera organises output into one subfolder per subject::

    watch_dir/
        105-10-0989-3/
            a1b2c3d4.jpg        <- left or right eye (determined by DB)
            e5f6a7b8.jpg        <- left or right eye (determined by DB)
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
import json
import logging
import re
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
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

# ---------------------------------------------------------------------------
# Expected file counts per subject folder
# ---------------------------------------------------------------------------
EXPECTED_JPGS = 2

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


class LateralityRequiredForImagesError(Exception):
    def __init__(self) -> None:
        super().__init__("Eye laterality is required for image files.")


class LateralityRequiredForReportsError(Exception):
    def __init__(self) -> None:
        super().__init__("Eye laterality is required for per-eye report files.")


class UnhandledFileExtensionError(Exception):
    def __init__(self, extension: str) -> None:
        super().__init__(f"Unexpected extension. Got {extension}.")


class InvalidDBColumnError(Exception):
    def __init__(self, value: str, label: str) -> None:
        super().__init__(f"Invalid SQL identifier for {label}: {value!r}")


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
        ".html": "text/html",
        ".htm": "text/html",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


# ===================================================================
# Camera SQLite database — column mapping
# ===================================================================

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_sql_identifier(value: str, label: str) -> str:
    if not _SQL_IDENTIFIER_RE.match(value):
        raise InvalidDBColumnError(value, label)
    return value


@dataclass(frozen=True)
class DBColumnMap:
    """Maps logical field names to actual SQLite table/column names.

    Every field has a placeholder default.  Pass the real names from
    the camera's schema via CLI flags (``--db-*``) or by constructing
    this dataclass directly.
    """

    # -- patients / demographics table --
    patient_table: str = "patients"
    patient_subject_id: str = "subject_identifier"
    patient_initials: str = "initials"
    patient_sex: str = "sex"
    patient_age: str = "age"

    # -- images / file-mapping table --
    image_table: str = "images"
    image_subject_id: str = "subject_identifier"
    image_filename: str = "filename"
    image_eye: str = "eye"

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            _validate_sql_identifier(getattr(self, field), field)


# ===================================================================
# Camera SQLite database
# ===================================================================


class CameraDB:
    """Read-only interface to the camera's SQLite database.

    All table and column names are supplied via :class:`DBColumnMap`,
    so no source edits are needed when the camera schema is discovered.
    """

    def __init__(self, db_path: Path, columns: DBColumnMap | None = None) -> None:
        self.db_path = db_path
        self.columns = columns or DBColumnMap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    # -- demographics --------------------------------------------------

    def get_demographics(self, subject_identifier: str) -> dict | None:
        """Return ``{"initials": ..., "sex": ..., "age": ...}`` or ``None``."""
        c = self.columns
        sql = (
            f"SELECT {c.patient_initials}, {c.patient_sex}, {c.patient_age}"
            f"  FROM {c.patient_table}"
            f" WHERE {c.patient_subject_id} = ?"
            f" LIMIT 1"
        )
        conn = self._connect()
        try:
            row = conn.execute(sql, (subject_identifier,)).fetchone()
            if row:
                return {
                    "initials": row[c.patient_initials] or "",
                    "sex": row[c.patient_sex] or "",
                    "age": row[c.patient_age],
                }
        finally:
            conn.close()
        return None

    # -- eye laterality ------------------------------------------------

    def get_eye_for_file(self, subject_identifier: str, filename: str) -> str | None:
        """Return ``'left'`` or ``'right'`` for *filename*, or ``None``."""
        c = self.columns
        sql = (
            f"SELECT {c.image_eye}"
            f"  FROM {c.image_table}"
            f" WHERE {c.image_subject_id} = ?"
            f"   AND {c.image_filename} = ?"
            f" LIMIT 1"
        )
        conn = self._connect()
        try:
            row = conn.execute(sql, (subject_identifier, filename)).fetchone()
            if row:
                return self._normalise_eye(row[c.image_eye])
        finally:
            conn.close()
        return None

    def get_file_map(
        self,
        subject_identifier: str,
        filenames: list[str],
    ) -> dict[str, str]:
        """Batch lookup: ``{filename: 'left'|'right'}`` for every file."""
        if not filenames:
            return {}
        c = self.columns
        placeholders = ",".join("?" * len(filenames))
        sql = (
            f"SELECT {c.image_filename}, {c.image_eye}"
            f"  FROM {c.image_table}"
            f" WHERE {c.image_subject_id} = ?"
            f"   AND {c.image_filename} IN ({placeholders})"
        )
        params = [subject_identifier, *filenames]
        result: dict[str, str] = {}
        conn = self._connect()
        try:
            for row in conn.execute(sql, params):
                eye = self._normalise_eye(row[c.image_eye])
                if eye:
                    result[row[c.image_filename]] = eye
        finally:
            conn.close()
        return result

    @staticmethod
    def _normalise_eye(raw: str | None) -> str | None:
        if not raw:
            return None
        val = raw.strip().upper()
        if val in EYE_LEFT_VALUES:
            return "left"
        if val in EYE_RIGHT_VALUES:
            return "right"
        logger.warning("Unknown eye value: %r", raw)
        return None


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

    def resolve(
        self,
        subject_identifier: str,
        initials: str,
        sex: str,
        age: int | None = None,
    ) -> dict | None:
        payload: dict = {
            "subject_identifier": subject_identifier,
            "initials": initials,
            "sex": sex,
        }
        if age is not None:
            payload["age"] = age
        if self.device_id:
            payload["device_id"] = self.device_id
        if self.site_id:
            payload["site_id"] = self.site_id

        r = self._post_json(
            f"{self.base_url}/resolve/",
            payload,
            label=f"resolve({subject_identifier})",
        )
        if r is not None and r.status_code in (STATUS_CODE_OK, STATUS_CODE_CREATED):
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
        session_id: str,
    ) -> dict | None:
        checksum = sha256_file(file_path)
        capture_dt = datetime.now(UTC).isoformat()
        url = (
            f"{self.base_url}/{subject_identifier}/{file_type}/?session_id={session_id}"
        )
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
    ) -> None:
        self.subject_identifier = subject_identifier
        self.directory = directory
        self.expected_htmls = expected_htmls
        self.files: dict[str, Path] = {}  # filename -> Path
        self.processing = False

    def add_file(self, path: Path) -> None:
        self.files[path.name] = path

    @property
    def jpgs(self) -> list[Path]:
        return [p for p in self.files.values() if p.suffix.lower() in (".jpg", ".jpeg")]

    @property
    def htmls(self) -> list[Path]:
        return [p for p in self.files.values() if p.suffix.lower() in (".html", ".htm")]

    @property
    def is_ready(self) -> bool:
        return (
            not self.processing
            and len(self.jpgs) >= EXPECTED_JPGS
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
        camera_db: CameraDB,
        watch_dir: Path,
        processed_dir: Path,
        report_type: str = REPORT_TYPE_COMBINED,
    ) -> None:
        self.api = api
        self.camera_db = camera_db
        self.watch_dir = watch_dir
        self.processed_dir = processed_dir
        self.report_type = report_type
        self._expected_htmls = 1 if report_type == REPORT_TYPE_COMBINED else 2
        self._subjects: dict[str, SubjectFiles] = {}
        self._lock = threading.Lock()

    # -- startup & periodic sweep --------------------------------------

    def scan_all(self) -> None:
        """Scan every subject directory. Called at startup and by the sweep."""
        for entry in sorted(self.watch_dir.iterdir()):
            if entry.is_dir() and entry.name != "processed":
                self._scan_subject_dir(entry)

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
            if path.parent == self.watch_dir and path.name != "processed":
                logger.info("New subject folder: %s", path.name)
            return

        # Only handle files one level below watch_dir (inside subject folders)
        if path.parent.parent != self.watch_dir:
            return
        if path.parent.name == "processed":
            return

        self._handle_file(path.parent.name, path)

    # -- file handling -------------------------------------------------

    def _scan_subject_dir(self, subject_dir: Path) -> None:
        subject_id = subject_dir.name
        with self._lock:
            sf = self._subjects.setdefault(
                subject_id,
                SubjectFiles(subject_id, subject_dir, self._expected_htmls),
            )
            for p in sorted(subject_dir.iterdir()):
                if p.is_file() and p.name not in sf.files:
                    sf.add_file(p)
            self._try_process(sf)

    def _handle_file(self, subject_id: str, path: Path) -> None:
        ext = path.suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".html", ".htm"):
            logger.debug("Ignoring: %s", path.name)
            return

        if not wait_for_stable(path):
            logger.warning("Timed out waiting for file: %s", path)
            return

        with self._lock:
            sf = self._subjects.setdefault(
                subject_id,
                SubjectFiles(subject_id, path.parent, self._expected_htmls),
            )
            sf.add_file(path)
            logger.info(
                "Detected %s/%s  (jpgs=%d, htmls=%d)",
                subject_id,
                path.name,
                len(sf.jpgs),
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

    def _process_subject(self, sf: SubjectFiles) -> None:  # noqa: C901 PLR0912 PLR0915
        sid = sf.subject_identifier
        logger.info("=== Processing %s ===", sid)

        # 1. Query camera DB for demographics
        demographics = self.camera_db.get_demographics(sid)
        if not demographics:
            logger.error("No demographics in camera DB for %s.", sid)
            self._mark_failed(sf)
            return

        # 2. Query camera DB for eye laterality of image files (and
        #    report files when report_type is per_eye)
        if self.report_type == REPORT_TYPE_COMBINED:
            # Only images need eye mapping; reports upload as "report"
            image_filenames = [
                fn
                for fn, p in sf.files.items()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png")
            ]
            file_map = self.camera_db.get_file_map(sid, image_filenames)
        else:
            file_map = self.camera_db.get_file_map(sid, list(sf.files.keys()))

        unmapped_images = [
            fn
            for fn in sf.files
            if fn not in file_map
            and sf.files[fn].suffix.lower() in (".jpg", ".jpeg", ".png")
        ]
        if unmapped_images:
            logger.warning(
                "Camera DB has no eye mapping for images: %s (subject=%s). "
                "Skipping these.",
                unmapped_images,
                sid,
            )

        # Build upload plan: [(api_file_type, path), ...]
        upload_plan: list[tuple[str, Path]] = []

        # Images — always need eye mapping
        for filename, eye in file_map.items():
            path = sf.files[filename]
            try:
                api_type = determine_api_file_type(eye, path.suffix, self.report_type)
            except (
                LateralityRequiredForImagesError,
                LateralityRequiredForReportsError,
                UnhandledFileExtensionError,
            ) as exc:
                logger.warning("Skipping %s: %s", filename, exc)
                continue
            upload_plan.append((api_type, path))

        # Reports — per_eye uses eye mapping (already in file_map above),
        #           combined assigns "report" directly
        if self.report_type == REPORT_TYPE_COMBINED:
            for html_path in sf.htmls:
                upload_plan.append(("report", html_path))  # noqa: PERF401
        else:
            # per_eye HTML files were included in file_map above; warn
            # about any that had no eye mapping
            unmapped_reports = [
                fn
                for fn in sf.files
                if fn not in file_map
                and sf.files[fn].suffix.lower() in (".html", ".htm")
            ]
            if unmapped_reports:
                logger.warning(
                    "Camera DB has no eye mapping for reports: %s (subject=%s). "
                    "Skipping these.",
                    unmapped_reports,
                    sid,
                )

        if not upload_plan:
            logger.error("No uploadable files for %s.", sid)
            self._mark_failed(sf)
            return

        logger.info(
            "Upload plan for %s: %s",
            sid,
            [(t, p.name) for t, p in upload_plan],
        )

        # 3. Ping
        if not self.api.ping():
            logger.error("Server unreachable. Will retry %s later.", sid)
            self._mark_failed(sf)
            return

        # 4. Resolve
        result = self.api.resolve(
            subject_identifier=sid,
            initials=demographics.get("initials", ""),
            sex=demographics.get("sex", ""),
            age=demographics.get("age"),
        )
        if not result:
            logger.error("Resolve failed for %s.", sid)
            self._mark_failed(sf)
            return

        session_id = result["session_id"]
        label = "reactivated" if result.get("reactivated") else "new"
        logger.info("Session %s (%s)", session_id, label)

        # 5. Upload each file
        for api_type, path in upload_plan:
            if not path.exists():
                logger.error("File disappeared: %s", path)
                self._mark_failed(sf)
                return
            size = path.stat().st_size
            logger.info("Uploading %s  %s (%d bytes) ...", api_type, path.name, size)
            upload_result = self.api.upload_file(sid, api_type, path, session_id)
            if upload_result:
                logger.info("  -> stored as %s", upload_result.get("stored_filename"))
            else:
                logger.error("Upload failed: %s/%s", sid, api_type)
                self._mark_failed(sf)
                return

        # 6. Check status
        status_data = self.api.status(sid)
        if status_data:
            logger.info(
                "Status: uploaded=%s  missing=%s  complete=%s",
                status_data.get("uploaded"),
                status_data.get("missing"),
                status_data.get("complete"),
            )

        # 7. Move to processed
        self._move_to_processed(sf)
        logger.info("=== Done: %s ===", sid)

    # -- post-processing -----------------------------------------------

    def _move_to_processed(self, sf: SubjectFiles) -> None:
        ts = datetime.now(tz=ZoneInfo("utc")).strftime("%Y%m%d_%H%M%S")
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
_CONFIG_KEYS_REQUIRED = ("watch_dir", "db_path", "api_url", "token")
_CONFIG_KEYS_OPTIONAL = ("device_id", "site_id", "log_level", "report_type")
_CONFIG_KEYS_DB = (
    "db_patient_table",
    "db_patient_subject_id",
    "db_patient_initials",
    "db_patient_sex",
    "db_patient_age",
    "db_image_table",
    "db_image_subject_id",
    "db_image_filename",
    "db_image_eye",
)


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
    # ---- first pass: extract --config so we can set defaults ----------
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre_args, _ = pre.parse_known_args()

    config: dict = {}
    if pre_args.config:
        if not pre_args.config.is_file():
            sys.stdout.write(f"Config file not found: {pre_args.config}\n")
            sys.exit(1)
        config = _load_config(pre_args.config)

    # ---- main parser --------------------------------------------------
    col_defaults = DBColumnMap()

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
        "--db-path",
        type=Path,
        default=None,
        help="Path to the camera's SQLite database.",
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

    db_group = parser.add_argument_group(
        "database column mapping",
        "Map logical fields to the camera's actual SQLite table/column names. "
        "All settable via --config JSON file.",
    )
    db_group.add_argument(
        "--db-patient-table",
        default=None,
        help=f"Patient demographics table (default: {col_defaults.patient_table}).",
    )
    db_group.add_argument(
        "--db-patient-subject-id",
        default=None,
        help=(
            "Column for subject identifier in patient table "
            f"(default: {col_defaults.patient_subject_id})."
        ),
    )
    db_group.add_argument(
        "--db-patient-initials",
        default=None,
        help=f"Column for initials (default: {col_defaults.patient_initials}).",
    )
    db_group.add_argument(
        "--db-patient-sex",
        default=None,
        help=f"Column for sex (default: {col_defaults.patient_sex}).",
    )
    db_group.add_argument(
        "--db-patient-age",
        default=None,
        help=f"Column for age (default: {col_defaults.patient_age}).",
    )
    db_group.add_argument(
        "--db-image-table",
        default=None,
        help=f"Image file mapping table (default: {col_defaults.image_table}).",
    )
    db_group.add_argument(
        "--db-image-subject-id",
        default=None,
        help=(
            "Column for subject identifier in image table "
            f"(default: {col_defaults.image_subject_id})."
        ),
    )
    db_group.add_argument(
        "--db-image-filename",
        default=None,
        help=f"Column for filename (default: {col_defaults.image_filename}).",
    )
    db_group.add_argument(
        "--db-image-eye",
        default=None,
        help=f"Column for eye laterality (default: {col_defaults.image_eye}).",
    )

    args = parser.parse_args()

    # ---- merge: CLI > config > built-in defaults ----------------------
    log_level = _resolve(args.log_level, config, "log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    watch_dir_raw = _resolve(args.watch_dir, config, "watch_dir")
    db_path_raw = _resolve(args.db_path, config, "db_path")
    api_url = _resolve(args.api_url, config, "api_url")
    token = _resolve(args.token, config, "token")

    missing = [
        name
        for name, val in [
            ("--watch-dir", watch_dir_raw),
            ("--db-path", db_path_raw),
            ("--api-url", api_url),
            ("--token", token),
        ]
        if not val
    ]
    if missing:
        parser.error(
            f"Missing required settings: {', '.join(missing)}. "
            "Provide via CLI flags or --config JSON file.",
        )

    watch_dir = Path(watch_dir_raw).resolve()
    if not watch_dir.is_dir():
        logger.error("Watch directory does not exist: %s", watch_dir)
        sys.exit(1)

    db_path = Path(db_path_raw).resolve()
    if not db_path.is_file():
        logger.error("Camera database not found: %s", db_path)
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

    try:
        column_map = DBColumnMap(
            patient_table=_resolve(
                args.db_patient_table,
                config,
                "db_patient_table",
                col_defaults.patient_table,
            ),
            patient_subject_id=_resolve(
                args.db_patient_subject_id,
                config,
                "db_patient_subject_id",
                col_defaults.patient_subject_id,
            ),
            patient_initials=_resolve(
                args.db_patient_initials,
                config,
                "db_patient_initials",
                col_defaults.patient_initials,
            ),
            patient_sex=_resolve(
                args.db_patient_sex,
                config,
                "db_patient_sex",
                col_defaults.patient_sex,
            ),
            patient_age=_resolve(
                args.db_patient_age,
                config,
                "db_patient_age",
                col_defaults.patient_age,
            ),
            image_table=_resolve(
                args.db_image_table,
                config,
                "db_image_table",
                col_defaults.image_table,
            ),
            image_subject_id=_resolve(
                args.db_image_subject_id,
                config,
                "db_image_subject_id",
                col_defaults.image_subject_id,
            ),
            image_filename=_resolve(
                args.db_image_filename,
                config,
                "db_image_filename",
                col_defaults.image_filename,
            ),
            image_eye=_resolve(
                args.db_image_eye,
                config,
                "db_image_eye",
                col_defaults.image_eye,
            ),
        )
    except InvalidDBColumnError:
        logger.exception("")
        sys.exit(1)

    logger.info("DB column mapping: %s", column_map)

    processed_dir = watch_dir / "processed"
    processed_dir.mkdir(exist_ok=True)

    camera_db = CameraDB(db_path, columns=column_map)
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
    handler = CameraWatchDog(api, camera_db, watch_dir, processed_dir, report_type)
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
