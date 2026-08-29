# pylint: disable=duplicate-code
'''
PDF Remediation Callas Font Fix Utility
'''
from pathlib import Path
import os
from python_on_whales import docker
from python_on_whales.exceptions import DockerException
from pdf_remediation.utilities.resources import (
    CALLAS_FONT_IMAGE,
    ROOT_DIR,
    append_to_csv,
    ensure_docker_desktop_running,
    print_console_message,
)

class Callas: # pylint: disable=too-few-public-methods
    '''
    Callas pdfToolbox font-fix utility.
    '''
    callas_error_codes = {
        104: "File could not be opened",
        105: "File is encrypted and could not be opened for writing",
        106: "File could not be saved",
        107: "File is damaged and needs repair"
    }

    @staticmethod
    def font_fix(
            input_pdf_path: Path,
            output_pdf_path: Path,
            workspace_path: Path = None) -> Path:
        '''
        Run Callas font-fix in Docker for one PDF.
        '''
        if workspace_path is None:
            raise ValueError("workspace_path is required.")

        license_name = os.getenv("ENV_CALLAS_LICENSE", "").strip()
        license_secret = os.getenv("ENV_CALLAS_SECRET", "").strip()
        pdfix_license_name = os.getenv("PDFIX_LICENSE_NAME", "").strip()
        pdfix_license_key = os.getenv("PDFIX_LICENSE_KEY", "").strip()
        env_file = ROOT_DIR / "resources" / "font" / ".env"
        if not (license_name and license_secret) and not env_file.is_file():
            raise RuntimeError(
                "Callas credentials are not configured. Set "
                "ENV_CALLAS_LICENSE and ENV_CALLAS_SECRET."
            )
        if not (pdfix_license_name and pdfix_license_key):
            raise RuntimeError(
                "PDFix credentials are not configured. The Callas font worker "
                "requires PDFIX_LICENSE_NAME and PDFIX_LICENSE_KEY."
            )
        output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        input_relative_path = Path(input_pdf_path).relative_to(workspace_path)
        output_relative_path = Path(output_pdf_path).relative_to(workspace_path)
        ensure_docker_desktop_running()

        try:
            run_options = {
                "volumes": [(workspace_path.resolve(), '/data')],
                "workdir": "/data",
                "remove": True,
            }
            if license_name and license_secret:
                run_options["envs"] = {
                    "ENV_CALLAS_LICENSE": license_name,
                    "ENV_CALLAS_SECRET": license_secret,
                }
            else:
                # Retain the ignored local env-file workflow for developers.
                run_options["env_files"] = [str(env_file)]
            docker.run(
                CALLAS_FONT_IMAGE,
                [
                    "fix",
                    "--name", pdfix_license_name,
                    "--key", pdfix_license_key,
                    "-i", str(input_relative_path),
                    "-o", str(output_relative_path),
                ],
                **run_options,
            )
        except DockerException as e:
            match e.return_code:
                case value if value >= 5 and value <= 8: # pylint: disable=chained-comparison
                    input_pdf_path.unlink(missing_ok=True)
                case value if value >= 104 and value <= 107: # pylint: disable=chained-comparison
                    error_detail = Callas.callas_error_codes.get(
                        e.return_code, "Unknown Error"
                    )
                    print_console_message(
                        "error",
                        f"{input_relative_path}: {error_detail}"
                    )
                    append_to_csv(
                        workspace_path.parent.parent / "callas-font-errors.csv",
                        [
                            input_relative_path,
                            e.return_code,
                            error_detail
                        ]
                    )
                    raise RuntimeError(
                        f"Callas worker failed: {error_detail} (exit {e.return_code})."
                    ) from None
                case _:
                    error_detail = f"Callas worker failed with exit code {e.return_code}."
                    print_console_message("error", error_detail)
                    # DockerException renders the complete docker command, including
                    # PDFix's required --key value. Never propagate or log it.
                    raise RuntimeError(error_detail) from None
        except Exception as e:
            print_console_message("error", f"Unexpected error: {e}")
            raise e

        return output_pdf_path

def font_fix(input_pdf_path: Path, output_pdf_path: Path, workspace_path: Path = None) -> Path:
    '''
    Backward-compatible wrapper around Callas.font_fix.
    '''
    return Callas.font_fix(input_pdf_path, output_pdf_path, workspace_path)
