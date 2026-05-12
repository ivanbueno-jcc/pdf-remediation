# pylint: disable=duplicate-code
'''
Reset the files in the working directory with the source files.
'''

import argparse
from .utilities.resources import get_pdf_file_paths, get_project_source_path
from .utilities.resources import (
    clear_workspace_folder,
    get_project_workspace_subfolder_path,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)


def main() -> int:
    '''
    Main function to reset the workspace folder from source PDFs.
    '''
    parser = argparse.ArgumentParser(
        description="Reset the files in the working directory with the source files."
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
    args = parser.parse_args()

    if args.project_name:
        print_console_banner("RESET WORKSPACE")
        print_console_key_value_rows([
            ("Project", args.project_name),
            ("Workspace", args.workspace_name),
            ("Folder", args.workspace_folder),
        ])

        source_path = get_project_source_path(args.project_name)
        workspace_folder_path = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder
        )
        clear_workspace_folder(workspace_folder_path)

        workspace_folder_path_processed = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            "processed"
        )
        clear_workspace_folder(workspace_folder_path_processed)

        semaphore = workspace_folder_path / ".remediation.lock"
        source_file_paths = get_pdf_file_paths(source_path)
        if len(source_file_paths) > 0:
            for file_path in source_file_paths:
                relative_path = file_path.relative_to(source_path)
                destination_path = workspace_folder_path / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(file_path.read_bytes())

            # Add a semaphore to only copy over the source once, until reset.
            semaphore.touch(exist_ok=True)

            print_console_section("RESET COMPLETE", "success")
            print_console_key_value_rows([
                ("Files Restored", len(source_file_paths)),
                ("Destination", workspace_folder_path.resolve()),
            ])
        else:
            print_console_section("NO SOURCE FILES", "warn")
            print_console_message("warn", "No PDF files found in the source.")
            return 0

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
