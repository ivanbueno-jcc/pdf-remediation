# pylint: disable=duplicate-code
'''
Move processed PDF files back to the active workspace folder.
'''

import argparse
from pathlib import Path
from .utilities.resources import get_pdf_file_paths, get_project_workspace_path
from .utilities.resources import (
    move_file_and_delete_source,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)

IGNORED_WORKSPACE_FOLDERS = {
    "remediated",
    "pdfix-cannot-process",
    "secured-cannot-process",
    "reports",
    "pdfix-unable-to-open",
    "unable-to-validate",
    "unable-to-process",
    "debug"
}


def main() -> int:
    '''
    Main function to move processed PDFs back to the active workspace folder.
    '''
    parser = argparse.ArgumentParser(
        description="Move processed files from workspace folders back to active/files."
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
        default='all',
        help="Workspace subfolder to scan (default: %(default)s)"
    )
    args = parser.parse_args()

    if args.project_name:
        print_console_banner("REPROCESS")
        print_console_key_value_rows([
            ("Project", args.project_name),
            ("Workspace", args.workspace_name),
            ("Folder", args.workspace_folder),
        ])

        workspace_path = get_project_workspace_path(
            args.project_name,
            args.workspace_name
        )

        workspace_folders = []
        if args.workspace_folder == "all":
            workspace_folders = sorted([
                subfolder_path.name
                for subfolder_path in workspace_path.iterdir()
                if (
                    subfolder_path.is_dir()
                    and subfolder_path.name not in IGNORED_WORKSPACE_FOLDERS
                )
            ])
        else:
            workspace_folders = [args.workspace_folder]

        workspace_folders = [
            workspace_folder
            for workspace_folder in workspace_folders
            if workspace_folder not in IGNORED_WORKSPACE_FOLDERS
        ]

        total_files_moved = 0
        source_directories = ["processed", "files"]
        for workspace_folder in workspace_folders:
            for source_directory in source_directories:
                if workspace_folder == "active" and source_directory == "files":
                    continue

                source_folder_path = workspace_path / workspace_folder / source_directory
                if not source_folder_path.exists():
                    continue

                source_file_paths = get_pdf_file_paths(source_folder_path)
                if len(source_file_paths) == 0:
                    continue

                for file_path in source_file_paths:
                    move_file_and_delete_source(
                        Path(file_path),
                        source_folder_path,
                        args.project_name,
                        args.workspace_name,
                        "active"
                    )

                total_files_moved += len(source_file_paths)
                print_console_message(
                    "success",
                    f"Moved {len(source_file_paths)} files from "
                    f"{workspace_folder}/{source_directory} to active/files."
                )

        if total_files_moved > 0:
            print_console_section("REPROCESS SUMMARY", "success")
            print_console_key_value_rows([
                ("Total Files Moved", total_files_moved),
            ])
        else:
            print_console_section("NO WORK", "warn")
            print_console_message("warn", "No PDF files found in processed or files folders.")
            return 0

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
