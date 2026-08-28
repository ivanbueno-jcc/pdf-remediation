'''
Paths, limits, and tunables for the PDF remediation web application.
'''

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_ROOT = REPO_ROOT / "resources" / "web-jobs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
CONFIG_DIR = REPO_ROOT / "resources" / "configuration"
VERAPDF_JAR = REPO_ROOT / "lib" / "greenfield-apps-1.28.0.jar"

ALLOWED_CONFIG_FILES = ("default.json", "default-slim.json")
DEFAULT_CONFIG_FILE = "default.json"

PROJECT_NAME = "p"
WORKSPACE_NAME = "default"
WEB_FOLDER_NAME = "_web"

MAX_FILES = 200
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_JOB_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_DISK_BYTES = 5 * 1024 * 1024 * 1024
MAX_STEM_LENGTH = 96

LOG_RING_BUFFER_LINES = 20000
SSE_POLL_SECONDS = 0.25
SSE_KEEPALIVE_SECONDS = 15.0
RETENTION_SWEEP_SECONDS = 6 * 60 * 60


def _read_int_env(name: str, default: int) -> int:
    '''
    Read a non-negative integer environment variable with a fallback.
    '''
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        parsed_value = int(raw_value)
    except ValueError:
        return default
    return parsed_value if parsed_value >= 0 else default


def job_ttl_hours() -> int:
    '''
    Return the job retention window in hours (0 disables the sweep).
    '''
    return _read_int_env("PDF_WEB_JOB_TTL_HOURS", 72)


def max_jobs_per_user() -> int:
    '''
    Return how many jobs one user may have queued or running at once.

    The pipeline runs one job at a time by design, so without a cap a single
    large batch would block everyone behind it.
    '''
    return max(1, _read_int_env("PDF_WEB_MAX_JOBS_PER_USER", 1))


def job_timeout_seconds() -> int:
    '''
    Return the wall-clock cap for a single pipeline run in seconds.
    '''
    return _read_int_env("PDF_WEB_JOB_TIMEOUT_SECONDS", 4 * 60 * 60)
