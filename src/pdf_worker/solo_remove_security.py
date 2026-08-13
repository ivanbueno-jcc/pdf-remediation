'''Remove standard PDF security from one PDF and print the result as JSON.'''

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pdfixsdk import GetPdfix, kFieldSignature, kSaveFull


class SoloRemoveSecurityError(RuntimeError):
    '''Operational error while removing security from a single PDF.'''


def now_iso() -> str:
    '''Return a local ISO-8601 timestamp for JSON output.'''
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_pdfix_error(pdfix: Any) -> str:
    '''Return the current PDFix error, with a useful fallback message.'''
    error = str(pdfix.GetError()).strip()
    if error and error != "No error.":
        return error
    return "Unknown PDFix error"


# pylint: disable=too-many-positional-arguments,too-many-arguments
def build_result(
        pdf_input_path: Path,
        pdf_output_path: Path,
        status: str,
        security_removed: bool,
        started_at: str,
        completed_at: str,
        exit_code: int,
        signature_fields_detected: int = 0,
        error: str | None = None) -> dict[str, Any]:
    '''Build the JSON payload for stdout.'''
    result = {
        "input_pdf_path": str(pdf_input_path),
        "output_pdf_path": str(pdf_output_path),
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "security_removed": security_removed,
        "exit_code": exit_code,
    }
    if signature_fields_detected:
        result["signature_fields_detected"] = signature_fields_detected
        result["warnings"] = [
            "Digital signatures in the output are invalid because removing "
            "security rewrites the PDF."
        ]
    if error:
        result["error"] = error
    return result


def validate_inputs(pdf_input_path: Path, pdf_output_path: Path) -> None:
    '''Validate CLI inputs before PDFix work starts.'''
    if not pdf_input_path.is_file():
        raise SoloRemoveSecurityError(f"Input PDF not found: {pdf_input_path}")
    if pdf_input_path.suffix.lower() != ".pdf":
        raise SoloRemoveSecurityError(
            f"Input file must use a .pdf extension: {pdf_input_path}"
        )
    if pdf_output_path.suffix.lower() != ".pdf":
        raise SoloRemoveSecurityError(
            f"Output file must use a .pdf extension: {pdf_output_path}"
        )
    if pdf_input_path == pdf_output_path:
        raise SoloRemoveSecurityError("Input and output PDF paths must be different.")


def make_temporary_output_path(pdf_output_path: Path) -> Path:
    '''Return a temporary PDF path beside the requested output path.'''
    return pdf_output_path.with_name(
        f".{pdf_output_path.stem}.{uuid4().hex}.tmp.pdf"
    )


def get_signature_field_count(doc: Any) -> int:
    '''Return the number of signature form fields present in a document.'''
    signature_fields = 0
    for index in range(doc.GetNumFormFields()):
        field = doc.GetFormField(index)
        if field is not None and field.GetType() == kFieldSignature:
            signature_fields += 1
    return signature_fields


def copy_output_file(pdf_input_path: Path, pdf_output_path: Path) -> None:
    '''Atomically copy an already-unsecured PDF without rewriting it.'''
    temporary_output_path = make_temporary_output_path(pdf_output_path)
    try:
        shutil.copy2(pdf_input_path, temporary_output_path)
        temporary_output_path.replace(pdf_output_path)
    finally:
        temporary_output_path.unlink(missing_ok=True)


def verify_unsecured_output(pdfix: Any, pdf_output_path: Path) -> None:
    '''Require the temporary output to reopen without PDF security enabled.'''
    verified_doc = pdfix.OpenDoc(str(pdf_output_path), "")
    if verified_doc is None:
        raise SoloRemoveSecurityError(
            "Unable to reopen the output PDF: " + get_pdfix_error(pdfix)
        )

    try:
        if verified_doc.IsSecured():
            raise SoloRemoveSecurityError("Output PDF is still secured after saving.")
    finally:
        verified_doc.Close()


def remove_security(pdf_input_path: str, pdf_output_path: str) -> dict[str, Any]:
    '''Remove empty-password PDF security from one PDF without changing the source.'''
    started_at = now_iso()
    input_path = Path(pdf_input_path).expanduser().resolve()
    output_path = Path(pdf_output_path).expanduser().resolve()
    temporary_output_path: Path | None = None
    doc = None
    signature_fields_detected = 0

    try:
        validate_inputs(input_path, output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        pdfix = GetPdfix()
        if pdfix is None:
            raise SoloRemoveSecurityError("PDFix initialization failed.")

        doc = pdfix.OpenDoc(str(input_path), "")
        if doc is None:
            raise SoloRemoveSecurityError(
                "Unable to open PDF with an empty password: " + get_pdfix_error(pdfix)
            )

        if not doc.IsSecured():
            doc.Close()
            doc = None
            copy_output_file(input_path, output_path)
            return build_result(
                input_path,
                output_path,
                "already_unsecured",
                False,
                started_at,
                now_iso(),
                0,
            )

        signature_fields_detected = get_signature_field_count(doc)
        if not doc.SetSecurityHandler(None):
            raise SoloRemoveSecurityError(
                "Unable to remove PDF security: " + get_pdfix_error(pdfix)
            )

        temporary_output_path = make_temporary_output_path(output_path)
        if not doc.Save(str(temporary_output_path), kSaveFull):
            raise SoloRemoveSecurityError(
                "Unable to save unsecured PDF: " + get_pdfix_error(pdfix)
            )
        doc.Close()
        doc = None

        verify_unsecured_output(pdfix, temporary_output_path)
        temporary_output_path.replace(output_path)
        return build_result(
            input_path,
            output_path,
            "success",
            True,
            started_at,
            now_iso(),
            0,
            signature_fields_detected,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return build_result(
            input_path,
            output_path,
            "error",
            False,
            started_at,
            now_iso(),
            1,
            signature_fields_detected,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        if doc is not None:
            doc.Close()
        if temporary_output_path is not None:
            temporary_output_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    '''Build the CLI parser.'''
    parser = argparse.ArgumentParser(
        description="Remove empty-password PDF security with PDFix and print the result as JSON."
    )
    parser.add_argument("pdf_input_path", help="Input PDF path.")
    parser.add_argument("pdf_output_path", help="Output PDF path.")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty-printed JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    '''CLI entrypoint.'''
    args = build_parser().parse_args(argv)
    result = remove_security(args.pdf_input_path, args.pdf_output_path)
    print(json.dumps(
        result,
        indent=None if args.compact else 2,
        sort_keys=True,
        default=str,
    ))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
