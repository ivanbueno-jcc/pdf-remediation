'''
Report on the tooling the remediation pipeline needs.

The probing itself lives in pdf_api.capabilities, next to the pipeline that
depends on it. This module only shapes those results for the browser: which
checks block submission, and which merely remove a stage.
'''

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pdf_api.capabilities import Capabilities, cached_probe
from pdf_remediation.utilities.resources import CALLAS_FONT_IMAGE, PDFIX_FONT_IMAGE

from .config import ALLOWED_CONFIG_FILES, CONFIG_DIR, JOBS_ROOT, SCRATCH_ROOT

# Java and the veraPDF jar are the only hard requirements. Without them
# validation reports every file as unvalidatable while still succeeding, so a
# missing one has to stop submission rather than warn about it.
REQUIRED_CHECKS = ("Java", "veraPDF", "Configs")


def _check(name: str, ok: bool, required: bool, detail: str) -> dict[str, Any]:
    '''
    Build one health check entry.
    '''
    return {"name": name, "ok": ok, "required": required, "detail": detail}


def describe(capabilities: Capabilities) -> list[dict[str, Any]]:
    '''
    Turn probe results into the rows the browser renders.
    '''
    detail = capabilities.detail
    return [
        _check("Java", capabilities.java, True, detail["java"]),
        _check("veraPDF", capabilities.verapdf_jar, True, detail["verapdf_jar"]),
        _check("Configs", True, True, detail["configuration_dir"]),
        _check("PDFix license", capabilities.pdfix_licence, False,
               detail["pdfix_licence"]),
        _check("Docker", capabilities.docker, False, detail["docker"]),
        _check("Callas license", capabilities.callas_licence, False,
               detail["callas_licence"]),
    ]


def collect_health() -> dict[str, Any]:
    '''
    Summarize whether the pipeline can be started.
    '''
    capabilities = cached_probe()
    checks = describe(capabilities)
    blocking = [
        check["name"] for check in checks
        if check["required"] and not check["ok"]
    ]
    return {
        "checks": checks,
        "can_submit": not blocking,
        "blocking": blocking,
        "docker_available": capabilities.docker,
        "recommend_skip_font_fix": not capabilities.can_font_fix_callas(),
    }


def _writable(path: Path) -> tuple[bool, str]:
    '''Verify a deployment volume can create and remove a small file.'''
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".ready-", dir=path, delete=True):
            pass
    except OSError as error:
        return False, str(error)
    return True, str(path)


def _docker_image_available(image: str) -> tuple[bool, str]:
    '''Return whether a pinned worker image is present on the host daemon.'''
    docker = shutil.which("docker")
    if docker is None:
        return False, "docker not found on PATH"
    try:
        result = subprocess.run(
            [docker, "image", "inspect", image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    return result.returncode == 0, image


def collect_readiness() -> dict[str, Any]:
    '''Check every dependency required by the production Azure deployment.'''
    capabilities = cached_probe()
    jobs_ok, jobs_detail = _writable(JOBS_ROOT)
    scratch_ok, scratch_detail = _writable(SCRATCH_ROOT)
    configs_ok = all((CONFIG_DIR / name).is_file() for name in ALLOWED_CONFIG_FILES)
    callas_image_ok, callas_image_detail = _docker_image_available(CALLAS_FONT_IMAGE)
    pdfix_image_ok, pdfix_image_detail = _docker_image_available(PDFIX_FONT_IMAGE)
    try:
        free_bytes = shutil.disk_usage(SCRATCH_ROOT).free
    except OSError:
        free_bytes = 0
    minimum_free = int(os.getenv("PDF_WEB_MIN_READY_DISK_BYTES", str(1024 ** 3)))

    checks = [
        _check("Java", capabilities.java, True, capabilities.detail["java"]),
        _check("veraPDF", capabilities.verapdf_jar, True,
               capabilities.detail["verapdf_jar"]),
        _check("Configs", configs_ok, True, str(CONFIG_DIR)),
        _check("PDFix license", capabilities.pdfix_licence, True,
               capabilities.detail["pdfix_licence"]),
        _check("Docker", capabilities.docker, True, capabilities.detail["docker"]),
        _check("Callas license", capabilities.callas_licence, True,
               capabilities.detail["callas_licence"]),
        _check("Callas image", callas_image_ok, True, callas_image_detail),
        _check("PDFix font image", pdfix_image_ok, True, pdfix_image_detail),
        _check("Jobs volume", jobs_ok, True, jobs_detail),
        _check("Scratch volume", scratch_ok, True, scratch_detail),
        _check(
            "Scratch free space", free_bytes >= minimum_free, True,
            f"{free_bytes} bytes free; {minimum_free} required",
        ),
    ]
    blocking = [check["name"] for check in checks if not check["ok"]]
    return {"ready": not blocking, "blocking": blocking, "checks": checks}


def cached_health(force: bool = False) -> dict[str, Any]:
    '''
    Return health results, re-probing at most every few seconds.
    '''
    cached_probe(force=force)
    return collect_health()
