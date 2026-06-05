|pypi| |actions| |codecov| |downloads| |clinicedc|

Fundus Camera Watchdog
======================

``fundus-camera-watchdog`` monitors a folder on the fundus camera
workstation and uploads files to a clinicedc project using the
``edc-retinopathy`` API.

It runs on the camera's workstation.  When the camera finishes an
examination and writes files to disk, the watchdog detects them,
confirms the subject has a camera session on the server, uploads each
file, and moves the completed folder to an archive.

Prerequisites
-------------

1. A running clinicedc server with ``edc-retinopathy`` installed and an
   API token created.

2. The camera software must write its output into the watched folder
   using the expected layout (see `Folder layout`_ below).

Folder layout
-------------

The camera creates one subfolder per subject.  Folder names follow a
configurable pattern (see ``subject_folder_pattern``).  Filenames
contain the eye laterality (``OD`` for right, ``OS`` for left),
extracted via ``filename_eye_pattern``.

**Combined report** (default)::

    C:\RetCamOutput\
        105-10-0989-3_\
            105-10-0989-3_Retina_OD_20260602_121802.jpg
            105-10-0989-3_Retina_OS_20260602_121803.jpg
            105-10-0989-3_Retina_OD_20260602_121802.dcm
            105-10-0989-3_Retina_OS_20260602_121803.dcm
            105-10-0989-3_Report_20260602.html
        105-10-0001-2_\
            ...

**Per-eye report**::

    C:\RetCamOutput\
        105-10-0989-3_\
            105-10-0989-3_Retina_OD_20260602_121802.jpg
            105-10-0989-3_Retina_OS_20260602_121803.jpg
            105-10-0989-3_Report_OD_20260602.html
            105-10-0989-3_Report_OS_20260602.html

Processing is triggered once a subject folder contains the expected
number of files:

- **Combined** (default): at least **2 DICOM** and **1 HTML** file.
- **Per-eye**: at least **2 DICOM** and **2 HTML** files.

By default only DICOM files are uploaded.  JPEG files are ignored
unless ``--include-jpgs`` is set.  HTML reports can be made optional
with ``--no-require-html``.

Quick start
-----------

1. Generate a sample config in the camera output folder::

    cd C:\RetCamOutput
    uvx fundus-camera-watchdog --create-config

   This creates ``fundus_camera_watchdog.json`` with sensible defaults.

2. Edit the config to fill in ``api_url``, ``subject_folder_pattern``,
   and optionally ``filename_eye_pattern``, ``device_id``, etc.

3. Set the API token as an environment variable (recommended)::

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
directory if ``--watch-dir`` is also omitted).  Use ``--create-config``
to generate a sample.

Example ``fundus_camera_watchdog.json``::

    {
        "watch_dir": "C:\\RetCamOutput",
        "api_url": "https://edc.example.com",
        "device_id": "RET-CAM-001",
        "site_id": "40",
        "report_type": "combined",
        "subject_folder_pattern": "^(?P<subject_identifier>\\d{3}-\\d{2}-\\d{4}-\\d)_$",
        "filename_eye_pattern": "(?P<eye>OD|OS)"
    }

Required settings
~~~~~~~~~~~~~~~~~

``watch_dir``
    Folder the camera writes subject subfolders to.  Defaults to the
    current directory if not provided.

``api_url``
    Base URL of the EDC server (e.g. ``https://edc.example.com``).

``token``
    DRF authentication token.  Can be provided via ``--token``, the
    config file, or ``FUNDUS_CAMERA_WATCHDOG_TOKEN`` env var
    (recommended).

Optional settings
~~~~~~~~~~~~~~~~~

``device_id``
    Identifier for this camera (sent to the server on resolve).

``site_id``
    Study site identifier.

``report_type``
    How the camera produces report files.  ``combined`` (default) means
    a single HTML file covers both eyes; ``per_eye`` means one HTML per
    eye.

``subject_folder_pattern``
    Regex to match subject folder names in the watch directory.  Use a
    named group ``subject_identifier`` to extract the ID from the folder
    name.  Default: ``^(?P<subject_identifier>.+)$`` (matches
    everything, folder name used as-is).

    Example for folders like ``105-40-1232-0_``::

        "subject_folder_pattern": "^(?P<subject_identifier>\\d{3}-\\d{2}-\\d{4}-\\d)_$"

    This matches the folder and extracts ``105-40-1232-0`` as the
    subject identifier (stripping the trailing underscore).

``filename_eye_pattern``
    Regex to extract eye laterality from filenames.  Use a named group
    ``eye``.  Default: ``(?P<eye>OD|OS)``.

    The extracted value is normalised automatically.  All of the
    following are recognised:

    - **Left eye**: ``L``, ``LE``, ``OS``, ``LEFT``
    - **Right eye**: ``R``, ``RE``, ``OD``, ``RIGHT``

``include_jpgs``
    Include JPEG files in uploads and readiness checks.  By default
    only DICOM files are uploaded.  Set to ``true`` or use
    ``--include-jpgs`` to also upload JPEGs.  Default: ``false``.

``require_html``
    Whether HTML report files are required before triggering uploads.
    Set to ``false`` or use ``--no-require-html`` to upload as soon as
    enough DICOM (or JPEG) files are present.  Default: ``true``.

``log_level``
    One of ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``.

Usage
-----

Run from the camera output folder::

    cd C:\RetCamOutput
    uvx fundus-camera-watchdog

With an explicit config file::

    uvx fundus-camera-watchdog --config C:\path\to\fundus_camera_watchdog.json

CLI flags override any value from the config file::

    uvx fundus-camera-watchdog --log-level DEBUG --report-type per_eye

Without a config file::

    uvx fundus-camera-watchdog ^
        --watch-dir C:\RetCamOutput ^
        --api-url https://edc.example.com ^
        --token YOUR_TOKEN ^
        --device-id RET-CAM-001 ^
        --subject-folder-pattern "^(?P<subject_identifier>\d{3}-\d{2}-\d{4}-\d)_$"

Stop with ``Ctrl+C``.

What it does
------------

The watchdog runs continuously and performs the following for each
subject:

1. **Detect** -- watches for new files in subject subfolders using
   filesystem events (``watchdog``), plus a periodic sweep every
   60 seconds as a safety net.

2. **Wait** -- each file is given up to 30 seconds to stabilise (stop
   growing) before being registered.

3. **Classify** -- extracts eye laterality from each filename using the
   configured pattern.  Maps files to API types:

   - ``*.dcm`` + OD -> ``right_dicom``
   - ``*.dcm`` + OS -> ``left_dicom``
   - ``*.jpg`` + OD -> ``right`` (only with ``--include-jpgs``)
   - ``*.jpg`` + OS -> ``left`` (only with ``--include-jpgs``)
   - ``*.html`` (combined) -> ``report``
   - ``*.html`` + OD (per_eye) -> ``right_report``
   - ``*.html`` + OS (per_eye) -> ``left_report``

4. **Resolve** -- ``POST /api/retinopathy/resolve/`` confirms a
   CameraSession exists on the server for this subject.

5. **Upload** -- sends each file to the server.  Original filenames are
   preserved.  Each upload includes a SHA-256 checksum.  Multiple files
   per eye are supported.  By default only DICOM and HTML files are
   uploaded; use ``--include-jpgs`` to also upload JPEG images.

6. **Verify** -- ``GET .../status/`` confirms the session received the
   expected files.

7. **Archive** -- the subject folder is moved to
   ``<watch-dir>/processed/<subject_id>_<timestamp>/``.

Error handling
--------------

- **Retries** -- every API call is retried up to 3 times with a
  5-second delay.

- **Failed subjects** -- if any step fails, the subject is left in
  place and retried on the next 60-second sweep.

- **Startup scan** -- on (re)start the watchdog scans all existing
  subject folders, picking up where it left off.

- **Thread safety** -- file detection and upload run on separate threads
  with proper locking.

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
