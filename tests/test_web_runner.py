'''Tests for launching and parsing the remediation pipeline subprocess.'''

from __future__ import annotations

import contextlib
import io
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from pdf_remediation.utilities.resources import (
    print_console_banner,
    print_console_message,
)
from pdf_web.config import REPO_ROOT
from pdf_web.models import Job, JobStatus
from pdf_web.store import JobStore
from pdf_web.runner import (
    PIPELINE_STEP_PATTERN,
    PipelineRunner,
    PIPELINE_STOPPED_PATTERN,
    SKIP_FONT_FIX_MARKER,
    TERMINUS_MARKERS,
    build_command,
    build_environment,
    iter_output_lines,
)


def make_job(**overrides) -> Job:
    '''Build a job with the fields the runner reads.'''
    fields = {
        "job_id": "20260827-151733-baf398",
        "created_at": datetime(2026, 8, 27, 15, 17, 33),
        "config_file": "default.json",
    }
    fields.update(overrides)
    return Job(**fields)


def captured(function, *args) -> list[str]:
    '''Run a console printer and return the lines it emitted.'''
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        function(*args)
    return [line for line in buffer.getvalue().splitlines() if line.strip()]


class BuildCommandTests(unittest.TestCase):
    '''The pipeline is invoked through go.py's documented CLI.'''

    def test_default_invocation(self) -> None:
        '''A plain job runs go.py against the fixed project and workspace.'''
        command = build_command(make_job())
        self.assertEqual(command[1:], [
            "-u", "-m", "pdf_remediation.go",
            "p", "default", "--config-file", "default.json",
        ])

    def test_workspace_is_always_default(self) -> None:
        '''Only the "default" workspace copies source into active/files.'''
        self.assertEqual(build_command(make_job())[5], "default")

    def test_optional_flags(self) -> None:
        '''Each option maps to the matching go.py flag.'''
        command = build_command(make_job(
            skip_font_fix=True,
            wcag_and_ua1_must_pass=True,
            verbose=True,
        ))
        for flag in ("--skip-font-fix", "--wcag-and-ua1-must-pass", "--verbose"):
            self.assertIn(flag, command)

    def test_flags_absent_by_default(self) -> None:
        '''Unset options add nothing to the command.'''
        command = build_command(make_job())
        for flag in ("--skip-font-fix", "--wcag-and-ua1-must-pass", "--verbose"):
            self.assertNotIn(flag, command)


class BuildEnvironmentTests(unittest.TestCase):
    '''The child environment isolates the job and keeps output parseable.'''

    def test_strips_pantheon_email(self) -> None:
        '''go.py downloads a Pantheon backup when source/ is empty.

        Seeding source/ is the real guard; removing the email means an
        accidental Terminus path fails fast instead of pulling a backup.
        '''
        with mock.patch.dict(os.environ, {"PANTHEON_EMAIL": "someone@example.com"}):
            environment = build_environment(make_job())
        self.assertNotIn("PANTHEON_EMAIL", environment)

    def test_overrides_project_base_path(self) -> None:
        '''Web jobs get their own base path, never resources/projects.'''
        environment = build_environment(make_job())
        base_path = environment["PROJECT_BASE_PATH"]
        self.assertEqual(
            base_path,
            "resources/web-jobs/20260827-151733-baf398"
        )
        self.assertNotIn("resources/projects", base_path)

    def test_project_base_path_resolves_from_repo_root(self) -> None:
        '''The path is relative, so the child must run at the repository root.'''
        environment = build_environment(make_job())
        self.assertFalse(Path(environment["PROJECT_BASE_PATH"]).is_absolute())
        self.assertTrue(
            (REPO_ROOT / environment["PROJECT_BASE_PATH"]).parent.name == "web-jobs"
        )

    def test_disables_colour_and_buffering(self) -> None:
        '''Plain unbuffered output is what makes streaming and parsing work.'''
        environment = build_environment(make_job())
        self.assertEqual(environment["NO_COLOR"], "1")
        self.assertEqual(environment["PYTHONUNBUFFERED"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")

    def test_dotenv_cannot_win(self) -> None:
        '''load_dotenv defaults to override=False, so our value must survive.

        This asserts the assumption rather than the mechanism: if the child
        ever loaded .env with override=True, PROJECT_BASE_PATH would silently
        revert to resources/projects and web jobs would write there.
        '''
        environment = build_environment(make_job())
        self.assertIn("web-jobs", environment["PROJECT_BASE_PATH"])


class OutputParsingTests(unittest.TestCase):
    '''Progress is derived from go.py's console output, which we do not own.'''

    def test_parses_real_step_banner(self) -> None:
        '''Parse the banner as pdf_remediation actually prints it.'''
        lines = captured(print_console_banner, "PIPELINE STEP 3: font_fix", "info")
        matches = [
            PIPELINE_STEP_PATTERN.match(line.strip()) for line in lines
        ]
        found = [match for match in matches if match]
        self.assertEqual(len(found), 1, f"expected one match in {lines!r}")
        self.assertEqual(found[0].group(1), "3")
        self.assertEqual(found[0].group(2), "font_fix")

    def test_banner_rules_do_not_match(self) -> None:
        '''The horizontal rules around a banner are not mistaken for steps.'''
        lines = captured(print_console_banner, "PIPELINE STEP 7: validate (final)", "info")
        rules = [line for line in lines if not PIPELINE_STEP_PATTERN.match(line.strip())]
        self.assertEqual(len(rules), 2)
        for rule in rules:
            self.assertIsNone(PIPELINE_STOPPED_PATTERN.match(rule.strip()))

    def test_parses_every_pipeline_step(self) -> None:
        '''All seven steps parse, and the step number is usable as an index.'''
        for number in range(1, 8):
            lines = captured(print_console_banner, f"PIPELINE STEP {number}: name", "info")
            match = next(
                PIPELINE_STEP_PATTERN.match(line.strip())
                for line in lines if PIPELINE_STEP_PATTERN.match(line.strip())
            )
            self.assertEqual(int(match.group(1)), number)

    def test_parses_real_stop_message(self) -> None:
        '''The failure line is parsed as pdf_remediation prints it.'''
        message = "Pipeline stopped: fix failed with exit code 1."
        lines = captured(print_console_message, "error", message)
        match = PIPELINE_STOPPED_PATTERN.match(lines[0].strip())
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), message)

    def test_skip_marker_matches_real_message(self) -> None:
        '''The skip notice go.py prints contains the marker we look for.'''
        lines = captured(
            print_console_message,
            "warn",
            "Skipping font_fix and font_fix_pdfix (pass without --skip-font-fix to enable)."
        )
        self.assertIn(SKIP_FONT_FIX_MARKER, lines[0])

    def test_terminus_markers_catch_the_download_path(self) -> None:
        '''Terminus announces itself before downloading; both markers are watched.

        download_source_with_terminus_result prints "Terminus detected: <path>"
        and calls the step banner with step 0.
        '''
        detected = captured(
            print_console_message, "info", "Terminus detected: /opt/homebrew/bin/terminus"
        )[0]
        step_zero = captured(print_console_banner, "PIPELINE STEP 0: download files", "info")
        self.assertTrue(any(marker in detected for marker in TERMINUS_MARKERS))
        self.assertTrue(any(
            marker in line for line in step_zero for marker in TERMINUS_MARKERS
        ))

    def test_step_zero_is_not_treated_as_a_pipeline_step(self) -> None:
        '''Step 0 is the download path, and no such key exists in the stepper.'''
        job = make_job()
        match = PIPELINE_STEP_PATTERN.match("PIPELINE STEP 0: download files")
        self.assertIsNotNone(match)
        self.assertNotIn(int(match.group(1)), job.steps)


class IterOutputLinesTests(unittest.TestCase):
    '''Progress bars redraw with carriage returns rather than newlines.'''

    def test_splits_on_newlines(self) -> None:
        '''Ordinary output splits into lines.'''
        stream = io.StringIO("one\ntwo\nthree\n")
        self.assertEqual(list(iter_output_lines(stream)), ["one", "two", "three"])

    def test_splits_on_carriage_returns(self) -> None:
        '''A redrawing progress bar becomes discrete lines, not one huge line.'''
        stream = io.StringIO("10%|\r50%|\r100%|\n")
        self.assertEqual(list(iter_output_lines(stream)), ["10%|", "50%|", "100%|"])

    def test_yields_trailing_partial_line(self) -> None:
        '''Output without a trailing newline is still reported.'''
        stream = io.StringIO("done, no newline")
        self.assertEqual(list(iter_output_lines(stream)), ["done, no newline"])

    def test_handles_chunk_boundaries(self) -> None:
        '''Lines longer than the read size reassemble correctly.'''
        line = "x" * 5000
        stream = io.StringIO(f"{line}\nshort\n")
        self.assertEqual(list(iter_output_lines(stream)), [line, "short"])


if __name__ == "__main__":
    unittest.main()


class QueuePositionTests(unittest.TestCase):
    '''Waiting users are told how long the line is, but not whose it is.'''

    def setUp(self) -> None:
        '''Create a runner without starting its worker thread.'''
        self.store = JobStore()
        self.runner = PipelineRunner(self.store)
        for job_id in ("20260827-120000-aaaaaa", "20260827-120001-bbbbbb",
                       "20260827-120002-cccccc"):
            self.store.add(make_job(job_id=job_id))

    def test_first_submission_waits_for_nobody(self) -> None:
        '''An idle worker means the job runs next.'''
        self.assertEqual(self.runner.submit("20260827-120000-aaaaaa"), 0)

    def test_positions_reflect_submission_order(self) -> None:
        '''Each later submission reports the jobs queued before it.'''
        self.runner.submit("20260827-120000-aaaaaa")
        self.assertEqual(self.runner.submit("20260827-120001-bbbbbb"), 1)
        self.assertEqual(self.runner.submit("20260827-120002-cccccc"), 2)

    def test_unknown_job_has_no_position(self) -> None:
        '''A job that is not waiting reports no position rather than zero.'''
        self.assertIsNone(self.runner.jobs_ahead("20260827-120000-aaaaaa"))

    def test_queue_depth_counts_only_waiting_jobs(self) -> None:
        '''Depth is what is queued, which drives the health readout.'''
        self.assertEqual(self.runner.queue_depth(), 0)
        self.runner.submit("20260827-120000-aaaaaa")
        self.runner.submit("20260827-120001-bbbbbb")
        self.assertEqual(self.runner.queue_depth(), 2)

    def test_pending_order_is_preserved(self) -> None:
        '''The queue is first in, first out.'''
        for job_id in ("20260827-120000-aaaaaa", "20260827-120001-bbbbbb"):
            self.runner.submit(job_id)
        self.assertEqual(
            self.runner.pending_job_ids(),
            ("20260827-120000-aaaaaa", "20260827-120001-bbbbbb")
        )


class CancellationRaceTests(unittest.TestCase):
    '''Cancelling between claiming a job and starting it must still stop it.'''

    def setUp(self) -> None:
        '''Create a runner holding one job, with no worker thread.'''
        self.store = JobStore()
        self.runner = PipelineRunner(self.store)
        self.job = make_job(job_id="20260827-120000-aaaaaa")
        self.store.add(self.job)

    def test_cancel_before_the_process_exists_prevents_the_run(self) -> None:
        '''There is no process to signal yet, so the flag must be honoured.

        Without the pre-run check the pipeline would run to completion and only
        then report itself cancelled, which stops nothing.
        '''
        self.runner.submit(self.job.job_id)
        # Claim the job the way the worker loop does, then cancel it.
        # pylint: disable=protected-access
        with self.runner._pending_lock:
            self.runner._pending.remove(self.job.job_id)
            self.runner._active_job_id = self.job.job_id
        self.assertTrue(self.runner.cancel(self.job.job_id))

        with (
            mock.patch.object(self.runner, "_guard_source_seeded"),
            mock.patch.object(self.runner, "_stream_pipeline") as pipeline,
        ):
            self.runner._run_job(self.job)

        pipeline.assert_not_called()
        self.assertEqual(self.job.status, JobStatus.CANCELLED)

    def test_an_uncancelled_job_still_runs(self) -> None:
        '''The guard must not stop ordinary jobs.'''
        # The source guard is a separate concern; stub it so this test is
        # about the cancellation check alone.
        # pylint: disable=protected-access
        with (
            mock.patch.object(self.runner, "_guard_source_seeded"),
            mock.patch.object(self.runner, "_stream_pipeline", return_value=0) as pipeline,
            mock.patch.object(self.runner, "_finish"),
        ):
            self.runner._run_job(self.job)
        pipeline.assert_called_once()


class RunningJobCancellationTests(unittest.TestCase):
    '''Cancelling a job that is mid-pipeline must stop it and say so.'''

    def setUp(self) -> None:
        '''Create a runner holding one job, with no worker thread.'''
        self.store = JobStore()
        self.runner = PipelineRunner(self.store)
        self.job = make_job(job_id="20260827-120000-aaaaaa")
        self.store.add(self.job)

    def _make_active(self) -> None:
        '''Put the job in the state the worker leaves it while running.'''
        # pylint: disable=protected-access
        with self.runner._pending_lock:
            self.runner._active_job_id = self.job.job_id

    def test_cancel_signals_the_process_group(self) -> None:
        '''Killing the group is what reaps the multiprocessing children.'''
        self._make_active()
        process = mock.Mock()
        # pylint: disable=protected-access
        self.runner._process = process

        with mock.patch("pdf_web.runner.terminate_process_group") as terminate:
            self.assertTrue(self.runner.cancel(self.job.job_id))

        terminate.assert_called_once_with(process)

    def test_a_killed_run_reports_cancelled_not_failed(self) -> None:
        '''Killing the pipeline makes it exit non-zero; that is not a failure.

        Reporting it as failed would tell the user their documents broke when
        in fact they stopped it themselves. The cancellation has to arrive
        while the pipeline is running, so that the check after it returns is
        the one under test rather than the check before it starts.
        '''
        self._make_active()
        # pylint: disable=protected-access
        self.runner._process = mock.Mock()

        def cancel_mid_run(_job):
            '''Cancel while the pipeline is running, then exit as killed.'''
            with mock.patch("pdf_web.runner.terminate_process_group"):
                self.runner.cancel(self.job.job_id)
            return -15

        with (
            mock.patch.object(self.runner, "_guard_source_seeded"),
            mock.patch.object(
                self.runner, "_stream_pipeline", side_effect=cancel_mid_run
            ) as pipeline,
            mock.patch("pdf_web.runner.harvest_job"),
            mock.patch("pdf_web.runner.save_meta"),
        ):
            self.runner._run_job(self.job)

        pipeline.assert_called_once()
        self.assertEqual(self.job.status, JobStatus.CANCELLED)
        self.assertIn("Cancelled", self.job.error)

    def test_a_genuinely_failed_run_still_reports_failed(self) -> None:
        '''The cancellation branch must not swallow real failures.'''
        self._make_active()
        with (
            mock.patch.object(self.runner, "_guard_source_seeded"),
            mock.patch.object(self.runner, "_stream_pipeline", return_value=1),
            mock.patch("pdf_web.runner.harvest_job"),
            mock.patch("pdf_web.runner.save_meta"),
        ):
            # pylint: disable=protected-access
            self.runner._run_job(self.job)

        self.assertEqual(self.job.status, JobStatus.FAILED)

    def test_worker_skips_jobs_already_finished(self) -> None:
        '''A job cancelled while queued is still in the queue; it must be skipped.'''
        self.job.status = JobStatus.CANCELLED
        self.runner.submit(self.job.job_id)
        # pylint: disable=protected-access
        self.runner._queue.put(None)

        with mock.patch.object(self.runner, "_run_job") as run_job:
            self.runner._worker_loop()

        run_job.assert_not_called()

    def test_worker_runs_a_job_that_is_not_finished(self) -> None:
        '''The skip must not swallow ordinary work.'''
        self.runner.submit(self.job.job_id)
        # pylint: disable=protected-access
        self.runner._queue.put(None)

        with mock.patch.object(self.runner, "_run_job") as run_job:
            self.runner._worker_loop()

        run_job.assert_called_once()
