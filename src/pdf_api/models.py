'''
Inputs and results for the single-PDF remediation pipeline.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pdf_remediation.solo import (
    DEFAULT_CALLAS_CLAUSE_TESTS,
    DEFAULT_PDFIX_FONT_CLAUSE_TESTS,
)

# The clauses that send a file to Callas font fixing, and the single clause that
# distinguishes the PDFix "missing unicode" step from the broader Callas work.
#
# Imported rather than restated. fix.py's routing list and font_fix.py's
# hand-off are the definitions; pdf_remediation.solo already collects them as
# named constants, so this stays in step with the batch pipeline instead of
# drifting from it.
FONT_ISSUE_CLAUSES = frozenset(DEFAULT_CALLAS_CLAUSE_TESTS)
MISSING_UNICODE_CLAUSE = DEFAULT_PDFIX_FONT_CLAUSE_TESTS[0]

# Clause-test to config mapping, as go.py passes it to fix_target.
DEFAULT_TARGETS: tuple[tuple[str, str], ...] = (
    ("5-1", "restore_metadata.json"),
    ("7.1-9", "restore_metadata.json"),
    ("7.1-5", "role_mapping_fix-7.1-5.json"),
    ("7.2-29", "language_fix-7.2-29.json"),
)


class StageStatus(StrEnum):
    '''
    Outcome of one pipeline stage.
    '''

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipelineStatus(StrEnum):
    '''
    Overall outcome of a pipeline run.

    These replace the batch pipeline's routing folders with results that mean
    something to a caller who never sees a workspace.
    '''

    ALREADY_COMPLIANT = "already_compliant"
    REMEDIATED = "remediated"
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class PipelineOptions:  # pylint: disable=too-many-instance-attributes
    '''
    Everything a caller can vary about one run.
    '''

    config_file: str = "default.json"
    wcag_and_ua1_must_pass: bool = False
    attempt_unlock: bool = True
    attempt_font_fix: bool = True
    attempt_targeted_fixes: bool = True
    targets: tuple[tuple[str, str], ...] = DEFAULT_TARGETS
    fix_timeout_seconds: int = 500
    font_fix_timeout_seconds: int = 600


@dataclass
class StageOutcome:
    '''
    What one stage did, for progress reporting and for the final record.
    '''

    name: str
    status: StageStatus
    detail: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view.
        '''
        return {
            "name": self.name,
            "status": str(self.status),
            "detail": self.detail,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "completed_at": (
                self.completed_at.isoformat(timespec="seconds")
                if self.completed_at else None
            ),
            "duration_seconds": self.duration_seconds(),
        }

    def duration_seconds(self) -> float | None:
        '''
        Return how long the stage took, once it has finished.
        '''
        if self.completed_at is None:
            return None
        return round((self.completed_at - self.started_at).total_seconds(), 2)


@dataclass
class PipelineResult:  # pylint: disable=too-many-instance-attributes
    '''
    The product of one run: a PDF and the two validation reports around it.
    '''

    status: PipelineStatus
    input_pdf_path: Path
    output_pdf_path: Path | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    stages: list[StageOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view.
        '''
        return {
            "status": str(self.status),
            "input_pdf_path": str(self.input_pdf_path),
            "output_pdf_path": (
                str(self.output_pdf_path) if self.output_pdf_path else None
            ),
            "before": self.before,
            "after": self.after,
            "stages": [stage.to_dict() for stage in self.stages],
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "error": self.error,
        }

    def succeeded(self) -> bool:
        '''
        Return whether the run produced a usable PDF.
        '''
        return self.status in {
            PipelineStatus.ALREADY_COMPLIANT,
            PipelineStatus.REMEDIATED,
            PipelineStatus.IMPROVED,
            PipelineStatus.UNCHANGED,
        }
