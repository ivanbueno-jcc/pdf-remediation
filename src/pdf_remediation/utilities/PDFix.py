import csv
from .Resources import CONFIG_FILE
from .Resources import stream_to_data
from pdfixsdk import *
from pathlib import Path
import json
from dotenv import load_dotenv
import multiprocessing
from parallelbar import progress_map

def get_page_count(inputPdfPath: str) -> list:
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

def get_page_count_multiprocess(workspace_folder_path: Path, file_paths: list, timestamp: str = None) -> dict:
    print('Counting files and pages...')
    input_files = [str(file_path) for file_path in file_paths]

    results = []
    results = progress_map(get_page_count, input_files, total=len(input_files), n_cpu=multiprocessing.cpu_count())
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

    report_path = workspace_folder_path.parent / "reports" / timestamp
    report_path.mkdir(parents=True, exist_ok=True)

    with open(report_path / f"page_count.csv", mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['path', 'page_count'])
        writer.writerows(page_counts_csv)

    return page_count_lookup

def fix(inputPdfPath: str, outputPdfPath: str) -> None:
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
        print('Unable to open pdf', pdfix.GetError())
        raise Exception('Unable to open pdf : ' + pdfix.GetError())

    command = doc.GetCommand()
    cmdStm = None
    commandPath = str(CONFIG_FILE)

    cmdStm = pdfix.CreateFileStream(commandPath, kPsReadOnly)
    if not cmdStm:
        print('Error', pdfix.GetError())
        raise Exception(pdfix.GetError())
    if not command.LoadParamsFromStream(cmdStm, kDataFormatJson):
        print('Error', pdfix.GetError())
        raise Exception(pdfix.GetError())
    cmdStm.Destroy()

    # run the command
    if not command.Run():
        # print(inputPdfPath)
        print('Error', pdfix.GetError())
        raise Exception(pdfix.GetError())

    # print(f"Remediation completed: {outputPdfPath}")

    if not doc.Save(outputPdfPath, kSaveFull):
        print('Unable to save', pdfix.GetError())
        raise Exception(pdfix.GetError())
    doc.Close()

    Path(inputPdfPath).unlink(missing_ok=True)

    # Delete file from the active directory once fixed.
    # original_file = Path(inputPdfPath)
    # print(original_file)
    # original_file.unlink(missing_ok=True)

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
