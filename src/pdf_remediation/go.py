# pylint: disable=duplicate-code,too-many-return-statements,too-many-branches,too-many-statements
'''
Run remediation modules in sequence.
'''

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from .utilities.resources import PROJECT_BASE_PATH, get_project_source_path


def run_module(module: str, module_args: list[str]) -> int:
    '''
    Run a package module with the current Python interpreter.
    '''
    command = [sys.executable, "-m", module, *module_args]
    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def run_command(command: list[str]) -> int:
    '''
    Run a command and return its exit code.
    '''
    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def move_contents(source_dir: Path, destination_dir: Path) -> None:
    '''
    Move all entries from source_dir into destination_dir.
    '''
    for entry in source_dir.iterdir():
        destination = destination_dir / entry.name

        if destination.exists():
            if entry.is_dir() and destination.is_dir():
                move_contents(entry, destination)
                entry.rmdir()
                continue

            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

        shutil.move(str(entry), str(destination))


def main() -> int:
    '''
    Run fix, font_fix, font_fix_pdfix, and full validation sequentially.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Run fix, font_fix, font_fix_pdfix, and validate --full sequentially "
            "for a project workspace."
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

    terminus_path = shutil.which("terminus")
    if terminus_path:
        print()
        print(f"Terminus detected: {terminus_path}")
        pantheon_email = input("Pantheon email for Terminus login: ").strip()
        if not pantheon_email:
            print("Pipeline stopped: Pantheon email is required when Terminus is installed.")
            return 1

        rc = run_command(["terminus", "auth:login", f"--email={pantheon_email}"])
        if rc != 0:
            print()
            print(f"Pipeline stopped: terminus auth:login failed with exit code {rc}.")
            return rc

        rc = run_command([
            "terminus",
            "backup:get",
            f"jcc-{args.project_name}.live",
            "--element=files",
            f"--to={source_path / 'files_live.tar.gz'}"
        ])
        if rc != 0:
            print()
            print(f"Pipeline stopped: terminus backup:get failed with exit code {rc}.")
            return rc

        backup_archive_path = source_path / "files_live.tar.gz"
        if not backup_archive_path.exists():
            print()
            print(f"Pipeline stopped: backup archive not found at {backup_archive_path}.")
            return 1

        print()
        print(f"Extracting backup archive: {backup_archive_path}")
        with tarfile.open(backup_archive_path, "r:gz") as tar:
            tar.extractall(path=source_path)

        backup_archive_path.unlink()
        print(f"Deleted archive: {backup_archive_path}")

        files_live_path = source_path / "files_live"
        if not files_live_path.exists() or not files_live_path.is_dir():
            print()
            print(f"Pipeline stopped: extracted files_live folder not found at {files_live_path}.")
            return 1

        move_contents(files_live_path, source_path)
        files_live_path.rmdir()
        print(f"Moved files from {files_live_path} to {source_path}")
        print(f"Deleted folder: {files_live_path}")
    else:
        print()
        print("Terminus not installed. Skipping Pantheon backup download.")

    print()
    print(f"PROJECT: {args.project_name}")
    print(f"WORKSPACE: {args.workspace_name}")
    print()
    print("PIPELINE")
    print("1) fix (active)")
    print("2) font_fix (font-issues)")
    print("3) font_fix_pdfix (font-issues-missing-unicode)")
    print("4) validate (--full)")

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

    validate_args = [
        args.project_name,
        args.workspace_name,
        "--full"
    ]

    rc = run_module("pdf_remediation.fix", fix_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: fix failed with exit code {rc}.")
        return rc

    rc = run_module("pdf_remediation.font_fix", font_fix_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: font_fix failed with exit code {rc}.")
        return rc

    rc = run_module("pdf_remediation.font_fix_pdfix", font_fix_pdfix_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: font_fix_pdfix failed with exit code {rc}.")
        return rc

    rc = run_module("pdf_remediation.validate", validate_args)
    if rc != 0:
        print()
        print(f"Pipeline stopped: validate --full failed with exit code {rc}.")
        return rc

    print()
    print("Pipeline completed successfully.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
