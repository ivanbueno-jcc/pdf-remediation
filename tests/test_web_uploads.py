'''Tests for upload sanitization and storage in the web application.'''

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pdf_web.config import MAX_FILE_BYTES, MAX_STEM_LENGTH
from pdf_web.store import is_valid_job_id
from pdf_web.uploads import (
    UploadError,
    looks_like_pdf,
    sanitize_upload_name,
    write_upload_stream,
)


class SanitizeUploadNameTests(unittest.TestCase):
    '''Upload filenames are attacker-controlled and feed both paths and reports.'''

    def test_strips_posix_traversal(self) -> None:
        '''A traversal attempt collapses to its bare filename.'''
        self.assertEqual(
            sanitize_upload_name("../../etc/passwd.pdf", set()),
            "passwd.pdf"
        )

    def test_strips_windows_traversal(self) -> None:
        '''Backslash separators are normalized before the basename is taken.'''
        self.assertEqual(
            sanitize_upload_name(r"..\..\windows\system32\evil.pdf", set()),
            "evil.pdf"
        )

    def test_strips_absolute_path(self) -> None:
        '''An absolute path keeps only its final component.'''
        self.assertEqual(
            sanitize_upload_name("/var/root/secret.pdf", set()),
            "secret.pdf"
        )

    def test_result_never_contains_separators(self) -> None:
        '''No sanitized name can address a directory.'''
        for raw_name in ("../a.pdf", r"..\b.pdf", "/c.pdf", "d/../e.pdf"):
            with self.subTest(raw_name=raw_name):
                stored = sanitize_upload_name(raw_name, set())
                self.assertNotIn("/", stored)
                self.assertNotIn("\\", stored)
                self.assertEqual(Path(stored).name, stored)

    def test_collapses_interior_dots(self) -> None:
        '''Interior dots are removed so veraPDF report names stay unique.

        veraPDF derives its report filename from ``stem.split('.')[0]``, so
        "a.b.pdf" and "a.c.pdf" would otherwise share one XML report.
        '''
        taken: set[str] = set()
        first = sanitize_upload_name("a.b.pdf", taken)
        second = sanitize_upload_name("a.c.pdf", taken)
        self.assertEqual(first, "a_b.pdf")
        self.assertEqual(second, "a_c.pdf")
        self.assertNotEqual(
            Path(first).stem.split(".")[0],
            Path(second).stem.split(".")[0]
        )

    def test_deduplicates_repeated_names(self) -> None:
        '''Repeated filenames get a numeric suffix rather than overwriting.'''
        taken: set[str] = set()
        names = [sanitize_upload_name("report.pdf", taken) for _ in range(3)]
        self.assertEqual(names, ["report.pdf", "report-2.pdf", "report-3.pdf"])

    def test_deduplication_is_case_insensitive(self) -> None:
        '''Case-only differences still collide on case-insensitive filesystems.'''
        taken: set[str] = set()
        first = sanitize_upload_name("Report.pdf", taken)
        second = sanitize_upload_name("report.pdf", taken)
        self.assertNotEqual(first.lower(), second.lower())

    def test_truncates_long_stems(self) -> None:
        '''Long names are capped so the flattened XML name stays openable.'''
        stored = sanitize_upload_name("x" * 400 + ".pdf", set())
        self.assertEqual(len(Path(stored).stem), MAX_STEM_LENGTH)

    def test_falls_back_when_nothing_survives(self) -> None:
        '''A name made entirely of stripped characters still yields a filename.'''
        self.assertEqual(sanitize_upload_name("...pdf", set()), "upload.pdf")

    def test_accepts_uppercase_extension(self) -> None:
        '''The extension check is case-insensitive.'''
        self.assertEqual(sanitize_upload_name("Scan.PDF", set()), "Scan.pdf")

    def test_rejects_non_pdf(self) -> None:
        '''Anything without a .pdf extension is refused.'''
        for raw_name in ("notes.txt", "archive.zip", "noextension", "x.pdf.exe"):
            with self.subTest(raw_name=raw_name):
                with self.assertRaises(UploadError):
                    sanitize_upload_name(raw_name, set())


class WriteUploadStreamTests(unittest.TestCase):
    '''Uploads stream to disk under a size cap.'''

    def setUp(self) -> None:
        '''Create a scratch directory for each test.'''
        # enterContext hands the directory to this test's cleanup.
        self.folder = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))

    def test_writes_and_reports_size(self) -> None:
        '''A normal upload lands intact and reports its byte count.'''
        destination = self.folder / "a.pdf"
        size = write_upload_stream([b"%PDF-", b"body"], destination, "a.pdf")
        self.assertEqual(size, 9)
        self.assertEqual(destination.read_bytes(), b"%PDF-body")

    def test_rejects_and_removes_oversized_upload(self) -> None:
        '''An oversized upload aborts mid-write and leaves nothing behind.'''
        destination = self.folder / "big.pdf"
        chunks = [b"x" * (1024 * 1024)] * (MAX_FILE_BYTES // (1024 * 1024) + 2)
        with self.assertRaises(UploadError):
            write_upload_stream(chunks, destination, "big.pdf")
        self.assertFalse(destination.exists())

    def test_rejects_empty_upload(self) -> None:
        '''An empty file is refused rather than queued for the pipeline.'''
        destination = self.folder / "empty.pdf"
        with self.assertRaises(UploadError):
            write_upload_stream([], destination, "empty.pdf")
        self.assertFalse(destination.exists())


class LooksLikePdfTests(unittest.TestCase):
    '''A .pdf extension alone does not make a file a PDF.'''

    def setUp(self) -> None:
        '''Create a scratch directory for each test.'''
        # enterContext hands the directory to this test's cleanup.
        self.folder = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))

    def test_accepts_pdf_header(self) -> None:
        '''A real PDF header is accepted.'''
        path = self.folder / "real.pdf"
        path.write_bytes(b"%PDF-1.7\nrest")
        self.assertTrue(looks_like_pdf(path))

    def test_rejects_other_content(self) -> None:
        '''Content without the PDF header is rejected.'''
        path = self.folder / "fake.pdf"
        path.write_bytes(b"not a pdf at all")
        self.assertFalse(looks_like_pdf(path))

    def test_rejects_missing_file(self) -> None:
        '''A missing file is rejected rather than raising.'''
        self.assertFalse(looks_like_pdf(self.folder / "absent.pdf"))


class JobIdValidationTests(unittest.TestCase):
    '''Job ids address directories, so the format is a path-safety boundary.'''

    def test_accepts_generated_shape(self) -> None:
        '''The identifier format the application generates is accepted.'''
        self.assertTrue(is_valid_job_id("20260827-151733-baf398"))

    def test_rejects_traversal_and_malformed_ids(self) -> None:
        '''Anything that could escape the jobs directory is rejected.'''
        for job_id in (
            "../../etc/passwd",
            "20260827-151733-baf398/..",
            "20260827-151733-BAF398",
            "20260827-151733-baf39",
            "notajob",
            "",
        ):
            with self.subTest(job_id=job_id):
                self.assertFalse(is_valid_job_id(job_id))


if __name__ == "__main__":
    unittest.main()
