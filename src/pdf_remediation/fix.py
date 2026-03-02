# pylint: disable=too-many-nested-blocks, duplicate-code
'''PDF Remediation Main Fix Script'''

import argparse
import csv
import multiprocessing
import queue
from datetime import datetime
from pathlib import Path
import plotext as plot
from parallelbar import progress_starmap, progress_map
from .utilities.pdfix import fix, get_page_count_multiprocess, is_pdf_secured
from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import append_to_csv, get_project_workspace_subfolder_file_paths
from .utilities.resources import print_workspace_summary
from .utilities.resources import get_project_path, get_project_workspace_subfolder_path
from .utilities.resources import (
    get_pdf_file_paths,
    get_project_workspace_file_paths,
    move_file_and_delete_source
)
from .utilities.resources import FIX_PROCESS_TIMEOUT_SECONDS

def _append_fix_worker_error(
        input_pdf_path: str,
        workspace_folder_path: Path,
        error_message: str) -> None:
    '''Persist worker failures to the same CSV used by fix().'''

    if workspace_folder_path is None:
        return

    try:
        relative_path = Path(input_pdf_path).relative_to(workspace_folder_path)
    except ValueError:
        relative_path = Path(input_pdf_path)

    error_file_path = workspace_folder_path.parent.parent.parent.parent \
        / "pdfix-cannot-process-files.csv"
    append_to_csv(error_file_path, [relative_path, error_message])

def _fix_process_target( # pylint: disable=too-many-arguments,too-many-positional-arguments
        input_pdf_path: str,
        output_pdf_path: str,
        config_file: str,
        workspace_folder_path: Path,
        verbose: bool,
        result_queue: multiprocessing.Queue) -> None:
    '''
    Execute fix in a dedicated child process and return exception details through a queue.
    '''
    try:
        fix(
            input_pdf_path,
            output_pdf_path,
            config_file,
            workspace_folder_path,
            verbose
        )
        result_queue.put(None)
    except BaseException as exc: # pylint: disable=broad-exception-caught
        result_queue.put(f"{type(exc).__name__}: {exc}")
        # Exit cleanly to avoid child-process traceback noise in the console.
        raise SystemExit(1) from exc

def fix_with_process_timeout( # pylint: disable=too-many-arguments,too-many-positional-arguments
        input_pdf_path: str,
        output_pdf_path: str,
        config_file: str = "default.json",
        workspace_folder_path: Path = None,
        verbose: bool = False,
        process_timeout: int = FIX_PROCESS_TIMEOUT_SECONDS) -> None:
    '''
    Run PDFix fix in an isolated process so hung native calls can be force-terminated.
    '''

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_fix_process_target,
        args=(
            input_pdf_path,
            output_pdf_path,
            config_file,
            workspace_folder_path,
            verbose,
            result_queue
        )
    )
    process.start()
    process.join(process_timeout)

    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()

        timeout_message = f"TimeoutError: function took longer than {process_timeout} s."
        _append_fix_worker_error(input_pdf_path, workspace_folder_path, timeout_message)
        raise TimeoutError(timeout_message)

    process_error = None
    try:
        process_error = result_queue.get_nowait()
    except queue.Empty:
        pass
    finally:
        result_queue.close()
        result_queue.join_thread()

    if process.exitcode != 0:
        if process_error:
            raise RuntimeError(process_error)

        unknown_error = f"Fix worker exited with code {process.exitcode}"
        _append_fix_worker_error(input_pdf_path, workspace_folder_path, unknown_error)
        raise RuntimeError(unknown_error)

def get_skipped_files_list(project_name: str) -> list[str]:
    '''
    Get a list of files to skip during processing.
    '''

    # open the skipped files list from a text file
    skipped_files = []
    skipped_files_path = get_project_path(project_name) / "skipped_files.txt"
    if skipped_files_path.exists():
        with open(skipped_files_path, 'r', encoding='utf-8') as f:
            for line in f:
                skipped_file = line.strip()
                if skipped_file and skipped_file not in skipped_files:
                    skipped_files.append(skipped_file)

    # Open the pdfix-cannot-process list from a csv file.
    # Use the first column as the relative file path to skip.
    pdfix_cannot_process_files = []
    pdfix_cannot_process_files_path = \
        get_project_path(project_name) / "pdfix-cannot-process-files.csv"
    if pdfix_cannot_process_files_path.exists():
        with open(pdfix_cannot_process_files_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if len(row) > 0:
                    relative_file_path = row[0].strip()
                    if relative_file_path and relative_file_path not in pdfix_cannot_process_files:
                        pdfix_cannot_process_files.append(relative_file_path)
    skipped_files.extend(pdfix_cannot_process_files)

    return skipped_files

def main(): # pylint: disable=too-many-locals, too-many-statements, too-many-branches
    '''Main function to remediate PDF files in a project workspace.'''

    multiprocessing.freeze_support()
    multiprocessing.set_start_method("spawn", force=True)

    parser = argparse.ArgumentParser(
        description="Remediate PDF files in a project workspace."
    )
    parser.add_argument("project_name", help="Project directory name.")
    parser.add_argument(
        "workspace_name",
        type=str,
        nargs='?',
        default='default',
        help="Workspace name (default: %(default)s)"
    )
    parser.add_argument(
        "workspace_folder",
        type=str,
        nargs='?',
        default='active',
        help="Workspace subfolder (default: %(default)s)"
    )
    parser.add_argument(
        "--config-file",
        "--c",
        type=str,
        default='default.json',
        help="Configuration file name (default: %(default)s)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action='store_true',
        help="Enable verbose output."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Chunk Size (default: %(default)s)"
    )
    parser.add_argument(
        "--n-cpu",
        type=int,
        default=4,
        help="Number of CPUs (default: %(default)s)"
    )
    parser.add_argument(
        "--debug",
        "-d",
        action='store_true',
        help="Enable debug output."
    )
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print(f"CONFIG FILE: {args.config_file}")
        print()

        if args.debug:
            args.verbose = True
            args.chunk_size = 1

        # workspace_path = get_project_workspace_path(
        #     args.project_name,
        #     args.workspace_name
        # )
        workspace_folder_path = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder
        )
        file_paths = get_project_workspace_file_paths(
            args.project_name,
            args.workspace_name,
            args.workspace_folder
        )
        file_paths_for_remediation = []
        file_paths_for_counting = []
        output_pdf_folder = workspace_folder_path.parent / "processed"
        output_pdf_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        skipped_files = get_skipped_files_list(args.project_name)

        if len(file_paths) > 0:

            for file_path in file_paths:
                relative_path = file_path.relative_to(workspace_folder_path)

                if str(relative_path) in skipped_files:
                    if args.verbose:
                        print(f" Skipping: {relative_path}")
                    continue

                destination_path = output_pdf_folder / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                file_paths_for_remediation.append([str(file_path), str(destination_path)])
                file_paths_for_counting.append(file_path)

            if len(skipped_files) > 0:
                print(f"Total skipped files: {len(skipped_files)}")

            processed_files_count = len(get_pdf_file_paths(output_pdf_folder))
            print(f"Total files processed: {processed_files_count}")
            print(f"Total files left to remediate: {len(file_paths_for_remediation)}")
            print()

            page_count_lookup = {}
            if len(file_paths_for_counting) > 0:
                page_count_lookup = get_page_count_multiprocess(
                    workspace_folder_path,
                    file_paths_for_counting,
                    timestamp
                )
            else:
                print("No files found for remediation.")

            # Filter out secured files that cannot be processed.
            print()
            print("Checking for secured files...")
            security_check_results = []
            security_input_files = [str(file_path) for file_path in file_paths_for_counting]
            security_check_results = progress_map(
                is_pdf_secured,
                security_input_files,
                total=len(security_input_files),
                n_cpu=4
            )

            secured_files_count = unable_to_open = 0
            for d in security_check_results:
                for file_path, security_status in d.items():
                    if not security_status in ["unsecured"]:
                        relative_path = Path(file_path).relative_to(workspace_folder_path)

                        if security_status in ["secured-cannot-process", "secured-needs-approval"]:
                            secured_files_count += 1
                            append_to_csv(
                                workspace_folder_path.parent.parent.parent.parent / "secured-files.csv", # pylint: disable=line-too-long
                                [relative_path, security_status]
                            )
                        elif security_status in ["pdfix-unable-to-open"]:
                            unable_to_open += 1
                            append_to_csv(
                                workspace_folder_path.parent.parent.parent.parent / "pdfix-unable-to-open.csv", # pylint: disable=line-too-long
                                [relative_path, security_status]
                            )

                        if args.verbose:
                            print(f" Skipping secured file: {relative_path}")
                        # remove from remediation list
                        file_paths_for_remediation = [
                            item for item in file_paths_for_remediation
                            if item[0] != file_path
                        ]

                        move_file_and_delete_source(
                            Path(file_path),
                            workspace_folder_path,
                            args.project_name,
                            args.workspace_name,
                            security_status
                        )
            print(f"Total secured files skipped: {secured_files_count}")
            print(f"Total files unable to open: {unable_to_open}")
            print()

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
            for input_path, input_workspace_path in file_paths_for_remediation:
                payload = (
                    input_path,
                    input_workspace_path,
                    args.config_file,
                    workspace_folder_path,
                    args.verbose
                )
                match page_count_lookup[input_path]:
                    case 1:
                        chunks['1'].append(payload)
                    case x if 1 < x <= 5:
                        chunks['2-5'].append(payload)
                    case x if 5 < x <= 10:
                        chunks['6-10'].append(payload)
                    case x if 10 < x <= 50:
                        chunks['11-50'].append(payload)
                    case x if 50 < x <= 100:
                        chunks['51-100'].append(payload)
                    case x if 100 < x <= 200:
                        chunks['101-200'].append(payload)
                    case x if 200 < x <= 500:
                        chunks['201-500'].append(payload)
                    case x if 500 < x <= 1000:
                        chunks['501-1000'].append(payload)
                    case x if 1000 < x <= 3000:
                        chunks['1001-3000'].append(payload)
                    case x if x > 3000:
                        chunks['3001 or more'].append(payload)

            if len(file_paths_for_remediation) > 0:
                print()
                page_count_file_num = []
                page_count_bucket = []
                for key, value in chunks.items():
                    page_count_bucket.append(key)
                    page_count_file_num.append(len(value))

                min_y = min(page_count_file_num)
                max_y = max(page_count_file_num)
                y_ticks = list(range(min_y, max_y + 1, 5)) # ticks every 5 units
                plot.yticks(y_ticks)

                plot.bar(page_count_bucket, page_count_file_num)
                plot.title("File Distribution by Page Count")
                plot.xlabel("Range")
                plot.ylabel("# of Files")
                plot.plotsize(50, 15)
                plot.show()

            # if value is large, split into sub-chunks.
            sub_chunks = {}
            del_chunks = []
            chunk_size = args.chunk_size
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

            if len(file_paths_for_remediation) > 0:
                print()
                print("REMEDIATING FILES...")
                for key, chunk_file_paths in chunks.items():
                    if len(chunk_file_paths) == 0:
                        continue

                    print()
                    print(f"Page count of {key}")

                    if args.verbose:
                        print()
                        print("   Files to process in this chunk:")
                        for input_path, input_workspace_path, _, _, _ in chunk_file_paths:
                            relative_chunk_path = Path(input_path).relative_to(
                                workspace_folder_path)
                            print(f"    * {relative_chunk_path}")
                        print()

                    progress_starmap(
                        fix_with_process_timeout,
                        chunk_file_paths,
                        total=len(chunk_file_paths),
                        error_behavior="coerce",
                        executor="threads",
                        n_cpu=4
                    )
        else:
            print("No PDF files to process in the active folder.")

        print()
        print("VALIDATING PROCESSED FILES...")
        file_paths_for_validation = []
        target_folder = "processed"
        file_paths_for_validation = get_project_workspace_subfolder_file_paths(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            "processed"
        )

        if len(file_paths_for_validation) > 0:
            validation_results = validate_pdf_multiprocess(
                output_pdf_folder,
                file_paths_for_validation,
                timestamp,
                target_folder
            )

            print()
            print("MOVING FILES BASED ON VALIDATION RESULTS...")
            # Loop through the validation results.
            # Move files that passed to a "remediated" folder in the same workspace.
            validation_iteration_counter = 0
            for file_path, ua1_result, _, wcag_result, _, _, _ in validation_results:
                if ua1_result is True and wcag_result is True:
                    validation_iteration_counter += 1

                    if args.verbose:
                        print(f"{file_path}")

                    move_file_and_delete_source(
                        Path(file_path),
                        output_pdf_folder,
                        args.project_name,
                        args.workspace_name,
                        "remediated"
                    )

                    continue

            print(f"Total valid files moved to remediated folder: {validation_iteration_counter}")

            validation_iteration_counter = 0
            for file_path, ua1_result, _, wcag_result, _, _, _ in validation_results:
                if ua1_result == 'Error' or wcag_result == 'Error':
                    validation_iteration_counter += 1
                    append_to_csv(
                        workspace_folder_path.parent.parent.parent.parent / "unable-to-validate.csv", # pylint: disable=line-too-long
                        [relative_path, ua1_result, wcag_result]
                    )

                    if args.verbose:
                        print(f"{file_path}")
                    move_file_and_delete_source(
                        Path(file_path),
                        output_pdf_folder,
                        args.project_name,
                        args.workspace_name,
                        "unable-to-validate"
                    )
                    continue
            print(f"Total error files moved to error folder: {validation_iteration_counter}")

            if args.workspace_folder != "font-issues":
                font_issue_clauses = [
                    '7.21.4.1',
                    '7.21.3.2',
                    '7.21.4.2',
                    '7.21.8',
                    '7.21.7',
                    '7.21.6',
                    '7.21.5'
                ]
                files_with_font_issues_total = 0
                for file_path, ua1_result, _, wcag_result, _, ua1_violations, \
                    wcag_violations in validation_results:

                    if ua1_result is False or wcag_result is False:
                        has_font_violation = False
                        for violation in ua1_violations + wcag_violations:
                            if violation['clause'] in font_issue_clauses:
                                has_font_violation = True
                                break

                        if has_font_violation:
                            files_with_font_issues_total += 1

                            if args.verbose:
                                print(f"{file_path}")

                            move_file_and_delete_source(
                                Path(file_path),
                                output_pdf_folder,
                                args.project_name,
                                args.workspace_name,
                                "font-issues"
                            )

                print(f"Total files with font issues: {files_with_font_issues_total}")

        else:
            print("No PDF files found for validation.")

        print()
        print("WORKSPACE SUMMARY")
        print(f"  {args.workspace_name}")
        print_workspace_summary(args.project_name, args.workspace_name)

if __name__ == '__main__':
    main()
