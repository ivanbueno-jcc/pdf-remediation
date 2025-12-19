from pathlib import Path

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

    for file_path in input_directory_path.glob(f"*.{file_type}"):
        file_paths.append((str(file_path), str(Path(output_directory_path / file_path.name)), str(Path(reports_directory_path))))

    return file_paths