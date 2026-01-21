'''
PDFix utility functions for PDF remediation and page count.
'''

import csv
import json
import multiprocessing
from pathlib import Path
from dotenv import load_dotenv
from parallelbar import progress_map
from pdfixsdk import *
from python_on_whales import docker
from .Resources import stream_to_data, get_configuration_file, append_to_csv

def get_page_count(inpput_pdf_path: str) -> list:
    '''
    Get page count of a PDF file.
    
    :param inpput_pdf_path: Description
    :type inpput_pdf_path: str
    :return: Description
    :rtype: list
    '''
    pdfix  = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')

    doc = pdfix.OpenDoc(inpput_pdf_path, "")
    if doc is None:
        return {inpput_pdf_path: 0}

    size = doc.GetNumPages()
    doc.Close()

    return {inpput_pdf_path: size}

def get_page_count_multiprocess(
        workspace_folder_path: Path,
        file_paths: list,
        timestamp: str = None,
        subfolder: str = None) -> dict:
    '''
    Multiprocess to get page counts for a list of PDF files.
    
    :param workspace_folder_path: Description
    :type workspace_folder_path: Path
    :param file_paths: Description
    :type file_paths: list
    :param timestamp: Description
    :type timestamp: str
    :param subfolder: Description
    :type subfolder: str
    :return: Description
    :rtype: dict
    '''

    print('Counting files and pages...')
    input_files = [str(file_path) for file_path in file_paths]

    results = []
    results = progress_map(
        get_page_count,
        input_files,
        total=len(input_files),
        n_cpu=multiprocessing.cpu_count()
    )
    page_counts_csv = []
    page_count_lookup = {}
    total_pages = 0
    for d in results:
        page_count_lookup.update(d)
        for file, count in d.items():
            total_pages += count
            page_counts_csv.append([file.replace(str(workspace_folder_path), ""), count])

    print(f"Total PDF files: {len(input_files):}")
    print(f"Total Pages: {total_pages}")

    report_path = workspace_folder_path.parent / "reports" / \
        f"{timestamp}-{subfolder if subfolder else 'files'}"
    report_path.mkdir(parents=True, exist_ok=True)

    with open(report_path / "page_count.csv", mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['path', 'page_count'])
        writer.writerows(page_counts_csv)

    return page_count_lookup

def fix(
        input_pdf_path: str,
        output_pdf_path: str,
        config_file: str = "default.json",
        workspace_folder_path: Path = None) -> None:
    '''
    Wrapper for PDFix remediation command.
    
    :param input_pdf_path: Description
    :type input_pdf_path: str
    :param output_pdf_path: Description
    :type output_pdf_path: str
    :param config_file: Description
    :type config_file: str
    :param workspace_folder_path: Description
    :type workspace_folder_path: Path
    '''

    pdfix  = GetPdfix()
    if pdfix is None:
        raise Exception('Pdfix Initialization fail')
    
    error_file_path = workspace_folder_path.parent.parent.parent.parent / "pdfix_cannot_process_files.csv"

    # Load the license and authorize the account.
    load_dotenv()
    pdfix_license_name = os.getenv('PDFIX_LICENSE_NAME')
    pdfix_license_key = os.getenv('PDFIX_LICENSE_KEY')
    if pdfix_license_name and pdfix_license_key:
        pdfix.GetAccountAuthorization().Authorize(pdfix_license_name, pdfix_license_key)

    doc = pdfix.OpenDoc(input_pdf_path, "")
    if doc is None:
        print('Unable to open pdf', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception('Unable to open pdf : ' + pdfix.GetError())

    command = doc.GetCommand()
    command_statement = None
    command_path = str(get_configuration_file(config_file))

    command_statement = pdfix.CreateFileStream(command_path, kPsReadOnly)
    if not command_statement:
        print('Error', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError())
    if not command.LoadParamsFromStream(command_statement, kDataFormatJson):
        print('Error', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError())
    command_statement.Destroy()

    # run the command
    if not command.Run():
        # print(input_pdf_path)
        print('Error', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError())

    # print(f"Remediation completed: {output_pdf_path}")

    if not doc.Save(output_pdf_path, kSaveFull):
        print('Unable to save', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError())
    doc.Close()

    Path(input_pdf_path).unlink(missing_ok=True)

    # Delete file from the active directory once fixed.
    # original_file = Path(input_pdf_path)
    # print(original_file)
    # original_file.unlink(missing_ok=True)

    # print(f"Remediation completed: {output_pdf_path}")

def font_fix(input_pdf_path: Path, output_pdf_path: Path, project_path: Path = None) -> None:
    '''
    Make a docker container font fix call to Callas pdfToolbox to fix font issues in the PDF.
    Use python-on-whales to make the call.
    '''
    # docker run -v "/Users/ivanbueno/Sites/pdf-remediation/resources/font/tmp:/data" 
    # --env-file "/Users/ivanbueno/Sites/pdf-remediation/resources/font/.env" -w "/data" 
    # --rm pdfix/font-fix-callas:v1.0.4 fix -i "input/input.pdf" -o "output/output.pdf"
    input_relative_path = input_pdf_path.relative_to(
        project_path / "resources" / "font" / "tmp" / "input"
    )
    output_relative_path = output_pdf_path.relative_to(
        project_path / "resources" / "font" / "tmp" / "output"
    )
    # docker.run(
    #     "pdfix/font-fix-callas:v1.0.4",
    #     "fix -i \"input/{}\" -o \"output/{}\"".format(input_relative_path, output_relative_path),
    #     volumes={str(project_path / "resources" / "font" / "tmp"): {'bind': '/data', 'mode': 'rw'}},
    #     env_file=str(project_path / "resources" / "font" / ".env"),
    #     working_dir="/data",
    #     remove=True
    # )



def License() -> json:
    pdfix = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')
    else:
        mem_stm = pdfix.CreateMemStream()
        pdfix.GetStandardAuthorization().SaveToStream(mem_stm, kDataFormatJson)
        bytes = bytearray(stream_to_data(mem_stm))
        json_data = json.loads(bytes.decode("utf-8"))
        mem_stm.Destroy()

        return json_data
    
def LicenseActivate(licenseKey: str) -> bool:
    pdfix = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')
    else:
        if not pdfix.GetStandardAuthorization().Activate(licenseKey):
            return False
        else:
            return True

def LicenseDeactivate() -> bool:
    pdfix = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')
    else:
        if not pdfix.GetStandardAuthorization().Deactivate():
            return False
        else:
            return True
