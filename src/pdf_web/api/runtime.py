'''Runtime dependencies shared by API routers.'''

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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


_PROVIDER: dict[str, Callable[[], ApiRuntime] | None] = {"value": None}


def configure_runtime(provider: Callable[[], ApiRuntime]) -> None:
    '''Install a provider that resolves current app dependencies per request.'''
    _PROVIDER["value"] = provider


def get_runtime() -> ApiRuntime:
    '''Return the active process dependencies for an API operation.'''
    provider = _PROVIDER["value"]
    if provider is None:
        raise RuntimeError("API runtime has not been configured.")
    return provider()  # pylint: disable=not-callable
