import argparse
import multiprocessing
import plotext as plot
from datetime import datetime
from pathlib import Path
from parallelbar import progress_starmap
from .utilities.PDFix import fix, get_page_count_multiprocess
from .utilities.VeraPDF import validate_pdf_multiprocess
from .utilities.Resources import get_project_source_path, get_project_workspace_subfolder_file_paths 
from .utilities.Resources import get_project_path, get_project_workspace_subfolder_path
from .utilities.Resources import get_project_workspace_file_paths, move_file_and_delete_source

def main():
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
        "--config_file",
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
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print(f"CONFIG FILE: {args.config_file}")
        print()

        workspace_folder_path = get_project_workspace_subfolder_path(args.project_name, args.workspace_name, args.workspace_folder)
        file_paths = get_project_workspace_file_paths(args.project_name, args.workspace_name, args.workspace_folder)
        file_paths_for_remediation = []
        output_pdf_folder = workspace_folder_path.parent / "processed"
        output_pdf_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # open the skipped files list from a text file
        skipped_files = []
        skipped_files_path = get_project_path(args.project_name) / "skipped_files.txt"

        if skipped_files_path.exists():
            with open(skipped_files_path, 'r') as f:
                for line in f:
                    skipped_file = line.strip()
                    if skipped_file and skipped_file not in skipped_files:
                        skipped_files.append(skipped_file)
        
        if len(file_paths):

            page_count_lookup = get_page_count_multiprocess(workspace_folder_path, file_paths, timestamp)

            if args.verbose:
                if len(skipped_files) > 0:
                    print()

            for file_path in file_paths:
                relative_path = file_path.relative_to(workspace_folder_path)

                if str(relative_path) in skipped_files:
                    if args.verbose:
                        print(f" Skipping: {relative_path}")
                    continue

                destination_path = output_pdf_folder / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                file_paths_for_remediation.append([str(file_path), str(destination_path)])

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
            for input, workspace_path in file_paths_for_remediation:
                match page_count_lookup[input]:
                    case 1:
                        chunks['1'].append((input, workspace_path, args.config_file))
                    case x if 1 < x <= 5:
                        chunks['2-5'].append((input, workspace_path, args.config_file))
                    case x if 5 < x <= 10:
                        chunks['6-10'].append((input, workspace_path, args.config_file))
                    case x if 10 < x <= 50:
                        chunks['11-50'].append((input, workspace_path, args.config_file))
                    case x if 50 < x <= 100:
                        chunks['51-100'].append((input, workspace_path, args.config_file)) 
                    case x if 100 < x <= 200:
                        chunks['101-200'].append((input, workspace_path, args.config_file))
                    case x if 200 < x <= 500:
                        chunks['201-500'].append((input, workspace_path, args.config_file))
                    case x if 500 < x <= 1000:
                        chunks['501-1000'].append((input, workspace_path, args.config_file))
                    case x if 1000 < x <= 3000:
                        chunks['1001-3000'].append((input, workspace_path, args.config_file))
                    case x if x > 3000:
                        chunks['3001 or more'].append((input, workspace_path, args.config_file))
                     
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
            print("REMEDIATING FILES...")
            for key, chunk_file_paths in chunks.items():
                if len(chunk_file_paths) == 0:
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

                print()
                print(f"Page count of {key}")

                if args.verbose:
                    print()
                    print("   Files to process in this chunk:")
                    for input, workspace_path, config_file in chunk_file_paths:
                        relative_chunk_path = Path(input).relative_to(workspace_folder_path)
                        print(f"    * {relative_chunk_path}")
                    print()

                results = progress_starmap(
                    fix,
                    chunk_file_paths,
                    total=len(chunk_file_paths),
                    error_behavior="coerce",
                    process_timeout=600,
                    n_cpu=4
                )
        else:
            print("No PDF files to process in the active folder.")
   
        print()
        file_paths_for_validation = []
        target_folder = "files"
        if len(file_paths_for_remediation) > 0:
            print("VALIDATING REMEDIATED FILES...")
            for input, output in file_paths_for_remediation:
                file_paths_for_validation.append(output)
        else:
            print("VALIDATING PROCESSED FILES...")
            target_folder = "processed"
            file_paths_for_validation = get_project_workspace_subfolder_file_paths(args.project_name, args.workspace_name, args.workspace_folder, "processed")

        if len(file_paths_for_validation) > 0:
            validation_results = validate_pdf_multiprocess(output_pdf_folder, file_paths_for_validation, timestamp, target_folder)

            # Loop through the validation results.  Move files that passed to a "remediated" folder in the same workspace.
            validation_iteration_counter = 0
            for file_path, is_compliant, violations, violation_count in validation_results:
                if is_compliant == True:
                    if validation_iteration_counter == 0:
                        print()
                        print("MOVING VALID FILES TO REMEDIATED FOLDER...")
                    validation_iteration_counter += 1
                    
                    if args.verbose:
                        print(f"{file_path}")
                    move_file_and_delete_source(Path(file_path), output_pdf_folder, args.project_name, args.workspace_name, "remediated")
                    continue
            print(f"Total valid files moved to remediated folder: {validation_iteration_counter}")

            validation_iteration_counter = 0
            for file_path, is_compliant, violations, violation_count in validation_results:
                if is_compliant == 'Error':
                    if validation_iteration_counter == 0:
                        print()
                        print("MOVING ERROR FILES TO ERROR FOLDER...")
                    validation_iteration_counter += 1
                    
                    if args.verbose:
                        print(f"{file_path}")
                    move_file_and_delete_source(Path(file_path), output_pdf_folder, args.project_name, args.workspace_name, "unable-to-process")
                    continue
            print(f"Total error files moved to error folder: {validation_iteration_counter}")

            if args.workspace_folder != "font-issues":
                print()
                print("CHECKING FOR FONT-RELATED VIOLATIONS IN INVALID FILES...")
                files_with_font_issues_total = 0
                for file_path, is_compliant, violations, violation_count in validation_results:
                    if is_compliant == False:
                        has_font_violation = False
                        for violation in violations:
                            if violation['clause'] in ['7.21.7', '7.21.4.1', '7.21.3.2', '7.21.4.2', '7.21.5', '7.21.8']:
                                has_font_violation = True
                                break

                        if has_font_violation:
                            files_with_font_issues_total += 1
                            if args.verbose:
                                print(f"{file_path}")
                            move_file_and_delete_source(Path(file_path), output_pdf_folder, args.project_name, args.workspace_name, "font-issues")

                print(f"Total files with font issues: {files_with_font_issues_total}")
                    
        else:
            print("No PDF files found for validation.")

if __name__ == '__main__':
    main()