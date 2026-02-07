#!/usr/bin/env python3
"""Recursively validate that files start with a PDF header."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

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
    if not root.exists():
        print(f"Error: folder not found: {root}")
        return 2
    if not root.is_dir():
        print(f"Error: not a folder: {root}")
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

    print(f"Scanned {checked} file(s) in {root}")
    print(f"Valid files: {len(valid_files)}")
    print(f"Invalid files: {len(invalid_files)}")
    print(f"Unreadable files: {len(unreadable_files)}")

    if valid_files:
        print(f"\nSample valid files (up to {SAMPLE_LIMIT}):")
        for path in valid_files[:SAMPLE_LIMIT]:
            print(f"- {path}")
        remaining_valid = len(valid_files) - SAMPLE_LIMIT
        if remaining_valid > 0:
            print(f"... and {remaining_valid} more valid file(s).")

    if invalid_files:
        print(
            f"\nSample invalid files (up to {SAMPLE_LIMIT}, showing first "
            f"{INVALID_SAMPLE_BYTES} bytes):"
        )
        for path, header in invalid_files[:SAMPLE_LIMIT]:
            print(f"- {path}: {format_header(header)}")
        remaining_invalid = len(invalid_files) - SAMPLE_LIMIT
        if remaining_invalid > 0:
            print(f"... and {remaining_invalid} more invalid file(s).")

    if unreadable_files:
        print("\nUnreadable files:")
        for path, reason in unreadable_files:
            print(f"- {path}: {reason}")

    if invalid_files or unreadable_files:
        return 1

    print(f"All files start with {PDF_HEADER!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
