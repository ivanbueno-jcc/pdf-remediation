'''Tests for the job registry, event stream, and metadata persistence.'''

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from pdf_web.models import JobStatus
from pdf_web.store import JobStore, load_meta, load_persisted_jobs, save_meta
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

    def test_owner_updates_are_incremental_and_typed(self) -> None:
        '''Live clients receive job changes instead of another full snapshot.'''
        version = self.store.owner_version(self.job.submitted_by)
        self.store.emit(self.job.job_id, "stage", {"name": "validate"})
        latest, updates = self.store.wait_for_owner_change(
            self.job.submitted_by, version, 0
        )

        self.assertGreater(latest, version)
        self.assertEqual(updates[-1][1:], ("job-updated", self.job.job_id))

    def test_job_event_wait_returns_events_without_polling(self) -> None:
        '''A job stream can block until its cursor advances.'''
        self.store.emit(self.job.job_id, "stage", {"name": "validate"})

        cursor, events, exists, terminal = self.store.wait_for_job_events(
            self.job.job_id, 0, 0
        )

        self.assertEqual(cursor, 1)
        self.assertEqual(events[0]["type"], "stage")
        self.assertTrue(exists)
        self.assertFalse(terminal)

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

    def test_remove_drops_job_and_events(self) -> None:
        '''Removing a job clears its event stream too.'''
        self.store.append_log(self.job.job_id, "line")
        self.store.remove(self.job.job_id)
        self.assertIsNone(self.store.get(self.job.job_id))
        self.assertEqual(self.store.events_since(self.job.job_id, 0), (0, []))

    def test_cursor_returns_only_new_events(self) -> None:
        '''Clients resume from a cursor without replaying what they have.'''
        for index in range(3):
            self.store.append_log(self.job.job_id, f"line {index}")
        cursor, events = self.store.events_since(self.job.job_id, 0)
        self.assertEqual(cursor, 3)
        self.assertEqual(len(events), 3)

        self.store.append_log(self.job.job_id, "line 3")
        cursor, events = self.store.events_since(self.job.job_id, cursor)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["line"], "line 3")

    def test_cursor_is_stable_when_nothing_new(self) -> None:
        '''Polling an idle job yields no events and the same cursor.'''
        self.store.append_log(self.job.job_id, "only line")
        cursor, _ = self.store.events_since(self.job.job_id, 0)
        again, events = self.store.events_since(self.job.job_id, cursor)
        self.assertEqual(again, cursor)
        self.assertEqual(events, [])

    def test_collapses_consecutive_progress_redraws(self) -> None:
        '''A progress bar must not flood the event log with thousands of lines.'''
        for percent in range(0, 101, 10):
            self.store.append_log(self.job.job_id, f" {percent}%|####| 1/10")
        _, events = self.store.events_since(self.job.job_id, 0)
        self.assertEqual(len(events), 1)
        self.assertIn("100%", events[0]["payload"]["line"])

    def test_keeps_ordinary_lines_between_progress_lines(self) -> None:
        '''Only adjacent progress lines collapse; real output is preserved.'''
        self.store.append_log(self.job.job_id, " 10%|##| 1/10")
        self.store.append_log(self.job.job_id, "[INFO] something happened")
        self.store.append_log(self.job.job_id, " 90%|#########| 9/10")
        _, events = self.store.events_since(self.job.job_id, 0)
        self.assertEqual(len(events), 3)

    def test_typed_events_are_recorded(self) -> None:
        '''Step and status events carry their payload to the browser.'''
        self.store.emit(self.job.job_id, "step", {"step": 2, "name": "fix"})
        _, events = self.store.events_since(self.job.job_id, 0)
        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[0]["payload"]["step"], 2)

    def test_events_for_unknown_job_are_dropped(self) -> None:
        '''Writing to a removed job neither raises nor resurrects it.'''
        self.store.append_log("20200101-000000-abcdef", "line")
        self.store.emit("20200101-000000-abcdef", "step", {})
        self.assertEqual(self.store.events_since("20200101-000000-abcdef", 0), (0, []))


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
