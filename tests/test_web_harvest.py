'''Tests for mapping uploads onto the pipeline's reports and output files.'''

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pdf_web.harvest import (
    FULL_VALIDATION_EXCLUDED,
    as_child_path,
    build_job_summary,
    build_stage_report,
    describe_missing_after,
    describe_outcome,
    expected_xml_name,
    find_final_pdf,
    latest_report_folder,
    load_results_index,
    read_report_xml,
    status_from_csv_value,
)
from pdf_web.models import FileResult

RESULTS_CSV_HEADER = (
    "path,ua1,ua1_failed_rules_count,wcag,wcag_failed_rules_count\n"
)

VIOLATION_XML = '''<?xml version="1.0" encoding="utf-8"?>
<report>
  <rule specification="ISO 14289-1:2014" clause="7.1" testNumber="9" tags="">
    <description>Metadata stream shall contain a dc:title entry</description>
  </rule>
</report>
'''


def verapdf_report_filename(pdf_path: Path, extension: str = "xml") -> str:
    '''Reimplement the report filename veraPDF builds, as an oracle.

    Mirrors utilities/verapdf.py lines 107-113. Kept independent of the
    application code on purpose: if expected_xml_name drifts from what
    veraPDF actually writes, violation lists go silently empty while the
    counts from the CSV keep working, so this must break loudly instead.
    '''
    parent_path = Path(pdf_path).parent.as_posix()
    parent_path_str = parent_path.replace("/", "-")
    stem = Path(pdf_path).stem.split(".")[0]
    return f"{parent_path_str}-{stem}.{extension}"


class ExpectedXmlNameTests(unittest.TestCase):
    '''The XML lookup depends on reproducing veraPDF's filename exactly.'''

    def test_matches_verapdf_formula(self) -> None:
        '''The reconstructed name equals what veraPDF would have written.'''
        candidates = [
            Path("resources/web-jobs/20260827-1-abc/p/workspace/default"
                 "/active/files/Sample_Report.pdf"),
            Path("resources/web-jobs/j/p/workspace/default/remediated"
                 "/files/report.pdf"),
            Path("a/b.pdf"),
            Path("deep/nested/path/with-dashes/file-name.pdf"),
            # An interior dot is the case that distinguishes veraPDF's
            # stem.split('.')[0] from a plain stem.
            Path("files/a.b.pdf"),
            Path("files/version.2.final.pdf"),
        ]
        for pdf_path in candidates:
            with self.subTest(pdf_path=str(pdf_path)):
                self.assertEqual(
                    expected_xml_name(pdf_path),
                    verapdf_report_filename(pdf_path)
                )

    def test_truncates_at_the_first_interior_dot(self) -> None:
        '''veraPDF keeps only the stem up to its first dot.

        This is why uploads collapse interior dots: without that, "a.b.pdf"
        and "a.c.pdf" would both report to "...-files-a.xml".
        '''
        self.assertEqual(expected_xml_name(Path("files/a.b.pdf")), "files-a.xml")
        self.assertEqual(
            expected_xml_name(Path("files/a.b.pdf")),
            expected_xml_name(Path("files/a.c.pdf")),
            "collision is veraPDF's behaviour; sanitization is what prevents it"
        )

    def test_matches_observed_report_name(self) -> None:
        '''The formula reproduces a filename observed on a real run.'''
        pdf_path = Path(
            "resources/web-jobs/20260827-151733-baf398/p/workspace/default"
            "/active/files/Sample_Report_v2.pdf"
        )
        self.assertEqual(
            expected_xml_name(pdf_path),
            "resources-web-jobs-20260827-151733-baf398-p-workspace-default"
            "-active-files-Sample_Report_v2.xml"
        )


class ReadReportXmlTests(unittest.TestCase):
    '''Locating one PDF's XML report inside a flat report folder.'''

    def setUp(self) -> None:
        '''Create a report folder with a ua1 profile directory.'''
        # enterContext hands the directory to this test's cleanup.
        self.root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.report_folder = self.root / "20260827_120000-full"
        self.xml_folder = self.report_folder / "xml" / "ua1"
        self.xml_folder.mkdir(parents=True)

    def test_finds_exact_reconstructed_name(self) -> None:
        '''The primary path is a direct filename reconstruction.'''
        pdf_path = self.root / "remediated" / "files" / "a.pdf"
        name = verapdf_report_filename(as_child_path(pdf_path))
        (self.xml_folder / name).write_text(VIOLATION_XML, encoding="utf-8")
        self.assertEqual(
            read_report_xml(self.report_folder, "ua1", pdf_path, "a.pdf"),
            VIOLATION_XML
        )

    def test_falls_back_to_unique_suffix_match(self) -> None:
        '''A differing path form still resolves through the suffix fallback.'''
        pdf_path = Path("/somewhere/else/remediated/files/a.pdf")
        (self.xml_folder / "other-root-remediated-files-a.xml").write_text(
            VIOLATION_XML, encoding="utf-8"
        )
        self.assertEqual(
            read_report_xml(self.report_folder, "ua1", pdf_path, "a.pdf"),
            VIOLATION_XML
        )

    def test_fallback_anchors_on_parent_folder(self) -> None:
        '''"report" must not match a report written for "final-report".'''
        pdf_path = Path("/root/remediated/files/report.pdf")
        (self.xml_folder / "x-remediated-files-final-report.xml").write_text(
            VIOLATION_XML, encoding="utf-8"
        )
        self.assertIsNone(
            read_report_xml(self.report_folder, "ua1", pdf_path, "report.pdf")
        )

    def test_ambiguous_fallback_returns_none(self) -> None:
        '''Two candidates are a guess, so nothing is returned.'''
        pdf_path = Path("/root/remediated/files/a.pdf")
        for prefix in ("one", "two"):
            (self.xml_folder / f"{prefix}-remediated-files-a.xml").write_text(
                VIOLATION_XML, encoding="utf-8"
            )
        self.assertIsNone(
            read_report_xml(self.report_folder, "ua1", pdf_path, "a.pdf")
        )

    def test_missing_profile_folder_returns_none(self) -> None:
        '''An absent profile directory is not an error.'''
        self.assertIsNone(
            read_report_xml(self.report_folder, "wcag", Path("/a/b.pdf"), "b.pdf")
        )


class StatusFromCsvValueTests(unittest.TestCase):
    '''veraPDF statuses arrive as CSV text, including the error sentinel.'''

    def test_maps_known_values(self) -> None:
        '''True, False, and Error map to the reported statuses.'''
        self.assertEqual(status_from_csv_value("True"), "pass")
        self.assertEqual(status_from_csv_value("False"), "fail")
        self.assertEqual(status_from_csv_value("Error"), "error")

    def test_maps_unknown_values(self) -> None:
        '''Anything unexpected is reported as unknown rather than passing.'''
        for value in ("", None, "maybe"):
            with self.subTest(value=value):
                self.assertEqual(status_from_csv_value(value), "unknown")


class ReportFolderTests(unittest.TestCase):
    '''Report folders are timestamped, so the newest one wins.'''

    def setUp(self) -> None:
        '''Create a reports directory.'''
        # enterContext hands the directory to this test's cleanup.
        self.reports = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))

    def test_picks_newest_matching_suffix(self) -> None:
        '''Timestamped names sort chronologically, so the last one is newest.'''
        for name in ("20260101_000000-full", "20260827_235959-full",
                     "20260501_120000-full", "20260901_000000-pre-fix"):
            (self.reports / name).mkdir()
        self.assertEqual(
            latest_report_folder(self.reports, "full").name,
            "20260827_235959-full"
        )

    def test_returns_none_without_matches(self) -> None:
        '''A missing or empty reports directory yields nothing.'''
        self.assertIsNone(latest_report_folder(self.reports, "full"))
        self.assertIsNone(latest_report_folder(self.reports / "absent", "full"))

    def test_indexes_results_by_basename(self) -> None:
        '''The two validation passes write different path forms for one file.'''
        folder = self.reports / "20260827_120000-full"
        folder.mkdir()
        (folder / "vera_validation_results.csv").write_text(
            RESULTS_CSV_HEADER
            + "/files/a.pdf,False,4,True,0\n"
            + "/b.pdf,True,0,True,0\n",
            encoding="utf-8"
        )
        index = load_results_index(folder)
        self.assertEqual(sorted(index), ["a.pdf", "b.pdf"])
        self.assertEqual(index["a.pdf"]["ua1_failed_rules_count"], "4")


class BuildStageReportTests(unittest.TestCase):
    '''Status comes from the CSV; the XML only supplies violation detail.'''

    def setUp(self) -> None:
        '''Create a report folder containing a results CSV.'''
        # enterContext hands the directory to this test's cleanup.
        self.root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.folder = self.root / "20260827_120000-full"
        (self.folder / "xml" / "ua1").mkdir(parents=True)
        (self.folder / "xml" / "wcag").mkdir(parents=True)

    def test_error_status_survives_missing_xml(self) -> None:
        '''veraPDF writes no XML when validation errors, and that is not a pass.

        Inferring status from XML presence would report these as passing.
        '''
        row = {
            "path": "/a.pdf", "ua1": "Error", "ua1_failed_rules_count": "0",
            "wcag": "Error", "wcag_failed_rules_count": "0",
        }
        report = build_stage_report(self.folder, row, None, "a.pdf", "before")
        self.assertEqual(report["status"], "error")
        self.assertFalse(report["passed"])
        self.assertEqual(report["profiles"]["ua1"]["status"], "error")
        self.assertEqual(report["profiles"]["ua1"]["violations"], [])

    def test_pass_requires_every_profile(self) -> None:
        '''A file passes only when both profiles pass.'''
        row = {
            "path": "/a.pdf", "ua1": "False", "ua1_failed_rules_count": "2",
            "wcag": "True", "wcag_failed_rules_count": "0",
        }
        report = build_stage_report(self.folder, row, None, "a.pdf", "after")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["failed_rules_count"], 2)

    def test_reads_violations_from_xml(self) -> None:
        '''Violation detail is parsed out of the stored report.'''
        pdf_path = self.root / "files" / "a.pdf"
        name = verapdf_report_filename(as_child_path(pdf_path))
        (self.folder / "xml" / "ua1" / name).write_text(
            VIOLATION_XML, encoding="utf-8"
        )
        row = {
            "path": "/a.pdf", "ua1": "False", "ua1_failed_rules_count": "1",
            "wcag": "True", "wcag_failed_rules_count": "0",
        }
        report = build_stage_report(self.folder, row, pdf_path, "a.pdf", "before")
        violations = report["profiles"]["ua1"]["violations"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["clause_test"], "7.1-9")

    def test_returns_none_without_a_row(self) -> None:
        '''A file absent from the CSV has no report for that stage.'''
        self.assertIsNone(
            build_stage_report(self.folder, None, None, "a.pdf", "after")
        )
        self.assertIsNone(
            build_stage_report(None, {"path": "/a.pdf"}, None, "a.pdf", "after")
        )


class FindFinalPdfTests(unittest.TestCase):
    '''A file's resting folder is its outcome, most successful first.'''

    def setUp(self) -> None:
        '''Create a workspace directory.'''
        # enterContext hands the directory to this test's cleanup.
        self.workspace = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))

    def _place(self, folder: str, directory: str, name: str) -> None:
        '''Put a stub PDF in one workspace folder.'''
        target = self.workspace / folder / directory
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(b"%PDF-")

    def test_prefers_remediated_over_other_folders(self) -> None:
        '''Remediated outranks every less successful routing folder.'''
        self._place("active", "files", "a.pdf")
        self._place("font-issues", "files", "a.pdf")
        self._place("remediated", "files", "a.pdf")
        outcome, path = find_final_pdf(self.workspace, "a.pdf")
        self.assertEqual(outcome, "remediated")
        self.assertTrue(path.is_file())

    def test_reports_routing_folder(self) -> None:
        '''A file routed to an error folder reports that folder.'''
        self._place("pdfix-unable-to-open", "files", "broken.pdf")
        outcome, _ = find_final_pdf(self.workspace, "broken.pdf")
        self.assertEqual(outcome, "pdfix-unable-to-open")
        self.assertIn(outcome, FULL_VALIDATION_EXCLUDED)

    def test_returns_none_when_absent(self) -> None:
        '''A file the pipeline never produced is not located.'''
        self.assertIsNone(find_final_pdf(self.workspace, "ghost.pdf"))


class SummaryTests(unittest.TestCase):
    '''Batch totals and clause rollups drive what the operator sees first.'''

    @staticmethod
    def _report(status: str, clauses: list[str]) -> dict:
        '''Build a minimal stage report with the given failing clauses.'''
        return {
            "status": status,
            "passed": status == "pass",
            "failed_rules_count": len(clauses),
            "profiles": {
                "ua1": {
                    "status": status,
                    "passed": status == "pass",
                    "failed_rules_count": len(clauses),
                    "violations": [
                        {"clause_test": clause, "description": f"desc {clause}"}
                        for clause in clauses
                    ],
                },
                "wcag": {
                    "status": "pass", "passed": True,
                    "failed_rules_count": 0, "violations": [],
                },
            },
        }

    def test_counts_totals_per_stage(self) -> None:
        '''Totals count each status, including files with no report.'''
        results = [
            FileResult("000", "remediated",
                       before=self._report("fail", ["7.1-9"]),
                       after=self._report("pass", [])),
            FileResult("001", "active",
                       before=self._report("fail", ["7.1-9"]),
                       after=self._report("fail", ["7.1-9"])),
            FileResult("002", "pdfix-unable-to-open",
                       before=self._report("error", []),
                       after=None),
        ]
        summary = build_job_summary(results)
        self.assertEqual(
            summary["before"]["totals"],
            {"pass": 0, "fail": 2, "error": 1, "none": 0}
        )
        self.assertEqual(
            summary["after"]["totals"],
            {"pass": 1, "fail": 1, "error": 0, "none": 1}
        )

    def test_rolls_up_clauses_by_file_count(self) -> None:
        '''Each clause counts the files it affects, worst first.'''
        results = [
            FileResult("000", "active", before=self._report("fail", ["7.1-9", "5-1"])),
            FileResult("001", "active", before=self._report("fail", ["7.1-9"])),
        ]
        clauses = build_job_summary(results)["before"]["clauses"]
        self.assertEqual(
            [(entry["clause_test"], entry["file_count"]) for entry in clauses],
            [("7.1-9", 2), ("5-1", 1)]
        )
        self.assertEqual(clauses[0]["profiles"], ["ua1"])
        self.assertEqual(clauses[0]["description"], "desc 7.1-9")

    def test_counts_each_file_once_per_clause(self) -> None:
        '''A clause failing repeatedly in one file still counts one file.'''
        results = [
            FileResult("000", "active",
                       before=self._report("fail", ["7.1-9", "7.1-9", "7.1-9"]))
        ]
        clauses = build_job_summary(results)["before"]["clauses"]
        self.assertEqual(clauses[0]["file_count"], 1)


class NoteTests(unittest.TestCase):
    '''Outcomes that are easy to misread carry an explanation.'''

    def test_explains_exclusion_from_final_validation(self) -> None:
        '''Error-routed files are never in the final report, and say so.'''
        note = describe_missing_after("pdfix-unable-to-open", None, Path("x"))
        self.assertIn("Excluded from final validation", note)

    def test_explains_a_run_that_stopped_early(self) -> None:
        '''No final report at all means the pipeline stopped before step 7.'''
        self.assertEqual(
            describe_missing_after("remediated", None, None),
            "Final validation did not run."
        )

    def test_no_note_when_after_report_exists(self) -> None:
        '''A file with a final report needs no explanation.'''
        self.assertIsNone(describe_missing_after("remediated", {"status": "pass"}, Path("x")))

    def test_distinguishes_still_failing_from_in_flight(self) -> None:
        '''"active" means still failing once the run is complete.'''
        self.assertIn("still fails", describe_outcome("active", True))
        self.assertIn("stopped", describe_outcome("active", False))
        self.assertIsNone(describe_outcome("remediated", True))


if __name__ == "__main__":
    unittest.main()
