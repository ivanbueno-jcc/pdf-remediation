'''Health and deployment-readiness probes for the remediation runtime.'''

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from pdf_api.capabilities import Capabilities, cached_probe
from pdf_remediation.utilities.resources import CALLAS_FONT_IMAGE, PDFIX_FONT_IMAGE

from ..config import ALLOWED_CONFIG_FILES, CONFIG_DIR, JOBS_ROOT, SCRATCH_ROOT

REQUIRED_CHECKS = ("Java", "veraPDF", "Configs")
READINESS_CACHE_SECONDS = 30.0
_READINESS_CACHE: dict[str, Any] = {"expires_at": 0.0, "value": None}
_READINESS_CACHE_LOCK = threading.Lock()


def _check(name: str, ok: bool, required: bool, detail: str) -> dict[str, Any]:
    '''Build one named readiness or capability check.'''
    return {"name": name, "ok": ok, "required": required, "detail": detail}


def describe(capabilities: Capabilities) -> list[dict[str, Any]]:
    '''Convert detected capabilities into user-facing readiness checks.'''
    detail = capabilities.detail
    return [
        _check("Java", capabilities.java, True, detail["java"]),
        _check("veraPDF", capabilities.verapdf_jar, True, detail["verapdf_jar"]),
        _check("Configs", True, True, detail["configuration_dir"]),
        _check("PDFix license", capabilities.pdfix_licence, False, detail["pdfix_licence"]),
        _check("Docker", capabilities.docker, False, detail["docker"]),
        _check("Callas license", capabilities.callas_licence, False, detail["callas_licence"]),
    ]


def collect_health() -> dict[str, Any]:
    '''Summarize optional and required runtime capabilities for health routes.'''
    capabilities = cached_probe()
    checks = describe(capabilities)
    blocking = [check["name"] for check in checks if check["required"] and not check["ok"]]
    return {
        "checks": checks,
        "can_submit": not blocking,
        "blocking": blocking,
        "docker_available": capabilities.docker,
        "recommend_skip_font_fix": not capabilities.can_font_fix_callas(),
    }


def _writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".ready-", dir=path, delete=True):
            pass
    except OSError as error:
        return False, str(error)
    return True, str(path)


def _docker_image_available(image: str) -> tuple[bool, str]:
    docker = shutil.which("docker")
    if docker is None:
        return False, "docker not found on PATH"
    try:
        result = subprocess.run(
            [docker, "image", "inspect", image], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    return result.returncode == 0, image


def _probe_readiness() -> dict[str, Any]:
    capabilities = cached_probe()
    jobs_ok, jobs_detail = _writable(JOBS_ROOT)
    scratch_ok, scratch_detail = _writable(SCRATCH_ROOT)
    configs_ok = all((CONFIG_DIR / name).is_file() for name in ALLOWED_CONFIG_FILES)
    callas_ok, callas_detail = _docker_image_available(CALLAS_FONT_IMAGE)
    pdfix_ok, pdfix_detail = _docker_image_available(PDFIX_FONT_IMAGE)
    try:
        free_bytes = shutil.disk_usage(SCRATCH_ROOT).free
    except OSError:
        free_bytes = 0
    minimum_free = int(os.getenv("PDF_WEB_MIN_READY_DISK_BYTES", str(1024 ** 3)))
    checks = [
        _check("Java", capabilities.java, True, capabilities.detail["java"]),
        _check("veraPDF", capabilities.verapdf_jar, True, capabilities.detail["verapdf_jar"]),
        _check("Configs", configs_ok, True, str(CONFIG_DIR)),
        _check(
            "PDFix license", capabilities.pdfix_licence, True,
            capabilities.detail["pdfix_licence"],
        ),
        _check("Docker", capabilities.docker, True, capabilities.detail["docker"]),
        _check(
            "Callas license", capabilities.callas_licence, True,
            capabilities.detail["callas_licence"],
        ),
        _check("Callas image", callas_ok, True, callas_detail),
        _check("PDFix font image", pdfix_ok, True, pdfix_detail),
        _check("Jobs volume", jobs_ok, True, jobs_detail),
        _check("Scratch volume", scratch_ok, True, scratch_detail),
        _check("Scratch free space", free_bytes >= minimum_free, True,
               f"{free_bytes} bytes free; {minimum_free} required"),
    ]
    blocking = [check["name"] for check in checks if not check["ok"]]
    return {"ready": not blocking, "blocking": blocking, "checks": checks}


def collect_readiness(force: bool = False) -> dict[str, Any]:
    '''Return readiness from an independent, thread-safe short-lived cache.'''
    now = time.monotonic()
    with _READINESS_CACHE_LOCK:
        cached = _READINESS_CACHE["value"]
        if not force and cached is not None and now < _READINESS_CACHE["expires_at"]:
            return deepcopy(cached)
        result = _probe_readiness()
        _READINESS_CACHE["value"] = result
        _READINESS_CACHE["expires_at"] = time.monotonic() + READINESS_CACHE_SECONDS
        return deepcopy(result)


def cached_health(force: bool = False) -> dict[str, Any]:
    '''Refresh capability probes when requested and return health data.'''
    cached_probe(force=force)
    return collect_health()
