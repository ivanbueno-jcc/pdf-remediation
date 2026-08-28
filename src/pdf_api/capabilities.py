'''
Probe the external tools the pipeline depends on.

Java and the veraPDF jar are required: without them validation silently reports
every file as unvalidatable while still exiting successfully. Docker and the
font licences are optional; their absence only removes the font-fix stages.
'''

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from pdf_remediation.utilities.resources import CONFIG_DIR, ROOT_DIR

VERAPDF_JAR = ROOT_DIR / "lib" / "greenfield-apps-1.28.0.jar"
CALLAS_ENV_PATH = ROOT_DIR / "resources" / "font" / ".env"
PROBE_TIMEOUT_SECONDS = 10.0
PROBE_CACHE_SECONDS = 5.0

_CACHE: dict[str, object] = {"expires_at": 0.0, "value": None}
_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Capabilities:
    '''
    What this machine can currently do.
    '''

    java: bool
    verapdf_jar: bool
    docker: bool
    pdfix_licence: bool
    callas_licence: bool
    detail: dict[str, str]

    def can_validate(self) -> bool:
        '''
        Return whether validation would produce real answers.
        '''
        return self.java and self.verapdf_jar

    def can_font_fix_callas(self) -> bool:
        '''
        Return whether the Callas font stage can run.
        '''
        return self.docker and self.callas_licence

    def can_font_fix_pdfix(self) -> bool:
        '''
        Return whether the PDFix font stage can run.
        '''
        return self.docker and self.pdfix_licence


def _java_available() -> tuple[bool, str]:
    '''
    Return whether a Java runtime can be launched.
    '''
    java_path = shutil.which("java")
    if java_path is None:
        return False, "java not found on PATH"
    try:
        result = subprocess.run(
            [java_path, "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"java -version failed: {error}"
    if result.returncode != 0:
        return False, "java -version returned a non-zero exit code"
    lines = (result.stderr or result.stdout or "").splitlines()
    return True, lines[0].strip() if lines else java_path


def _docker_available() -> tuple[bool, str]:
    '''
    Return whether the Docker daemon is reachable.
    '''
    docker_path = shutil.which("docker")
    if docker_path is None:
        return False, "docker not found on PATH"
    try:
        result = subprocess.run(
            [docker_path, "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "docker info timed out"
    if result.returncode == 0:
        return True, "daemon reachable"
    return False, "daemon not running"


def probe() -> Capabilities:
    '''
    Run every probe and return what is available.
    '''
    load_dotenv()
    java, java_detail = _java_available()
    docker, docker_detail = _docker_available()
    jar = VERAPDF_JAR.is_file()
    pdfix_licence = bool(
        os.getenv("PDFIX_LICENSE_NAME", "").strip()
        and os.getenv("PDFIX_LICENSE_KEY", "").strip()
    )
    callas_licence = CALLAS_ENV_PATH.is_file()

    return Capabilities(
        java=java,
        verapdf_jar=jar,
        docker=docker,
        pdfix_licence=pdfix_licence,
        callas_licence=callas_licence,
        detail={
            "java": java_detail,
            "verapdf_jar": (
                VERAPDF_JAR.name if jar else f"missing {VERAPDF_JAR}"
            ),
            "docker": docker_detail,
            "pdfix_licence": (
                "configured" if pdfix_licence
                else "PDFIX_LICENSE_NAME/PDFIX_LICENSE_KEY not set"
            ),
            "callas_licence": (
                "configured" if callas_licence
                else f"missing {CALLAS_ENV_PATH}"
            ),
            "configuration_dir": str(CONFIG_DIR),
        },
    )


def cached_probe(force: bool = False) -> Capabilities:
    '''
    Return probe results, re-probing at most once every few seconds.
    '''
    with _CACHE_LOCK:
        now = time.monotonic()
        cached = _CACHE["value"]
        if not force and cached is not None and now < float(_CACHE["expires_at"]):
            return cached  # type: ignore[return-value]
        value = probe()
        _CACHE["value"] = value
        _CACHE["expires_at"] = now + PROBE_CACHE_SECONDS
        return value


def configuration_exists(config_file: str) -> bool:
    '''
    Return whether a named PDFix configuration is present.

    Worth checking explicitly: get_configuration_file silently falls back to
    default.json for a missing name, so a typo would otherwise run the wrong
    remediation without complaint.
    '''
    return (Path(CONFIG_DIR) / config_file).is_file()
