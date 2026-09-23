'''Runtime dependencies shared by API routers.'''

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import Request


@dataclass(frozen=True)
class ApiRuntime:  # pylint: disable=too-many-instance-attributes
    '''Current process services exposed to route handlers.'''

    store: Any
    runner: Any
    jobs_root: Path
    config_dir: Path
    new_job_id: Callable[[set[str]], str]
    assert_disk_space: Callable[[], None]
    cached_health: Callable[[], dict[str, Any]]
    collect_readiness: Callable[[], dict[str, Any]]
    describe_mode: Callable[[], dict[str, Any]]


def get_runtime(request: Request) -> ApiRuntime:
    '''Resolve dependencies from the FastAPI application handling the request.'''
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("API runtime has not been configured for this app.")
    return runtime
