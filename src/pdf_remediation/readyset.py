# pylint: disable=duplicate-code
'''
Run go.py sequentially for multiple project names.
'''

import argparse
import subprocess
import sys


def print_processing_banner(project_name: str, position: int, total: int) -> None:
    '''
    Print a large banner to highlight the project currently being processed.
    '''
    title = f"PROJECT {position}/{total}"
    argument_line = f"{project_name}"
    width = max(120, len(title) + 8, len(argument_line) + 8)
    border = "#" * width

    print()
    print(border)
    print(border)
    print(f"## {title.center(width - 6)} ##")
    print(f"## {argument_line.center(width - 6)} ##")
    print(f"## {' '.center(width - 6)} ##")
    print(border)
    print(border)
    print()


def run_go(project_name: str) -> int:
    '''
    Run the go module for a single project and return its exit code.
    '''
    command = [sys.executable, "-m", "pdf_remediation.go", project_name]
    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def main() -> int:
    '''
    Execute go.py sequentially for each provided project name.
    '''
    parser = argparse.ArgumentParser(
        description="Run go.py sequentially for each provided project name."
    )
    parser.add_argument(
        "project_names",
        nargs="+",
        help="One or more project names to run through go.py in order."
    )
    args = parser.parse_args()

    total_projects = len(args.project_names)
    for index, project_name in enumerate(args.project_names, start=1):
        print_processing_banner(project_name, index, total_projects)
        rc = run_go(project_name)
        if rc != 0:
            print()
            print(
                f"readyset stopped: go.py failed for '{project_name}' "
                f"with exit code {rc}."
            )
            return rc

    print()
    print("readyset completed successfully.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
