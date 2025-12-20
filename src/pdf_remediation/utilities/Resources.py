from pathlib import Path
import ctypes
import json

ROOT_DIR = Path(__file__).parent.parent.parent.parent

OUTPUT_DIR = ROOT_DIR / "resources/output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

INPUT_DIR = ROOT_DIR / "resources/input"
INPUT_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_DIR = ROOT_DIR / "resources/reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = ROOT_DIR / "resources/configuration"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = CONFIG_DIR / "make-accessible.json"

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