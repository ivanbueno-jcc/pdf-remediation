'''Tests for the standalone PDF security removal command.'''

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pdfixsdk import (  # pylint: disable=wrong-import-position
    GetPdfix,
    PdfRect,
    PdfStandardSecurityParams,
    kSaveFull,
)
from pdf_worker import solo_remove_security  # pylint: disable=wrong-import-position


def create_pdf(pdf_path: Path, secured: bool) -> None:
    '''Create a one-page PDFix fixture, optionally protected by an empty password.'''
    pdfix = GetPdfix()
    if pdfix is None:
        raise RuntimeError("PDFix initialization failed.")

    doc = pdfix.CreateDoc()
    rect = PdfRect()
    rect.left = 0
    rect.bottom = 0
    rect.right = 612
    rect.top = 792
    if doc.CreatePage(0, rect) is None:
        raise RuntimeError(f"Unable to create test page: {pdfix.GetError()}")

    if secured:
        security_handler = pdfix.CreateStandardSecurityHandler(
            "", "owner", PdfStandardSecurityParams()
        )
        if security_handler is None or not doc.SetSecurityHandler(security_handler):
            raise RuntimeError(f"Unable to secure test PDF: {pdfix.GetError()}")

    try:
        if not doc.Save(str(pdf_path), kSaveFull):
            raise RuntimeError(f"Unable to save test PDF: {pdfix.GetError()}")
    finally:
        doc.Close()


class SoloRemoveSecurityTests(unittest.TestCase):
    '''Unit and PDFix integration tests for remove_security().'''

    def test_returns_error_for_missing_input(self) -> None:
        '''A missing PDF produces a structured error without creating output.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_path = root / "output.pdf"

            result = solo_remove_security.remove_security(
                str(root / "missing.pdf"), str(output_path)
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["exit_code"], 1)
            self.assertIn("Input PDF not found", result["error"])
            self.assertFalse(output_path.exists())

    def test_returns_error_for_non_pdf_input(self) -> None:
        '''A non-PDF input is rejected before PDFix is initialized.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.txt"
            input_path.write_text("not a PDF", encoding="utf-8")

            result = solo_remove_security.remove_security(
                str(input_path), str(root / "output.pdf")
            )

            self.assertEqual(result["status"], "error")
            self.assertIn(".pdf extension", result["error"])

    def test_returns_error_when_input_and_output_match(self) -> None:
        '''The source path cannot also be the requested output path.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.pdf"
            input_path.write_bytes(b"not opened")

            result = solo_remove_security.remove_security(str(input_path), str(input_path))

            self.assertEqual(result["status"], "error")
            self.assertIn("must be different", result["error"])

    @mock.patch.object(solo_remove_security, "GetPdfix")
    def test_returns_error_when_pdfix_cannot_open_input(self, get_pdfix: mock.Mock) -> None:
        '''PDFix open failures remain structured and do not create output.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.pdf"
            output_path = root / "output.pdf"
            input_path.write_bytes(b"placeholder")
            pdfix = mock.Mock()
            pdfix.OpenDoc.return_value = None
            pdfix.GetError.return_value = "Password required"
            get_pdfix.return_value = pdfix

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            self.assertEqual(result["status"], "error")
            self.assertIn("empty password", result["error"])
            self.assertFalse(output_path.exists())

    @mock.patch.object(solo_remove_security, "load_dotenv")
    @mock.patch.object(solo_remove_security, "GetPdfix")
    def test_passes_configured_license_to_pdfix(
            self,
            get_pdfix: mock.Mock,
            load_dotenv: mock.Mock) -> None:
        '''Configured account credentials are passed before the PDF is opened.'''
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
                solo_remove_security.os.environ,
                {
                    "PDFIX_LICENSE_NAME": "test-license-name",
                    "PDFIX_LICENSE_KEY": "test-license-key",
                }):
            root = Path(temp_dir)
            input_path = root / "input.pdf"
            output_path = root / "output.pdf"
            input_path.write_bytes(b"unsecured")
            doc = mock.Mock()
            doc.IsSecured.return_value = False
            pdfix = mock.Mock()
            pdfix.OpenDoc.return_value = doc
            get_pdfix.return_value = pdfix

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            load_dotenv.assert_called_once_with()
            pdfix.GetAccountAuthorization.return_value.Authorize.assert_called_once_with(
                "test-license-name", "test-license-key"
            )
            pdfix.OpenDoc.assert_called_once_with(str(input_path.resolve()), "")
            self.assertEqual(result["status"], "already_unsecured")

    @mock.patch.object(solo_remove_security, "GetPdfix")
    def test_returns_error_when_save_fails(self, get_pdfix: mock.Mock) -> None:
        '''A failed PDFix save leaves no output file behind.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.pdf"
            output_path = root / "output.pdf"
            input_path.write_bytes(b"placeholder")
            doc = mock.Mock()
            doc.IsSecured.return_value = True
            doc.GetNumFormFields.return_value = 0
            doc.SetSecurityHandler.return_value = True
            doc.Save.return_value = False
            pdfix = mock.Mock()
            pdfix.OpenDoc.return_value = doc
            pdfix.GetError.return_value = "Save failed"
            get_pdfix.return_value = pdfix

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            self.assertEqual(result["status"], "error")
            self.assertIn("Unable to save unsecured PDF", result["error"])
            self.assertFalse(output_path.exists())
            doc.Close.assert_called_once()

    @mock.patch.object(solo_remove_security, "GetPdfix")
    def test_returns_error_when_saved_output_remains_secured(self, get_pdfix: mock.Mock) -> None:
        '''Post-save verification prevents a still-secured output from being published.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.pdf"
            output_path = root / "output.pdf"
            input_path.write_bytes(b"placeholder")
            input_doc = mock.Mock()
            input_doc.IsSecured.return_value = True
            input_doc.GetNumFormFields.return_value = 0
            input_doc.SetSecurityHandler.return_value = True
            input_doc.Save.return_value = True
            verified_doc = mock.Mock()
            verified_doc.IsSecured.return_value = True
            pdfix = mock.Mock()
            pdfix.OpenDoc.side_effect = [input_doc, verified_doc]
            get_pdfix.return_value = pdfix

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            self.assertEqual(result["status"], "error")
            self.assertIn("still secured", result["error"])
            self.assertFalse(output_path.exists())
            verified_doc.Close.assert_called_once()

    @mock.patch.object(solo_remove_security, "GetPdfix")
    def test_warns_when_removing_security_from_signed_pdf(self, get_pdfix: mock.Mock) -> None:
        '''Signature fields are reported when rewriting a secured PDF.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.pdf"
            output_path = root / "output.pdf"
            input_path.write_bytes(b"placeholder")
            signature_field = mock.Mock()
            signature_field.GetType.return_value = solo_remove_security.kFieldSignature
            input_doc = mock.Mock()
            input_doc.IsSecured.return_value = True
            input_doc.GetNumFormFields.return_value = 1
            input_doc.GetFormField.return_value = signature_field
            input_doc.SetSecurityHandler.return_value = True

            def save_pdf(path: str, _flags: int) -> bool:
                Path(path).write_bytes(b"unsecured")
                return True

            input_doc.Save.side_effect = save_pdf
            verified_doc = mock.Mock()
            verified_doc.IsSecured.return_value = False
            pdfix = mock.Mock()
            pdfix.OpenDoc.side_effect = [input_doc, verified_doc]
            get_pdfix.return_value = pdfix

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["signature_fields_detected"], 1)
            self.assertIn("Digital signatures", result["warnings"][0])
            self.assertEqual(output_path.read_bytes(), b"unsecured")

    def test_removes_empty_password_security_with_pdfix(self) -> None:
        '''A real PDFix encrypted fixture is saved with security removed.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "secured.pdf"
            output_path = root / "unsecured.pdf"
            create_pdf(input_path, secured=True)

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["security_removed"])
            self.assertTrue(input_path.exists())
            self.assertTrue(output_path.is_file())
            pdfix = GetPdfix()
            output_doc = pdfix.OpenDoc(str(output_path), "")
            self.assertIsNotNone(output_doc)
            try:
                self.assertFalse(output_doc.IsSecured())
            finally:
                output_doc.Close()

    def test_copies_already_unsecured_pdf_without_rewriting_it(self) -> None:
        '''Unsecured PDFs are copied byte-for-byte and reported as idempotent.'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.pdf"
            output_path = root / "output.pdf"
            create_pdf(input_path, secured=False)
            source_data = input_path.read_bytes()

            result = solo_remove_security.remove_security(str(input_path), str(output_path))

            self.assertEqual(result["status"], "already_unsecured")
            self.assertFalse(result["security_removed"])
            self.assertEqual(output_path.read_bytes(), source_data)

    @mock.patch.object(solo_remove_security, "remove_security")
    def test_compact_cli_output_is_one_line_json(self, remove_security: mock.Mock) -> None:
        '''The compact flag writes one JSON object on a single line.'''
        remove_security.return_value = {
            "status": "success",
            "exit_code": 0,
            "security_removed": True,
        }
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = solo_remove_security.main(["input.pdf", "output.pdf", "--compact"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), remove_security.return_value)
        self.assertEqual(stdout.getvalue().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
