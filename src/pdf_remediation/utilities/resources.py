# pylint: disable=too-many-branches, too-many-return-statements, too-many-lines
'''
Utility functions for managing project resources and paths.
'''
from collections.abc import Callable
from pathlib import Path
import csv
import ctypes
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
from dotenv import load_dotenv
import plotext as plot

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = ROOT_DIR / "resources/configuration"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "default.json"

PROJECT_BASE_PATH = Path(os.getenv('PROJECT_BASE_PATH', './resources/projects'))
PROJECT_BASE_PATH.mkdir(parents=True, exist_ok=True)

CALLAS_FONT_IMAGE = "pdfix/font-fix-callas:v1.0.5"
PDFIX_FONT_IMAGE = "pdfix/font-fix-pdfix:v1.0.5"
FIX_PROCESS_TIMEOUT_SECONDS = 500
_DOCKER_STATE = {"ready": False}

def parse_cli_filters(values: list[str] | None) -> set[str]:
    '''
    Parse CLI filters, accepting both space-separated and comma-separated values.
    '''
    if not values:
        return set()

    parsed_filters = set()
    for raw_value in values:
        if raw_value is None:
            continue
        for chunk in str(raw_value).split(","):
            item = chunk.strip()
            if item:
                parsed_filters.add(item)

    return parsed_filters

def _get_page_count_bucket(page_count: int) -> str | None:
    '''
    Return the page-count bucket label for a given page count.
    '''
    if page_count == 1:
        return "1"
    if 1 < page_count <= 5:
        return "2-5"
    if 5 < page_count <= 10:
        return "6-10"
    if 10 < page_count <= 50:
        return "11-50"
    if 50 < page_count <= 100:
        return "51-100"
    if 100 < page_count <= 200:
        return "101-200"
    if 200 < page_count <= 500:
        return "201-500"
    if 500 < page_count <= 1000:
        return "501-1000"
    if 1000 < page_count <= 3000:
        return "1001-3000"
    if page_count > 3000:
        return "3001 or more"
    return None

def _plot_page_count_distribution(chunks: dict[str, list]) -> None:
    '''
    Render a terminal chart for the page-count distribution.
    '''

    page_count_file_num = []
    page_count_bucket = []
    for key, value in chunks.items():
        page_count_bucket.append(key)
        page_count_file_num.append(len(value))

    min_y = min(page_count_file_num)
    max_y = max(page_count_file_num)
    y_ticks = list(range(min_y, max_y + 1, 5))
    if len(y_ticks) == 0:
        y_ticks = [min_y]

    plot.yticks(y_ticks)
    plot.bar(page_count_bucket, page_count_file_num)
    plot.title("File Distribution by Page Count")
    plot.xlabel("Range")
    plot.ylabel("# of Files")
    plot.plotsize(50, 15)
    plot.show()

def split_large_page_count_chunks(chunks: dict[str, list], chunk_size: int) -> dict[str, list]:
    '''
    Split buckets that exceed chunk_size into numbered sub-buckets.
    '''
    if chunk_size <= 0:
        return chunks

    sub_chunks = {}
    del_chunks = []
    for key, value in chunks.items():
        if len(value) > chunk_size:
            del_chunks.append(key)
            chunk_count = len(value) // chunk_size + 1
            for i in range(chunk_count):
                chunk_key = f"{key} - part {i+1} of {chunk_count}"
                sub_chunks[chunk_key] = value[i*chunk_size:(i+1)*chunk_size]

    remaining_chunks = chunks.copy()
    for key in del_chunks:
        del remaining_chunks[key]
    sub_chunks.update(remaining_chunks)
    return sub_chunks

def get_page_count_chunks(
        file_paths_for_remediation: list,
        page_count_lookup: dict,
        payload_builder: Callable[..., tuple],
        chunk_size: int | None = None,
        show_chart: bool = True) -> dict[str, list]:
    '''
    Build remediation payload chunks grouped by page-count ranges.
    '''
    chunks = {
        '1': [],
        '2-5': [],
        '6-10': [],
        '11-50': [],
        '51-100': [],
        '101-200': [],
        '201-500': [],
        '501-1000': [],
        '1001-3000': [],
        '3001 or more': []
    }

    for file_path_tuple in file_paths_for_remediation:
        input_path = file_path_tuple[0]
        payload = payload_builder(*file_path_tuple)
        page_count = page_count_lookup[str(input_path)]
        page_count_bucket = _get_page_count_bucket(page_count)
        if page_count_bucket is not None:
            chunks[page_count_bucket].append(payload)

    if show_chart and len(file_paths_for_remediation) > 0:
        print()
        _plot_page_count_distribution(chunks)

    if chunk_size is not None:
        return split_large_page_count_chunks(chunks, chunk_size)
    return chunks


def _get_project_env_path() -> Path:
    '''
    Return the project .env file path.
    '''
    return ROOT_DIR / ".env"


def _save_env_value(key: str, value: str) -> None:
    '''
    Upsert a key/value in the project .env file.
    '''
    env_path = _get_project_env_path()
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


def _move_contents(source_dir: Path, destination_dir: Path) -> None:
    '''
    Move all entries from source_dir into destination_dir.
    '''
    for entry in source_dir.iterdir():
        destination = destination_dir / entry.name

        if destination.exists():
            if entry.is_dir() and destination.is_dir():
                _move_contents(entry, destination)
                entry.rmdir()
                continue

            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

        shutil.move(str(entry), str(destination))


def _run_command(command: list[str]) -> int:
    '''
    Run a command and return its exit code.
    '''
    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def download_source_with_terminus_result(
        project_name: str,
        source_path: Path,
        verbose: bool = False,
        print_banner: Callable[[int, str], None] | None = None) -> tuple[int, bool]:
    '''
    Download and extract Pantheon files via Terminus into source_path.

    Returns a tuple of (exit_code, downloaded). The downloaded flag is True only
    when Terminus fetched and extracted a backup into source_path.
    '''
    terminus_path = shutil.which("terminus")
    if terminus_path is None:
        return 0, False

    if print_banner is not None:
        print_banner(0, "download files")

    print()
    print(f"Terminus detected: {terminus_path}")
    pantheon_email = os.getenv("PANTHEON_EMAIL", "").strip()
    if pantheon_email:
        print(f"Using saved Pantheon email: {pantheon_email}")
    else:
        pantheon_email = input("Pantheon email for Terminus login: ").strip()
        if pantheon_email:
            _save_env_value("PANTHEON_EMAIL", pantheon_email)
            os.environ["PANTHEON_EMAIL"] = pantheon_email
            print(f"Saved Pantheon email to {_get_project_env_path()}")

    if not pantheon_email:
        print("Pipeline stopped: Pantheon email is required when Terminus is installed.")
        return 1, False

    rc = _run_command(["terminus", "auth:login", f"--email={pantheon_email}"])
    if rc != 0:
        print()
        print(f"Pipeline stopped: terminus auth:login failed with exit code {rc}.")
        return rc, False

    backup_archive_path = source_path / "files_live.tar.gz"
    rc = _run_command([
        "terminus",
        "backup:get",
        f"jcc-{project_name}.live",
        "--element=files",
        f"--to={backup_archive_path}"
    ])
    if rc == 1:
        print()
        print(
            "WARNING: terminus backup:get failed with exit code 1. "
            "Proceeding with the remaining pipeline steps."
        )
        return 0, False
    if rc != 0:
        print()
        print(f"Pipeline stopped: terminus backup:get failed with exit code {rc}.")
        return rc, False

    if not backup_archive_path.exists():
        print()
        print(f"Pipeline stopped: backup archive not found at {backup_archive_path}.")
        return 1, False

    print()
    if verbose:
        print(f"Extracting backup archive: {backup_archive_path}")
    with tarfile.open(backup_archive_path, "r:gz") as tar:
        tar.extractall(path=source_path)

    backup_archive_path.unlink()
    if verbose:
        print(f"Deleted archive: {backup_archive_path}")

    files_live_path = source_path / "files_live"
    if not files_live_path.exists() or not files_live_path.is_dir():
        print()
        print(
            "Pipeline stopped: extracted files_live folder not found at "
            f"{files_live_path}."
        )
        return 1, False

    _move_contents(files_live_path, source_path)
    files_live_path.rmdir()
    if verbose:
        print(f"Moved files from {files_live_path} to {source_path}")
        print(f"Deleted folder: {files_live_path}")

    return 0, True


def download_source_with_terminus(
        project_name: str,
        source_path: Path,
        verbose: bool = False,
        print_banner: Callable[[int, str], None] | None = None) -> int:
    '''
    Download and extract Pantheon files via Terminus into source_path.

    Returns 0 when skipped or successful. Returns non-zero for fatal errors.
    '''
    rc, _downloaded = download_source_with_terminus_result(
        project_name=project_name,
        source_path=source_path,
        verbose=verbose,
        print_banner=print_banner
    )
    return rc


def _docker_daemon_is_running() -> bool:
    '''
    Return True when Docker daemon is reachable.
    '''
    result = subprocess.run(
        ["docker", "info"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0


def _launch_docker_desktop() -> bool:
    '''
    Launch Docker Desktop on supported platforms.
    '''
    operating_system = platform.system()
    if operating_system == "Darwin":
        result = subprocess.run(
            ["open", "-a", "Docker"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0

    if operating_system == "Windows":
        docker_desktop_path = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        ) / "Docker" / "Docker" / "Docker Desktop.exe"
        if docker_desktop_path.exists():
            result = subprocess.run(
                ["cmd", "/c", "start", "", str(docker_desktop_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return result.returncode == 0

    return False


def ensure_docker_desktop_running(
        timeout_seconds: int = 120,
        poll_interval_seconds: int = 2,
        verbose: bool = False) -> None:
    '''
    Ensure Docker daemon is running; launch Docker Desktop when available.
    '''
    if _DOCKER_STATE["ready"]:
        return

    if shutil.which("docker") is None:
        raise RuntimeError(
            "Docker CLI was not found. Install Docker Desktop and ensure 'docker' "
            "is available in PATH."
        )

    if _docker_daemon_is_running():
        _DOCKER_STATE["ready"] = True
        return

    if verbose:
        print("Docker daemon not detected. Launching Docker Desktop...")

    if not _launch_docker_desktop():
        raise RuntimeError(
            "Docker daemon is not running and Docker Desktop could not be launched "
            "automatically. Start Docker manually and re-run."
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _docker_daemon_is_running():
            _DOCKER_STATE["ready"] = True
            if verbose:
                print("Docker is ready.")
            return
        time.sleep(poll_interval_seconds)

    raise RuntimeError(
        f"Docker Desktop did not become ready within {timeout_seconds} seconds."
    )

def get_configuration_file(config_file: str = "default.json") -> Path:
    '''
    Get the configuration file path.
    :param config_file: Configuration file name.
    :type config_file: str
    :return: Configuration file path.
    :rtype: Path
    '''
    config_path = CONFIG_DIR / config_file
    if not config_path.exists():
        config_path = CONFIG_FILE
    return config_path

def get_project_path(project_name: str) -> Path:
    '''
    Get the project path.
    
    :param project_name: Description
    :type project_name: str
    :return: Description
    :rtype: Path
    '''
    project_path = PROJECT_BASE_PATH / project_name
    project_path.mkdir(parents=True, exist_ok=True)
    return project_path

def get_project_source_path(project_name: str) -> Path:
    '''
    Get the project source path.
    
    :param project_name: Description
    :type project_name: str
    :return: Description
    :rtype: Path
    '''
    source_path = get_project_path(project_name) / "source"
    source_path.mkdir(parents=True, exist_ok=True)
    return source_path

def get_project_workspace_path(project_name: str, workspace_name: str = "default") -> Path:
    '''
    Get the project workspace path.
    
    :param project_name: Description
    :type project_name: str
    :param workspace_name: Description
    :type workspace_name: str
    :return: Description
    :rtype: Path
    '''
    workspace_path = get_project_path(project_name) / "workspace" / workspace_name
    workspace_path.mkdir(parents=True, exist_ok=True)

    workspace_subfolders = ["active", "remediated"]
    for subfolder in workspace_subfolders:
        subfolder_path = workspace_path / subfolder
        subfolder_path.mkdir(parents=True, exist_ok=True)

    return workspace_path

def get_pdf_file_paths(folder_path: Path) -> list[Path]:
    '''
    Return recursive PDF file paths for any case variant of ".pdf".
    '''
    if not folder_path.exists():
        return []
    return list(folder_path.rglob("*.[Pp][Dd][Ff]"))

def get_project_workspace_file_paths(
        project_name: str,
        workspace_name: str,
        subfolder_name: str) -> list:
    '''
    Get the PDF file paths in the project workspace subfolder.
    
    :param project_name: Description
    :type project_name: str
    :param workspace_name: Description
    :type workspace_name: str
    :param subfolder_name: Description
    :type subfolder_name: str
    :return: Description
    :rtype: list
    '''
    subfolder_path = get_project_workspace_subfolder_path(
        project_name, workspace_name, subfolder_name)
    file_paths = get_pdf_file_paths(subfolder_path)

    if len(file_paths) == 0 and workspace_name == "default" and subfolder_name == "active":
        # check if workspace folder contains pdf files
        source_path = get_project_source_path(project_name)
        semaphore = subfolder_path / ".remediation.lock"
        if not semaphore.exists():
            source_file_paths = get_pdf_file_paths(source_path)
            if len(source_file_paths) > 0:
                for file_path in source_file_paths:
                    relative_path = file_path.relative_to(source_path)
                    destination_path = subfolder_path / relative_path
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    destination_path.write_bytes(file_path.read_bytes())

                # Add a semaphore to only copy over the source once, until reset.
                semaphore.touch(exist_ok=True)

                # Re-run to get the file paths again.
                file_paths = get_project_workspace_file_paths(
                    project_name, workspace_name, subfolder_name)
            else:
                print("No PDF files found.")
                print()
                print("Please add PDF files to the source folder and re-run the script:")
                print(f"{source_path.resolve()}")
        else:
            print("All the PDF files have been processed.")

    return file_paths

def get_project_workspace_subfolder_path(
        project_name: str,
        workspace_name: str,
        subfolder_name: str,
        directory: str = "files") -> Path:
    '''
    Get the project workspace subfolder path.
    
    :param project_name: Description
    :type project_name: str
    :param workspace_name: Description
    :type workspace_name: str
    :param subfolder_name: Description
    :type subfolder_name: str
    :param directory: Description
    :type directory: str
    :return: Description
    :rtype: Path
    '''
    subfolder_path = get_project_workspace_path(project_name, workspace_name) \
        / subfolder_name / directory
    subfolder_path.mkdir(parents=True, exist_ok=True)
    return subfolder_path

def get_project_workspace_subfolder_file_paths(
        project_name: str,
        workspace_name: str,
        subfolder_name: str,
        directory: str = "files") -> list:
    '''
    Get the PDF file paths in the project workspace subfolder and directory.
    
    :param project_name: Description
    :type project_name: str
    :param workspace_name: Description
    :type workspace_name: str
    :param subfolder_name: Description
    :type subfolder_name: str
    :param directory: Description
    :type directory: str
    :return: Description
    :rtype: list
    '''
    file_paths = None

    if directory == "files":
        file_paths = get_project_workspace_file_paths(project_name, workspace_name, subfolder_name)
    else:
        subfolder_path = get_project_workspace_subfolder_path(
            project_name, workspace_name, subfolder_name, directory)
        file_paths = get_pdf_file_paths(subfolder_path)

    return file_paths

def get_full_workspace_file_paths(
        project_name: str,
        workspace_name: str,
        ignored_subfolders: list[str] = None) -> tuple[Path, list[Path], list[Path]]:
    '''
    Return all PDF files from every workspace folder's files/ and processed/ subdirectories.
    '''
    workspace_path = get_project_workspace_path(project_name, workspace_name)
    ignored_subfolders = ignored_subfolders or []
    ignored_subfolder_set = set(ignored_subfolders)
    scanned_paths = []
    file_paths = []
    seen_paths = set()

    for workspace_subfolder_path in sorted(workspace_path.iterdir()):
        if not workspace_subfolder_path.is_dir():
            continue
        if workspace_subfolder_path.name in ignored_subfolder_set:
            continue

        for directory_name in ["files", "processed"]:
            workspace_subfolder_directory_path = workspace_subfolder_path / directory_name
            if not workspace_subfolder_directory_path.exists():
                continue

            scanned_paths.append(workspace_subfolder_directory_path)
            for file_path in sorted(get_pdf_file_paths(workspace_subfolder_directory_path)):
                file_path_str = str(file_path)
                if file_path_str not in seen_paths:
                    seen_paths.add(file_path_str)
                    file_paths.append(file_path)

    return workspace_path, scanned_paths, file_paths

def get_relative_report_path(
        input_pdf_path: str,
        workspace_folder_path: Path,
        relative_base_paths: list[Path] = None) -> str:
    '''
    Convert an input file path into a report-friendly relative path.
    '''
    file_path = Path(input_pdf_path)

    if relative_base_paths:
        sorted_base_paths = sorted(
            relative_base_paths,
            key=lambda path: len(str(path)),
            reverse=True
        )
        for base_path in sorted_base_paths:
            try:
                relative_path = file_path.relative_to(base_path)
                return f"/{relative_path.as_posix()}"
            except ValueError:
                continue

    return str(input_pdf_path).replace(str(workspace_folder_path), "")

def move_file_and_delete_source(
        source_path: Path,
        source_folder: Path,
        project_name: str,
        workspace_name: str,
        subfolder_name: str) -> bool:
    '''
    Move a file from the source folder to the destination subfolder and delete the source file.
    
    :param source_path: Description
    :type source_path: Path
    :param source_folder: Description
    :type source_folder: Path
    :param project_name: Description
    :type project_name: str
    :param workspace_name: Description
    :type workspace_name: str
    :param subfolder_name: Description
    :type subfolder_name: str
    :return: True if the file was moved; False when source file is unavailable.
    :rtype: bool
    '''
    if not source_path.exists():
        return False

    destination_subfolder_path = get_project_workspace_subfolder_path(
        project_name, workspace_name, subfolder_name)
    try:
        relative_path = source_path.relative_to(source_folder)
    except ValueError:
        relative_path = Path(source_path.name)

    destination_path = destination_subfolder_path / relative_path
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        destination_path.write_bytes(source_path.read_bytes())
        source_path.unlink()
    except FileNotFoundError:
        return False

    return True

def _route_validated_files(
        validation_results: list,
        output_pdf_folder: Path,
        project_name: str,
        workspace_name: str,
        verbose: bool = False) -> int:
    '''
    Move validation-passing files into the remediated folder.
    '''
    moved_count = 0
    for file_path, _, _, wcag_result, _, _, _ in validation_results:
        if wcag_result is True:
            if verbose:
                print(f"{file_path}")
            if move_file_and_delete_source(
                Path(file_path),
                output_pdf_folder,
                project_name,
                workspace_name,
                "remediated"
            ):
                moved_count += 1
    return moved_count

# pylint: disable=too-many-arguments, too-many-positional-arguments
def _route_error_validations(
        validation_results: list,
        output_pdf_folder: Path,
        workspace_folder_path: Path,
        project_name: str,
        workspace_name: str,
        verbose: bool = False) -> int:
    '''
    Move files with validation execution errors into unable-to-validate.
    '''
    moved_count = 0
    unable_to_validate_csv_path = \
        workspace_folder_path.parent.parent.parent.parent / "unable-to-validate.csv"

    for file_path, ua1_result, _, wcag_result, _, _, _ in validation_results:
        if ua1_result == 'Error' or wcag_result == 'Error':
            try:
                relative_path = Path(file_path).relative_to(output_pdf_folder)
            except ValueError:
                relative_path = Path(file_path)

            append_to_csv(
                unable_to_validate_csv_path,
                [relative_path, ua1_result, wcag_result]
            )

            if verbose:
                print(f"{file_path}")
            if move_file_and_delete_source(
                Path(file_path),
                output_pdf_folder,
                project_name,
                workspace_name,
                "unable-to-validate"
            ):
                moved_count += 1

    return moved_count

# pylint: disable=too-many-arguments, too-many-locals, too-many-positional-arguments
def _route_font_issue_validations(
        validation_results: list,
        output_pdf_folder: Path,
        project_name: str,
        workspace_name: str,
        font_issue_clauses: list[str],
        font_issue_subfolder: str,
        verbose: bool = False) -> int:
    '''
    Move files with matching font-related validation violations.
    '''
    moved_count = 0
    font_issue_clause_set = set(font_issue_clauses)
    for file_path, ua1_result, _, wcag_result, _, ua1_violations, wcag_violations in validation_results: # pylint: disable=line-too-long
        if ua1_result == 'Error' or wcag_result == 'Error':
            continue

        if ua1_result is False or wcag_result is False:
            has_font_violation = False
            for violation in ua1_violations + wcag_violations:
                if violation['clause'] in font_issue_clause_set:
                    has_font_violation = True
                    break

            if has_font_violation:
                if verbose:
                    print(f"{file_path}")
                if move_file_and_delete_source(
                    Path(file_path),
                    output_pdf_folder,
                    project_name,
                    workspace_name,
                    font_issue_subfolder
                ):
                    moved_count += 1

    return moved_count

# pylint: disable=too-many-arguments, too-many-positional-arguments
def route_validation_results(
        validation_results: list,
        output_pdf_folder: Path,
        workspace_folder_path: Path,
        project_name: str,
        workspace_name: str,
        verbose: bool = False,
        font_issue_clauses: list[str] | None = None,
        font_issue_subfolder: str | None = None,
        font_issue_summary_message: str | None = None,
        font_issues_after_errors: bool = False) -> dict[str, int]:
    '''
    Route validation results to destination workspace folders and print summary counts.
    '''
    print()
    print("MOVING FILES BASED ON VALIDATION RESULTS...")

    valid_files_total = _route_validated_files(
        validation_results,
        output_pdf_folder,
        project_name,
        workspace_name,
        verbose
    )
    print(f"Total valid files moved to remediated folder: {valid_files_total}")

    font_issues_enabled = (
        font_issue_clauses is not None and
        font_issue_subfolder is not None and
        len(font_issue_clauses) > 0
    )

    font_issue_files_total = 0
    error_files_total = 0

    if font_issues_after_errors:
        error_files_total = _route_error_validations(
            validation_results,
            output_pdf_folder,
            workspace_folder_path,
            project_name,
            workspace_name,
            verbose
        )
        print(f"Total error files moved to error folder: {error_files_total}")

        if font_issues_enabled:
            font_issue_files_total = _route_font_issue_validations(
                validation_results,
                output_pdf_folder,
                project_name,
                workspace_name,
                font_issue_clauses,
                font_issue_subfolder,
                verbose
            )
    else:
        if font_issues_enabled:
            font_issue_files_total = _route_font_issue_validations(
                validation_results,
                output_pdf_folder,
                project_name,
                workspace_name,
                font_issue_clauses,
                font_issue_subfolder,
                verbose
            )

        error_files_total = _route_error_validations(
            validation_results,
            output_pdf_folder,
            workspace_folder_path,
            project_name,
            workspace_name,
            verbose
        )
        print(f"Total error files moved to error folder: {error_files_total}")

    if font_issue_summary_message is not None and font_issues_enabled:
        print(font_issue_summary_message.format(count=font_issue_files_total))

    return {
        "valid": valid_files_total,
        "errors": error_files_total,
        "font_issues": font_issue_files_total
    }


def clear_workspace_folder(workspace_folder_path):
    '''
    Clear the workspace folder.
    
    :param workspace_folder_path: Description
    '''
    if not workspace_folder_path.exists():
        workspace_folder_path.mkdir(parents=True, exist_ok=True)
        return

    for entry in workspace_folder_path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

def append_to_csv(file_path: Path, row: list) -> None:
    '''
    Append a row to a CSV file.
    
    :param file_path: Description
    :type file_path: Path
    :param row: Description
    :type row: list
    '''
    unique_key = str(row[0])
    existing_keys = set()

    if file_path.exists():
        try:
            with open(file_path, mode='r', newline='', encoding='utf-8') as infile:
                reader = csv.reader(infile)
                for r in reader:
                    if r: # Ensure the row is not empty
                        existing_keys.add(r[0])
        except Exception: # pylint: disable=broad-exception-caught
            return False

    if unique_key in existing_keys:
        return False

    try:
        with open(file_path, mode='a', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)
            writer.writerow(row)
        return True
    except Exception as e: # pylint: disable=broad-exception-caught
        print(f"Error writing to CSV file: {e}")
        return False

    # with open(file_path, mode='a', newline='', encoding='utf-8') as csvfile:
    #     csv_writer = csv.writer(csvfile)
    #     csv_writer.writerow(row)

def print_workspace_summary(
        project_name: str,
        workspace_name: str,
        ignored_subfolders: list = None) -> None:
    '''
    Print a summary of the workspace folders and file counts.
    
    :param project_name: Description
    :type project_name: str
    :param workspace_name: Description
    :type workspace_name: str
    :param ignored_subfolders: Workspace subfolders to skip in the summary.
    :type ignored_subfolders: list
    '''
    workspace_path = get_project_workspace_path(project_name, workspace_name)
    ignored_subfolders = ignored_subfolders or []
    ignored_subfolder_set = set(ignored_subfolders)

    summary_file_total = 0
    workspaces = {}
    for subfolder_path in workspace_path.iterdir():
        if subfolder_path.is_dir():
            if subfolder_path.name in ignored_subfolder_set:
                continue

            num_of_pdf_files = len(get_pdf_file_paths(subfolder_path))
            summary_file_total += num_of_pdf_files
            workspaces[subfolder_path.name] = {
                "total": num_of_pdf_files
            }

            if Path(subfolder_path / "files").exists():
                num_of_pdf_files_in_files = len(
                    get_pdf_file_paths(subfolder_path / "files")
                )
                workspaces[subfolder_path.name]["files"] = num_of_pdf_files_in_files

            if Path(subfolder_path / "processed").exists():
                num_of_pdf_files_in_processed = len(
                    get_pdf_file_paths(subfolder_path / "processed")
                )
                workspaces[subfolder_path.name]["processed"] = num_of_pdf_files_in_processed

    for folder_name, values in workspaces.items():
        workspace_percentage = round(100 * (values['total']/summary_file_total)) \
            if summary_file_total > 0 else 0

        print()
        print(f"    + {folder_name}: {values['total']} files ({workspace_percentage}%)")

        if 'files' in values:
            print(f"      - files: {values['files']}")
        if 'processed' in values:
            print(f"      - processed: {values['processed']}")

def stream_to_data(stm):
    '''
    Return raw data from stream object.

    :param stm: Description
    '''
    size = stm.GetSize()
    raw_data = (ctypes.c_ubyte * size)()
    stm.Read(0, raw_data, size)
    return raw_data

def bytearray_to_data(byte_array):
    '''
    Convert bytearray to c_ubyte array.
    
    :param byte_array: Description
    '''
    size = len(byte_array)
    return (ctypes.c_ubyte * size).from_buffer(byte_array)

def json_to_raw_data(json_dict):
    '''
    Convert JSON dictionary to raw data.
    
    :param json_dict: Description
    '''
    json_str = json.dumps(json_dict)
    json_data = bytearray(json_str.encode("utf-8"))
    json_data_size = len(json_str)
    json_data_raw = (ctypes.c_ubyte * json_data_size).from_buffer(json_data)
    return json_data_raw, json_data_size
