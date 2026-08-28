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
from pdf_web.models import Job
from pdf_web.runner import (
    PIPELINE_STEP_PATTERN,
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
