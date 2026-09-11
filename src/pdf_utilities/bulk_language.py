'''Detect the primary language of every PDF in a directory.'''

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pdf_worker.solo_language import NO_LANGUAGE, read_document_language


REPORT_FIELDS = ("path", "language", "status", "error")


def pdf_files(directory: Path) -> list[Path]:
    '''Return all PDFs below ``directory`` in stable order.'''
    return sorted(
        path for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def _progress(current: int, total: int, width: int = 30) -> None:
    '''Write a compact progress bar to stderr.'''
    if total == 0:
        fraction = 1.0
    else:
        fraction = current / total
    completed = int(width * fraction)
    bar = "#" * completed + "-" * (width - completed)
    print(f"\r[{bar}] {current}/{total}", end="", file=sys.stderr, flush=True)
    if current == total:
        print(file=sys.stderr)


def _result_path(directory: Path) -> Path:
    '''Return the timestamped report directory.'''
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return directory / "_result" / timestamp


def _process_file(file_path: Path, directory: Path) -> dict[str, str]:
    '''Detect one PDF and convert failures into a report row.'''
    row = {
        "path": file_path.relative_to(directory).as_posix(),
        "language": "",
        "status": "success",
        "error": "",
    }
    try:
        language = read_document_language(str(file_path))
        row["language"] = language
        row["status"] = "no_language" if language == NO_LANGUAGE else "success"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def _write_report(report_directory: Path, rows: Iterable[dict[str, str]]) -> Path:
    '''Write the CSV report and return its path.'''
    report_directory.mkdir(parents=True, exist_ok=True)
    report_path = report_directory / "report.csv"
    with report_path.open("w", newline="", encoding="utf-8") as report_file:
        writer = csv.DictWriter(report_file, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return report_path


def process_directory(directory: str) -> dict[str, Any]:
    '''Detect languages for all PDFs and write ``_result/<timestamp>/report.csv``.'''
    input_directory = Path(directory).expanduser().resolve()
    if not input_directory.is_dir():
        raise ValueError(f"Input directory not found: {input_directory}")

    files = pdf_files(input_directory)
    rows: list[dict[str, str]] = []
    _progress(0, len(files))
    for index, file_path in enumerate(files, start=1):
        rows.append(_process_file(file_path, input_directory))
        _progress(index, len(files))

    report_path = _write_report(_result_path(input_directory), rows)
    counts = Counter(row["status"] for row in rows)
    return {
        "input_directory": str(input_directory),
        "report_path": str(report_path),
        "total_files": len(files),
        "success": counts["success"],
        "no_language": counts["no_language"],
        "errors": counts["error"],
    }


def main(argv: list[str] | None = None) -> int:
    '''CLI entrypoint.'''
    parser = argparse.ArgumentParser(
        description="Detect the primary language of every PDF in a directory."
    )
    parser.add_argument("directory", help="Directory to scan recursively.")
    args = parser.parse_args(argv)
    try:
        result = process_directory(args.directory)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("Language detection summary")
    print(f"  Files scanned: {result['total_files']}")
    print(f"  Language set: {result['success']}")
    print(f"  No language set: {result['no_language']}")
    print(f"  Errors: {result['errors']}")
    print(f"  Report: {result['report_path']}")
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
