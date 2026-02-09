# pylint: disable=duplicate-code,too-many-return-statements,too-many-branches,too-many-statements
'''
Run remediation modules in sequence.
'''

import argparse
import os
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


def get_env_path() -> Path:
    '''
    Return the project .env file path.
    '''
    return Path(__file__).resolve().parents[2] / ".env"


def save_env_value(key: str, value: str) -> None:
    '''
    Upsert a key/value in the project .env file.
    '''
    env_path = get_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_lines: list[str] = []

    if env_path.exists():
        env_lines = env_path.read_text(encoding="utf-8").splitlines()

    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key} = "{escaped_value}"'
    updated_lines: list[str] = []
    replaced = False

    for line in env_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            line_key = stripped.split("=", 1)[0].strip()
            if line_key == key:
                updated_lines.append(new_line)
                replaced = True
                continue
        updated_lines.append(line)

    if not replaced:
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        updated_lines.append(new_line)

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


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


def main() -> int: # pylint: disable=too-many-locals
    '''
    Run full validation, fix, font_fix, font_fix_pdfix, then full validation again.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Run validate --full, fix, font_fix, font_fix_pdfix, then validate --full again "
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

    project_initialized = False
    project_path = Path(PROJECT_BASE_PATH) / args.project_name
    if not project_path.exists():
        print(f"Project not found. Initializing: {args.project_name}")
        rc = run_module("pdf_remediation.init", [args.project_name])
        if rc != 0:
            print()
            print(f"Pipeline stopped: init failed with exit code {rc}.")
            return rc
        project_initialized = True

    source_path = get_project_source_path(args.project_name).resolve()
    print(f"SOURCE: {source_path}")
    source_is_empty = not any(source_path.iterdir())

    if source_is_empty:
        terminus_path = shutil.which("terminus")
        if terminus_path:
            print_pipeline_banner(0, "download files")
            print()
            print(f"Terminus detected: {terminus_path}")
            pantheon_email = os.getenv("PANTHEON_EMAIL", "").strip()
            if pantheon_email:
                print(f"Using saved Pantheon email: {pantheon_email}")
            else:
                pantheon_email = input("Pantheon email for Terminus login: ").strip()
                if pantheon_email:
                    save_env_value("PANTHEON_EMAIL", pantheon_email)
                    os.environ["PANTHEON_EMAIL"] = pantheon_email
                    print(f"Saved Pantheon email to {get_env_path()}")

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
            if rc == 1:
                print()
                print(
                    "WARNING: terminus backup:get failed with exit code 1. "
                    "Proceeding with the remaining pipeline steps."
                )
            elif rc != 0:
                print()
                print(f"Pipeline stopped: terminus backup:get failed with exit code {rc}.")
                return rc

            if rc == 0:
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
                    print(
                        "Pipeline stopped: extracted files_live folder not found at "
                        f"{files_live_path}."
                    )
                    return 1

                move_contents(files_live_path, source_path)
                files_live_path.rmdir()
                print(f"Moved files from {files_live_path} to {source_path}")
                print(f"Deleted folder: {files_live_path}")

    print()
    print(f"PROJECT: {args.project_name}")
    print(f"WORKSPACE: {args.workspace_name}")
    print()
    print("PIPELINE")
    print("1) validate [pre-fix, init only]")
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

    print_pipeline_banner(1, "validate (pre-fix)")
    if project_initialized:
        rc = run_module("pdf_remediation.validate", pre_validate_args)
        if rc != 0:
            print()
            print(f"Pipeline stopped: pre-fix validate --full failed with exit code {rc}.")
            return rc
    else:
        print()
        print("Skipping pre-fix validate --full (project already initialized).")

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
