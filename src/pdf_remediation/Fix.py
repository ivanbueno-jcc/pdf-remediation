from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR
from .utilities.Resources import getFilePaths
from .utilities.VeraPDF import validatePdf, writeValidationReport
from .utilities.PDFix import Fix, GetPageCount
import multiprocessing
import sys
from datetime import datetime
from parallelbar import progress_starmap
from collections import ChainMap

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

    # Cut list to 25000 files for testing purposes
    # file_paths = file_paths[:25000]

    process_count = 4
    print(f"Using {process_count} out of {multiprocessing.cpu_count()} CPU cores.")
    print(f"Found {len(file_paths)} PDF files.")
    if len(file_paths) == 0:
        exit()

    # Extract the input file out of the file_paths list
    input_files = []
    for input, output, report in file_paths:
        input_files.append(input)

    # Get the total number of pages
    results = []
    with multiprocessing.Pool(processes=4) as pool:
        results.append(pool.map(GetPageCount, input_files))
    
    result_list = results.pop()
    page_counts = dict(ChainMap(*result_list))

    total_pages = 0
    for file, count in page_counts.items():
        total_pages += count
    print(f"With {total_pages} total pages.")

    # split the file_paths into batches based on the page count.
    chunks = {
        '10 or less': [],
        '11-50': [],
        '51-100': [],
        '101-200': [],
        '201-500': [],
        '501-1000': [],
        '1001-3000': [],
        '3001 or more': []
    }
    for input, output, report in file_paths:
        match page_counts[input]:
            case x if x <= 10:
                chunks['10 or less'].append((input, output, report))
            case x if 10 < x <= 50:
                chunks['11-50'].append((input, output, report))
            case x if 50 < x <= 100:
                chunks['51-100'].append((input, output, report)) 
            case x if 100 < x <= 200:
                chunks['101-200'].append((input, output, report))
            case x if 200 < x <= 500:
                chunks['201-500'].append((input, output, report))
            case x if 500 < x <= 1000:
                chunks['501-1000'].append((input, output, report))
            case x if 1000 < x <= 3000:
                chunks['1001-3000'].append((input, output, report))
            case x if x > 3000:
                chunks['3001 or more'].append((input, output, report))

    print()
    for key, value in chunks.items():
        if len(value) == 0:
            continue    

        print(f"Processing files with {key} pages.")
        print("Remediating files...")
        results = progress_starmap(Fix, value, total=len(value), n_cpu=process_count)

        print("Validating files...")
        # Switch the input and output directories for the second round of validation
        output_file_paths = []
        for input, output, report in value:
            output_file_paths.append((output, output, report))
        results = progress_starmap(validatePdf, output_file_paths, total=len(output_file_paths), n_cpu=process_count)

        print("Writing validation report...")
        writeValidationReport(folder, results)

        print()


