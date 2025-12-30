from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR
from .utilities.Resources import getFilePaths
from .utilities.VeraPDF import validatePdf, writeValidationReport
from .utilities.PDFix import Fix, GetPageCount
import multiprocessing
import sys
from datetime import datetime
from parallelbar import progress_starmap, progress_map
import re

if __name__ == '__main__':
    multiprocessing.freeze_support()
    multiprocessing.set_start_method("spawn", force=True)

    folder = ''
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        print(f"Fixing {folder}.")
    else:
        print("Missing argument. Please provide specify an existing folder under Resources containing PDF files.")
        exit()

    file_paths = getFilePaths("pdf", folder)

    process_count = 4
    print(f"Using {process_count} out of {multiprocessing.cpu_count()} CPU cores.")
    print(f"Found {len(file_paths):,} PDF files.")
    if len(file_paths) == 0:
        exit()

    # Extract the input file out of the file_paths list
    input_files = []
    for input, output, report in file_paths:
        input_files.append(input)

    # Get the total number of pages
    print()
    print("Counting pages...")
    results = []
    # with multiprocessing.Pool(processes=4) as pool:
    #     results.append(pool.map(GetPageCount, input_files))
    results = progress_map(GetPageCount, input_files, total=len(input_files))
    page_counts = {}
    for d in results:
        page_counts.update(d)

    total_pages = 0
    for file, count in page_counts.items():
        total_pages += count
    print(f"{total_pages:,} total pages.")

    # split the file_paths into batches based on the page count.
    chunks = {
        '1': [],
        '2-5': [],
        '6-10': [],
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
            case 1:
                chunks['1'].append((input, output, report))
            case x if 1 < x <= 5:
                chunks['2-5'].append((input, output, report))
            case x if 5 < x <= 10:
                chunks['6-10'].append((input, output, report))
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
    print("File distribution by page count:")
    for key, value in chunks.items():
        print(f"{key}: {len(value)} files")
    # if value is large, split into sub-chunks.
    sub_chunks = {}
    del_chunks = []
    chunk_size = 500
    for key, value in chunks.items():
        if len(value) > chunk_size:
            del_chunks.append(key)
            chunk_count = len(value) // chunk_size + 1
            for i in range(chunk_count):
                chunk_key = f"{key} - part {i+1} of {chunk_count}"
                sub_chunks[chunk_key] = value[i*chunk_size:(i+1)*chunk_size]
    for key in del_chunks:
        del chunks[key]
        
    sub_chunks.update(chunks)
    chunks = sub_chunks

    print()
    for key, value in chunks.items():
        if len(value) == 0:
            continue    

        # # skip smaller files for testing
        # numbers_as_strings = re.findall(r'\d+', key)
        # numbers_as_integers = [int(num) for num in numbers_as_strings]
        # item1, item2, item3 = numbers_as_integers + [0]*(3 - len(numbers_as_integers))
        # if (item1 == 1 and item2 < 2460) \
        #     or (item2 == 10 and item3 < 4000) \
        #     or (item2 == 50 and item3 < 4000) \
        #     or (item2 == 100 and item3 < 347) \
        #     or (item2 == 5 and item3 < 4000):
        #     continue
        # print(numbers_as_integers)


        print(f"FILES WITH PAGE COUNT OF {key}")
        print("Remediating...")

        results = progress_starmap(
            Fix, 
            value, 
            total=len(value), 
            error_behavior="coerce", 
            process_timeout=600
        )

        print("Validating...")
        # Switch the input and output directories for the second round of validation
        output_file_paths = []
        for input, output, report in value:
            output_file_paths.append((output, output, report))
        results = progress_starmap(validatePdf, output_file_paths, total=len(output_file_paths))

        print("Writing report...")
        writeValidationReport(folder, results)

        print()


