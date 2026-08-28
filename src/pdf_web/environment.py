'''
Probe the external tools the remediation pipeline depends on.
'''

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any

from dotenv import load_dotenv

from .config import CONFIG_DIR, REPO_ROOT, VERAPDF_JAR, ALLOWED_CONFIG_FILES

PROBE_CACHE_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 10.0
CALLAS_ENV_PATH = REPO_ROOT / "resources" / "font" / ".env"

_CACHE: dict[str, Any] = {"expires_at": 0.0, "value": None}
_CACHE_LOCK = threading.Lock()


def _check(name: str, ok: bool, required: bool, detail: str) -> dict[str, Any]:
    '''
    Build one health check entry.
    '''
    return {"name": name, "ok": ok, "required": required, "detail": detail}


def probe_java() -> dict[str, Any]:
    '''
    Check that a Java runtime is available for veraPDF.
    '''
    java_path = shutil.which("java")
    if java_path is None:
        return _check("Java", False, True, "java not found on PATH")
    try:
        result = subprocess.run(
            [java_path, "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as error:
        return _check("Java", False, True, f"java -version failed: {error}")

    if result.returncode != 0:
        return _check("Java", False, True, "java -version returned a non-zero exit code")

    first_line = (result.stderr or result.stdout or "").splitlines()
    detail = first_line[0].strip() if first_line else java_path
    return _check("Java", True, True, detail)


def probe_verapdf_jar() -> dict[str, Any]:
    '''
    Check that the veraPDF validation jar is present.
    '''
    if VERAPDF_JAR.is_file():
        return _check("veraPDF", True, True, VERAPDF_JAR.name)
    return _check("veraPDF", False, True, f"missing {VERAPDF_JAR}")


def probe_configurations() -> dict[str, Any]:
    '''
    Check that the offered remediation configurations exist.
    '''
    missing = [
        name for name in ALLOWED_CONFIG_FILES
        if not (CONFIG_DIR / name).is_file()
    ]
    if missing:
        return _check("Configs", False, True, f"missing: {', '.join(missing)}")
    return _check("Configs", True, True, ", ".join(ALLOWED_CONFIG_FILES))


def probe_pdfix_license() -> dict[str, Any]:
    '''
    Check that PDFix account credentials are configured.
    '''
    load_dotenv()
    has_name = bool(os.getenv("PDFIX_LICENSE_NAME", "").strip())
    has_key = bool(os.getenv("PDFIX_LICENSE_KEY", "").strip())
    if has_name and has_key:
        return _check("PDFix license", True, False, "PDFIX_LICENSE_NAME and _KEY set")
    return _check(
        "PDFix license",
        False,
        False,
        "PDFIX_LICENSE_NAME/PDFIX_LICENSE_KEY not set; remediation may be limited"
    )


def probe_docker() -> dict[str, Any]:
    '''
    Check whether the Docker daemon is reachable for the font-fix steps.
    '''
    docker_path = shutil.which("docker")
    if docker_path is None:
        return _check("Docker", False, False, "docker not found; skip the font-fix steps")
    try:
        result = subprocess.run(
            [docker_path, "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return _check("Docker", False, False, "docker info timed out")

    if result.returncode == 0:
        return _check("Docker", True, False, "daemon reachable")
    return _check("Docker", False, False, "daemon not running; skip the font-fix steps")


def probe_callas_license() -> dict[str, Any]:
    '''
    Check whether the Callas font-fix license file is present.
    '''
    if CALLAS_ENV_PATH.is_file():
        return _check("Callas license", True, False, "resources/font/.env present")
    return _check(
        "Callas license",
        False,
        False,
        "resources/font/.env missing; the font_fix step may fail"
    )


def collect_health() -> dict[str, Any]:
    '''
    Run every probe and summarize whether the pipeline can be started.
    '''
    checks = [
        probe_java(),
        probe_verapdf_jar(),
        probe_configurations(),
        probe_pdfix_license(),
        probe_docker(),
        probe_callas_license(),
    ]
    by_name = {check["name"]: check for check in checks}
    can_submit = all(check["ok"] for check in checks if check["required"])
    blocking = [
        check["name"] for check in checks
        if check["required"] and not check["ok"]
    ]
    return {
        "checks": checks,
        "can_submit": can_submit,
        "blocking": blocking,
        "docker_available": by_name["Docker"]["ok"],
        "recommend_skip_font_fix": not by_name["Docker"]["ok"],
    }


def cached_health(force: bool = False) -> dict[str, Any]:
    '''
    Return health results, re-probing at most once every few seconds.
    '''
    with _CACHE_LOCK:
        now = time.monotonic()
        if not force and _CACHE["value"] is not None and now < _CACHE["expires_at"]:
            return _CACHE["value"]
        value = collect_health()
        _CACHE["value"] = value
        _CACHE["expires_at"] = now + PROBE_CACHE_SECONDS
        return value
