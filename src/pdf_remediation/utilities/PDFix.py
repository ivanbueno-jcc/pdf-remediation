import csv
from .Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR, CONFIG_FILE
from .Resources import getFilePaths, stream_to_data
from pdfixsdk import *
from pathlib import Path
import json
from dotenv import load_dotenv
import multiprocessing
from parallelbar import progress_map

def GetPageCount(inputPdfPath: str) -> list:
    # Open the PDF document
    pdfix  = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')

    doc = pdfix.OpenDoc(inputPdfPath, "")
    if doc is None:
        return {inputPdfPath: 0}

    size = doc.GetNumPages()

    doc.Close()

    return {inputPdfPath: size}

def get_page_count_total(workspace_folder_path: Path, file_paths: list, timestamp: str = None) -> int:
    print('Counting files and pages...')
    input_files = [str(file_path) for file_path in file_paths]

    results = []
    results = progress_map(GetPageCount, input_files, total=len(input_files), n_cpu=multiprocessing.cpu_count())
    page_counts = []
    total_pages = 0
    for d in results:
        for file, count in d.items():
            total_pages += count
            page_counts.append([file.replace(str(workspace_folder_path), ""), count])
    
    print(f"Total PDF files: {len(input_files):}")
    print(f"Total Pages: {total_pages}")

    report_path = workspace_folder_path.parent / "reports" / timestamp
    report_path.mkdir(parents=True, exist_ok=True)

    with open(report_path / f"page_count.csv", mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['path', 'page_count'])
        writer.writerows(page_counts)

    return total_pages

def Fix(inputPdfPath: str, outputPdfPath: str, reportPath: str) -> None:
    # print(f"Remediating: {inputPdfPath}")

    pdfix  = GetPdfix()
    if pdfix is None:
        raise Exception('Pdfix Initialization fail')

    # Load the license and authorize the account.
    load_dotenv()
    pdfix_license_name = os.getenv('PDFIX_LICENSE_NAME')
    pdfix_license_key = os.getenv('PDFIX_LICENSE_KEY')
    if pdfix_license_name and pdfix_license_key:
        pdfix.GetAccountAuthorization().Authorize(pdfix_license_name, pdfix_license_key)

    doc = pdfix.OpenDoc(inputPdfPath, "")
    if doc is None:
        raise Exception('Unable to open pdf : ' + pdfix.GetError())

    command = doc.GetCommand()
    cmdStm = None
    commandPath = str(CONFIG_FILE)

    cmdStm = pdfix.CreateFileStream(commandPath, kPsReadOnly)
    if not cmdStm:
        raise Exception(pdfix.GetError())
    if not command.LoadParamsFromStream(cmdStm, kDataFormatJson):
        raise Exception(pdfix.GetError())
    cmdStm.Destroy()

    # run the command
    if not command.Run():
        # print(inputPdfPath)
        raise Exception(pdfix.GetError())

    # print(f"Remediation completed: {outputPdfPath}")

    # create the directory if it does not exist
    Path(outputPdfPath).parent.mkdir(parents=True, exist_ok=True)
    if not doc.Save(outputPdfPath, kSaveFull):
        raise Exception(pdfix.GetError())
    doc.Close()
    # print(f"Remediation completed: {outputPdfPath}")

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
