'''PDF Remediation Main Fix Script'''

import argparse
import multiprocessing
from datetime import datetime
from pathlib import Path
import plotext as plot
from parallelbar import progress_starmap
from .utilities.callas import font_fix
from .utilities.pdfix import get_page_count_multiprocess
from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import get_project_workspace_subfolder_file_paths
from .utilities.resources import get_project_workspace_path
from .utilities.resources import get_project_workspace_subfolder_path
from .utilities.resources import get_project_workspace_file_paths, move_file_and_delete_source

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
        default='font-issues',
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
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print()

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

            page_count_lookup = {}
            if len(file_paths_for_counting) > 0:
                page_count_lookup = get_page_count_multiprocess(
                    workspace_folder_path,
                    file_paths_for_counting,
                    timestamp
                )
            else:
                print("No files found for remediation.")

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
            for input_path, output_path in file_paths_for_remediation:
                payload = (input_path, output_path, workspace_path)
                match page_count_lookup[str(input_path)]:
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
                print("FIXING FONT ISSUES...")
                for key, chunk_file_paths in chunks.items():
                    if len(chunk_file_paths) == 0:
                        continue

                    print()
                    print(f"Page count of {key}")

                    if args.verbose:
                        print()
                        print("   Files to process in this chunk:")
                        for input_path, _, _ in chunk_file_paths:
                            relative_chunk_path = Path(input_path).relative_to(workspace_folder_path)
                            print(f"    * {relative_chunk_path}")
                        print()

                    progress_starmap(
                        font_fix,
                        chunk_file_paths,
                        total=len(chunk_file_paths),
                        error_behavior="coerce",
                        process_timeout=600,
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
            for file_path, is_compliant, violations, _ in validation_results:
                if is_compliant is True:
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
            for file_path, is_compliant, violations, _ in validation_results:
                if is_compliant == 'Error':
                    validation_iteration_counter += 1

                    if args.verbose:
                        print(f"{file_path}")
                    move_file_and_delete_source(
                        Path(file_path),
                        output_pdf_folder,
                        args.project_name,
                        args.workspace_name,
                        "unable-to-process"
                    )
                    continue
            print(f"Total error files moved to error folder: {validation_iteration_counter}")

        else:
            print("No PDF files found for validation.")

        print()
        print("SUMMARY")
        summary_file_total = 0
        workspaces = {}
        for subfolder_path in workspace_path.iterdir():
            if subfolder_path.is_dir():
                num_of_pdf_files = len(list(subfolder_path.rglob("*.pdf")))
                summary_file_total += num_of_pdf_files
                workspaces[subfolder_path.name] = num_of_pdf_files

        for folder_name, count in workspaces.items():
            print(f"* {folder_name}: {count} files ({round(100 * (count/summary_file_total))}%)")

if __name__ == '__main__':
    main()
