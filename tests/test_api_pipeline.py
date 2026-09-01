'''Tests for the single-PDF pipeline's decision logic and scratch workspace.'''

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf_api.models import (
    DEFAULT_TARGETS,
    FONT_ISSUE_CLAUSES,
    MISSING_UNICODE_CLAUSE,
    PipelineOptions,
    PipelineStatus,
)
from pdf_api.pipeline import _outcome_status, _validate_inputs, process_pdf
from pdf_api.scratch import scratch_workspace
from pdf_api.stages import (
    failing_clause_tests,
    failing_clauses,
    matching_target_actions,
    meets_compliance_gate,
)


def report(profiles: dict[str, tuple[str, list[dict]]]) -> dict:
    '''Build a validation report in solo_validate's shape.'''
    return {
        "status": "pass" if all(s == "pass" for s, _ in profiles.values()) else "fail",
        "passed": all(s == "pass" for s, _ in profiles.values()),
        "failed_rules_count": sum(len(v) for _, v in profiles.values()),
        "profiles": {
            name: {
                "status": status,
                "passed": status == "pass",
                "failed_rules_count": len(violations),
                "violations": violations,
            }
            for name, (status, violations) in profiles.items()
        },
    }


def violation(clause: str, test: str) -> dict:
    '''Build one violation with the clause_test id solo_validate synthesizes.'''
    return {"clause": clause, "test": test, "clause_test": f"{clause}-{test}"}


class ScratchWorkspaceTests(unittest.TestCase):
    '''The batch utilities derive write paths from the workspace by depth.'''

    def test_depth_satisfies_both_derivations(self) -> None:
        '''Error CSVs must land inside the scratch tree, not above it.

        pdfix.fix writes four levels above the files folder, and the Docker
        font steps write two above the workspace. Both have to resolve to the
        same collectable root, and only one depth satisfies them together.
        '''
        with scratch_workspace() as scratch:
            self.assertEqual(
                scratch.files.parent.parent.parent.parent, scratch.root,
                "error CSVs would be written outside the scratch directory"
            )
            self.assertEqual(
                scratch.workspace.parent.parent, scratch.root,
                "font-step CSVs would be written outside the scratch directory"
            )

    def test_working_folders_are_inside_the_docker_mount(self) -> None:
        '''The font steps express their arguments relative to the workspace.'''
        with scratch_workspace() as scratch:
            for folder in (scratch.files, scratch.processed, scratch.staging):
                with self.subTest(folder=folder.name):
                    self.assertTrue(folder.is_relative_to(scratch.workspace))
                    self.assertTrue(folder.is_dir())

    def test_removed_on_exit(self) -> None:
        '''Nothing survives a run.'''
        with scratch_workspace() as scratch:
            root = scratch.root
            (scratch.files / "a.pdf").write_bytes(b"%PDF-")
        self.assertFalse(root.exists())

    def test_removed_even_when_the_run_raises(self) -> None:
        '''A failing run must not leak a directory.'''
        root = None
        with self.assertRaises(RuntimeError):
            with scratch_workspace() as scratch:
                root = scratch.root
                raise RuntimeError("stage failed")
        self.assertFalse(root.exists())

    def test_staging_copies_rather_than_moves(self) -> None:
        '''PDFix deletes the file it is given, so the original must survive.'''
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            with scratch_workspace() as scratch:
                staged = scratch.stage_input(source)
                self.assertTrue(staged.is_file())
                self.assertNotEqual(staged, source)
                staged.unlink()
                self.assertTrue(source.is_file())

    def test_collects_error_csvs_as_diagnostics(self) -> None:
        '''The utilities record the real failure reason only in these files.'''
        with scratch_workspace() as scratch:
            (scratch.root / "pdfix-cannot-process-files.csv").write_text(
                "a.pdf,Unable to open\n", encoding="utf-8"
            )
            diagnostics = scratch.collect_diagnostics()
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["source"], "pdfix-cannot-process-files.csv")
        self.assertIn("Unable to open", diagnostics[0]["detail"])

    def test_prepare_output_clears_a_prior_stage_result_up_front(self) -> None:
        '''The underlying tools require a clean target before they run.'''
        with scratch_workspace() as scratch:
            output_path = scratch.output_path("a.pdf")
            output_path.write_bytes(b"%PDF-stage-one")
            backup_source = scratch.staging / "input.pdf"
            backup_source.write_bytes(b"%PDF-stage-one")

            with scratch.prepare_output("a.pdf", backup_source=backup_source) as yielded:
                self.assertEqual(yielded, output_path)
                self.assertFalse(output_path.exists())

    def test_prepare_output_restores_the_prior_result_on_failure(self) -> None:
        '''A failed stage must not delete the file its caller falls back to.

        Every stage writes to the same processed/name path, so from the
        second stage onward that path is also the caller's current file. If a
        stage fails after clearing it, the caller silently keeps a Path to a
        file that no longer exists.
        '''
        with scratch_workspace() as scratch:
            output_path = scratch.output_path("a.pdf")
            output_path.write_bytes(b"%PDF-previous-stage-output")
            backup_source = scratch.staging / "input.pdf"
            backup_source.write_bytes(b"%PDF-previous-stage-output")

            with self.assertRaises(RuntimeError):
                with scratch.prepare_output("a.pdf", backup_source=backup_source):
                    raise RuntimeError("stage failed")

            self.assertTrue(output_path.is_file())
            self.assertEqual(output_path.read_bytes(), b"%PDF-previous-stage-output")

    def test_prepare_output_leaves_no_backup_file_behind(self) -> None:
        '''The restoration copy is scratch-internal and must not leak.'''
        with scratch_workspace() as scratch:
            backup_source = scratch.staging / "input.pdf"
            backup_source.write_bytes(b"%PDF-1.7\n")

            with scratch.prepare_output("a.pdf", backup_source=backup_source) as output_path:
                output_path.write_bytes(b"%PDF-fixed")

            self.assertEqual(
                [entry.name for entry in scratch.staging.iterdir()],
                ["input.pdf"],
            )


class ComplianceGateTests(unittest.TestCase):
    '''The gate decides whether a file is returned untouched.'''

    def test_wcag_alone_by_default(self) -> None:
        '''Default matches go.py: WCAG passing is enough.'''
        passing = report({"ua1": ("fail", [violation("7.1", "9")]), "wcag": ("pass", [])})
        self.assertTrue(meets_compliance_gate(passing, False))

    def test_both_profiles_when_demanded(self) -> None:
        '''The stricter gate requires UA1 as well.'''
        partial = report({"ua1": ("fail", [violation("7.1", "9")]), "wcag": ("pass", [])})
        self.assertFalse(meets_compliance_gate(partial, True))

        full = report({"ua1": ("pass", []), "wcag": ("pass", [])})
        self.assertTrue(meets_compliance_gate(full, True))

    def test_failing_wcag_never_passes(self) -> None:
        '''WCAG is required under either setting.'''
        failing = report({"ua1": ("pass", []), "wcag": ("fail", [violation("7.1", "9")])})
        self.assertFalse(meets_compliance_gate(failing, False))
        self.assertFalse(meets_compliance_gate(failing, True))

    def test_pdfua1_can_be_the_only_required_profile(self) -> None:
        '''A PDF/UA-1-only run ignores a WCAG failure for its outcome gate.'''
        ua1_only = report({
            "ua1": ("pass", []),
            "wcag": ("fail", [violation("7.1", "9")]),
        })
        self.assertTrue(meets_compliance_gate(ua1_only, ("ua1",)))
        self.assertFalse(meets_compliance_gate(ua1_only, ("wcag", "ua1")))

    def test_empty_profile_selection_never_passes(self) -> None:
        '''An empty gate cannot accidentally mark a file compliant.'''
        full = report({"ua1": ("pass", []), "wcag": ("pass", [])})
        self.assertFalse(meets_compliance_gate(full, ()))


class ClauseExtractionTests(unittest.TestCase):
    '''Font routing matches bare clauses; targeted fixes match clause-tests.'''

    def test_extracts_clauses_across_profiles(self) -> None:
        '''Both profiles contribute failures.'''
        both = report({
            "ua1": ("fail", [violation("7.21.6", "3")]),
            "wcag": ("fail", [violation("7.1", "9")]),
        })
        self.assertEqual(failing_clauses(both), {"7.21.6", "7.1"})
        self.assertEqual(failing_clause_tests(both), {"7.21.6-3", "7.1-9"})

    def test_font_clauses_trigger_the_callas_stage(self) -> None:
        '''The seven font clauses are what send a file to Callas.'''
        for clause in FONT_ISSUE_CLAUSES:
            with self.subTest(clause=clause):
                failing = report({"ua1": ("fail", [violation(clause, "1")]), "wcag": ("pass", [])})
                self.assertTrue(FONT_ISSUE_CLAUSES & failing_clauses(failing))

    def test_non_font_clause_does_not(self) -> None:
        '''An unrelated failure must not start a Docker container.'''
        failing = report({"ua1": ("fail", [violation("7.1", "9")]), "wcag": ("pass", [])})
        self.assertFalse(FONT_ISSUE_CLAUSES & failing_clauses(failing))

    def test_missing_unicode_is_a_single_clause(self) -> None:
        '''The PDFix font step exists only for clause 7.21.7.'''
        self.assertIn(MISSING_UNICODE_CLAUSE, FONT_ISSUE_CLAUSES)
        residual = report({"ua1": ("fail", [violation("7.21.7", "1")]), "wcag": ("pass", [])})
        self.assertIn(MISSING_UNICODE_CLAUSE, failing_clauses(residual))

        other_font = report({"ua1": ("fail", [violation("7.21.6", "3")]), "wcag": ("pass", [])})
        self.assertNotIn(MISSING_UNICODE_CLAUSE, failing_clauses(other_font))


class TargetedActionTests(unittest.TestCase):
    '''Targeted configs are chosen by clause-test, and de-duplicated.'''

    def test_two_clause_tests_sharing_a_config_run_it_once(self) -> None:
        '''5-1 and 7.1-9 both map to restore_metadata.json.'''
        failing = report({
            "ua1": ("fail", [violation("5", "1"), violation("7.1", "9")]),
            "wcag": ("pass", []),
        })
        actions, matched = matching_target_actions(failing, DEFAULT_TARGETS)
        self.assertEqual(actions, ["restore_metadata.json"])
        self.assertEqual(matched, ["5-1", "7.1-9"])

    def test_order_follows_the_target_list(self) -> None:
        '''Chaining order is the configured order, not the report's.'''
        failing = report({
            "ua1": ("fail", [violation("7.2", "29"), violation("5", "1")]),
            "wcag": ("pass", []),
        })
        actions, _ = matching_target_actions(failing, DEFAULT_TARGETS)
        self.assertEqual(actions, ["restore_metadata.json", "language_fix-7.2-29.json"])

    def test_unmatched_failures_select_nothing(self) -> None:
        '''An unrelated failure must not trigger a targeted config.'''
        failing = report({"ua1": ("fail", [violation("7.99", "1")]), "wcag": ("pass", [])})
        actions, matched = matching_target_actions(failing, DEFAULT_TARGETS)
        self.assertEqual(actions, [])
        self.assertEqual(matched, [])


class OutcomeStatusTests(unittest.TestCase):
    '''The result names what the run achieved, not where a file was filed.'''

    def test_passing_after_is_remediated(self) -> None:
        '''The success case.'''
        failure = [violation("7.1", "9")]
        before = report({"ua1": ("fail", failure), "wcag": ("fail", failure)})
        after = report({"ua1": ("pass", []), "wcag": ("pass", [])})
        self.assertEqual(_outcome_status(before, after), PipelineStatus.REMEDIATED)

    def test_fewer_failures_is_improved(self) -> None:
        '''Partial progress is reported as such, not as failure.'''
        before = report({
            "ua1": ("fail", [violation("7.1", "9"), violation("5", "1")]),
            "wcag": ("pass", []),
        })
        after = report({"ua1": ("fail", [violation("7.1", "9")]), "wcag": ("pass", [])})
        self.assertEqual(_outcome_status(before, after), PipelineStatus.IMPROVED)

    def test_same_failures_is_unchanged(self) -> None:
        '''Remediation ran and achieved nothing.'''
        same = report({"ua1": ("fail", [violation("7.1", "9")]), "wcag": ("pass", [])})
        self.assertEqual(_outcome_status(same, same), PipelineStatus.UNCHANGED)

    def test_validation_error_is_failure(self) -> None:
        '''An unvalidatable result is not an improvement.'''
        before = report({"ua1": ("fail", [violation("7.1", "9")]), "wcag": ("pass", [])})
        after = {"status": "error", "passed": False, "failed_rules_count": 0, "profiles": {}}
        self.assertEqual(_outcome_status(before, after), PipelineStatus.FAILED)

    def test_wcag_only_can_be_remediated_while_pdfua1_fails(self) -> None:
        '''Only the selected WCAG profile determines this run's success.'''
        failures = [violation("7.1", "9")]
        before = report({"ua1": ("fail", failures), "wcag": ("fail", failures)})
        after = report({"ua1": ("fail", failures), "wcag": ("pass", [])})
        self.assertEqual(
            _outcome_status(before, after, ("wcag",)),
            PipelineStatus.REMEDIATED,
        )

    def test_pdfua1_only_can_be_remediated_while_wcag_fails(self) -> None:
        '''Only the selected PDF/UA-1 profile determines this run's success.'''
        failures = [violation("7.1", "9")]
        before = report({"ua1": ("fail", failures), "wcag": ("fail", failures)})
        after = report({"ua1": ("pass", []), "wcag": ("fail", failures)})
        self.assertEqual(
            _outcome_status(before, after, ("ua1",)),
            PipelineStatus.REMEDIATED,
        )

    def test_both_profiles_must_pass_when_both_are_selected(self) -> None:
        '''A partial pass is not remediated under the combined requirement.'''
        failures = [violation("7.1", "9")]
        before = report({"ua1": ("fail", failures), "wcag": ("fail", failures)})
        after = report({"ua1": ("fail", failures), "wcag": ("pass", [])})
        self.assertEqual(
            _outcome_status(before, after, ("wcag", "ua1")),
            PipelineStatus.IMPROVED,
        )

    def test_improvement_ignores_profiles_that_were_not_selected(self) -> None:
        '''Progress outside the selected requirement does not change the outcome.'''
        failures = [violation("7.1", "9")]
        before = report({
            "ua1": ("fail", [*failures, violation("5", "1")]),
            "wcag": ("fail", failures),
        })
        after = report({"ua1": ("fail", failures), "wcag": ("fail", failures)})
        self.assertEqual(
            _outcome_status(before, after, ("wcag",)),
            PipelineStatus.UNCHANGED,
        )


class InputValidationTests(unittest.TestCase):
    '''Bad requests are refused before any work starts.'''

    def setUp(self) -> None:
        '''Create a scratch directory holding one PDF.'''
        self.folder = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.pdf = self.folder / "a.pdf"
        self.pdf.write_bytes(b"%PDF-1.7\n")

    def test_accepts_a_valid_request(self) -> None:
        '''The happy path passes validation.'''
        self.assertIsNone(_validate_inputs(self.pdf, PipelineOptions()))

    def test_rejects_a_missing_file(self) -> None:
        '''A path that does not exist is refused.'''
        message = _validate_inputs(self.folder / "absent.pdf", PipelineOptions())
        self.assertIn("not found", message)

    def test_rejects_a_non_pdf_extension(self) -> None:
        '''Extension is checked before anything is opened.'''
        other = self.folder / "a.txt"
        other.write_bytes(b"hello")
        self.assertIn(".pdf extension", _validate_inputs(other, PipelineOptions()))

    def test_rejects_an_unknown_configuration(self) -> None:
        '''A typo must not silently fall back to default.json.

        get_configuration_file substitutes the default for a missing name, so
        without this check the wrong remediation would run without complaint.
        '''
        message = _validate_inputs(self.pdf, PipelineOptions(config_file="nope.json"))
        self.assertIn("nope.json", message)

    def test_rejects_an_empty_validation_requirement(self) -> None:
        '''At least one profile must determine the result.'''
        message = _validate_inputs(
            self.pdf,
            PipelineOptions(require_wcag=False, require_pdfua1=False),
        )
        self.assertIn("at least one validation profile", message)


class PipelineStageOptionTests(unittest.TestCase):
    '''Each optional repair stage honors its pipeline option.'''

    def test_core_remediation_can_be_skipped(self) -> None:
        '''Disabling remediation records a skipped stage and never invokes PDFix.'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_pdf = root / "input.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            failing = report({"wcag": ("fail", [violation("1", "1")])})
            capabilities = mock.Mock()
            capabilities.can_validate.return_value = True

            with (
                mock.patch("pdf_api.pipeline.cached_probe", return_value=capabilities),
                mock.patch("pdf_api.pipeline.stages.validate", return_value=failing),
                mock.patch("pdf_api.pipeline.stages.is_secured", return_value="unsecured"),
                mock.patch("pdf_api.pipeline.stages.run_fix") as run_fix,
            ):
                result = process_pdf(
                    input_pdf,
                    root / "output",
                    PipelineOptions(
                        attempt_fix=False,
                        attempt_font_fix=False,
                        attempt_targeted_fixes=False,
                    ),
                )

            run_fix.assert_not_called()
            self.assertFalse(result.initially_secured)
            fix_stage = next(stage for stage in result.stages if stage.name == "fix")
            self.assertEqual(str(fix_stage.status), "skipped")
            self.assertEqual(fix_stage.detail, "Disabled by request.")

    def test_secured_input_is_reported_before_the_unlock_stage(self) -> None:
        '''Already-compliant secured files still retain their initial lock state.'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_pdf = root / "input.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            passing = report({"wcag": ("pass", [])})
            capabilities = mock.Mock()
            capabilities.can_validate.return_value = True

            with (
                mock.patch("pdf_api.pipeline.cached_probe", return_value=capabilities),
                mock.patch("pdf_api.pipeline.stages.validate", return_value=passing),
                mock.patch(
                    "pdf_api.pipeline.stages.is_secured",
                    return_value="secured-needs-approval",
                ),
                mock.patch("pdf_api.pipeline.stages.unlock") as unlock,
            ):
                result = process_pdf(input_pdf, root / "output")

            self.assertTrue(result.initially_secured)
            self.assertEqual(result.status, PipelineStatus.ALREADY_COMPLIANT)
            unlock.assert_not_called()

    def test_pdfua1_only_controls_the_already_compliant_outcome(self) -> None:
        '''A selected PDF/UA-1 pass can return the original despite WCAG failure.'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_pdf = root / "input.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            selected_passes = report({
                "ua1": ("pass", []),
                "wcag": ("fail", [violation("7.1", "9")]),
            })
            capabilities = mock.Mock()
            capabilities.can_validate.return_value = True

            with (
                mock.patch("pdf_api.pipeline.cached_probe", return_value=capabilities),
                mock.patch(
                    "pdf_api.pipeline.stages.validate", return_value=selected_passes
                ),
                mock.patch(
                    "pdf_api.pipeline.stages.is_secured", return_value="unsecured"
                ),
                mock.patch("pdf_api.pipeline.stages.run_fix") as run_fix,
            ):
                result = process_pdf(
                    input_pdf,
                    root / "output",
                    PipelineOptions(require_wcag=False, require_pdfua1=True),
                )

            self.assertEqual(result.status, PipelineStatus.ALREADY_COMPLIANT)
            run_fix.assert_not_called()

    def test_secured_input_is_reported_when_unlocking_is_disabled(self) -> None:
        '''A failed run still tells the job row that its input was secured.'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_pdf = root / "input.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            failing = report({"wcag": ("fail", [violation("1", "1")])})
            capabilities = mock.Mock()
            capabilities.can_validate.return_value = True

            with (
                mock.patch("pdf_api.pipeline.cached_probe", return_value=capabilities),
                mock.patch("pdf_api.pipeline.stages.validate", return_value=failing),
                mock.patch(
                    "pdf_api.pipeline.stages.is_secured",
                    return_value="secured-cannot-process",
                ),
            ):
                result = process_pdf(
                    input_pdf, root / "output", PipelineOptions(attempt_unlock=False)
                )

            self.assertTrue(result.initially_secured)
            self.assertEqual(result.status, PipelineStatus.FAILED)
            self.assertIn("unlocking is disabled", result.error)


if __name__ == "__main__":
    unittest.main()
