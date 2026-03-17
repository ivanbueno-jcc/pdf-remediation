'''
List the status of the project.
'''
import argparse
from .utilities.resources import (
    get_pdf_file_paths,
    get_project_source_path,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
    print_workspace_summary
)

def main():
    '''Main function to list project status.'''

    parser = argparse.ArgumentParser(
        description="List the status of the project."
    )
    parser.add_argument("project_name", help="Project directory name.")
    args = parser.parse_args()

    if args.project_name:
        source_path = get_project_source_path(args.project_name)
        file_paths = get_pdf_file_paths(source_path)

        print_console_banner("STATUS")
        print_console_key_value_rows([
            ("Project", args.project_name),
            ("Source", source_path.resolve()),
            ("Total Source PDFs", len(file_paths)),
        ])

        if len(file_paths) > 0:
            print_console_message("success", f"Source files found: {len(file_paths)}")
        else:
            print_console_section("NO SOURCE FILES", "warn")
            print_console_message("warn", "No PDF files found in the source.")
            print_console_message("info", f"Copy PDF files to: {source_path.resolve()}", indent=2)

        # Count the number of workspaces
        print_console_section("WORKSPACES", "info")
        workspace_main_path = source_path.parent / "workspace"
        for workspace_path in workspace_main_path.iterdir():
            if workspace_path.is_dir():
                total_pdfs = 0
                for subfolder_path in workspace_path.iterdir():
                    if subfolder_path.is_dir() and subfolder_path.name != "reports":
                        total_pdfs += len(get_pdf_file_paths(subfolder_path))

                print_console_message(
                    "",
                    f"{workspace_path.name} ({total_pdfs} PDFs)",
                    indent=2
                )
                print_workspace_summary(
                    args.project_name,
                    workspace_path.name,
                    ignored_subfolders=["reports"]
                )

if __name__ == '__main__':
    main()
