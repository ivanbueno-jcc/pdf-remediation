'''
Initialize project structure.
'''

import argparse
from .utilities.resources import (
    get_project_source_path,
    get_project_workspace_path,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Initialize project structure."
    )
    parser.add_argument("project_name", help="Project directory name.")
    args = parser.parse_args()

    if args.project_name:
        source_path = get_project_source_path(args.project_name)
        workspace_path = get_project_workspace_path(args.project_name)
        print_console_banner("INIT PROJECT")
        print_console_key_value_rows([
            ("Project", args.project_name),
            ("Source", source_path.resolve()),
            ("Workspace", workspace_path.resolve()),
        ])
        print_console_message("info", f"Copy PDF files to: {source_path.resolve()}")
