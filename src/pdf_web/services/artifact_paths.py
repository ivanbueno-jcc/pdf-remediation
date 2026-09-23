'''Validate filesystem paths before serving job artifacts.'''

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


class ArtifactPathService:  # pylint: disable=too-few-public-methods
    '''Authorize existing files below the configured jobs root.'''

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root.resolve()

    def require_file(self, candidate: Path) -> Path:
        '''Return a file below the jobs root or raise a not-found response.'''
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._jobs_root) or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Not found.")
        return resolved
