'''
Run the pdf_remediation go pipeline for queued jobs in a single worker thread.
'''

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import IO, Iterator

from .config import (
    PROJECT_NAME,
    REPO_ROOT,
    WORKSPACE_NAME,
    job_timeout_seconds,
)
from .harvest import harvest_job
from .models import Job, JobStatus, StepState
from .store import JobStore, save_meta

PIPELINE_STEP_PATTERN = re.compile(r"^PIPELINE STEP (\d+): (.+)$")
PIPELINE_STOPPED_PATTERN = re.compile(r"^\[ERROR\]\s+(Pipeline stopped: .+)$")
SKIP_FONT_FIX_MARKER = "Skipping font_fix and font_fix_pdfix"
TERMINUS_MARKERS = ("Terminus detected", "PIPELINE STEP 0:")
LINE_BREAK_PATTERN = re.compile(r"[\r\n]")
TERMINATE_GRACE_SECONDS = 10.0


class PipelineRunner:
    '''
    Serialize pipeline runs so only one go.py invocation is active at a time.
    '''

    def __init__(self, store: JobStore) -> None:
        '''
        Create a runner bound to a job store.
        '''
        self._store = store
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._process_lock = threading.Lock()
        self._harvest_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._stopping = False

    def start(self) -> None:
        '''
        Start the worker thread.
        '''
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="pdf-web-runner",
            daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        '''
        Stop the worker thread and terminate any running pipeline.
        '''
        self._stopping = True
        self._queue.put(None)
        with self._process_lock:
            process = self._process
        if process is not None:
            terminate_process_group(process)
        if self._thread is not None:
            self._thread.join(timeout=TERMINATE_GRACE_SECONDS + 5.0)
            self._thread = None

    def submit(self, job_id: str) -> int:
        '''
        Queue a job and return its position in the queue.
        '''
        self._queue.put(job_id)
        return self._queue.qsize()

    def queue_depth(self) -> int:
        '''
        Return the number of jobs waiting to run.
        '''
        return self._queue.qsize()

    def _worker_loop(self) -> None:
        '''
        Drain the job queue one job at a time.
        '''
        while True:
            job_id = self._queue.get()
            if job_id is None:
                self._queue.task_done()
                return
            try:
                job = self._store.get(job_id)
                if job is not None and not self._stopping:
                    self._run_job(job)
                elif job is not None:
                    self._finish(job, JobStatus.FAILED, "Server is shutting down.")
            finally:
                self._queue.task_done()

    def _run_job(self, job: Job) -> None:
        '''
        Execute one job end to end and harvest whatever it produced.
        '''
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        self._store.emit(job.job_id, "status", {"status": str(job.status)})

        try:
            self._guard_source_seeded(job)
            return_code = self._stream_pipeline(job)
            job.return_code = return_code
            if return_code == 0 and job.error is None:
                self._mark_remaining_steps_done(job)
                self._finish(job, JobStatus.COMPLETED, None)
            else:
                self._mark_current_step_failed(job)
                message = job.error or (
                    f"Pipeline exited with code {return_code}."
                )
                self._finish(job, JobStatus.FAILED, message)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._mark_current_step_failed(job)
            self._finish(job, JobStatus.FAILED, f"{type(error).__name__}: {error}")

    def _guard_source_seeded(self, job: Job) -> None:
        '''
        Refuse to launch when the source folder is empty.

        go.py downloads a Pantheon backup with Terminus whenever source/ is
        empty, which must never happen for a web-submitted job.
        '''
        if not job.source_path.is_dir() or not any(job.source_path.iterdir()):
            raise RuntimeError(
                "Refusing to run: the job source folder is empty."
            )

    def _stream_pipeline(self, job: Job) -> int:
        '''
        Launch go.py and stream its combined output into the job store.
        '''
        job.web_path.mkdir(parents=True, exist_ok=True)
        command = build_command(job)
        self._log(job, f"$ {' '.join(command)}")

        with subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=build_environment(job),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        ) as process:
            with self._process_lock:
                self._process = process

            watchdog = threading.Timer(
                job_timeout_seconds(),
                self._time_out,
                args=(job, process)
            )
            watchdog.daemon = True
            watchdog.start()

            try:
                if process.stdout is not None:
                    for line in iter_output_lines(process.stdout):
                        self._handle_line(job, process, line)
                return_code = process.wait()
            finally:
                watchdog.cancel()
                terminate_process_group(process)
                with self._process_lock:
                    self._process = None

        return return_code

    def _time_out(self, job: Job, process: subprocess.Popen) -> None:
        '''
        Abort a pipeline that exceeded the wall-clock cap.
        '''
        job.error = (
            f"Pipeline stopped: exceeded the {job_timeout_seconds()} second time limit."
        )
        self._log(job, f"[ERROR] {job.error}")
        terminate_process_group(process)

    def _handle_line(self, job: Job, process: subprocess.Popen, line: str) -> None:
        '''
        Route one captured output line into job state and the event log.
        '''
        text = line.rstrip()
        self._log(job, text)
        stripped = text.strip()

        if any(marker in stripped for marker in TERMINUS_MARKERS):
            job.error = (
                "Pipeline stopped: a source download was triggered unexpectedly."
            )
            terminate_process_group(process)
            return

        step_match = PIPELINE_STEP_PATTERN.match(stripped)
        if step_match is not None:
            self._advance_step(job, int(step_match.group(1)), step_match.group(2).strip())
            return

        if SKIP_FONT_FIX_MARKER in stripped:
            job.steps[3] = StepState.SKIPPED
            job.steps[4] = StepState.SKIPPED
            self._store.emit(job.job_id, "step", {"skipped": [3, 4]})
            return

        stopped_match = PIPELINE_STOPPED_PATTERN.match(stripped)
        if stopped_match is not None:
            job.error = stopped_match.group(1)

    def _advance_step(self, job: Job, number: int, name: str) -> None:
        '''
        Mark a pipeline step running and close out any earlier running step.
        '''
        if number not in job.steps:
            return
        for earlier in range(1, number):
            if job.steps.get(earlier) == StepState.RUNNING:
                job.steps[earlier] = StepState.DONE
        job.steps[number] = StepState.RUNNING
        job.current_step = number
        self._store.emit(job.job_id, "step", {"step": number, "name": name})

        # Step 1 has written the pre-fix report by the time step 2 announces
        # itself, so before-results can be shown without waiting for the run.
        if number >= 2 and not job.results:
            self._harvest_early(job)

    def _mark_remaining_steps_done(self, job: Job) -> None:
        '''
        Close out any step still marked running after a successful run.
        '''
        for number, state in job.steps.items():
            if state == StepState.RUNNING:
                job.steps[number] = StepState.DONE
        job.current_step = None

    def _mark_current_step_failed(self, job: Job) -> None:
        '''
        Mark the step that was running when the pipeline stopped.
        '''
        for number, state in job.steps.items():
            if state == StepState.RUNNING:
                job.steps[number] = StepState.FAILED

    def _harvest_early(self, job: Job) -> None:
        '''
        Publish before-results mid-run without blocking the output reader.
        '''
        def run() -> None:
            '''
            Harvest the pre-fix report and announce the partial results.
            '''
            with self._harvest_lock:
                if job.is_terminal():
                    return
                try:
                    harvest_job(job, final=False)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    self._log(job, f"[WARN] Early result harvesting failed: {error}")
                    return
            self._store.emit(job.job_id, "results", {"stage": "before"})

        thread = threading.Thread(
            target=run,
            name=f"pdf-web-harvest-{job.job_id}",
            daemon=True
        )
        thread.start()

    def _finish(self, job: Job, status: JobStatus, error: str | None) -> None:
        '''
        Harvest results, persist metadata, and emit the terminal event.
        '''
        job.status = status
        job.finished_at = datetime.now()
        if error is not None:
            job.error = error

        try:
            with self._harvest_lock:
                harvest_job(job)
        except Exception as harvest_error:  # pylint: disable=broad-exception-caught
            self._log(job, f"[ERROR] Result harvesting failed: {harvest_error}")

        job.partial = status == JobStatus.FAILED and any(
            result.before is not None or result.final_pdf_path is not None
            for result in job.results
        )

        try:
            save_meta(job)
        except OSError as save_error:
            self._log(job, f"[ERROR] Could not persist job metadata: {save_error}")

        self._store.emit(job.job_id, "status", {"status": str(job.status)})
        self._store.emit(job.job_id, "done", {"status": str(job.status)})

    def _log(self, job: Job, line: str) -> None:
        '''
        Record one line in the event stream and the on-disk log.
        '''
        self._store.append_log(job.job_id, line)
        try:
            with job.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            pass


def build_command(job: Job) -> list[str]:
    '''
    Build the go.py invocation for a job.
    '''
    command = [
        sys.executable,
        "-u",
        "-m",
        "pdf_remediation.go",
        PROJECT_NAME,
        WORKSPACE_NAME,
        "--config-file",
        job.config_file,
    ]
    if job.skip_font_fix:
        command.append("--skip-font-fix")
    if job.wcag_and_ua1_must_pass:
        command.append("--wcag-and-ua1-must-pass")
    if job.verbose:
        command.append("--verbose")
    return command


def build_environment(job: Job) -> dict[str, str]:
    '''
    Build the child environment: isolated project base, unbuffered plain text.

    PYTHONUNBUFFERED is set in the environment rather than relying on -u because
    go.py re-invokes each pipeline step as its own interpreter without -u, so
    only the environment reaches those grandchildren.
    '''
    environment = dict(os.environ)
    environment["PROJECT_BASE_PATH"] = _relative_base_path(job)
    environment["NO_COLOR"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["TERM"] = "dumb"
    environment.pop("PANTHEON_EMAIL", None)
    return environment


def _relative_base_path(job: Job) -> str:
    '''
    Return the job base path relative to the repository root when possible.
    '''
    try:
        return job.base_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return job.base_path.as_posix()


def iter_output_lines(stream: IO[str]) -> Iterator[str]:
    '''
    Yield output lines, treating carriage returns as line breaks.
    '''
    buffer = ""
    while True:
        chunk = stream.read(1024)
        if not chunk:
            break
        buffer += chunk
        parts = LINE_BREAK_PATTERN.split(buffer)
        buffer = parts.pop()
        yield from parts
    if buffer:
        yield buffer


def terminate_process_group(process: subprocess.Popen) -> None:
    '''
    Terminate a pipeline and every process it spawned.

    The pipeline forks multiprocessing pools, so signalling the process group is
    the only reliable way to reap the grandchildren.
    '''
    if process.poll() is not None:
        return
    try:
        group_id = os.getpgid(process.pid)
    except (OSError, AttributeError):
        process.terminate()
        return

    try:
        os.killpg(group_id, signal.SIGTERM)
    except OSError:
        return

    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group_id, signal.SIGKILL)
        except OSError:
            pass


def seed_source_folder(job: Job, sources: list[Path]) -> None:
    '''
    Copy already-staged PDFs into a new job's source folder.
    '''
    job.source_path.mkdir(parents=True, exist_ok=True)
    for source in sources:
        destination = job.source_path / source.name
        destination.write_bytes(source.read_bytes())
