'''SQLite-backed job metadata persistence and filesystem retention policy.'''

from __future__ import annotations

import json
import hashlib
import logging
import re
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from pdf_api.models import PipelineResult, PipelineStatus

from ..config import JOBS_ROOT, job_ttl_hours
from ..identity import legacy_job_owner, normalize_user
from ..models import JobPaths, JobRecord, JobSpec, JobState, JobStatus, UploadedFile

if TYPE_CHECKING:
    from ..store import JobStore

_JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_DATABASE_NAME = "_pdf_web.sqlite3"
_SCHEMA_VERSION = 3
_LOGGER = logging.getLogger(__name__)
_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED_DATABASES: set[Path] = set()
_HASH_LOCK = threading.Lock()
_PENDING_HASHES: set[tuple[Path, str]] = set()
_HASH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf-artifact-hash")


def is_valid_job_id(job_id: str) -> bool:
    '''Return whether a job ID matches the filesystem-safe identifier format.'''
    return bool(_JOB_ID_PATTERN.match(job_id or ""))


def database_path(jobs_root: Path) -> Path:
    '''Return the metadata database path for one job volume.'''
    return jobs_root / _DATABASE_NAME


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path = path.resolve()
    with _INITIALIZATION_LOCK:
        if resolved_path not in _INITIALIZED_DATABASES:
            _initialize_database(resolved_path)
            _INITIALIZED_DATABASES.add(resolved_path)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database(jobs_root: Path) -> None:
    '''Initialize or upgrade the jobs database before serving requests.'''
    _connect(database_path(jobs_root)).close()


def _initialize_database(path: Path) -> None:
    '''Apply versioned schema migrations once, before opening a working connection.'''
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than supported "
                f"version {_SCHEMA_VERSION}."
            )
        if version < 1:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    original_name TEXT NOT NULL DEFAULT '',
                    stored_name TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    page_count INTEGER,
                    config_file TEXT NOT NULL DEFAULT '',
                    parent_job_id TEXT,
                    attempt_number INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL
                )
            """)
            connection.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)")
            }
            additions = {
                "started_at": "TEXT",
                "finished_at": "TEXT",
                "original_name": "TEXT NOT NULL DEFAULT ''",
                "stored_name": "TEXT NOT NULL DEFAULT ''",
                "size_bytes": "INTEGER NOT NULL DEFAULT 0",
                "page_count": "INTEGER",
                "config_file": "TEXT NOT NULL DEFAULT ''",
                "parent_job_id": "TEXT",
                "attempt_number": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE jobs ADD COLUMN {name} {declaration}"
                    )
            for row in connection.execute("SELECT job_id, payload FROM jobs").fetchall():
                try:
                    old_payload = json.loads(row["payload"])
                    old_file = old_payload.get("file") or {}
                except (TypeError, json.JSONDecodeError, AttributeError):
                    continue
                connection.execute(
                    "UPDATE jobs SET owner=?, created_at=?, started_at=?, finished_at=?, "
                    "status=?, original_name=?, stored_name=?, size_bytes=?, page_count=?, "
                    "config_file=?, parent_job_id=?, attempt_number=? WHERE job_id=?",
                    (
                        old_payload.get("submitted_by", ""),
                        old_payload.get("created_at", ""),
                        old_payload.get("started_at"), old_payload.get("finished_at"),
                        old_payload.get("status", "queued"),
                        old_file.get("original_name", ""), old_file.get("stored_name", ""),
                        int(old_file.get("size_bytes") or 0), old_file.get("page_count"),
                        old_payload.get("config_file", ""), old_payload.get("parent_job_id"),
                        int(old_payload.get("attempt_number") or 1), row["job_id"],
                    ),
                )
            connection.execute("PRAGMA user_version = 2")
            version = 2
        if version < 3:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS job_stages (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_seconds REAL,
                    PRIMARY KEY(job_id, sequence)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS job_artifacts (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT,
                    created_at TEXT NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    PRIMARY KEY(job_id, kind)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    detail TEXT
                )
            """)
            for statement in (
                "CREATE INDEX IF NOT EXISTS jobs_owner_created "
                "ON jobs(owner, created_at DESC, job_id DESC)",
                "CREATE INDEX IF NOT EXISTS jobs_status_created "
                "ON jobs(status, created_at DESC, job_id DESC)",
                "CREATE INDEX IF NOT EXISTS jobs_owner_status_created "
                "ON jobs(owner, status, created_at DESC, job_id DESC)",
                "CREATE INDEX IF NOT EXISTS jobs_original_name "
                "ON jobs(original_name COLLATE NOCASE)",
                "CREATE INDEX IF NOT EXISTS job_events_job_time "
                "ON job_events(job_id, created_at, event_id)",
                "CREATE INDEX IF NOT EXISTS job_artifacts_kind ON job_artifacts(kind, job_id)",
            ):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    connection.close()


@contextmanager
def _database(path: Path) -> Iterator[sqlite3.Connection]:
    '''Open a short-lived connection and commit or roll back its transaction.'''
    connection = _connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _record_status_change(
        connection: sqlite3.Connection, job: JobRecord, payload: dict[str, Any],
        old_status: str | None) -> None:
    new_status = str(job.state.status)
    if old_status == new_status:
        return
    detail = json.dumps({
        "outcome": payload.get("outcome"),
        "error": payload.get("error"),
    }, separators=(",", ":"))
    connection.execute(
        "INSERT INTO job_events(job_id, from_status, to_status, created_at, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (job.spec.job_id, old_status, new_status, datetime.now().isoformat(), detail),
    )


def _sync_stages(connection: sqlite3.Connection, job: JobRecord) -> None:
    for sequence, stage in enumerate(job.state.stages):
        connection.execute(
            "INSERT INTO job_stages(job_id, sequence, name, status, detail, "
            "started_at, finished_at, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id, sequence) DO UPDATE SET name=excluded.name, "
            "status=excluded.status, detail=excluded.detail, "
            "started_at=excluded.started_at, finished_at=excluded.finished_at, "
            "duration_seconds=excluded.duration_seconds",
            (
                job.spec.job_id, sequence, stage.get("name", ""),
                stage.get("status", "unknown"), stage.get("detail"),
                stage.get("started_at"), stage.get("completed_at"),
                stage.get("duration_seconds"),
            ),
        )
    connection.execute(
        "DELETE FROM job_stages WHERE job_id = ? AND sequence >= ?",
        (job.spec.job_id, len(job.state.stages)),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_artifacts(jobs_root: Path, job_id: str) -> None:
    '''Hash pending artifacts without delaying the request or worker that cataloged them.'''
    if not is_valid_job_id(job_id):
        return
    with _database(database_path(jobs_root)) as connection:
        rows = connection.execute(
            "SELECT kind, relative_path, size_bytes, modified_ns FROM job_artifacts "
            "WHERE job_id = ? AND sha256 IS NULL",
            (job_id,),
        ).fetchall()
    job_dir = (jobs_root / job_id).resolve()
    for row in rows:
        path = (job_dir / row["relative_path"]).resolve()
        if not path.is_relative_to(job_dir) or not path.is_file():
            continue
        stat = path.stat()
        if stat.st_size != row["size_bytes"] or stat.st_mtime_ns != row["modified_ns"]:
            continue
        checksum = _file_sha256(path)
        with _database(database_path(jobs_root)) as connection:
            connection.execute(
                "UPDATE job_artifacts SET sha256 = ? WHERE job_id = ? AND kind = ? "
                "AND sha256 IS NULL AND size_bytes = ? AND modified_ns = ?",
                (checksum, job_id, row["kind"], row["size_bytes"], row["modified_ns"]),
            )


def _has_unhashed_artifacts(jobs_root: Path, job_id: str) -> bool:
    with _database(database_path(jobs_root)) as connection:
        return connection.execute(
            "SELECT 1 FROM job_artifacts WHERE job_id = ? AND sha256 IS NULL LIMIT 1",
            (job_id,),
        ).fetchone() is not None


def _schedule_artifact_hashes(
        jobs_root: Path, job_id: str, retry_count: int = 0) -> None:
    '''Schedule one deduplicated background hash pass for a job.'''
    key = (database_path(jobs_root).resolve(), job_id)
    with _HASH_LOCK:
        if key in _PENDING_HASHES:
            return
        _PENDING_HASHES.add(key)
    future = _HASH_EXECUTOR.submit(_hash_artifacts, jobs_root, job_id)

    def completed(result) -> None:
        with _HASH_LOCK:
            _PENDING_HASHES.discard(key)
        error = result.exception()
        if error is not None:
            _LOGGER.error(
                "Background artifact hashing failed for job %s", job_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            return
        try:
            if retry_count < 2 and _has_unhashed_artifacts(jobs_root, job_id):
                _schedule_artifact_hashes(jobs_root, job_id, retry_count + 1)
        except (OSError, sqlite3.Error):
            _LOGGER.exception("Could not check pending artifact hashes for job %s", job_id)

    future.add_done_callback(completed)


def _sync_artifacts(connection: sqlite3.Connection, job: JobRecord) -> None:
    candidates = {
        "input_pdf": job.paths.input_path,
        "output_pdf": job.artifact("pdf"),
        "before_report": job.paths.output_dir / "before.json",
        "after_report": job.paths.output_dir / "after.json",
        "pipeline_log": job.paths.log_path,
        "bundle": job.paths.bundle_path,
    }
    present: set[str] = set()
    for kind, path in candidates.items():
        if path is None or not path.is_file():
            continue
        present.add(kind)
        stat = path.stat()
        relative_path = path.relative_to(job.paths.base_path).as_posix()
        previous = connection.execute(
            "SELECT size_bytes, modified_ns, sha256, created_at, relative_path "
            "FROM job_artifacts WHERE job_id = ? AND kind = ?",
            (job.spec.job_id, kind),
        ).fetchone()
        if previous and previous["size_bytes"] == stat.st_size \
                and previous["modified_ns"] == stat.st_mtime_ns \
                and previous["relative_path"] == relative_path:
            checksum = previous["sha256"]
            created_at = previous["created_at"]
        else:
            checksum = None
            created_at = datetime.now().isoformat()
        connection.execute(
            "INSERT INTO job_artifacts(job_id, kind, relative_path, size_bytes, "
            "sha256, created_at, modified_ns) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id, kind) DO UPDATE SET relative_path=excluded.relative_path, "
            "size_bytes=excluded.size_bytes, sha256=excluded.sha256, "
            "modified_ns=excluded.modified_ns",
            (job.spec.job_id, kind, relative_path, stat.st_size, checksum,
             created_at, stat.st_mtime_ns),
        )
    if present:
        placeholders = ",".join("?" for _ in present)
        connection.execute(
            f"DELETE FROM job_artifacts WHERE job_id = ? AND kind NOT IN ({placeholders})",
            (job.spec.job_id, *present),
        )


def refresh_artifacts(job: JobRecord) -> None:
    '''Refresh artifact catalog entries after out-of-band bundle creation.'''
    try:
        with _database(database_path(job.paths.root)) as connection:
            _sync_artifacts(connection, job)
    except (OSError, sqlite3.Error):
        _LOGGER.exception("Could not refresh artifact catalog for job %s", job.spec.job_id)
        return
    _schedule_artifact_hashes(job.paths.root, job.spec.job_id)


def _backfill_derived_tables(jobs: list[JobRecord], jobs_root: Path) -> None:
    '''Populate normalized stage and artifact rows for pre-upgrade job records.'''
    with _database(database_path(jobs_root)) as connection:
        for job in jobs:
            _sync_stages(connection, job)
            _sync_artifacts(connection, job)
    for job in jobs:
        try:
            _hash_artifacts(jobs_root, job.spec.job_id)
        except (OSError, sqlite3.Error):
            _LOGGER.exception("Could not hash artifacts for job %s", job.spec.job_id)


def persist_stage(job: JobRecord, sequence: int, stage: dict[str, Any]) -> None:
    '''Persist one completed stage as soon as the runner reports it.'''
    try:
        with _database(database_path(job.paths.root)) as connection:
            connection.execute(
                "INSERT INTO job_stages(job_id, sequence, name, status, detail, "
                "started_at, finished_at, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id, sequence) DO UPDATE SET name=excluded.name, "
                "status=excluded.status, detail=excluded.detail, "
                "started_at=excluded.started_at, finished_at=excluded.finished_at, "
                "duration_seconds=excluded.duration_seconds",
                (job.spec.job_id, sequence, stage.get("name", ""),
                 stage.get("status", "unknown"), stage.get("detail"),
                 stage.get("started_at"), stage.get("completed_at"),
                 stage.get("duration_seconds")),
            )
    except sqlite3.Error as error:
        raise OSError(f"Could not persist stage for job {job.spec.job_id}: {error}") from error


def save_meta(job: JobRecord) -> None:
    '''Persist the latest job snapshot in SQLite, preserving the old call API.'''
    with job.state.lock:
        payload = job.to_dict()
        result = job.state.result
        payload["output_pdf_path"] = (
            result.output_pdf_path.relative_to(job.paths.base_path).as_posix()
            if result and result.output_pdf_path
            and result.output_pdf_path.is_relative_to(job.paths.base_path)
            else None
        )
        payload_json = dict(payload)
        for normalized_field in (
            "job_id", "submitted_by", "parent_job_id", "attempt_number",
            "created_at", "started_at", "finished_at", "status", "config_file",
            "file", "stages",
        ):
            payload_json.pop(normalized_field, None)
        serialized = json.dumps(payload_json, separators=(",", ":"), default=str)
        values = (
            job.spec.job_id,
            job.spec.submitted_by,
            job.spec.created_at.isoformat(),
            job.state.started_at.isoformat() if job.state.started_at else None,
            job.state.finished_at.isoformat() if job.state.finished_at else None,
            str(job.state.status),
            job.spec.file.original_name,
            job.spec.file.stored_name,
            job.spec.file.size_bytes,
            job.state.page_count,
            job.spec.config_file,
            job.spec.parent_job_id,
            job.spec.attempt_number,
            serialized,
        )
    try:
        with _database(database_path(job.paths.root)) as connection:
            previous = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job.spec.job_id,)
            ).fetchone()
            old_status = previous["status"] if previous else None
            connection.execute(
                "INSERT INTO jobs(job_id, owner, created_at, started_at, finished_at, "
                "status, original_name, stored_name, size_bytes, page_count, "
                "config_file, parent_job_id, attempt_number, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET owner=excluded.owner, "
                "created_at=excluded.created_at, started_at=excluded.started_at, "
                "finished_at=excluded.finished_at, status=excluded.status, "
                "original_name=excluded.original_name, stored_name=excluded.stored_name, "
                "size_bytes=excluded.size_bytes, page_count=excluded.page_count, "
                "config_file=excluded.config_file, "
                "parent_job_id=excluded.parent_job_id, "
                "attempt_number=excluded.attempt_number, payload=excluded.payload",
                values,
            )
            _record_status_change(connection, job, payload, old_status)
            _sync_stages(connection, job)
            _sync_artifacts(connection, job)
        meta_path = job.paths.meta_path
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = meta_path.with_name(
            meta_path.name + "." + uuid.uuid4().hex + ".partial"
        )
        partial_path.write_text(
            json.dumps(payload, separators=(",", ":"), default=str),
            encoding="utf-8",
        )
        try:
            partial_path.replace(meta_path)
        finally:
            partial_path.unlink(missing_ok=True)
    except sqlite3.Error as error:
        raise OSError(
            f"SQLite metadata persistence failed for job {job.spec.job_id}: {error}"
        ) from error
    try:
        _hash_artifacts(job.paths.root, job.spec.job_id)
    except (OSError, sqlite3.Error):
        _LOGGER.exception("Could not hash artifacts for job %s", job.spec.job_id)


def delete_persisted_job(job_id: str, jobs_root: Path) -> None:
    '''Remove one job's database record after its artifact directory is deleted.'''
    with _database(database_path(jobs_root)) as connection:
        connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))


def query_job_page(
        jobs_root: Path, owner: str, cursor: str | None = None,
        limit: int | None = 100) -> tuple[list[str], int, str | None]:
    '''Return an owner-scoped, newest-first job page using SQLite keyset paging.'''
    with _database(database_path(jobs_root)) as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE owner = ?", (owner,)
        ).fetchone()[0]
        parameters: list[Any] = [owner]
        cursor_clause = ""
        if cursor is not None:
            cursor_row = connection.execute(
                "SELECT created_at, job_id FROM jobs WHERE owner = ? AND job_id = ?",
                (owner, cursor),
            ).fetchone()
            if cursor_row is None:
                raise ValueError("Invalid queue cursor.")
            cursor_clause = (
                " AND (created_at < ? OR (created_at = ? AND job_id < ?))"
            )
            parameters.extend((cursor_row["created_at"], cursor_row["created_at"], cursor))

        fetch_limit = limit + 1 if limit is not None else None
        limit_clause = " LIMIT ?" if fetch_limit is not None else ""
        if fetch_limit is not None:
            parameters.append(fetch_limit)
        rows = connection.execute(
            "SELECT job_id FROM jobs WHERE owner = ?" + cursor_clause
            + " ORDER BY created_at DESC, job_id DESC" + limit_clause,
            parameters,
        ).fetchall()

    job_ids = [row["job_id"] for row in rows]
    has_more = limit is not None and len(job_ids) > limit
    if has_more:
        job_ids = job_ids[:limit]
    next_cursor = job_ids[-1] if has_more and job_ids else None
    return job_ids, total, next_cursor


def load_meta(meta_path: Path) -> JobRecord | None:
    '''Load legacy JSON metadata, retaining compatibility for old records.'''
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _job_from_payload(meta_path.parent.parent.parent, payload)


def _load_database(path: Path, jobs_root: Path) -> list[JobRecord]:
    if not path.is_file():
        return []
    try:
        with _database(path) as connection:
            rows = connection.execute("SELECT * FROM jobs").fetchall()
            stages_by_job: dict[str, list[dict[str, Any]]] = {}
            for stage in connection.execute(
                "SELECT job_id, name, status, detail, started_at, finished_at, "
                "duration_seconds FROM job_stages ORDER BY job_id, sequence"
            ):
                stages_by_job.setdefault(stage["job_id"], []).append({
                    "name": stage["name"],
                    "status": stage["status"],
                    "detail": stage["detail"],
                    "started_at": stage["started_at"],
                    "completed_at": stage["finished_at"],
                    "duration_seconds": stage["duration_seconds"],
                })
    except sqlite3.DatabaseError:
        return []
    jobs = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload.update({
            "job_id": row["job_id"],
            "submitted_by": row["owner"],
            "parent_job_id": row["parent_job_id"],
            "attempt_number": row["attempt_number"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "config_file": row["config_file"],
            "file": {
                "original_name": row["original_name"],
                "stored_name": row["stored_name"],
                "size_bytes": row["size_bytes"],
                "page_count": row["page_count"],
            },
            # Existing payloads may contain stages from before table migration.
            "stages": stages_by_job.get(row["job_id"], payload.get("stages", [])),
        })
        job = _job_from_payload(jobs_root, payload)
        if job is not None:
            jobs.append(job)
    return jobs


def _job_from_payload(jobs_root: Path, payload: Any) -> JobRecord | None:
    '''Build a validated in-memory record from its persisted payload.'''
    if not isinstance(payload, dict):
        return None
    job_id = payload.get("job_id", "")
    file_payload = payload.get("file")
    if not is_valid_job_id(job_id) or not isinstance(file_payload, dict):
        return None

    legacy_both = bool(payload.get("wcag_and_ua1_must_pass"))
    if "require_wcag" in payload or "require_pdfua1" in payload:
        require_wcag = bool(payload.get("require_wcag"))
        require_pdfua1 = bool(payload.get("require_pdfua1"))
    else:
        require_wcag, require_pdfua1 = True, legacy_both

    stored_name = file_payload.get("stored_name", "")
    spec = JobSpec(
        job_id=job_id,
        created_at=_parse_datetime(payload.get("created_at")),
        config_file=payload.get("config_file", ""),
        file=UploadedFile(
            original_name=file_payload.get("original_name", ""),
            stored_name=stored_name,
            size_bytes=int(file_payload.get("size_bytes") or 0),
            page_count=(int(file_payload["page_count"])
                        if file_payload.get("page_count") is not None else None),
        ),
        submitted_by=normalize_user(payload.get("submitted_by")) or (legacy_job_owner() or ""),
        attempt_unlock=bool(payload.get("attempt_unlock", True)),
        attempt_fix=bool(payload.get("attempt_fix", True)),
        skip_font_fix=bool(payload.get("skip_font_fix")),
        attempt_targeted_fixes=bool(payload.get("attempt_targeted_fixes", True)),
        wcag_and_ua1_must_pass=legacy_both,
        require_wcag=require_wcag,
        require_pdfua1=require_pdfua1,
        verbose=bool(payload.get("verbose")),
        parent_job_id=payload.get("parent_job_id"),
        attempt_number=int(payload.get("attempt_number") or 1),
    )
    state = JobState(
        status=_parse_status(payload.get("status")),
        outcome=payload.get("outcome"),
        error=payload.get("error"),
        started_at=_parse_optional_datetime(payload.get("started_at")),
        finished_at=_parse_optional_datetime(payload.get("finished_at")),
        stages=list(payload.get("stages") or []),
        page_count=spec.file.page_count,
        has_pdf=False,
    )
    job = JobRecord(spec, state, JobPaths(job_id, stored_name, jobs_root))
    output_relative = payload.get("output_pdf_path")
    state.result = PipelineResult(
        status=_parse_pipeline_status(payload.get("outcome")),
        input_pdf_path=job.paths.input_path,
        output_pdf_path=job.paths.base_path / output_relative if output_relative else None,
        before=_read_report(job.paths.output_dir / "before.json"),
        after=_read_report(job.paths.output_dir / "after.json"),
        initially_secured=(payload.get("initially_secured")
                           if isinstance(payload.get("initially_secured"), bool) else None),
        warnings=list(payload.get("warnings") or []),
        diagnostics=list(payload.get("diagnostics") or []),
        error=payload.get("error"),
    )
    state.has_pdf = job.artifact("pdf") is not None
    return job


def load_persisted_jobs(
        store: "JobStore", jobs_root: Path | None = None) -> tuple[int, int]:
    '''Load SQLite records and import legacy per-job JSON files once.'''
    jobs_root = jobs_root or JOBS_ROOT
    jobs_root.mkdir(parents=True, exist_ok=True)
    records = {job.spec.job_id: job for job in _load_database(
        database_path(jobs_root), jobs_root
    )}
    missing_directories = [
        job_id for job_id in records if not (jobs_root / job_id).is_dir()
    ]
    for job_id in missing_directories:
        records.pop(job_id)
        delete_persisted_job(job_id, jobs_root)
    legacy_records: list[JobRecord] = []
    for job_path in sorted(jobs_root.iterdir()):
        if not job_path.is_dir() or not is_valid_job_id(job_path.name):
            continue
        meta_path = job_path / "_web" / "meta.json"
        if not meta_path.is_file():
            continue
        job = load_meta(meta_path)
        if job is not None and job.spec.job_id not in records:
            records[job.spec.job_id] = job
            legacy_records.append(job)

    for job in legacy_records:
        save_meta(job)
    _backfill_derived_tables(list(records.values()), jobs_root)
    loaded = unowned = 0
    for job in sorted(records.values(), key=lambda item: item.spec.created_at):
        if not job.is_terminal():
            job.state.status = JobStatus.FAILED
            job.state.error = job.state.error or "Server restarted while this job was running."
            save_meta(job)
        if not job.spec.submitted_by:
            unowned += 1
        store.add(job)
        loaded += 1
    return loaded, unowned


def sweep_expired_jobs(
        store: "JobStore", jobs_root: Path | None = None) -> int:
    '''Remove expired terminal job directories and their SQLite rows.'''
    jobs_root = jobs_root or JOBS_ROOT
    ttl_hours = job_ttl_hours()
    if ttl_hours <= 0 or not jobs_root.is_dir():
        return 0
    cutoff = datetime.now() - timedelta(hours=ttl_hours)
    removed = 0
    for job_path in sorted(jobs_root.iterdir()):
        if not job_path.is_dir() or not is_valid_job_id(job_path.name):
            continue
        job = store.get(job_path.name)
        if job is not None and not job.is_terminal():
            continue
        try:
            if datetime.fromtimestamp(job_path.stat().st_mtime) > cutoff:
                continue
        except OSError:
            continue
        with store.job_artifact_lock(job_path.name) as exists:
            live_job = store.get_mutable(job_path.name) if exists else None
            if live_job is not None and not live_job.is_terminal():
                continue
            try:
                shutil.rmtree(job_path)
            except OSError:
                continue
            store.remove(job_path.name)
            with _database(database_path(jobs_root)) as connection:
                connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_path.name,))
            removed += 1
    return removed


def _read_report(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_pipeline_status(value: object) -> PipelineStatus:
    try:
        return PipelineStatus(str(value))
    except ValueError:
        return PipelineStatus.FAILED


def _parse_status(value: object) -> JobStatus:
    try:
        return JobStatus(str(value))
    except ValueError:
        return JobStatus.FAILED


def _parse_datetime(value: object) -> datetime:
    return _parse_optional_datetime(value) or datetime.now()


def _parse_optional_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
