'''
Initialize project structure.
'''

import argparse
from .utilities.resources import get_project_source_path, get_project_workspace_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Initialize project structure."
    )
    parser.add_argument("project_name", help="Project directory name.")
    args = parser.parse_args()

    if args.project_name:
        print(f"Initializing {args.project_name} project structure.")

        source_path = get_project_source_path(args.project_name)
        workspace_path = get_project_workspace_path(args.project_name)

        print(f"Copy the pdf files to: {source_path.resolve()}")
