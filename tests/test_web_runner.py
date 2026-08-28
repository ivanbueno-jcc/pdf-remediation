'''Tests for scheduling pipeline runs across the worker pool.'''

from __future__ import annotations

import os
import threading
import time
import unittest
from unittest import mock

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

    def test_positions_follow_submission_order(self) -> None:
        '''Queue order is drop order.'''
        self.assertEqual(self.runner.submit("20260827-120000-aaaaaa", ALICE), 0)
        self.assertEqual(self.runner.submit("20260827-120001-bbbbbb", ALICE), 1)
        self.assertEqual(self.runner.submit("20260827-120002-cccccc", BOB), 2)

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


if __name__ == "__main__":
    unittest.main()
