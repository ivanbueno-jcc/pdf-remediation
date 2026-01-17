import argparse
import sys
from .utilities.Resources import get_project_source_path, get_project_workspace_path

def main():
    parser = argparse.ArgumentParser(
        description="List the status of the project."
    )
    parser.add_argument("project_name", help="Project directory name.")
    args = parser.parse_args()

    if args.project_name:

        print(f"PROJECT: {args.project_name}")
        source_path = get_project_source_path(args.project_name)
        file_paths = list(source_path.rglob("*.pdf"))

        print()
        if len(file_paths) > 0:
            print(f"TOTAL PDF FILES: {len(file_paths)}")
        else:
            print("No PDF files found in the source.")
            print(f"Copy PDF files to the source path: {source_path.resolve()}")

        # Count the number of workspaces
        print()
        print("WORKSPACES")
        workspace_main_path = source_path.parent / "workspace"
        workspaces = {}
        for workspace_path in workspace_main_path.iterdir():
            if workspace_path.is_dir():
                workspace_name = workspace_path.name
                workspaces[workspace_name] = {}
                for subfolder_path in workspace_path.iterdir():
                    if subfolder_path.is_dir():
                        pdf_files = list(subfolder_path.rglob("*.pdf"))
                        workspaces[workspace_name][subfolder_path.name] = len(pdf_files)

        for workspace_name, folders in workspaces.items():
            print()
            print(f"  {workspace_name} ({round(100 * (folders['remediated'] / sum(folders.values())))}%):")
            for folder_name, count in folders.items():
                print(f"    {folder_name}: {count}")

if __name__ == '__main__':
    main()
