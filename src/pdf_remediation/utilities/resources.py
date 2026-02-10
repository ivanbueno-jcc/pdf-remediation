'''
Utility functions for managing project resources and paths.
'''
from pathlib import Path
import csv
import ctypes
import json
import os
import platform
import shutil
import subprocess
import time
from dotenv import load_dotenv

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
    file_paths = list(subfolder_path.rglob("*.pdf"))

    if len(file_paths) == 0 and workspace_name == "default" and subfolder_name == "active":
        # check if workspace folder contains pdf files
        source_path = get_project_source_path(project_name)
        semaphore = subfolder_path / ".remediation.lock"
        if not semaphore.exists():
            if len(list(source_path.rglob("*.pdf"))) > 0:
                for file_path in source_path.rglob("*.pdf"):
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
        file_paths = list(subfolder_path.rglob("*.pdf"))

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
            for file_path in sorted(workspace_subfolder_directory_path.rglob("*.pdf")):
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
        subfolder_name: str) -> None:
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
    '''
    destination_subfolder_path = get_project_workspace_subfolder_path(
        project_name, workspace_name, subfolder_name)
    relative_path = source_path.relative_to(source_folder)
    destination_path = destination_subfolder_path / relative_path
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(source_path.read_bytes())
    source_path.unlink()


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

            num_of_pdf_files = len(list(subfolder_path.rglob("*.pdf")))
            summary_file_total += num_of_pdf_files
            workspaces[subfolder_path.name] = {
                "total": num_of_pdf_files
            }

            if Path(subfolder_path / "files").exists():
                num_of_pdf_files_in_files = len(
                    list((subfolder_path / "files").rglob("*.pdf"))
                )
                workspaces[subfolder_path.name]["files"] = num_of_pdf_files_in_files

            if Path(subfolder_path / "processed").exists():
                num_of_pdf_files_in_processed = len(
                    list((subfolder_path / "processed").rglob("*.pdf"))
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
