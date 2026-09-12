'''Shared helpers for standalone PDFix command-line tools.'''

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def authorize_pdfix(pdfix: Any) -> None:
    '''Authorize PDFix when credentials are configured.'''
    load_dotenv()
    license_name = os.getenv("PDFIX_LICENSE_NAME")
    license_key = os.getenv("PDFIX_LICENSE_KEY")
    if license_name and license_key:
        pdfix.GetAccountAuthorization().Authorize(license_name, license_key)


def validate_pdf_input(
        pdf_input_path: Path, error_type: type[Exception]) -> None:
    '''Validate that a path names an existing PDF, using the caller's error type.'''
    if not pdf_input_path.is_file():
        raise error_type(f"Input PDF not found: {pdf_input_path}")
    if pdf_input_path.suffix.lower() != ".pdf":
        raise error_type(f"Input file must use a .pdf extension: {pdf_input_path}")
