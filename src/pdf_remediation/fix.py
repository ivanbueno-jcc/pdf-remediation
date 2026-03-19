# pylint: disable=too-many-nested-blocks, duplicate-code
'''PDF Remediation Main Fix Script'''

import argparse
import csv
import multiprocessing
import queue
from datetime import datetime
from pathlib import Path
from parallelbar import progress_starmap, progress_map
from .utilities.pdfix import fix, get_page_count_multiprocess, is_pdf_secured
from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import append_to_csv, get_project_workspace_subfolder_file_paths
from .utilities.resources import print_workspace_summary
from .utilities.resources import get_project_path, get_project_workspace_subfolder_path
from .utilities.resources import (
    get_pdf_file_paths,
    get_page_count_chunks,
    get_project_workspace_file_paths,
    move_file_and_delete_source,
    print_console_banner,
    print_console_key_value_rows,
    print_console_list,
    print_console_message,
    print_console_section,
    route_validation_results
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
        reported_input_pdf_path: str | None,
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
            verbose,
            reported_input_pdf_path=reported_input_pdf_path
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
        reported_input_pdf_path: str | None = None,
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
            reported_input_pdf_path,
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
        _append_fix_worker_error(
            reported_input_pdf_path or input_pdf_path,
            workspace_folder_path,
            timeout_message
        )
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
        _append_fix_worker_error(
            reported_input_pdf_path or input_pdf_path,
            workspace_folder_path,
            unknown_error
        )
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
        if args.debug:
            args.verbose = True
            args.chunk_size = 1

        print_console_banner("FIX")
        print_console_key_value_rows([
            ("Project", args.project_name),
            ("Workspace", args.workspace_name),
            ("Folder", args.workspace_folder),
            ("Config File", args.config_file),
            ("Chunk Size", args.chunk_size),
            ("Verbose", args.verbose),
            ("Debug", args.debug),
        ])

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
                        print_console_message("debug", f"Skipping: {relative_path}", indent=2)
                    continue

                destination_path = output_pdf_folder / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                file_paths_for_remediation.append([str(file_path), str(destination_path)])
                file_paths_for_counting.append(file_path)

            if len(skipped_files) > 0:
                print_console_message("warn", f"Configured skipped files: {len(skipped_files)}")

            processed_files_count = len(get_pdf_file_paths(output_pdf_folder))
            print_console_section("WORKLOAD", "info")
            print_console_key_value_rows([
                ("Already Processed", processed_files_count),
                ("Left To Remediate", len(file_paths_for_remediation)),
            ])

            page_count_lookup = {}
            if len(file_paths_for_counting) > 0:
                page_count_lookup = get_page_count_multiprocess(
                    workspace_folder_path,
                    file_paths_for_counting,
                    timestamp
                )
            else:
                print_console_message("warn", "No files found for remediation.")

            # Filter out secured files that cannot be processed.
            print_console_section("SECURITY SCREENING", "info")
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
                    if not security_status in ["unsecured", "secured-needs-approval"]:
                        relative_path = Path(file_path).relative_to(workspace_folder_path)

                        if security_status in ["secured-cannot-process"]:
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
                            print_console_message(
                                "debug",
                                f"Skipping secured file: {relative_path}",
                                indent=2
                            )
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
            print_console_key_value_rows([
                ("Secured Files Skipped", secured_files_count),
                ("Files Unable To Open", unable_to_open),
            ])

            chunks = get_page_count_chunks(
                file_paths_for_remediation=file_paths_for_remediation,
                page_count_lookup=page_count_lookup,
                payload_builder=lambda input_path, output_path: (
                    input_path,
                    output_path,
                    args.config_file,
                    workspace_folder_path,
                    args.verbose
                ),
                chunk_size=args.chunk_size
            )

            if len(file_paths_for_remediation) > 0:
                print_console_section("REMEDIATING FILES", "info")
                for key, chunk_file_paths in chunks.items():
                    if len(chunk_file_paths) == 0:
                        continue

                    print_console_message("log", f"Page bucket: {key}")

                    if args.verbose:
                        print_console_list(
                            [
                                Path(input_path).relative_to(workspace_folder_path)
                                for input_path, _, _, _, _ in chunk_file_paths
                            ],
                            indent=4
                        )

                    progress_starmap(
                        fix_with_process_timeout,
                        chunk_file_paths,
                        total=len(chunk_file_paths),
                        error_behavior="coerce",
                        executor="threads",
                        n_cpu=4
                    )
        else:
            print_console_section("NO WORK", "warn")
            print_console_message("warn", "No PDF files to process in the active folder.")

        print_console_section("VALIDATING PROCESSED FILES", "info")
        file_paths_for_validation = []
        target_folder = "processed"
        file_paths_for_validation = get_project_workspace_subfolder_file_paths(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            "processed"
        )
        print_console_key_value_rows([("Processed Files Found", len(file_paths_for_validation))])

        if len(file_paths_for_validation) > 0:
            validation_results = validate_pdf_multiprocess(
                output_pdf_folder,
                file_paths_for_validation,
                timestamp,
                target_folder
            )

            font_issue_clauses = None
            font_issue_subfolder = None
            font_issue_summary_message = None
            if args.workspace_folder != "font-issues":
                font_issue_clauses = [
                    '7.21.4.1',
                    '7.21.3.2',
                    '7.21.4.2',
                    '7.21.8',
                    '7.21.7',
                    '7.21.6',
                    '7.21.5',
                    '1.4.8'
                ]
                font_issue_subfolder = "font-issues"
                font_issue_summary_message = "Total files with font issues: {count}"

            route_validation_results(
                validation_results=validation_results,
                output_pdf_folder=output_pdf_folder,
                workspace_folder_path=workspace_folder_path,
                project_name=args.project_name,
                workspace_name=args.workspace_name,
                verbose=args.verbose,
                font_issue_clauses=font_issue_clauses,
                font_issue_subfolder=font_issue_subfolder,
                font_issue_summary_message=font_issue_summary_message,
                font_issues_after_errors=True
            )

        else:
            print_console_message("warn", "No PDF files found for validation.")

        print_console_section("WORKSPACE SUMMARY", "log")
        print_console_key_value_rows([("Workspace", args.workspace_name)])
        print_workspace_summary(args.project_name, args.workspace_name)

if __name__ == '__main__':
    main()
