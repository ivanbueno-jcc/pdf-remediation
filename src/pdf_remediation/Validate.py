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

if __name__ == '__main__':
    folder = ''
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        print(f"Processing {folder} files.")
    else:
        print("Missing argument. Please provide specify an existing folder under Resources containing PDF files.")
        exit()

    multiprocessing.freeze_support()
    file_paths = getFilePaths("pdf", folder)
    process_count = 4
    print(f"Found {len(file_paths)} PDF files.")

    if len(file_paths) == 0:
        exit()
    
    # Get the total number of pages
    # Extract the input file out of the file_paths list
    input_files = []
    for input, output, report in file_paths:
        input_files.append(input)
    results = []
    with multiprocessing.Pool(processes=4) as pool:
        results.append(pool.map(GetPageCount, input_files))
    
    total_pages = 0
    for result in results:
        for r in result:
            total_pages += r[1]

    print(f"With {total_pages} total pages.")

    print(f"Using {process_count} out of {multiprocessing.cpu_count()} CPU cores.")
    print()
    print("Starting VeraPDF Validation...")
    start_wall = time.perf_counter()
    with multiprocessing.Pool(processes=process_count) as pool:
        results = pool.starmap(validatePdf, file_paths)
    end_wall = time.perf_counter()
    print(f"VeraPDF Validation completed in {end_wall - start_wall:.2f} seconds.")

    writeValidationReport(folder, results)
