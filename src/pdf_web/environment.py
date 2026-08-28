'''
Report on the tooling the remediation pipeline needs.

The probing itself lives in pdf_api.capabilities, next to the pipeline that
depends on it. This module only shapes those results for the browser: which
checks block submission, and which merely remove a stage.
'''

from __future__ import annotations

from typing import Any

from pdf_api.capabilities import Capabilities, cached_probe

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


def cached_health(force: bool = False) -> dict[str, Any]:
    '''
    Return health results, re-probing at most every few seconds.
    '''
    cached_probe(force=force)
    return collect_health()
