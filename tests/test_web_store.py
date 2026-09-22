'''Tests for the job registry, owner updates, and metadata persistence.'''

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from pdf_web.models import JobStatus
from pdf_web.store import (
    JobStore, load_meta, load_persisted_jobs, save_meta, sweep_expired_jobs,
)
from tests.web_factories import (
    add_completed_result, make_job, write_job_artifacts,
)





class JobStoreTests(unittest.TestCase):
    '''The store is written by the worker thread and read by request handlers.'''

    def setUp(self) -> None:
        '''Create a store holding one job.'''
        self.store = JobStore()
        self.job = make_job()
        self.store.add(self.job)

    def test_round_trips_a_job(self) -> None:
        '''A registered job is retrievable and listed.'''
        snapshot = self.store.get(self.job.job_id)
        self.assertIsNot(snapshot, self.job)
        self.assertEqual(snapshot.job_id, self.job.job_id)
        self.assertEqual([job.job_id for job in self.store.list_jobs()],
                         [self.job.job_id])

    def test_snapshot_mutation_does_not_change_the_live_job(self) -> None:
        '''Nested request data is detached from the runner-owned object.'''
        snapshot = self.store.snapshot(self.job.job_id)
        snapshot.stages.append({"name": "request-only"})
        snapshot.file.original_name = "changed.pdf"

        live = self.store.get_mutable(self.job.job_id)
        self.assertEqual(live.stages, [])
        self.assertEqual(live.file.original_name, self.job.file.original_name)

    def test_owner_subscriber_receives_updates_without_blocking_threads(self) -> None:
        '''Owner updates are delivered through an async queue.'''
        async def exercise() -> tuple[str, str]:
            subscriber_id, updates = self.store.subscribe_owner(self.job.submitted_by)
            try:
                self.store.emit(self.job.job_id, "stage", {"name": "validate"})
                await asyncio.sleep(0)
                return (await updates.get_batch())[0]
            finally:
                self.store.unsubscribe_owner(self.job.submitted_by, subscriber_id)

        self.assertEqual(
            asyncio.run(exercise()), ("job-updated", self.job.job_id)
        )

    def test_owner_subscriber_coalesces_repeated_job_updates(self) -> None:
        '''A slow stream retains only the latest update for each job.'''
        async def exercise() -> list[tuple[str, str]]:
            subscriber_id, updates = self.store.subscribe_owner(self.job.submitted_by)
            try:
                for index in range(100):
                    self.store.emit(self.job.job_id, "stage", {"step": index})
                await asyncio.sleep(0)
                return await updates.get_batch()
            finally:
                self.store.unsubscribe_owner(self.job.submitted_by, subscriber_id)

        batch = asyncio.run(exercise())
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0], ("job-updated", self.job.job_id))

    def test_lists_newest_first(self) -> None:
        '''The job list is ordered newest first without re-sorting.'''
        second = make_job("20260827-160000-abc123")
        self.store.add(second)
        self.assertEqual(
            [job.job_id for job in self.store.list_jobs()],
            [second.job_id, self.job.job_id]
        )

    def test_lists_one_owner_page_without_other_owners(self) -> None:
        '''The indexed queue view returns only the requested owner's jobs.'''
        other = make_job("20260827-160000-abc123", submitted_by="other@example.com")
        own = make_job("20260827-170000-def456", submitted_by=self.job.submitted_by)
        self.store.add(other)
        self.store.add(own)

        jobs, total, next_cursor = self.store.list_jobs_for_user(
            self.job.submitted_by, limit=1
        )

        self.assertEqual([job.job_id for job in jobs], [own.job_id])
        self.assertEqual(total, 2)
        self.assertEqual(next_cursor, own.job_id)

    def test_owner_cursor_continues_from_last_job(self) -> None:
        '''A cursor returns the next older page for the same owner.'''
        older = make_job("20260827-110000-abc123", submitted_by=self.job.submitted_by)
        newer = make_job("20260827-130000-def456", submitted_by=self.job.submitted_by)
        self.store.add(older)
        self.store.add(newer)

        first, _, cursor = self.store.list_jobs_for_user(
            self.job.submitted_by, limit=2
        )
        second, _, next_cursor = self.store.list_jobs_for_user(
            self.job.submitted_by, cursor, limit=2
        )

        self.assertEqual([job.job_id for job in first], [newer.job_id, older.job_id])
        self.assertEqual([job.job_id for job in second], [self.job.job_id])
        self.assertIsNone(next_cursor)

    def test_owner_cursor_rejects_unknown_job(self) -> None:
        '''A cursor from another owner's history cannot cross the boundary.'''
        with self.assertRaisesRegex(ValueError, "Invalid queue cursor"):
            self.store.list_jobs_for_user(
                self.job.submitted_by, "20260827-160000-abc123", limit=1
            )

    def test_removing_a_job_updates_later_owner_cursors(self) -> None:
        '''Deleting a job keeps the indexed cursor positions consistent.'''
        middle = make_job("20260827-130000-abc123", submitted_by=self.job.submitted_by)
        newest = make_job("20260827-140000-def456", submitted_by=self.job.submitted_by)
        self.store.add(middle)
        self.store.add(newest)
        self.store.remove(middle.job_id)

        jobs, total, _ = self.store.list_jobs_for_user(
            self.job.submitted_by, cursor=newest.job_id, limit=10
        )

        self.assertEqual([job.job_id for job in jobs], [self.job.job_id])
        self.assertEqual(total, 2)

class MetadataPersistenceTests(unittest.TestCase):
    '''Persisted metadata is what makes download links survive a restart.'''

    def setUp(self) -> None:
        '''Point the jobs root at a scratch directory.'''
        # enterContext hands the directory to this test's cleanup.
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))

    def test_round_trips_a_completed_job(self) -> None:
        '''Saving and reloading preserves what the browser needs.'''
        job = make_job()
        job.status = JobStatus.COMPLETED
        job.finished_at = job.created_at + timedelta(minutes=4)
        job.stages = [{"name": "fix", "status": "ok", "detail": "Applied."}]
        job.require_wcag = True
        job.require_pdfua1 = True
        add_completed_result(job, write_job_artifacts(job))
        job.result.initially_secured = True

        save_meta(job)
        restored = load_meta(job.meta_path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.job_id, job.job_id)
        self.assertEqual(restored.status, JobStatus.COMPLETED)
        self.assertEqual(restored.config_file, "default-slim.json")
        self.assertTrue(restored.attempt_unlock)
        self.assertTrue(restored.attempt_fix)
        self.assertTrue(restored.skip_font_fix)
        self.assertTrue(restored.attempt_targeted_fixes)
        self.assertTrue(restored.require_wcag)
        self.assertTrue(restored.require_pdfua1)
        self.assertEqual(restored.validation_requirement, "wcag and pdfua1")
        self.assertTrue(restored.initially_secured)
        self.assertEqual(restored.stages, job.stages)
        self.assertEqual(restored.file.original_name, "Report v2.pdf")

    def test_restores_output_pdf_location(self) -> None:
        '''The stored path is relative, so downloads work after a restart.'''
        job = make_job()
        job.status = JobStatus.COMPLETED
        pdf_path = write_job_artifacts(job)
        add_completed_result(job, pdf_path)

        save_meta(job)
        restored = load_meta(job.meta_path)

        self.assertEqual(restored.result.output_pdf_path, pdf_path)
        self.assertTrue(restored.result.output_pdf_path.is_file())

    def test_rejects_malformed_metadata(self) -> None:
        '''Corrupt or foreign metadata is skipped rather than crashing startup.'''
        bad_path = self.jobs_root / "meta.json"
        bad_path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(load_meta(bad_path))

        bad_path.write_text('{"job_id": "../escape"}', encoding="utf-8")
        self.assertIsNone(load_meta(bad_path))

    def test_missing_file_returns_none(self) -> None:
        '''An absent metadata file is not an error.'''
        self.assertIsNone(load_meta(self.jobs_root / "absent.json"))

    def test_failed_atomic_replace_preserves_previous_metadata(self) -> None:
        '''A failed update must not truncate metadata already on disk.'''
        job = make_job()
        save_meta(job)
        original = job.meta_path.read_bytes()
        job.status = JobStatus.FAILED

        with mock.patch.object(Path, "replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                save_meta(job)

        self.assertEqual(job.meta_path.read_bytes(), original)
        self.assertEqual(list(job.web_path.glob("meta.json.*.partial")), [])

    def test_legacy_strict_metadata_restores_both_profiles(self) -> None:
        '''Jobs saved by the old strict checkbox retain their original gate.'''
        job = make_job()
        payload = job.to_dict()
        payload.pop("require_wcag")
        payload.pop("require_pdfua1")
        payload["wcag_and_ua1_must_pass"] = True
        job.meta_path.parent.mkdir(parents=True, exist_ok=True)
        job.meta_path.write_text(json.dumps(payload), encoding="utf-8")

        restored = load_meta(job.meta_path)

        self.assertEqual(restored.required_profiles(), ("wcag", "ua1"))
        self.assertEqual(restored.validation_requirement, "wcag and pdfua1")


class RetentionSweepTests(unittest.TestCase):
    '''Retention coordinates directory removal with live artifact requests.'''

    def setUp(self) -> None:
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch("pdf_web.store.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch("pdf_web.store.job_ttl_hours", return_value=1))
        self.store = JobStore()
        self.job = make_job(status=JobStatus.COMPLETED)
        self.job.base_path.mkdir(parents=True)
        old_time = (self.job.created_at - timedelta(hours=2)).timestamp()
        os.utime(self.job.base_path, (old_time, old_time))
        self.store.add(self.job)

    def test_waits_for_artifact_access_before_removing_directory(self) -> None:
        '''An active download or bundle build finishes before cleanup proceeds.'''
        started = threading.Event()
        finished = threading.Event()

        def sweep() -> None:
            started.set()
            sweep_expired_jobs(self.store)
            finished.set()

        with self.store.job_artifact_lock(self.job.job_id) as exists:
            self.assertTrue(exists)
            worker = threading.Thread(target=sweep)
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(finished.wait(timeout=0.05))
            self.assertTrue(self.job.base_path.exists())

        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(finished.is_set())
        self.assertFalse(self.job.base_path.exists())
        self.assertIsNone(self.store.get(self.job.job_id))

    def test_does_not_remove_a_job_that_became_active(self) -> None:
        '''A job that becomes active before the lock is acquired is retained.'''
        with self.store.job_artifact_lock(self.job.job_id):
            worker = threading.Thread(target=sweep_expired_jobs, args=(self.store,))
            worker.start()
            self.job.status = JobStatus.RUNNING

        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.job.base_path.exists())
        self.assertIsNotNone(self.store.get(self.job.job_id))

if __name__ == "__main__":
    unittest.main()


class LegacyJobLoadingTests(unittest.TestCase):
    '''Jobs predating ownership must be reported, not silently unreachable.'''

    def setUp(self) -> None:
        '''Point the jobs root at a scratch directory in multi-user mode.'''
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch("pdf_web.store.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch.dict(
            os.environ, {"PDF_WEB_PROXY_SECRET": "s3cret"}
        ))
        os.environ.pop("PDF_WEB_LEGACY_JOB_OWNER", None)

    def _write_job(self, job_id: str, owner: str) -> None:
        '''Persist one job, optionally without an owner.'''
        job = make_job(job_id=job_id)
        job.submitted_by = owner
        job.status = JobStatus.COMPLETED
        save_meta(job)

    def test_counts_jobs_without_an_owner(self) -> None:
        '''The count is what lets an operator notice and act on them.'''
        self._write_job("20260827-120000-aaaaaa", "alice@example.com")
        self._write_job("20260827-120001-bbbbbb", "")
        self._write_job("20260827-120002-cccccc", "")

        store = JobStore()
        loaded, unowned = load_persisted_jobs(store)

        self.assertEqual(loaded, 3)
        self.assertEqual(unowned, 2)

    def test_unowned_jobs_stay_unreachable(self) -> None:
        '''Counting them must not quietly assign them to somebody.'''
        self._write_job("20260827-120001-bbbbbb", "")
        store = JobStore()
        load_persisted_jobs(store)
        self.assertEqual(store.get("20260827-120001-bbbbbb").submitted_by, "")

    def test_configured_owner_adopts_them(self) -> None:
        '''An operator can take ownership deliberately.'''
        self._write_job("20260827-120001-bbbbbb", "")
        os.environ["PDF_WEB_LEGACY_JOB_OWNER"] = "admin@example.com"

        store = JobStore()
        _loaded, unowned = load_persisted_jobs(store)

        self.assertEqual(unowned, 0)
        self.assertEqual(
            store.get("20260827-120001-bbbbbb").submitted_by, "admin@example.com"
        )
