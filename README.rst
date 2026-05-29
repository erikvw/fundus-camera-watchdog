|pypi| |actions| |codecov| |downloads| |clinicedc|

Fundus Camera Watchdog
======================

``fundus-camera-watchdog`` monitors a folder on the fundus camera workstation and uploads files to a CLINICEDC project using the ``edc-retinopathy`` API.

It is designed to run on the camera's workstation. When the camera finishes an examination and writes files to disk, the watchdog detects them, resolves the subject against the CLINICEDC server, uploads each file, and moves the completed folder to an archive folder.

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

Because filenames are random UUIDs, the watchdog queries the camera's SQLite database to determine which file belongs to which eye.

Processing is triggered once a subject folder contains the expected number
of files:

- **Combined** (default): at least **2 JPEG** and **1 HTML** file (3 files total).
- **Per-eye**: at least **2 JPEG** and **2 HTML** files (4 files total).

Quick start
-----------

1. Generate a sample config in the camera output folder::

    cd C:\RetCamOutput
    uvx fundus-camera-watchdog --create-config

   This creates ``fundus_camera_watchdog.json`` in the current directory
   with sensible defaults. If the camera output folder is elsewhere, pass
   ``--watch-dir``::

    uvx fundus-camera-watchdog --create-config --watch-dir C:\RetCamOutput

2. Edit the generated config to fill in ``db_path``, ``api_url``,
   ``device_id``, etc.

3. Set the API token as an environment variable (recommended — keeps the
   token out of the config file)::

    REM Windows (persists across reboots)
    setx FUNDUS_CAMERA_WATCHDOG_TOKEN "YOUR_DRF_TOKEN"

4. Start the watchdog::

    cd C:\RetCamOutput
    uvx fundus-camera-watchdog

Configuration
-------------

Settings are resolved in the following order (highest priority first):

1. CLI flags (e.g. ``--api-url``)
2. JSON config file
3. ``FUNDUS_CAMERA_WATCHDOG_TOKEN`` environment variable (token only)
4. Built-in defaults

Config file discovery
~~~~~~~~~~~~~~~~~~~~~

If ``--config`` is not provided, the watchdog looks for
``fundus_camera_watchdog.json`` in the watch directory (or the current
directory if ``--watch-dir`` is also omitted). If found, it is loaded
automatically. Use ``--create-config`` to generate a sample config.

If ``--watch-dir`` is not provided and there is no ``watch_dir`` entry in the
config file, the watchdog defaults to the current directory.

Example ``fundus_camera_watchdog.json``::

    {
        "watch_dir": "C:\\RetCamOutput",
        "db_path": "C:\\RetCamOutput\\camera.db",
        "api_url": "https://edc.example.com",
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

Required settings
~~~~~~~~~~~~~~~~~

``watch_dir``
    Folder the camera writes subject subfolders to. Defaults to the current
    directory if not provided.

``db_path``
    Path to the camera's SQLite database.

``api_url``
    Base URL of the EDC server (e.g. ``https://edc.example.com``).

``token``
    DRF authentication token for the camera user. Can be provided via:

    - ``--token`` CLI flag
    - ``token`` key in the config file
    - ``FUNDUS_CAMERA_WATCHDOG_TOKEN`` environment variable (recommended)

Optional settings
~~~~~~~~~~~~~~~~~

``device_id``
    Identifier for this camera (sent to the server with each session).

``site_id``
    Study site identifier.

``report_type``
    How the camera writes its analysis reports. ``combined`` (default) means
    a single HTML file covers both eyes; ``per_eye`` means one HTML per eye.
    This controls how many files the watchdog expects before triggering an
    upload (3 for combined, 4 for per_eye).

``log_level``
    One of ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``.

Database column mapping
~~~~~~~~~~~~~~~~~~~~~~~

These keys tell the watchdog which tables and columns to query in the
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

The watchdog normalises eye values automatically.  All of the following
are recognised:

- **Left eye**: ``L``, ``LE``, ``OS``, ``LEFT``
- **Right eye**: ``R``, ``RE``, ``OD``, ``RIGHT``

Usage
-----

Run from the camera output folder (auto-discovers ``fundus_camera_watchdog.json``)::

    cd C:\RetCamOutput
    uvx fundus-camera-watchdog

With an explicit config file::

    uvx fundus-camera-watchdog --config C:\path\to\fundus_camera_watchdog.json

CLI flags override any value from the config file::

    uvx fundus-camera-watchdog --log-level DEBUG --report-type per_eye

Without a config file (all flags on the command line)::

    uvx fundus-camera-watchdog ^
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

The watchdog runs continuously and performs the following for each
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

- **Startup scan** — on (re)start the watchdog scans all existing
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

Creating a test watch directory
-------------------------------

To try the watchdog without a real camera, create a sample watch directory
with dummy files that pass the API's content validation.

1. Create the folder structure::

    mkdir -p /tmp/watchdog_test/105-10-0001-2
    cd /tmp/watchdog_test

2. Generate a sample config::

    uvx fundus-camera-watchdog --create-config

3. Create two dummy JPEG images (1x1 pixel)::

    uv run --with pillow python -c "
    from PIL import Image
    import uuid
    for _ in range(2):
        img = Image.new('RGB', (1, 1), color='red')
        img.save(f'105-10-0001-2/{uuid.uuid4().hex}.jpg', 'JPEG')
    "

4. Create a dummy HTML report::

    uv run python -c "
    import uuid
    from pathlib import Path
    name = f'105-10-0001-2/{uuid.uuid4().hex}.html'
    Path(name).write_text('<!doctype html><html><body>Report</body></html>')
    "

5. Create the camera SQLite database with matching file entries::

    uv run python -c "
    import sqlite3, os
    conn = sqlite3.connect('camera.db')
    conn.execute('''CREATE TABLE patients (
        subject_identifier TEXT PRIMARY KEY,
        initials TEXT, sex TEXT, age INTEGER)''')
    conn.execute('INSERT INTO patients VALUES (?, ?, ?, ?)',
        ('105-10-0001-2', 'JD', 'M', 35))
    conn.execute('''CREATE TABLE images (
        subject_identifier TEXT, filename TEXT, eye TEXT)''')
    files = sorted(os.listdir('105-10-0001-2'))
    jpgs = [f for f in files if f.endswith('.jpg')]
    htmls = [f for f in files if f.endswith('.html')]
    rows = []
    for jpg, eye in zip(jpgs, ['L', 'R']):
        rows.append(('105-10-0001-2', jpg, eye))
    for html, eye in zip(htmls, ['L', 'R']):
        rows.append(('105-10-0001-2', html, eye))
    conn.executemany('INSERT INTO images VALUES (?, ?, ?)', rows)
    conn.commit()
    conn.close()
    print(f'Created camera.db with {len(rows)} file entries')
    "

6. Edit ``fundus_camera_watchdog.json`` to set ``db_path``, ``api_url``,
   and token, then start the watchdog::

    uvx fundus-camera-watchdog

The resulting folder should look like::

    /tmp/watchdog_test/
        fundus_camera_watchdog.json
        camera.db
        105-10-0001-2/
            a1b2c3d4...jpg
            e5f6a7b8...jpg
            c9d0e1f2...html

.. |pypi| image:: https://img.shields.io/pypi/v/fundus-camera-watchdog.svg
    :target: https://pypi.python.org/pypi/fundus-camera-watchdog

.. |actions| image:: https://github.com/erikvw/fundus-camera-watchdog/actions/workflows/build.yml/badge.svg
  :target: https://github.com/erikvw/fundus-camera-watchdog/actions/workflows/build.yml

.. |codecov| image:: https://codecov.io/gh/erikvw/fundus-camera-watchdog/branch/develop/graph/badge.svg
  :target: https://codecov.io/gh/erikvw/fundus-camera-watchdog

.. |downloads| image:: https://pepy.tech/badge/fundus-camera-watchdog
   :target: https://pepy.tech/project/fundus-camera-watchdog

.. |clinicedc| image:: https://img.shields.io/badge/framework-Clinic_EDC-green
   :alt:Made with clinicedc
   :target: https://github.com/clinicedc
