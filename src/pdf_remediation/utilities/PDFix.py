from .Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR, CONFIG_FILE
from .Resources import getFilePaths
from pdfixsdk import *
from pathlib import Path

def GetPageCount(inputPdfPath: str) -> list:
    # Open the PDF document
    pdfix  = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')

    doc = pdfix.OpenDoc(inputPdfPath, "")
    if doc is None:
        return [inputPdfPath.split("/")[-1], -1]

    size = doc.GetNumPages()

    doc.Close()

    # Get filename from inputPdfPath
    filename = inputPdfPath.split("/")[-1]

    return [filename, size]

def Fix(inputPdfPath: str, outputPdfPath: str, reportPath: str) -> None:
    # print(f"Remediating: {inputPdfPath}")

    pdfix  = GetPdfix()
    if pdfix is None:
        print('Pdfix Initialization fail')

    doc = pdfix.OpenDoc(inputPdfPath, "")
    if doc is None:
        print('Unable to open pdf : ' + pdfix.GetError())

    command = doc.GetCommand()
    cmdStm = None
    commandPath = str(CONFIG_FILE)

    cmdStm = pdfix.CreateFileStream(commandPath, kPsReadOnly)
    if not cmdStm:
        print(pdfix.GetError())

    if not command.LoadParamsFromStream(cmdStm, kDataFormatJson):
        print(pdfix.GetError())

    cmdStm.Destroy()

    # run the command
    if not command.Run():
        print(pdfix.GetError())

    if not doc.Save(outputPdfPath, kSaveFull):
        print(pdfix.GetError())

    doc.Close()
    # print(f"Remediation completed: {outputPdfPath}")
