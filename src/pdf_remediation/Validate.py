from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR
from .utilities.Resources import getFilePaths
from .utilities.VeraPDF import validatePdf, writeValidationReport
from .utilities.PDFix import GetPageCount
from pathlib import Path
import multiprocessing
import time
import csv
import sys
from datetime import datetime
from parallelbar import progress_starmap, progress_map

if __name__ == '__main__':
    folder = ''
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        print(f"Processing {folder}.")
    else:
        print("Missing argument. Please provide specify an existing folder under Resources containing PDF files.")
        exit()

    multiprocessing.freeze_support()
    file_paths = getFilePaths("pdf", folder)
    process_count = 4
    print(f"Found {len(file_paths):,} PDF files.")

    if len(file_paths) == 0:
        exit()
    
    print()
    print('Counting pages...')
    # Get the total number of pages
    # Extract the input file out of the file_paths list
    input_files = []
    for input, output, report in file_paths:
        input_files.append(input)
    results = []
    results = progress_map(GetPageCount, input_files, total=len(input_files), n_cpu=process_count)
    page_counts = {}
    for d in results:
        page_counts.update(d)

    total_pages = 0
    for file, count in page_counts.items():
        total_pages += count
    print(f"{total_pages:,} total pages.")

    print(f"Using {process_count} out of {multiprocessing.cpu_count()} CPU cores.")
    print()
    print("Validating PDFs...")
    results = progress_starmap(validatePdf, file_paths, total=len(file_paths))

    writeValidationReport(folder, results)
