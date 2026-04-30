# pylint: disable=duplicate-code
'''
Validate one PDF and print the result as JSON.
'''

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pdf_remediation.utilities.verapdf import in_memory_validation


PROFILES = ("ua1", "wcag")


def now_iso() -> str:
    '''
    Return a local ISO-8601 timestamp for JSON output.
    '''
    return datetime.now().astimezone().isoformat(timespec="seconds")


def status_from_result(value: object) -> str:
    '''
    Normalize veraPDF status values.
    '''
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    if value == "Error":
        return "error"
    return str(value).lower()


def with_clause_test(rule: dict[str, Any]) -> dict[str, Any]:
    '''
    Add a compact clause-test identifier to a veraPDF rule dictionary.
    '''
    normalized_rule = {
        key: value
        for key, value in rule.items()
        if value is not None
    }
    clause = str(normalized_rule.get("clause", "")).strip()
    test = str(normalized_rule.get("test", "")).strip()
    if clause and test:
        normalized_rule["clause_test"] = f"{clause}-{test}"
    elif clause:
        normalized_rule["clause_test"] = clause
    return normalized_rule


def validate_profile(pdf_path: Path, profile: str) -> dict[str, Any]:
    '''
    Validate one profile and return a JSON-serializable result.
    '''
    raw_status, failed_rules_count, rules = in_memory_validation(
        str(pdf_path),
        profile
    )
    status = status_from_result(raw_status)
    violations = [
        with_clause_test(rule)
        for rule in rules
        if isinstance(rule, dict)
    ]
    return {
        "status": status,
        "passed": status == "pass",
        "failed_rules_count": int(failed_rules_count or 0),
        "violations": violations,
    }


def build_error_result(pdf_input_path: str, message: str) -> dict[str, Any]:
    '''
    Build a JSON error payload.
    '''
    return {
        "input_pdf_path": pdf_input_path,
        "validated_at": now_iso(),
        "status": "error",
        "passed": False,
        "error": message,
        "profiles": {},
    }


def validate_pdf(pdf_input_path: str) -> dict[str, Any]:
    '''
    Validate one PDF path against UA1 and WCAG.
    '''
    pdf_path = Path(pdf_input_path).expanduser().resolve()
    if not pdf_path.is_file():
        return build_error_result(str(pdf_path), f"Input PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        return build_error_result(
            str(pdf_path),
            f"Input file must use a .pdf extension: {pdf_path}"
        )

    profiles = {
        profile: validate_profile(pdf_path, profile)
        for profile in PROFILES
    }
    has_error = any(result["status"] == "error" for result in profiles.values())
    all_passed = all(result["passed"] for result in profiles.values())
    failed_rules_count = sum(
        result["failed_rules_count"]
        for result in profiles.values()
    )

    if has_error:
        status = "error"
    elif all_passed:
        status = "pass"
    else:
        status = "fail"

    return {
        "input_pdf_path": str(pdf_path),
        "validated_at": now_iso(),
        "status": status,
        "passed": all_passed and not has_error,
        "failed_rules_count": failed_rules_count,
        "profiles": profiles,
    }


def build_parser() -> argparse.ArgumentParser:
    '''
    Build the CLI parser.
    '''
    parser = argparse.ArgumentParser(
        description="Validate one PDF and print UA1/WCAG results as JSON."
    )
    parser.add_argument("pdf_input_path", help="Input PDF path.")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty-printed JSON."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    '''
    CLI entrypoint.
    '''
    args = build_parser().parse_args(argv)
    result = validate_pdf(args.pdf_input_path)
    print(json.dumps(
        result,
        indent=None if args.compact else 2,
        sort_keys=True
    ))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
