'''
PDF Remediation Callas Font Fix Utility
'''
from pathlib import Path
from python_on_whales import docker
from python_on_whales.exceptions import DockerException
from pdf_remediation.utilities.resources import append_to_csv

def font_fix(input_pdf_path: Path, output_pdf_path: Path, workspace_path: Path = None) -> Path:
    '''
    Make a docker container font fix call to Callas pdfToolbox to fix font issues in the PDF.
    Use python-on-whales to make the call.
    '''

    project_path = workspace_path.parent.parent.parent.parent.parent
    env_file = str(project_path / "resources" / "font" / ".env")
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    input_relative_path = Path(input_pdf_path).relative_to(workspace_path)
    output_relative_path = Path(output_pdf_path).relative_to(workspace_path)

    callas_error_codes = {
        104: "File could not be opened",
        105: "File is encrypted and could not be opened for writing",
        106: "File could not be saved",
        107: "File is damaged and needs repair"
    }

    try:
        docker.run(
            "pdfix/font-fix-callas:v1.0.4",
            ["fix", "-i", str(input_relative_path), "-o", str(output_relative_path)],
            volumes=[(workspace_path.resolve(), '/data')],
            env_files=[env_file],
            workdir="/data",
            remove=True
        )
    except DockerException as e:
        match e.return_code:
            case value if value >= 5 and value <= 8: # pylint: disable=chained-comparison
                input_pdf_path.unlink(missing_ok=True)
            case value if value >= 104 and value <= 107: # pylint: disable=chained-comparison
                print(f"{input_relative_path}: \
                      {callas_error_codes.get(e.return_code, 'Unknown Error')}")
                append_to_csv(
                    workspace_path.parent.parent / "callas_font_fix_errors.csv",
                    [
                        input_relative_path,
                        e.return_code,
                        callas_error_codes.get(e.return_code, "Unknown Error")
                    ]
                )
                raise DockerException(0) # pylint: disable=raise-missing-from, no-value-for-parameter
            case _:
                print(f"DockerException occurred: {e}")
                raise DockerException(0) # pylint: disable=raise-missing-from, no-value-for-parameter
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        raise e
