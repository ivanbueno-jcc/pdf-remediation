'''Tests for the job registry, event stream, and metadata persistence.'''

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from pdf_web.models import FileResult, Job, JobStatus, StepState, UploadedFile
from pdf_web.store import JobStore, load_meta, save_meta


def make_job(job_id: str = "20260827-151733-baf398") -> Job:
    '''Build a job with one uploaded file.'''
    job = Job(
        job_id=job_id,
        created_at=datetime(2026, 8, 27, 15, 17, 33),
        config_file="default-slim.json",
        skip_font_fix=True,
    )
    job.files.append(UploadedFile("000", "Report v2.pdf", "Report_v2.pdf", 1234))
    return job


class JobStoreTests(unittest.TestCase):
    '''The store is written by the worker thread and read by request handlers.'''

    def setUp(self) -> None:
        '''Create a store holding one job.'''
        self.store = JobStore()
        self.job = make_job()
        self.store.add(self.job)

    def test_round_trips_a_job(self) -> None:
        '''A registered job is retrievable and listed.'''
        self.assertIs(self.store.get(self.job.job_id), self.job)
        self.assertEqual([job.job_id for job in self.store.list_jobs()],
                         [self.job.job_id])

    def test_lists_newest_first(self) -> None:
        '''The job list is ordered for display without re-sorting.'''
        second = make_job("20260827-160000-abc123")
        self.store.add(second)
        self.assertEqual(
            [job.job_id for job in self.store.list_jobs()],
            [second.job_id, self.job.job_id]
        )

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
        job.steps[1] = StepState.DONE
        job.steps[3] = StepState.SKIPPED
        job.summary = {"before": {"totals": {"pass": 0, "fail": 1}}}
        pdf_path = job.workspace_path / "remediated" / "files" / "Report_v2.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF-")
        job.results.append(FileResult(
            file_id="000",
            outcome="remediated",
            final_pdf_path=pdf_path,
            before={"status": "fail", "profiles": {}},
            after={"status": "pass", "profiles": {}},
        ))

        save_meta(job)
        restored = load_meta(job.meta_path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.job_id, job.job_id)
        self.assertEqual(restored.status, JobStatus.COMPLETED)
        self.assertEqual(restored.config_file, "default-slim.json")
        self.assertTrue(restored.skip_font_fix)
        self.assertEqual(restored.steps[1], StepState.DONE)
        self.assertEqual(restored.steps[3], StepState.SKIPPED)
        self.assertEqual(restored.summary, job.summary)
        self.assertEqual(restored.files[0].original_name, "Report v2.pdf")

    def test_restores_output_pdf_location(self) -> None:
        '''The stored path is relative, so downloads work after a restart.'''
        job = make_job()
        job.status = JobStatus.COMPLETED
        pdf_path = job.workspace_path / "remediated" / "files" / "Report_v2.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF-")
        job.results.append(FileResult("000", "remediated", final_pdf_path=pdf_path))

        save_meta(job)
        restored = load_meta(job.meta_path)

        self.assertEqual(restored.results[0].final_pdf_path, pdf_path)
        self.assertTrue(restored.results[0].final_pdf_path.is_file())

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


if __name__ == "__main__":
    unittest.main()
