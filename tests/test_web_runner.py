'''Tests for scheduling pipeline runs across the worker pool.'''

from __future__ import annotations

import os
import threading
import time
import unittest
from unittest import mock

from pdf_api.models import PipelineResult, PipelineStatus
from pdf_web.models import JobStatus
from pdf_web.runner import PipelineRunner
from pdf_web.store import JobStore
from tests.web_factories import make_job

ALICE = "alice@example.com"
BOB = "bob@example.com"


class SchedulerTestCase(unittest.TestCase):
    '''Base case with a runner whose workers are never started.'''

    def setUp(self) -> None:
        '''Create a runner and an empty store.'''
        self.enterContext(mock.patch.dict(os.environ, {}, clear=False))
        for name in ("PDF_WEB_MAX_CONCURRENT_JOBS", "PDF_WEB_MAX_RUNNING_JOBS_PER_USER"):
            os.environ.pop(name, None)
        self.store = JobStore()
        self.runner = PipelineRunner(self.store)

    def add(self, job_id: str, owner: str) -> None:
        '''Register and queue one job.'''
        self.store.add(make_job(job_id=job_id, submitted_by=owner))
        self.runner.submit(job_id, owner)


class QueuePositionTests(SchedulerTestCase):
    '''Waiting users are told how long the line is, but not whose it is.'''

    def test_first_submission_waits_for_nobody(self) -> None:
        '''An empty queue means the job is next.'''
        self.assertEqual(self.runner.submit("20260827-120000-aaaaaa", ALICE), 0)

    def test_positions_follow_fifo_order(self) -> None:
        '''The oldest queued job is next.'''
        self.assertEqual(self.runner.submit("20260827-120000-aaaaaa", ALICE), 0)
        self.assertEqual(self.runner.submit("20260827-120001-bbbbbb", ALICE), 1)
        self.assertEqual(self.runner.submit("20260827-120002-cccccc", BOB), 2)
        self.assertEqual(
            self.runner.pending_job_ids(),
            ("20260827-120000-aaaaaa", "20260827-120001-bbbbbb", "20260827-120002-cccccc"),
        )
        # pylint: disable=protected-access
        self.assertEqual(self.runner._claim_next(), "20260827-120000-aaaaaa")

    def test_batch_preserves_fifo_order_after_existing_jobs(self) -> None:
        '''A batch keeps its own order and follows jobs already waiting.'''
        self.runner.submit("20260827-120000-aaaaaa", ALICE)

        positions = self.runner.submit_batch(
            ("20260827-120001-bbbbbb", "20260827-120002-cccccc"),
            BOB,
        )

        self.assertEqual(positions, [1, 2])
        self.assertEqual(
            self.runner.pending_job_ids(),
            (
                "20260827-120000-aaaaaa",
                "20260827-120001-bbbbbb",
                "20260827-120002-cccccc",
            ),
        )

    def test_running_jobs_are_not_counted_as_ahead(self) -> None:
        '''With a pool, a running job no longer blocks one waiting job for one.'''
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", BOB)
        claimed = self.runner._claim_next()  # pylint: disable=protected-access

        self.assertEqual(self.runner.jobs_ahead(claimed), 0)
        remaining = ("20260827-120001-bbbbbb" if claimed.endswith("aaaaaa")
                     else "20260827-120000-aaaaaa")
        self.assertEqual(self.runner.jobs_ahead(remaining), 0)

    def test_unknown_job_has_no_position(self) -> None:
        '''A job that is not waiting reports nothing rather than zero.'''
        self.assertIsNone(self.runner.jobs_ahead("20260827-120000-aaaaaa"))

    def test_selected_pending_positions_use_the_current_queue_index(self) -> None:
        '''Queue positions are direct lookups after one structural rebuild.'''
        self.runner.submit("20260827-120000-aaaaaa", ALICE)
        self.runner.submit("20260827-120001-bbbbbb", BOB)
        self.runner.submit("20260827-120002-cccccc", ALICE)

        self.assertEqual(
            self.runner.pending_positions_for({"20260827-120001-bbbbbb"}),
            {"20260827-120001-bbbbbb": 1},
        )

    def test_queue_generation_changes_only_when_order_changes(self) -> None:
        '''State updates do not rebuild the pending-position index.'''
        initial = self.runner.queue_generation()
        self.runner.submit("20260827-120000-aaaaaa", ALICE)
        after_submit = self.runner.queue_generation()
        self.assertGreater(after_submit, initial)
        self.runner.pending_positions_for({"20260827-120000-aaaaaa"})
        self.assertEqual(self.runner.queue_generation(), after_submit)
        self.runner.cancel("20260827-120000-aaaaaa")
        self.assertGreater(self.runner.queue_generation(), after_submit)

    def test_user_activity_tracks_pending_and_running_jobs(self) -> None:
        '''Active polling state is scoped to the requesting owner.'''
        self.add("20260827-120000-aaaaaa", ALICE)
        self.assertEqual(self.runner.user_activity(ALICE), (0, True))
        self.assertEqual(self.runner.user_activity(BOB), (0, False))

        claimed = self.runner._claim_next()  # pylint: disable=protected-access
        self.assertEqual(claimed, "20260827-120000-aaaaaa")
        self.assertEqual(self.runner.user_activity(ALICE), (1, True))

        self.runner._release(claimed)  # pylint: disable=protected-access
        self.assertEqual(self.runner.user_activity(ALICE), (0, False))


class PerUserCapTests(SchedulerTestCase):
    '''One user must not be able to hold the whole pool.'''

    def test_claim_skips_a_capped_owner(self) -> None:
        '''The point of the design: a queued job behind a capped owner still starts.

        A plain FIFO queue could not do this without reordering, which would
        cost the capped user their place.
        '''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "1"
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", ALICE)
        self.add("20260827-120002-cccccc", BOB)

        # pylint: disable=protected-access
        first = self.runner._claim_next()
        second = self.runner._claim_next()

        self.assertEqual(first, "20260827-120000-aaaaaa")
        self.assertEqual(second, "20260827-120002-cccccc", "Bob should not wait for Alice")

    def test_capped_owner_keeps_its_place(self) -> None:
        '''Skipping must not demote the skipped job.'''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "1"
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", ALICE)
        self.add("20260827-120002-cccccc", BOB)

        # pylint: disable=protected-access
        first = self.runner._claim_next()
        self.runner._claim_next()
        self.runner._release(first)

        self.assertEqual(self.runner._claim_next(), "20260827-120001-bbbbbb")

    def test_higher_cap_lets_one_owner_take_more(self) -> None:
        '''The cap is configurable.'''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "2"
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", ALICE)

        # pylint: disable=protected-access
        self.assertEqual(self.runner._claim_next(), "20260827-120000-aaaaaa")
        self.assertEqual(self.runner._claim_next(), "20260827-120001-bbbbbb")

    def test_a_zero_cap_cannot_deadlock_the_pool(self) -> None:
        '''A cap of zero would make no job eligible and stall every worker.'''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "0"
        self.add("20260827-120000-aaaaaa", ALICE)
        # pylint: disable=protected-access
        self.assertEqual(self.runner._claim_next(), "20260827-120000-aaaaaa")

    def test_workers_wait_rather_than_spin_when_all_are_capped(self) -> None:
        '''A fully blocked queue must park on the condition, not busy-loop.'''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "1"
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", ALICE)
        # pylint: disable=protected-access
        first = self.runner._claim_next()

        claimed: list[str] = []
        waiter = threading.Thread(
            target=lambda: claimed.append(self.runner._claim_next()), daemon=True
        )
        waiter.start()
        waiter.join(timeout=0.3)
        self.assertTrue(waiter.is_alive(), "worker should be blocked, not spinning")

        self.runner._release(first)
        waiter.join(timeout=2.0)
        self.assertEqual(claimed, ["20260827-120001-bbbbbb"])

    def test_stopping_releases_every_waiter(self) -> None:
        '''Shutdown must not leave a worker parked forever.'''
        results: list[str | None] = []
        waiter = threading.Thread(
            target=lambda: results.append(self.runner._claim_next()),  # pylint: disable=protected-access
            daemon=True,
        )
        waiter.start()
        time.sleep(0.1)
        self.runner.stop()
        waiter.join(timeout=2.0)
        self.assertEqual(results, [None])


class CancellationTests(SchedulerTestCase):
    '''Cancelling must work whether a job is queued or running.'''

    def test_cancelling_a_queued_job_finalizes_it(self) -> None:
        '''Nothing is executing it, so the runner completes it directly.'''
        self.add("20260827-120000-aaaaaa", ALICE)
        self.assertTrue(self.runner.cancel("20260827-120000-aaaaaa"))

        job = self.store.get("20260827-120000-aaaaaa")
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertNotIn("20260827-120000-aaaaaa", self.runner.pending_job_ids())

    def test_cancelling_frees_the_slot_for_the_next_job(self) -> None:
        '''A cancelled job must not still be occupying the queue.'''
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", ALICE)
        self.runner.cancel("20260827-120000-aaaaaa")
        # pylint: disable=protected-access
        self.assertEqual(self.runner._claim_next(), "20260827-120001-bbbbbb")

    def test_cancelling_a_running_job_sets_the_flag(self) -> None:
        '''The pipeline checks this between stages.'''
        self.add("20260827-120000-aaaaaa", ALICE)
        # pylint: disable=protected-access
        claimed = self.runner._claim_next()
        self.assertTrue(self.runner.cancel(claimed))
        self.assertTrue(self.runner._is_cancelled(claimed))

    def test_cancelling_an_unknown_job_reports_nothing_stopped(self) -> None:
        '''A finished job is not cancellable.'''
        self.assertFalse(self.runner.cancel("20260827-129999-ffffff"))

    def test_release_clears_the_cancel_flag(self) -> None:
        '''Otherwise the flag would leak and affect a later job of the same id.'''
        self.add("20260827-120000-aaaaaa", ALICE)
        # pylint: disable=protected-access
        claimed = self.runner._claim_next()
        self.runner.cancel(claimed)
        self.runner._release(claimed)
        self.assertFalse(self.runner._is_cancelled(claimed))


class QueueStatusTests(SchedulerTestCase):
    '''"Queued, 0 ahead" is now legitimate and needs explaining.'''

    def test_reports_waiting_on_your_own_limit(self) -> None:
        '''Without this the UI would show a stalled job with nothing ahead.'''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "1"
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120001-bbbbbb", ALICE)
        self.runner._claim_next()  # pylint: disable=protected-access

        status = self.runner.queue_status("20260827-120001-bbbbbb", ALICE)
        self.assertTrue(status["waiting_on_your_limit"])
        self.assertEqual(status["your_running"], 1)
        self.assertEqual(status["your_limit"], 1)

    def test_another_user_is_not_blocked_by_that_limit(self) -> None:
        '''The cap is per user.'''
        os.environ["PDF_WEB_MAX_RUNNING_JOBS_PER_USER"] = "1"
        self.add("20260827-120000-aaaaaa", ALICE)
        self.add("20260827-120002-cccccc", BOB)
        self.runner._claim_next()  # pylint: disable=protected-access

        status = self.runner.queue_status("20260827-120002-cccccc", BOB)
        self.assertFalse(status["waiting_on_your_limit"])


class PageCountExecutionTests(SchedulerTestCase):
    '''Optional page metadata is calculated after work has left the request.'''

    def test_page_count_is_calculated_by_the_worker(self) -> None:
        '''A valid page count is persisted and does not block pipeline execution.'''
        job = make_job(job_id="20260827-120010-aaaaaa", submitted_by=ALICE)
        self.store.add(job)
        result = PipelineResult(
            status=PipelineStatus.FAILED,
            input_pdf_path=job.paths.input_path,
            error="Test pipeline result.",
        )

        with mock.patch("pdf_web.runner.get_pdf_page_count", return_value=7), \
                mock.patch("pdf_web.runner.process_pdf", return_value=result) as process, \
                mock.patch("pdf_web.runner.save_meta") as persist:
            self.runner._run_job(job)  # pylint: disable=protected-access

        self.assertEqual(job.state.page_count, 7)
        process.assert_called_once()
        self.assertEqual(persist.call_count, 2)

    def test_pipeline_exception_is_recorded_as_terminal_failure(self) -> None:
        '''Unexpected pipeline errors do not strand a job in the running state.'''
        job = make_job(job_id="20260827-120012-cccccc", submitted_by=ALICE)
        self.store.add(job)

        with mock.patch("pdf_web.runner.get_pdf_page_count", return_value=None), \
                mock.patch("pdf_web.runner.process_pdf", side_effect=RuntimeError("boom")), \
                mock.patch("pdf_web.runner.save_meta"):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.runner._run_job(job)  # pylint: disable=protected-access

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.outcome, str(PipelineStatus.FAILED))
        self.assertEqual(job.error, "RuntimeError: boom")

    def test_configured_timeout_marks_result_as_failed(self) -> None:
        '''A pipeline result that crosses the deadline is reported as timed out.'''
        job = make_job(job_id="20260827-120013-dddddd", submitted_by=ALICE)
        self.store.add(job)
        result = PipelineResult(
            status=PipelineStatus.REMEDIATED,
            input_pdf_path=job.paths.input_path,
        )

        with mock.patch("pdf_web.runner.get_pdf_page_count", return_value=None), \
                mock.patch("pdf_web.runner.job_timeout_seconds", return_value=0), \
                mock.patch("pdf_web.runner.process_pdf", return_value=result), \
                mock.patch("pdf_web.runner.save_meta"):
            self.runner._run_job(job)  # pylint: disable=protected-access

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.outcome, str(PipelineStatus.FAILED))
        self.assertIn("configured timeout of 0 seconds", job.error)

    def test_page_count_failure_does_not_prevent_processing(self) -> None:
        '''Page count is display metadata and cannot reject or fail a job.'''
        job = make_job(job_id="20260827-120011-bbbbbb", submitted_by=ALICE)
        self.store.add(job)
        result = PipelineResult(
            status=PipelineStatus.FAILED,
            input_pdf_path=job.paths.input_path,
            error="Test pipeline result.",
        )

        with mock.patch("pdf_web.runner.get_pdf_page_count", return_value=None), \
                mock.patch("pdf_web.runner.process_pdf", return_value=result) as process, \
                mock.patch("pdf_web.runner.save_meta"):
            self.runner._run_job(job)  # pylint: disable=protected-access

        self.assertIsNone(job.state.page_count)
        process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
