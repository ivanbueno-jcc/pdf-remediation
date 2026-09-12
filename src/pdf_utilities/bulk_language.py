'''Detect the primary language of every PDF in a directory.'''

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pdf_worker.solo_language import (
    NO_LANGUAGE,
    read_document_language,
    set_document_language,
)


LANGUAGE_BY_SUFFIX = {
    "c": "zh-CN",
    "k": "ko-KR",
    "s": "es-US",
    "v": "vi-VN",
    "f": "fa-IR",
    "cm": "km-KH",
    "t": "tl-PH",
}
REPORT_FIELDS = ("path", "source_path", "language", "status", "error")


def pdf_files(directory: Path) -> list[Path]:
    '''Return all PDFs below ``directory`` in stable order.'''
    return sorted(
        path for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() == ".pdf"
        and "_result" not in path.relative_to(directory).parts
    )


def _progress(current: int, total: int, width: int = 30) -> None:
    '''Write a compact progress bar to stderr.'''
    if total == 0:
        fraction = 1.0
    else:
        fraction = current / total
    completed = int(width * fraction)
    progress_display = "#" * completed + "-" * (width - completed)
    print(f"\r[{progress_display}] {current}/{total}", end="", file=sys.stderr, flush=True)
    if current == total:
        print(file=sys.stderr)


def _result_path(directory: Path) -> Path:
    '''Return the timestamped report directory.'''
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    return directory / "_result" / timestamp


def _language_for_filename(file_path: Path) -> str | None:
    '''Return the mapped language for the longest matching filename suffix.'''
    stem = file_path.stem.lower()
    for suffix in sorted(LANGUAGE_BY_SUFFIX, key=len, reverse=True):
        if stem.endswith(suffix):
            return LANGUAGE_BY_SUFFIX[suffix]
    return None


def _process_file(
        file_path: Path,
        source_directory: Path,
        report_directory: Path,
        set_mode: bool) -> dict[str, str]:
    '''Detect one PDF and convert failures into a report row.'''
    row = {
        "path": file_path.relative_to(report_directory).as_posix(),
        "source_path": "",
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
    if set_mode:
        row["source_path"] = file_path.relative_to(source_directory).as_posix()
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


def _prepare_files(
        source_files: list[Path], input_directory: Path,
        report_directory: Path, set_mode: bool) -> list[Path]:
    '''Copy source files into the report set, setting mapped languages when requested.'''
    if not set_mode:
        return source_files

    files_directory = report_directory / "files"
    copied_files: list[Path] = []
    for source_path in source_files:
        destination_path = files_directory / source_path.relative_to(input_directory)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        mapped_language = _language_for_filename(source_path)
        if mapped_language is None:
            shutil.copy2(source_path, destination_path)
        else:
            result = set_document_language(
                str(source_path), str(destination_path), mapped_language
            )
            if result["exit_code"] != 0:
                # Keep the source file available in the report set when editing fails.
                shutil.copy2(source_path, destination_path)
        copied_files.append(destination_path)
    return copied_files


def process_directory(directory: str, set_mode: bool = False) -> dict[str, Any]:
    '''Detect, optionally set, and report languages for all PDFs.'''
    input_directory = Path(directory).expanduser().resolve()
    if not input_directory.is_dir():
        raise ValueError(f"Input directory not found: {input_directory}")

    source_files = pdf_files(input_directory)
    report_directory = _result_path(input_directory)
    files = _prepare_files(source_files, input_directory, report_directory, set_mode)

    rows: list[dict[str, str]] = []
    _progress(0, len(files))
    for index, file_path in enumerate(files, start=1):
        rows.append(_process_file(
            file_path, input_directory, report_directory if set_mode else input_directory,
            set_mode
        ))
        _progress(index, len(files))

    report_path = _write_report(report_directory, rows)
    counts = Counter(row["status"] for row in rows)
    language_tally = Counter(
        row["language"] for row in rows if row["status"] != "error"
    )
    return {
        "input_directory": str(input_directory),
        "report_path": str(report_path),
        "total_files": len(files),
        "set_mode": set_mode,
        "success": counts["success"],
        "no_language": counts["no_language"],
        "errors": counts["error"],
        "language_tally": dict(sorted(language_tally.items())),
    }


def main(argv: list[str] | None = None) -> int:
    '''CLI entrypoint.'''
    parser = argparse.ArgumentParser(
        description="Detect or set the primary language of every PDF in a directory."
    )
    parser.add_argument("directory", help="Directory to scan recursively.")
    parser.add_argument(
        "--set",
        dest="set_mode",
        action="store_true",
        help="Set mapped languages from filename suffixes before rescanning copied files."
    )
    args = parser.parse_args(argv)
    try:
        result = process_directory(args.directory, set_mode=args.set_mode)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("Language detection summary")
    print(f"  Files scanned: {result['total_files']}")
    print(f"  Language detected: {result['success']}")
    print(f"  No language set: {result['no_language']}")
    print(f"  Errors: {result['errors']}")
    print("  Language tally:")
    if result["language_tally"]:
        for language, count in result["language_tally"].items():
            print(f"    {language}: {count}")
    else:
        print("    (none)")
    print(f"  Report: {result['report_path']}")
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
