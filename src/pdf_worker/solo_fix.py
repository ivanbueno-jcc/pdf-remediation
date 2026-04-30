# pylint: disable=duplicate-code
'''
Remediate one PDF and print the result as JSON.
'''

from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pdf_remediation.fix import fix_with_process_timeout
from pdf_remediation.utilities.resources import CONFIG_DIR


class SoloFixError(RuntimeError):
    '''
    Operational error while processing a single PDF.
    '''


def now_iso() -> str:
    '''
    Return a local ISO-8601 timestamp for JSON output.
    '''
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_scratch_workspace() -> tuple[Path, Path, Path]:
    '''
    Create a temporary workspace shape for existing PDFix error handling.
    '''
    scratch_root_path = Path(tempfile.mkdtemp(prefix="pdf-worker-solo-fix-"))
    files_path = scratch_root_path / "project" / "workspace" / "active" / "files"
    output_path = scratch_root_path / "project" / "workspace" / "active" / "processed"
    files_path.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)
    return scratch_root_path, files_path, output_path


def replace_output_file(source_path: Path, output_path: Path) -> None:
    '''
    Copy the remediated PDF into place without exposing a partial output file.
    '''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source_path, tmp_path)
        tmp_path.replace(output_path)
    finally:
        tmp_path.unlink(missing_ok=True)

# pylint: disable=too-many-positional-arguments,too-many-arguments
def build_result(
        pdf_input_path: Path,
        pdf_output_path: Path,
        config_file: str,
        status: str,
        started_at: str,
        completed_at: str,
        exit_code: int,
        error: str | None = None) -> dict[str, Any]:
    '''
    Build the JSON payload for stdout.
    '''
    result = {
        "input_pdf_path": str(pdf_input_path),
        "output_pdf_path": str(pdf_output_path),
        "config_file": config_file,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "exit_code": exit_code,
    }
    if error:
        result["error"] = error
    return result


def validate_inputs(pdf_input_path: Path, config_file: str) -> None:
    '''
    Validate CLI inputs before any remediation work starts.
    '''
    if not pdf_input_path.is_file():
        raise SoloFixError(f"Input PDF not found: {pdf_input_path}")
    if pdf_input_path.suffix.lower() != ".pdf":
        raise SoloFixError(f"Input file must use a .pdf extension: {pdf_input_path}")
    if not (CONFIG_DIR / config_file).is_file():
        raise SoloFixError(
            f"Configuration file not found under resources/configuration: {config_file}"
        )


def fix_pdf(
        pdf_input_path: str,
        pdf_output_path: str,
        config_file: str = "default.json") -> dict[str, Any]:
    '''
    Run PDFix remediation for one PDF without using the project routing pipeline.
    '''
    started_at = now_iso()
    input_path = Path(pdf_input_path).expanduser().resolve()
    output_path = Path(pdf_output_path).expanduser().resolve()
    scratch_root_path: Path | None = None

    try:
        validate_inputs(input_path, config_file)
        scratch_root_path, files_path, scratch_output_path = create_scratch_workspace()
        staged_input_path = files_path / input_path.name
        staged_output_path = scratch_output_path / input_path.name
        shutil.copy2(input_path, staged_input_path)

        with contextlib.redirect_stdout(sys.stderr):
            fix_with_process_timeout(
                str(staged_input_path),
                str(staged_output_path),
                config_file,
                files_path,
                False,
                reported_input_pdf_path=str(input_path)
            )

        if not staged_output_path.is_file():
            raise SoloFixError(f"PDFix did not create an output PDF: {staged_output_path}")

        replace_output_file(staged_output_path, output_path)
        return build_result(
            input_path,
            output_path,
            config_file,
            "success",
            started_at,
            now_iso(),
            0
        )
    except Exception as exc: # pylint: disable=broad-exception-caught
        return build_result(
            input_path,
            output_path,
            config_file,
            "error",
            started_at,
            now_iso(),
            1,
            f"{type(exc).__name__}: {exc}"
        )
    finally:
        if scratch_root_path is not None:
            shutil.rmtree(scratch_root_path, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    '''
    Build the CLI parser.
    '''
    parser = argparse.ArgumentParser(
        description="Remediate one PDF with PDFix and print the result as JSON."
    )
    parser.add_argument("pdf_input_path", help="Input PDF path.")
    parser.add_argument("pdf_output_path", help="Output PDF path.")
    parser.add_argument(
        "--config-file",
        default="default.json",
        help="PDFix remediation config under resources/configuration (default: %(default)s)."
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty-printed JSON."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    '''
    CLI entrypoint.
    '''
    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    result = fix_pdf(
        args.pdf_input_path,
        args.pdf_output_path,
        args.config_file
    )
    print(json.dumps(
        result,
        indent=None if args.compact else 2,
        sort_keys=True,
        default=str
    ))
    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
