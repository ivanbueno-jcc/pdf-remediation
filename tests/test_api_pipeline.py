'''Tests for the single-PDF pipeline's decision logic and scratch workspace.'''

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pdf_api.models import (
    DEFAULT_TARGETS,
    FONT_ISSUE_CLAUSES,
    MISSING_UNICODE_CLAUSE,
    PipelineOptions,
    PipelineStatus,
)
from pdf_api.pipeline import _outcome_status, _validate_inputs
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


if __name__ == "__main__":
    unittest.main()
