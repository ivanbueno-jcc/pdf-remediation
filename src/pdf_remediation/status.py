'''
List the status of the project.
'''
import argparse
from .utilities.resources import get_project_source_path, print_workspace_summary

def main():
    '''Main function to list project status.'''

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
        print()
        workspace_main_path = source_path.parent / "workspace"
        for workspace_path in workspace_main_path.iterdir():
            if workspace_path.is_dir():
                workspace_name = workspace_path.name
                print(f"  * {workspace_name}")
                print_workspace_summary(args.project_name, workspace_name)
                print()

if __name__ == '__main__':
    main()
