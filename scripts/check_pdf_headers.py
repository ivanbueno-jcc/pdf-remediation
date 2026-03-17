# pylint: skip-file
#!/usr/bin/env python3
"""Recursively validate that files start with a PDF header."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pdf_remediation.utilities.resources import (  # pylint: disable=wrong-import-position
    print_console_banner,
    print_console_key_value_rows,
    print_console_list,
    print_console_message,
    print_console_section,
)

PDF_HEADER = b"%PDF-"
SAMPLE_LIMIT = 3
INVALID_SAMPLE_BYTES = 32


def format_header(header: bytes) -> str:
    if not header:
        return "<empty>"
    printable = "".join(chr(b) if 32 <= b <= 126 else "." for b in header)
    as_hex = " ".join(f"{b:02x}" for b in header)
    return f'"{printable}" ({as_hex})'


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            yield Path(dirpath) / filename


def check_file(path: Path) -> tuple[bool, bytes]:
    with path.open("rb") as file:
        header = file.read(max(len(PDF_HEADER), INVALID_SAMPLE_BYTES))
    return header.startswith(PDF_HEADER), header


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check that every file in a folder (recursively) starts with %PDF-."
        )
    )
    parser.add_argument("folder", type=Path, help="Folder to scan recursively.")
    args = parser.parse_args()

    root = args.folder.expanduser().resolve()
    print_console_banner("CHECK PDF HEADERS")
    print_console_key_value_rows([("Folder", root)])
    if not root.exists():
        print_console_message("error", f"Folder not found: {root}")
        return 2
    if not root.is_dir():
        print_console_message("error", f"Not a folder: {root}")
        return 2

    checked = 0
    valid_files: list[Path] = []
    invalid_files: list[tuple[Path, bytes]] = []
    unreadable_files: list[tuple[Path, str]] = []

    for path in iter_files(root):
        checked += 1
        try:
            is_valid, header = check_file(path)
        except OSError as error:
            unreadable_files.append((path, str(error)))
            continue
        if is_valid:
            valid_files.append(path)
        else:
            invalid_files.append((path, header))

    print_console_section("SCAN SUMMARY", "info")
    print_console_key_value_rows([
        ("Scanned Files", checked),
        ("Valid Files", len(valid_files)),
        ("Invalid Files", len(invalid_files)),
        ("Unreadable Files", len(unreadable_files)),
    ])

    if valid_files:
        print_console_section(f"SAMPLE VALID FILES (UP TO {SAMPLE_LIMIT})", "success")
        print_console_list(valid_files[:SAMPLE_LIMIT], indent=2)
        remaining_valid = len(valid_files) - SAMPLE_LIMIT
        if remaining_valid > 0:
            print_console_message("info", f"... and {remaining_valid} more valid file(s).", indent=2)

    if invalid_files:
        print_console_section(
            (
                f"SAMPLE INVALID FILES (UP TO {SAMPLE_LIMIT}, SHOWING FIRST "
                f"{INVALID_SAMPLE_BYTES} BYTES)"
            ),
            "warn"
        )
        print_console_list(
            [f"{path}: {format_header(header)}" for path, header in invalid_files[:SAMPLE_LIMIT]],
            indent=2
        )
        remaining_invalid = len(invalid_files) - SAMPLE_LIMIT
        if remaining_invalid > 0:
            print_console_message("warn", f"... and {remaining_invalid} more invalid file(s).", indent=2)

    if unreadable_files:
        print_console_section("UNREADABLE FILES", "warn")
        print_console_list(
            [f"{path}: {reason}" for path, reason in unreadable_files],
            indent=2
        )

    if invalid_files or unreadable_files:
        return 1

    print_console_message("success", f"All files start with {PDF_HEADER!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
