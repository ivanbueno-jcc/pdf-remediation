# pylint: disable=undefined-variable
'''
PDFix utility functions for PDF remediation and page count.
'''

import csv
import json
import multiprocessing
import os
from pathlib import Path
from dotenv import load_dotenv
from parallelbar import progress_map
from pdfixsdk import * # pylint: disable=wildcard-import, unused-wildcard-import
from python_on_whales import docker
from python_on_whales.exceptions import DockerException, NoSuchImage
from .resources import PDFIX_FONT_IMAGE, stream_to_data, get_configuration_file, append_to_csv

def pull_image(image_name: str) -> None:
    '''
    Pull a Docker image if it is not already present locally.

    :param image_name: Name of the Docker image to pull.
    :type image_name: str
    '''
    try:
        docker.image.inspect(image_name)
        print(f"Docker image '{image_name}' is already present locally.")
    except NoSuchImage:
        print(f"Pulling Docker image '{image_name}'...")
        docker.image.pull(image_name)
        print(f"Docker image '{image_name}' pulled successfully.")

def is_pdf_secured(input_pdf_path: str) -> bool:
    '''
    Check if a PDF file is secured.
    
    :param input_pdf_path: Description
    :type input_pdf_path: str
    :return: Description
    :rtype: bool
    '''
    pdfix  = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')

    doc = pdfix.OpenDoc(input_pdf_path, "")
    if doc is None:
        # print('Unable to open pdf', pdfix.GetError())
        return {input_pdf_path: 'Error'}

    is_secured = doc.IsSecured()

    doc.Close()

    return {input_pdf_path: is_secured}


def get_page_count(input_pdf_path: str) -> list:
    '''
    Get page count of a PDF file.
    
    :param input_pdf_path: Description
    :type input_pdf_path: str
    :return: Description
    :rtype: list
    '''
    pdfix  = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')

    doc = pdfix.OpenDoc(input_pdf_path, "")
    if doc is None:
        return {input_pdf_path: 0}

    size = doc.GetNumPages()
    doc.Close()

    return {input_pdf_path: size}

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
        workspace_folder_path: Path = None,
        verbose: bool = False) -> None:
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
        raise Exception('Pdfix Initialization fail') # pylint: disable=broad-exception-raised

    error_file_path = workspace_folder_path.parent.parent.parent.parent \
        / "pdfix_cannot_process_files.csv"

    # Load the license and authorize the account.
    load_dotenv()
    pdfix_license_name = os.getenv('PDFIX_LICENSE_NAME')
    pdfix_license_key = os.getenv('PDFIX_LICENSE_KEY')
    if pdfix_license_name and pdfix_license_key:
        pdfix.GetAccountAuthorization().Authorize(pdfix_license_name, pdfix_license_key)

    doc = pdfix.OpenDoc(input_pdf_path, "")
    if doc is None:
        if verbose:
            print('Unable to open pdf', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception('Unable to open pdf : ' + pdfix.GetError()) # pylint: disable=broad-exception-raised

    command = doc.GetCommand()
    command_statement = None
    command_path = str(get_configuration_file(config_file))

    command_statement = pdfix.CreateFileStream(command_path, kPsReadOnly)
    if not command_statement:
        if verbose:
            print('Error', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError()) # pylint: disable=broad-exception-raised
    if not command.LoadParamsFromStream(command_statement, kDataFormatJson):
        if verbose:
            print('Error', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError()) # pylint: disable=broad-exception-raised
    command_statement.Destroy()

    # run the command
    if not command.Run():
        # print(input_pdf_path)
        if verbose:
            print('Error', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError()) # pylint: disable=broad-exception-raised

    # print(f"Remediation completed: {output_pdf_path}")

    if not doc.Save(output_pdf_path, kSaveFull):
        if verbose:
            print('Unable to save', pdfix.GetError())
        append_to_csv(
            error_file_path,
            [Path(input_pdf_path).relative_to(workspace_folder_path), pdfix.GetError()]
        )
        raise Exception(pdfix.GetError()) # pylint: disable=broad-exception-raised
    doc.Close()

    Path(input_pdf_path).unlink(missing_ok=True)

    # print(f"Remediation completed: {output_pdf_path}")

def font_fix_pdfix(
        input_pdf_path: Path,
        output_pdf_path: Path,
        workspace_path: Path = None) -> None:
    '''
    PDFix font fix utility.
    :param input_pdf_path: Description
    :type input_pdf_path: Path
    :param output_pdf_path: Description
    :type output_pdf_path: Path
    :param workspace_path: Description
    :type workspace_path: Path
    '''

    # Load the license and authorize the account.
    load_dotenv()
    pdfix_license_name = os.getenv('PDFIX_LICENSE_NAME')
    pdfix_license_key = os.getenv('PDFIX_LICENSE_KEY')

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    input_relative_path = Path(input_pdf_path).relative_to(workspace_path)
    output_relative_path = Path(output_pdf_path).relative_to(workspace_path)

    try:
        docker.run(
            PDFIX_FONT_IMAGE,
            [
                "fix-missing-unicode",
                "--name", pdfix_license_name,
                "--key", pdfix_license_key,
                "-i", str(input_relative_path),
                "-o", str(output_relative_path)
            ],
            volumes=[(workspace_path.resolve(), '/data')],
            workdir="/data",
            remove=True
        )
    except DockerException as e:
        print(f"DockerException occurred: {e}")
        match e.return_code:
            case _:
                append_to_csv(
                    workspace_path.parent.parent / "pdfix-font-errors.csv",
                    [
                        input_relative_path,
                        e.return_code
                    ]
                )
                input_pdf_path.unlink(missing_ok=True)
                raise DockerException(0) # pylint: disable=raise-missing-from, no-value-for-parameter
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        raise e
    finally:
        input_pdf_path.unlink(missing_ok=True)

def license_status() -> json:
    '''
    Display license information.
    
    :return: Description
    :rtype: Any
    '''
    pdfix = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')
        return False

    # Load the license and authorize the account.
    load_dotenv()
    pdfix_license_name = os.getenv('PDFIX_LICENSE_NAME')
    pdfix_license_key = os.getenv('PDFIX_LICENSE_KEY')
    if pdfix_license_name and pdfix_license_key:
        pdfix.GetAccountAuthorization().Authorize(pdfix_license_name, pdfix_license_key)

    mem_stm = pdfix.CreateMemStream()
    pdfix.GetAccountAuthorization().SaveToStream(mem_stm, kDataFormatJson)
    bytes_data = bytearray(stream_to_data(mem_stm))
    json_data = json.loads(bytes_data.decode("utf-8"))
    mem_stm.Destroy()

    return json_data

def license_activate(license_key: str) -> bool:
    '''
    Activate the license.
    
    :param licenseKey: Description
    :type licenseKey: str
    :return: Description
    :rtype: bool
    '''
    pdfix = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')
        return False

    if not pdfix.GetStandardAuthorization().Activate(license_key):
        return False

    return True

def license_deactivate() -> bool:
    '''
    Deactivate the license.
    '''
    pdfix = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')
        return False

    if not pdfix.GetStandardAuthorization().Deactivate():
        return False

    return True
