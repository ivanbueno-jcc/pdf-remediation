from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR
from .utilities.Resources import getFilePaths
from .utilities.VeraPDF import validatePdf
from .utilities.PDFix import Fix, GetPageCount
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

    # Cut list to 25000 files for testing purposes
    file_paths = file_paths[:25000]

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
    
    total_pages = 0
    for result in results:
        for r in result:
            total_pages += r[1]

    print(f"With {total_pages} total pages.")

    # print()
    # print("Starting VeraPDF Validation...")
    # start_wall = time.perf_counter()
    # start_cpu = time.process_time()
    # with multiprocessing.Pool(processes=process_count) as pool:
    #     results = pool.starmap(validatePdf, file_paths)
    # end_wall = time.perf_counter()
    # end_cpu = time.process_time()

    # failed_rules = []
    # passed = failed = error = 0
    # for row in results:
    #     filename, result, rules = row
    #     if result == False:
    #         failed += 1
    #     elif result == True:
    #         passed += 1
    #     elif result == 'Error':
    #         error += 1
        
    #     if len(rules) > 0:
    #         for rule in rules:
    #             failed_rules.append([filename, rule["specification"], rule["clause"], rule["tags"], rule["description"]])

    #     del row[2]

    # print(f"Passed: {passed}, Failed: {failed}, Errors: {error}")
    # print()
    # print("Time taken:")
    # print(f"{end_wall - start_wall:.2f} seconds (wall time)")
    # print(f"{end_cpu - start_cpu:.2f} seconds (CPU time)")

    # # Write results to CSV
    # timestamp_string = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # with open(REPORTS_DIR / folder / f"vera_validation_results_{timestamp_string}.csv", mode='w', newline='') as file:
    #     writer = csv.writer(file)
    #     writer.writerow(['Filename', 'Validation Result'])
    #     writer.writerows(results)
    # print(f"Detailed results saved to {REPORTS_DIR / folder / f'vera_validation_results_{timestamp_string}.csv'}")

    print()
    print("Starting PDFix remediation...")
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    with multiprocessing.Pool(processes=process_count) as pool:
        results = pool.starmap(Fix, file_paths)
    end_wall = time.perf_counter()
    end_cpu = time.process_time()

    print()
    print("PDFix remediation completed.")
    print("Time taken:")
    print(f"{end_wall - start_wall:.2f} seconds (wall time)")
    print(f"{end_cpu - start_cpu:.2f} seconds (CPU time)")

    # Switch the input and output directories for the second round of validation
    output_file_paths = []
    for input, output, report in file_paths:
        output_file_paths.append((output, output, report))

    print()
    print("Starting VeraPDF Validation...")
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    with multiprocessing.Pool(processes=process_count) as pool:
        results = pool.starmap(validatePdf, output_file_paths)
    end_wall = time.perf_counter()
    end_cpu = time.process_time()

    failed_rules = []
    passed = failed = error = 0
    for row in results:
        filename, result, rules = row
        if result == False:
            failed += 1
        elif result == True:
            passed += 1
        elif result == 'Error':
            error += 1
        
        if len(rules) > 0:
            for rule in rules:
                failed_rules.append([filename, rule["specification"], rule["clause"], rule["tags"], rule["description"]])

        del row[2]

    print(f"Passed: {passed}, Failed: {failed}, Errors: {error}")
    print()
    print("Time taken:")
    print(f"{end_wall - start_wall:.2f} seconds (wall time)")
    print(f"{end_cpu - start_cpu:.2f} seconds (CPU time)")

    # Write results to CSV
    timestamp_string = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    with open(REPORTS_DIR / folder / f"__{folder}_vera_validation_results_{timestamp_string}.csv", mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Filename', 'Validation Result'])
        writer.writerows(results)
    print(f"Detailed results saved to {REPORTS_DIR / folder / f'__{folder}_vera_validation_results_{timestamp_string}.csv'}")
    if len(failed_rules) > 0:
        with open(REPORTS_DIR / folder / f"__{folder}_failed_rules_{timestamp_string}.csv", mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['Filename', 'Specification', 'Clause', 'Tags', 'Description'])
            writer.writerows(failed_rules)
        print(f"Failed rules saved to {REPORTS_DIR / folder / f'__{folder}_failed_rules_{timestamp_string}.csv'}")