import argparse
import multiprocessing
from datetime import datetime
from pathlib import Path
from parallelbar import progress_starmap
from .utilities.PDFix import fix, get_page_count_multiprocess
from .utilities.VeraPDF import validate_pdf_multiprocess
from .utilities.Resources import get_project_source_path, get_project_workspace_subfolder_file_paths 
from .utilities.Resources import get_project_workspace_subfolder_path
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
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print(f"CONFIG FILE: {args.config_file}")
        print()

        source_path = get_project_source_path(args.project_name)
        workspace_folder_path = get_project_workspace_subfolder_path(args.project_name, args.workspace_name, args.workspace_folder)
        file_paths = get_project_workspace_file_paths(args.project_name, args.workspace_name, args.workspace_folder)
        file_paths_for_remediation = []
        output_pdf_folder = workspace_folder_path.parent / "processed"
        output_pdf_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if len(file_paths):

            page_count_lookup = get_page_count_multiprocess(workspace_folder_path, file_paths, timestamp)

            for file_path in file_paths:
                relative_path = file_path.relative_to(workspace_folder_path)
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
            print()
            print("FILE DISTRIBUTION BY PAGE COUNT:")
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
                if is_compliant:
                    if validation_iteration_counter == 0:
                        print()
                        print("MOVING VALID FILES TO REMEDIATED FOLDER...")
                    validation_iteration_counter += 1
                    
                    print(f"{file_path}")
                    move_file_and_delete_source(Path(file_path), output_pdf_folder, args.project_name, args.workspace_name, "remediated")
                    continue
            
            print()
            print("CHECKING FOR FONT-RELATED VIOLATIONS IN INVALID FILES...")
            files_with_font_issues_total = 0
            for file_path, is_compliant, violations, violation_count in validation_results:
                if not is_compliant:
                    has_font_violation = False
                    for violation in violations:
                        if violation['clause'] in ['7.21.7', '7.21.4.1', '7.21.3.2', '7.21.4.2', '7.21.5', '7.21.8']:
                            has_font_violation = True
                            break

                    if has_font_violation:
                        files_with_font_issues_total += 1
                        print(f"{file_path}")
                        move_file_and_delete_source(Path(file_path), output_pdf_folder, args.project_name, args.workspace_name, "font-issues")

            print(f"Total files with font issues: {files_with_font_issues_total}")
                    
        else:
            print("No PDF files found for validation.")

if __name__ == '__main__':
    main()