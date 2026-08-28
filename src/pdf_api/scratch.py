'''
A throwaway directory shaped to satisfy the batch utilities' path expectations.

Several functions in pdf_remediation derive write targets from the workspace
argument rather than taking them explicitly: error CSVs land a fixed number of
levels above the folder they are given, and the Docker font steps bind the
workspace as a volume and express their arguments relative to it. None of that
is configurable, so a single-PDF run has to present a directory tree of the
right shape.

One layout satisfies every such derivation at once:

    <root>/                     error CSVs land here
      proj/
        ws/                     workspace_path (Docker mount, 2 levels up)
          active/
            files/              workspace_folder_path (4 levels up)
            processed/

The depth is load-bearing and the names are not: `files/.parent * 4` and
`ws/.parent * 2` must both resolve to <root> so every CSV lands in the same
collectable place. Getting this wrong scatters error CSVs outside the scratch
tree, which is why the constructor asserts it.
'''

from __future__ import annotations

import csv
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

# Written by the batch utilities into the scratch root. Collected as diagnostics
# rather than discarded, because they carry the actual reason a stage failed.
DIAGNOSTIC_CSV_NAMES = (
    "pdfix-cannot-process-files.csv",
    "secured-files.csv",
    "pdfix-unable-to-open.csv",
    "unable-to-validate.csv",
    "callas-font-errors.csv",
    "pdfix-font-errors.csv",
)


@dataclass(frozen=True)
class Scratch:
    '''
    Paths inside one run's throwaway workspace.
    '''

    root: Path
    workspace: Path
    files: Path
    processed: Path
    staging: Path

    def stage_input(self, source: Path, name: str | None = None) -> Path:
        '''
        Copy a PDF into the working folder and return the copy.

        Always work on a copy: the PDFix fix deletes its input on success, and
        the PDFix font step deletes its input unconditionally.
        '''
        destination = self.files / (name or source.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def output_path(self, name: str) -> Path:
        '''
        Return a path in the processed folder for a stage's output.
        '''
        self.processed.mkdir(parents=True, exist_ok=True)
        return self.processed / name

    def collect_diagnostics(self) -> list[dict[str, str]]:
        '''
        Read back whatever the batch utilities recorded about this run.
        '''
        diagnostics: list[dict[str, str]] = []
        for name in DIAGNOSTIC_CSV_NAMES:
            path = self.root / name
            if not path.is_file():
                continue
            try:
                with path.open(newline="", encoding="utf-8") as handle:
                    rows = [row for row in csv.reader(handle) if any(row)]
            except OSError:
                continue
            for row in rows:
                diagnostics.append({
                    "source": name,
                    "detail": ", ".join(str(cell).strip() for cell in row),
                })
        return diagnostics


@contextmanager
def scratch_workspace(prefix: str = "pdf-api-") -> Iterator[Scratch]:
    '''
    Create the workspace shape for one run and remove it afterwards.
    '''
    root = Path(tempfile.mkdtemp(prefix=prefix))
    workspace = root / "proj" / "ws"
    files = workspace / "active" / "files"
    processed = workspace / "active" / "processed"
    staging = workspace / "active" / "staging"

    for path in (files, processed, staging):
        path.mkdir(parents=True, exist_ok=True)

    # The derivations the batch utilities perform, asserted rather than assumed:
    # if either breaks, error CSVs would be written outside the scratch tree.
    assert files.parent.parent.parent.parent == root
    assert workspace.parent.parent == root

    try:
        yield Scratch(
            root=root,
            workspace=workspace,
            files=files,
            processed=processed,
            staging=staging,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def replace_output_file(source: Path, destination: Path) -> None:
    '''
    Move a finished PDF into place without exposing a partial file.
    '''
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
