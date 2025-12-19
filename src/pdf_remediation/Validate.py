from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR
from .utilities.Resources import getFilePaths
from .utilities.VeraPDF import validatePdf
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
        print("Please provide a folder under Resources containing PDF files.")
        exit()

    multiprocessing.freeze_support()
    file_paths = getFilePaths("pdf", folder)
    process_count = 4
    print(f"Found {len(file_paths)} PDF files.")
    print(f"Using {process_count} out of {multiprocessing.cpu_count()} CPU cores.")
    print()
    print("Starting VeraPDF Validation...")
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    with multiprocessing.Pool(processes=process_count) as pool:
        results = pool.starmap(validatePdf, file_paths)
    end_wall = time.perf_counter()
    end_cpu = time.process_time()

    passed = failed = error = 0
    for filename, result in results:
        if result == False:
            failed += 1
        elif result == True:
            passed += 1
        elif result == 'Error':
            error += 1

    print(f"Passed: {passed}, Failed: {failed}, Errors: {error}")
    print()
    print("Time taken:")
    print(f"{end_wall - start_wall:.2f} seconds (wall time)")
    print(f"{end_cpu - start_cpu:.2f} seconds (CPU time)")

    # Write results to CSV
    timestamp_string = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    with open(REPORTS_DIR / folder / f"vera_validation_results_{timestamp_string}.csv", mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Filename', 'Validation Result'])
        writer.writerows(results)
    print(f"Detailed results saved to {REPORTS_DIR / folder / f'vera_validation_results_{timestamp_string}.csv'}")
