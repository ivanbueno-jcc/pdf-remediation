from pathlib import Path
import ctypes
import json
from dotenv import load_dotenv
import os

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent.parent.parent

CONFIG_DIR = ROOT_DIR / "resources/configuration"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "make-accessible.json"

PROJECT_BASE_PATH = Path(os.getenv('PROJECT_BASE_PATH', './resources/projects'))
PROJECT_BASE_PATH.mkdir(parents=True, exist_ok=True)

def get_project_path(project_name: str) -> Path:
    project_path = PROJECT_BASE_PATH / project_name
    project_path.mkdir(parents=True, exist_ok=True)
    return project_path

def get_project_source_path(project_name: str) -> Path:
    source_path = get_project_path(project_name) / "source"
    source_path.mkdir(parents=True, exist_ok=True)
    return source_path

def get_project_workspace_path(project_name: str, workspace_name: str = "default") -> Path:
    workspace_path = get_project_path(project_name) / "workspace" / workspace_name
    workspace_path.mkdir(parents=True, exist_ok=True)

    workspace_subfolders = ["active", "remediated"]
    for subfolder in workspace_subfolders:
        subfolder_path = workspace_path / subfolder
        subfolder_path.mkdir(parents=True, exist_ok=True)

    return workspace_path

def get_project_workspace_file_paths(project_name: str, workspace_name: str, subfolder_name: str) -> list:
    subfolder_path = get_project_workspace_subfolder_path(project_name, workspace_name, subfolder_name)
    file_paths = list(subfolder_path.rglob("*.pdf"))
  
    if len(file_paths) == 0 and workspace_name == "default" and subfolder_name == "active":
        # check if workspace folder contains pdf files
        source_path = get_project_source_path(project_name)
        semaphore = subfolder_path / ".remediation.lock"
        if not semaphore.exists() and len(list(source_path.rglob("*.pdf"))):
            for file_path in source_path.rglob("*.pdf"):
                relative_path = file_path.relative_to(source_path)
                destination_path = subfolder_path / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(file_path.read_bytes())

            # Add a semaphore to only copy over the source once, until reset.
            semaphore.touch(exist_ok=True)

            # Re-run to get the file paths again.
            file_paths = get_project_workspace_file_paths(project_name, workspace_name, subfolder_name)
        else:
            print(f"No PDF files found.")
            print()
            print("Please add PDF files to the source folder and re-run the script:")
            print(f"{source_path.resolve()}")
            exit()

    return file_paths

def get_project_workspace_subfolder_path(project_name: str, workspace_name: str, subfolder_name: str) -> Path:
    subfolder_path = get_project_workspace_path(project_name, workspace_name) / subfolder_name / "files"
    subfolder_path.mkdir(parents=True, exist_ok=True)
    return subfolder_path

OUTPUT_DIR = ROOT_DIR / "resources/output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

INPUT_DIR = ROOT_DIR / "resources/input"
INPUT_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_DIR = ROOT_DIR / "resources/reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def getFilePaths(file_type: str, directory: str) -> list:
    file_paths = []
    input_directory_path = Path(INPUT_DIR / directory)

    output_directory_path = Path(OUTPUT_DIR / directory)
    output_directory_path.mkdir(parents=True, exist_ok=True)

    reports_directory_path = Path(REPORTS_DIR / directory)
    reports_directory_path.mkdir(parents=True, exist_ok=True)

    for file_path in input_directory_path.rglob(f"*.{file_type}"):
        output_file = str(file_path.parent).replace(str(INPUT_DIR), str(OUTPUT_DIR)) + "/" + file_path.name
        file_paths.append(
           (str(file_path), 
            output_file, 
            str(Path(reports_directory_path))
            ))
    return file_paths

# return raw data from stream object
def stream_to_data(stm):
  size = stm.GetSize()
  raw_data = (ctypes.c_ubyte * size)()
  stm.Read(0, raw_data, size)
  return raw_data

def bytearray_to_data(byte_array): 
  size = len(byte_array)
  return (ctypes.c_ubyte * size).from_buffer(byte_array)

# function to convert json dictionary to c_ubyte array
def jsonToRawData(json_dict):
    json_str = json.dumps(json_dict)
    json_data = bytearray(json_str.encode("utf-8"))
    json_data_size = len(json_str)
    json_data_raw = (ctypes.c_ubyte * json_data_size).from_buffer(json_data)
    return json_data_raw, json_data_size