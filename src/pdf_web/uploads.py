'''
Accept, sanitize, and store PDF uploads for a remediation job.
'''

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

from .config import MAX_FILE_BYTES, MAX_STEM_LENGTH

UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9_-]")
UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadError(ValueError):
    '''
    An upload was rejected before any pipeline work started.
    '''


def sanitize_upload_name(raw_name: str, taken_names: set[str]) -> str:
    '''
    Return a filesystem-safe, collision-free, report-safe PDF filename.

    Interior dots are removed because veraPDF derives its report filename from
    ``Path(name).stem.split('.')[0]``, so "a.b.pdf" and "a.c.pdf" would
    otherwise overwrite each other's XML report.
    '''
    base_name = PurePosixPath(str(raw_name or "").replace("\\", "/")).name
    if not base_name.lower().endswith(".pdf"):
        raise UploadError(f"Only .pdf uploads are accepted: {raw_name}")

    stem = unicodedata.normalize("NFKD", base_name[:-4])
    stem = UNSAFE_CHARACTERS.sub("_", stem).strip("_-")[:MAX_STEM_LENGTH]
    stem = stem.strip("_-") or "upload"

    candidate = f"{stem}.pdf"
    counter = 2
    while candidate.lower() in taken_names:
        candidate = f"{stem}-{counter}.pdf"
        counter += 1

    taken_names.add(candidate.lower())
    return candidate


def write_upload_stream(chunks, destination: Path, original_name: str) -> int:
    '''
    Write an upload to disk, aborting once the per-file cap is exceeded.
    '''
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    try:
        with destination.open("wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_BYTES:
                    raise UploadError(
                        f"File exceeds the {MAX_FILE_BYTES} byte limit: {original_name}"
                    )
                handle.write(chunk)
    except UploadError:
        destination.unlink(missing_ok=True)
        raise

    if total_bytes == 0:
        destination.unlink(missing_ok=True)
        raise UploadError(f"File is empty: {original_name}")

    return total_bytes


def looks_like_pdf(path: Path) -> bool:
    '''
    Return whether a stored file starts with the PDF magic bytes.
    '''
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False
