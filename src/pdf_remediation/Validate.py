from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, getFilePaths
from .utilities.Vera import validatePdf
from pathlib import Path
import multiprocessing
import time
import csv

if __name__ == '__main__':
    multiprocessing.freeze_support()
    file_paths = getFilePaths("pdf", "courts")

    print()
    print("VeraPDF VALIDATION")
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    with multiprocessing.Pool(processes=4) as pool:
        results = pool.starmap(validatePdf, file_paths)
    end_wall = time.perf_counter()
    end_cpu = time.process_time()
    # print(f"Passed: {results.count(True)}, Failed: {results.count(False)}, Errors: {results.count('Error')}")
    print(f"{end_wall - start_wall:.2f} seconds (wall time)")
    print(f"{end_cpu - start_cpu:.2f} seconds (CPU time)")
    print(results)
    # # Write results to CSV
    # with open(outputPath + '/noncompliant_vera_validation_results.csv', mode='w', newline='') as file:
    #     writer = csv.writer(file)
    #     writer.writerows(results)
