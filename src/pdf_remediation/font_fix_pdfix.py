# pylint: disable=too-many-nested-blocks, duplicate-code
'''Missing Unicode Utility using PDFix'''

import argparse
import multiprocessing
from datetime import datetime
from pathlib import Path
from parallelbar import progress_starmap
from .utilities.pdfix import font_fix_pdfix, get_page_count_multiprocess, pull_image
from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import PDFIX_FONT_IMAGE, get_project_workspace_subfolder_file_paths
from .utilities.resources import get_project_workspace_path, print_workspace_summary
from .utilities.resources import get_project_workspace_subfolder_path
from .utilities.resources import (
    get_pdf_file_paths,
    get_page_count_chunks,
    get_project_workspace_file_paths,
    print_console_banner,
    print_console_key_value_rows,
    print_console_list,
    print_console_message,
    print_console_section,
    route_validation_results
)

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
        default='font-issues-missing-unicode',
        help="Workspace subfolder (default: %(default)s)"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Chunk Size (default: %(default)s)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action='store_true',
        help="Enable verbose output."
    )
    parser.add_argument(
        "--n-cpu",
        type=int,
        default=multiprocessing.cpu_count(),
        help="Number of CPU cores to use (default: all available cores)."
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

        print_console_banner("FONT FIX PDFIX")
        print_console_key_value_rows([
            ("Project", args.project_name),
            ("Workspace", args.workspace_name),
            ("Folder", args.workspace_folder),
            ("Chunk Size", args.chunk_size),
            ("Workers", args.n_cpu),
            ("Verbose", args.verbose),
            ("Debug", args.debug),
        ])

        workspace_path = get_project_workspace_path(
            args.project_name,
            args.workspace_name
        )
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

        if len(file_paths) > 0:

            for file_path in file_paths:
                relative_path = file_path.relative_to(workspace_folder_path)

                destination_path = output_pdf_folder / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                file_paths_for_remediation.append([file_path, destination_path])
                file_paths_for_counting.append(file_path)

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

            chunks = get_page_count_chunks(
                file_paths_for_remediation=file_paths_for_remediation,
                page_count_lookup=page_count_lookup,
                payload_builder=lambda input_path, output_path: (
                    input_path,
                    output_path,
                    workspace_path
                ),
                chunk_size=args.chunk_size
            )

            if len(file_paths_for_remediation) > 0:
                pull_image(PDFIX_FONT_IMAGE, verbose=args.verbose)

                print_console_section("FIXING MISSING UNICODE FONT WITH PDFIX", "info")
                for key, chunk_file_paths in chunks.items():
                    if len(chunk_file_paths) == 0:
                        continue

                    # Rewrite key for better readability in the console.
                    # For example, from "2-5 - (3/6)" to "2-5 pages (3/6)"
                    key_parts = key.split(" - ")
                    if len(key_parts) == 2:
                        page_range = key_parts[0].replace("pages", "").strip()
                        chunk_index = key_parts[1].strip()
                        key = f"{page_range} pages {chunk_index}"

                    print_console_message("log", f"{key}")

                    if args.verbose:
                        print_console_list(
                            [
                                Path(input_path).relative_to(workspace_folder_path)
                                for input_path, _, _ in chunk_file_paths
                            ],
                            indent=4
                        )

                    progress_starmap(
                        font_fix_pdfix,
                        chunk_file_paths,
                        total=len(chunk_file_paths),
                        error_behavior="coerce",
                        process_timeout=600,
                        n_cpu=args.n_cpu
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

            route_validation_results(
                validation_results=validation_results,
                output_pdf_folder=output_pdf_folder,
                workspace_folder_path=workspace_folder_path,
                project_name=args.project_name,
                workspace_name=args.workspace_name,
                verbose=args.verbose
            )

        else:
            print_console_message("warn", "No PDF files found for validation.")

        print_console_section("WORKSPACE SUMMARY", "log")
        print_console_key_value_rows([("Workspace", args.workspace_name)])
        print_workspace_summary(args.project_name, args.workspace_name)

if __name__ == '__main__':
    main()
