# pylint: disable=duplicate-code,too-many-return-statements,too-many-branches,too-many-statements
'''
Run remediation modules in sequence.
'''

import argparse
from pathlib import Path
import subprocess
import sys

from .utilities.resources import (
    PROJECT_BASE_PATH,
    download_source_with_terminus,
    get_project_source_path,
)


def run_module(module: str, module_args: list[str]) -> int:
    '''
    Run a package module with the current Python interpreter.
    '''
    command = [sys.executable, "-m", module, *module_args]
    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def print_pipeline_banner(step_number: int, step_name: str) -> None:
    '''
    Print a high-visibility banner for each pipeline step.
    '''
    title = f"PIPELINE STEP {step_number}: {step_name}"
    border = "=" * max(72, len(title))
    print()
    print(border)
    print(title)
    print(border)


def main() -> int: # pylint: disable=too-many-locals
    '''
    Run optional pre-fix validation, fix, font_fix, font_fix_pdfix, then final full validation.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Run optional pre-fix validate, fix, font_fix, font_fix_pdfix, then "
            "validate --full for a project workspace."
        )
    )
    parser.add_argument("project_name", help="Project directory name.")
    parser.add_argument(
        "workspace_name",
        type=str,
        nargs='?',
        default='default',
        help="Workspace name (default: %(default)s)"
    )
    parser.add_argument(
        "--config-file",
        "--c",
        type=str,
        default='default.json',
        help="Configuration file name for fix.py (default: %(default)s)"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Chunk size for font_fix.py and font_fix_pdfix.py (default: %(default)s)"
    )
    parser.add_argument(
        "--n-cpu",
        type=int,
        default=None,
        help="CPU count for font_fix_pdfix.py (--n-cpu)."
    )
    parser.add_argument(
        "--pre-validate",
        action='store_true',
        help="Run pre-fix validate step (disabled by default)."
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action='store_true',
        help="Enable verbose output in each step."
    )
    parser.add_argument(
        "--debug",
        "-d",
        action='store_true',
        help="Enable debug mode in each step."
    )
    args = parser.parse_args()

    project_path = Path(PROJECT_BASE_PATH) / args.project_name
    if not project_path.exists():
        print(f"Project not found. Initializing: {args.project_name}")
        rc = run_module("pdf_remediation.init", [args.project_name])
        if rc != 0:
            print()
            print(f"Pipeline stopped: init failed with exit code {rc}.")
            return rc

    source_path = get_project_source_path(args.project_name).resolve()
    print(f"SOURCE: {source_path}")
    source_is_empty = not any(source_path.iterdir())

    if source_is_empty:
        rc = download_source_with_terminus(
            project_name=args.project_name,
            source_path=source_path,
            verbose=args.verbose,
            print_banner=print_pipeline_banner
        )
        if rc != 0:
            return rc

    print()
    print(f"PROJECT: {args.project_name}")
    print(f"WORKSPACE: {args.workspace_name}")
    print()
    print("PIPELINE")
    print("1) validate (--skip-page-count) [pre-fix, optional via --pre-validate]")
    print("2) fix (active)")
    print("3) font_fix (font-issues)")
    print("4) font_fix_pdfix (font-issues-missing-unicode)")
    print("5) validate (--full --skip-page-count) [final]")

    fix_args = [
        args.project_name,
        args.workspace_name,
        "active",
        "--config-file",
        args.config_file
    ]
    if args.verbose:
        fix_args.append("--verbose")
    if args.debug:
        fix_args.append("--debug")

    font_fix_args = [
        args.project_name,
        args.workspace_name,
        "font-issues",
        "--chunk-size",
        str(args.chunk_size)
    ]
    if args.verbose:
        font_fix_args.append("--verbose")
    if args.debug:
        font_fix_args.append("--debug")

    font_fix_pdfix_args = [
        args.project_name,
        args.workspace_name,
        "font-issues-missing-unicode",
        "--chunk-size",
        str(args.chunk_size)
    ]
    if args.n_cpu is not None:
        font_fix_pdfix_args.extend(["--n-cpu", str(args.n_cpu)])
    if args.verbose:
        font_fix_pdfix_args.append("--verbose")
    if args.debug:
        font_fix_pdfix_args.append("--debug")

    pre_validate_args = [
        args.project_name,
        args.workspace_name,
        "--skip-page-count"
    ]
    final_validate_args = [*pre_validate_args, "--full"]

    if args.pre_validate:
        print_pipeline_banner(1, "validate (pre-fix)")
        rc = run_module("pdf_remediation.validate", pre_validate_args)
        if rc != 0:
            print()
            print(f"Pipeline stopped: pre-fix validate failed with exit code {rc}.")
            return rc
    else:
        print()
        print("Skipping pre-fix validate (pass --pre-validate to enable).")

    print_pipeline_banner(2, "fix")
    rc = run_module("pdf_remediation.fix", fix_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: fix failed with exit code {rc}.")
        return rc

    print_pipeline_banner(3, "font_fix")
    rc = run_module("pdf_remediation.font_fix", font_fix_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: font_fix failed with exit code {rc}.")
        return rc

    print_pipeline_banner(4, "font_fix_pdfix")
    rc = run_module("pdf_remediation.font_fix_pdfix", font_fix_pdfix_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: font_fix_pdfix failed with exit code {rc}.")
        return rc

    print_pipeline_banner(5, "validate (final)")
    rc = run_module("pdf_remediation.validate", final_validate_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: final validate failed with exit code {rc}.")
        return rc

    print()
    print("Pipeline completed successfully.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
