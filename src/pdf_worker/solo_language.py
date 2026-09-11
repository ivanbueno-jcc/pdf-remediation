'''Read or set the primary language of one PDF.'''

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from pdfixsdk import GetPdfix, kSaveFull


NO_LANGUAGE = "no language set"


class SoloLanguageError(RuntimeError):
    '''Operational error while reading or setting a PDF language.'''


def get_pdfix_error(pdfix: Any) -> str:
    '''Return the current PDFix error with a useful fallback.'''
    error = str(pdfix.GetError()).strip()
    return error if error and error != "No error." else "Unknown PDFix error"


def authorize_pdfix(pdfix: Any) -> None:
    '''Authorize PDFix when credentials are configured.'''
    load_dotenv()
    license_name = os.getenv("PDFIX_LICENSE_NAME")
    license_key = os.getenv("PDFIX_LICENSE_KEY")
    if license_name and license_key:
        pdfix.GetAccountAuthorization().Authorize(license_name, license_key)


def validate_input(pdf_input_path: Path) -> None:
    '''Validate an input PDF path.'''
    if not pdf_input_path.is_file():
        raise SoloLanguageError(f"Input PDF not found: {pdf_input_path}")
    if pdf_input_path.suffix.lower() != ".pdf":
        raise SoloLanguageError(
            f"Input file must use a .pdf extension: {pdf_input_path}"
        )


def validate_output(pdf_input_path: Path, pdf_output_path: Path) -> None:
    '''Validate an output PDF path.'''
    if pdf_output_path.suffix.lower() != ".pdf":
        raise SoloLanguageError(
            f"Output file must use a .pdf extension: {pdf_output_path}"
        )
    if pdf_input_path == pdf_output_path:
        raise SoloLanguageError("Input and output PDF paths must be different.")


def open_pdf(pdf_input_path: Path) -> tuple[Any, Any]:
    '''Initialize PDFix and open a PDF.'''
    pdfix = GetPdfix()
    if pdfix is None:
        raise SoloLanguageError("PDFix initialization failed.")
    authorize_pdfix(pdfix)
    doc = pdfix.OpenDoc(str(pdf_input_path), "")
    if doc is None:
        raise SoloLanguageError(
            "Unable to open PDF: " + get_pdfix_error(pdfix)
        )
    return pdfix, doc


def read_document_language(pdf_input_path: str) -> str:
    '''Return the primary document language, or ``no language set``.'''
    input_path = Path(pdf_input_path).expanduser().resolve()
    validate_input(input_path)
    _pdfix, doc = open_pdf(input_path)
    try:
        return doc.GetLang().strip() or NO_LANGUAGE
    finally:
        doc.Close()


def get_language(pdf_input_path: str) -> str:
    '''Compatibility-facing alias for :func:`read_document_language`.'''
    return read_document_language(pdf_input_path)


def _temporary_output_path(pdf_output_path: Path) -> Path:
    return pdf_output_path.with_name(
        f".{pdf_output_path.stem}.{uuid4().hex}.tmp.pdf"
    )


def set_document_language(
        pdf_input_path: str, pdf_output_path: str, language: str) -> dict[str, Any]:
    '''Set the primary document language without changing the source PDF.'''
    input_path = Path(pdf_input_path).expanduser().resolve()
    output_path = Path(pdf_output_path).expanduser().resolve()
    temporary_path: Path | None = None
    doc = None
    try:
        validate_input(input_path)
        validate_output(input_path, output_path)
        language = language.strip()
        if not language:
            raise SoloLanguageError("Language must not be empty.")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        pdfix, doc = open_pdf(input_path)
        if not doc.SetLang(language):
            raise SoloLanguageError(
                "Unable to set PDF language: " + get_pdfix_error(pdfix)
            )
        temporary_path = _temporary_output_path(output_path)
        if not doc.Save(str(temporary_path), kSaveFull):
            raise SoloLanguageError(
                "Unable to save PDF: " + get_pdfix_error(pdfix)
            )
        doc.Close()
        doc = None

        _verified_pdfix, verified_doc = open_pdf(temporary_path)
        try:
            actual_language = verified_doc.GetLang().strip()
        finally:
            verified_doc.Close()
        if actual_language != language:
            raise SoloLanguageError(
                f"Output PDF language is {actual_language or NO_LANGUAGE!r}, "
                f"expected {language!r}."
            )
        temporary_path.replace(output_path)
        return {
            "input_pdf_path": str(input_path),
            "output_pdf_path": str(output_path),
            "language": language,
            "status": "success",
            "exit_code": 0,
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            "input_pdf_path": str(input_path),
            "output_pdf_path": str(output_path),
            "language": language,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "exit_code": 1,
        }
    finally:
        if doc is not None:
            doc.Close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def set_language(
        pdf_input_path: str, pdf_output_path: str, language: str) -> dict[str, Any]:
    '''Compatibility-facing alias for :func:`set_document_language`.'''
    return set_document_language(pdf_input_path, pdf_output_path, language)


def build_parser() -> argparse.ArgumentParser:
    '''Build the CLI parser.'''
    parser = argparse.ArgumentParser(description="Read or set a PDF document language.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    get_parser = subparsers.add_parser("get", help="Read the document language.")
    get_parser.add_argument("pdf_input_path")
    set_parser = subparsers.add_parser("set", help="Set the document language.")
    set_parser.add_argument("pdf_input_path")
    set_parser.add_argument("pdf_output_path")
    set_parser.add_argument("language")
    set_parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    '''CLI entrypoint.'''
    args = build_parser().parse_args(argv)
    if args.command == "get":
        try:
            print(read_document_language(args.pdf_input_path))
            return 0
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    result = set_document_language(
        args.pdf_input_path, args.pdf_output_path, args.language
    )
    print(json.dumps(
        result,
        indent=None if args.compact else 2,
        sort_keys=True,
    ))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
