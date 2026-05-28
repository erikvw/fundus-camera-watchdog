Camera folder watcher
=====================

``camera_watchdog.py`` monitors a folder on the retinopathy camera workstation
for new subject output and uploads the files to the ``edc-retinopathy`` API automatically.

It is designed to run continuously on a **Windows** machine alongside the camera software. When the camera finishes an examination and writes files to disk, the watcher detects them, resolves the subject against the EDC server, uploads each file, and moves the completed folder to an archive.

Prerequisites
-------------

1. A running CLINICEDC server with ``edc-retinopathy`` installed and an API token created.

2. The camera software must write its output into the watched folder using the expected layout (see `Folder layout`_ below).

3. The camera's SQLite database must be accessible from the workstation (typically a local file).

Folder layout
-------------

The camera creates one subfolder per subject, named with the subject identifier. Inside each subfolder are UUID-named files.

**Combined report** (default — one HTML covers both eyes)::

    C:\RetCamOutput\
        105-10-0989-3\
            a1b2c3d4.jpg          <- eye image (left or right)
            e5f6a7b8.jpg          <- eye image (left or right)
            c9d0e1f2.html         <- combined report (both eyes)
        105-10-0001-2\
            ...

**Per-eye report** (one HTML per eye)::

    C:\RetCamOutput\
        105-10-0989-3\
            a1b2c3d4.jpg          <- eye image (left or right)
            e5f6a7b8.jpg          <- eye image (left or right)
            c9d0e1f2.html         <- eye report (left or right)
            f3a4b5c6.html         <- eye report (left or right)
        105-10-0001-2\
            ...

Because filenames are random UUIDs, the watcher queries the camera's SQLite database to determine which file belongs to which eye.

Processing is triggered once a subject folder contains the expected number
of files:

- **Combined** (default): at least **2 JPEG** and **1 HTML** file (3 files total).
- **Per-eye**: at least **2 JPEG** and **2 HTML** files (4 files total).

Configuration
-------------

All settings live in a single JSON file. Create ``camera_config.json``::

    {
        "watch_dir": "C:\\RetCamOutput",
        "db_path": "C:\\RetCamOutput\\camera.db",
        "api_url": "https://edc.example.com",
        "token": "YOUR_DRF_TOKEN",
        "device_id": "RET-CAM-001",
        "site_id": "40",

        "db_patient_table": "patients",
        "db_patient_subject_id": "subject_identifier",
        "db_patient_initials": "initials",
        "db_patient_sex": "sex",
        "db_patient_age": "age",

        "db_image_table": "images",
        "db_image_subject_id": "subject_identifier",
        "db_image_filename": "filename",
        "db_image_eye": "eye",

        "report_type": "combined"
    }

Required keys
~~~~~~~~~~~~~

``watch_dir``
    Folder the camera writes subject subfolders to.

``db_path``
    Path to the camera's SQLite database.

``api_url``
    Base URL of the EDC server (e.g. ``https://edc.example.com``).

``token``
    DRF authentication token for the camera user.

Optional keys
~~~~~~~~~~~~~

``device_id``
    Identifier for this camera (sent to the server with each session).

``site_id``
    Study site identifier.

``report_type``
    How the camera writes its analysis reports. ``combined`` (default) means
    a single HTML file covers both eyes; ``per_eye`` means one HTML per eye.
    This controls how many files the watcher expects before triggering an
    upload (3 for combined, 4 for per_eye).

``log_level``
    One of ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``.

Database column mapping
~~~~~~~~~~~~~~~~~~~~~~~

These keys tell the watcher which tables and columns to query in the
camera's SQLite database. Adjust them to match your camera vendor's
actual schema.

+-----------------------------+------------------------------------------+--------------------------+
| Config key                  | Purpose                                  | Default                  |
+=============================+==========================================+==========================+
| ``db_patient_table``        | Table containing patient demographics    | ``patients``             |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_patient_subject_id``   | Column for subject identifier            | ``subject_identifier``   |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_patient_initials``     | Column for initials                      | ``initials``             |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_patient_sex``          | Column for sex (M/F)                     | ``sex``                  |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_patient_age``          | Column for age in years                  | ``age``                  |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_image_table``          | Table mapping files to eyes              | ``images``               |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_image_subject_id``     | Column for subject identifier            | ``subject_identifier``   |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_image_filename``       | Column for UUID filename                 | ``filename``             |
+-----------------------------+------------------------------------------+--------------------------+
| ``db_image_eye``            | Column for eye laterality                | ``eye``                  |
+-----------------------------+------------------------------------------+--------------------------+

The watcher normalises eye values automatically.  All of the following
are recognised:

- **Left eye**: ``L``, ``LE``, ``OS``, ``LEFT``
- **Right eye**: ``R``, ``RE``, ``OD``, ``RIGHT``

Usage
-----

With a config file (recommended)::

    uv run camera_watchdog.py --config camera_config.json

CLI flags override any value from the config file::

    uv run camera_watchdog.py --config camera_config.json --log-level DEBUG --report-type per_eye

Without a config file (all flags on the command line)::

    uv run camera_watchdog.py ^
        --watch-dir C:\RetCamOutput ^
        --db-path C:\RetCamOutput\camera.db ^
        --api-url https://edc.example.com ^
        --token YOUR_TOKEN ^
        --device-id RET-CAM-001 ^
        --db-patient-table Exams ^
        --db-patient-subject-id patient_code ^
        --db-image-table CapturedFiles ^
        --db-image-eye laterality

Stop with ``Ctrl+C``.

What it does
------------

The watcher runs continuously and performs the following for each
subject:

1. **Detect** — watches for new files in subject subfolders using
   filesystem events (``watchdog``), plus a periodic sweep every 60 seconds as a safety net.

2. **Wait** — each file is given up to 30 seconds to stabilise (stop growing) before being registered, so half-written files from the camera are not picked up prematurely.

3. **Query camera DB** — retrieves the subject's demographics
   (initials, sex, age) and determines which JPEG is the left eye
   and which is the right. In per-eye mode, the HTML reports are also
   mapped to eyes; in combined mode the HTML is uploaded without eye
   mapping.

4. **Resolve** — ``POST /api/retinopathy/resolve/`` validates the
   subject against ``RegisteredSubject`` on the CLINICEDC server and
   creates (or reactivates) a session.

5. **Upload** — sends each file to the correct API endpoint:

   - ``*.jpg`` + left eye  -> ``POST .../left/``
   - ``*.jpg`` + right eye -> ``POST .../right/``
   - ``*.html`` (combined) -> ``POST .../report/``
   - ``*.html`` + left eye (per_eye) -> ``POST .../left_report/``
   - ``*.html`` + right eye (per_eye) -> ``POST .../right_report/``

   Each upload includes a SHA-256 checksum for integrity verification.

6. **Verify** — ``GET .../status/`` confirms the session received all expected files.

7. **Archive** — the entire subject folder is moved to
   ``<watch-dir>/processed/<subject_id>_<timestamp>/``.

Error handling
--------------

- **Retries** — every API call is retried up to 3 times with a
  5-second delay between attempts.

- **Failed subjects** — if any step fails (server unreachable,
  validation error, upload failure), the subject is marked as
  unprocessed and will be retried on the next 60-second sweep.

- **Startup scan** — on (re)start the watcher scans all existing
  subject folders, so a restart after a crash picks up where it left off.

- **Thread safety** — file detection and upload run on separate
  threads with proper locking, so multiple subjects can be uploaded concurrently.

Inspecting the camera database
------------------------------

To discover your camera's actual table and column names, open the
SQLite database and list its schema::

    sqlite3 camera.db
    .tables
    .schema patients
    .schema images

Then update the ``db_*`` keys in your config file to match.
