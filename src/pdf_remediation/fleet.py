# pylint: disable=duplicate-code,too-many-branches,too-many-statements
'''
Run project-level modules sequentially across multiple projects.
'''

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from .utilities.resources import (
    PROJECT_BASE_PATH,
    parse_cli_filters,
    print_console_banner,
    print_console_command,
    print_console_key_value_rows,
    print_console_list,
    print_console_message,
    print_console_section,
)

DEBUG_FILES_BASE_PATH = Path('resources/debug/_files')


def print_processing_banner(project_name: str, position: int, total: int) -> None:
    '''
    Print a large banner to highlight the project currently being processed.
    '''
    print_console_banner(f"PROJECT {position}/{total}: {project_name}", "info")


def _collect_project_names(
        selected_project_names: list[str],
        excluded_project_names: set[str] | None = None,
        allow_missing: bool = False) -> tuple[list[str], list[str]]:
    '''
    Build the project name list from selected names or all project directories.
    '''
    excluded_project_names = excluded_project_names or set()

    if selected_project_names:
        if allow_missing:
            ordered_unique: list[str] = []
            seen: set[str] = set()
            for project_name in selected_project_names:
                if project_name in excluded_project_names:
                    continue
                if project_name in seen:
                    continue
                seen.add(project_name)
                ordered_unique.append(project_name)
            return ordered_unique, []

        existing: list[str] = []
        missing: list[str] = []
        for project_name in selected_project_names:
            if project_name in excluded_project_names:
                continue
            project_path = Path(PROJECT_BASE_PATH) / project_name
            if project_path.is_dir():
                existing.append(project_name)
            else:
                missing.append(project_name)
        return existing, missing

    project_names = sorted([
        project_path.name
        for project_path in Path(PROJECT_BASE_PATH).iterdir()
        if project_path.is_dir() and project_path.name not in excluded_project_names
    ])
    return project_names, []

# pylint: disable=too-many-return-statements
def _build_module_args(action: str, args: argparse.Namespace, project_name: str) -> list[str]:
    '''
    Build module args per action.
    '''
    if action == 'debug':
        module_args = [project_name, args.workspace_name]
        if args.clause_tests:
            module_args.append('--clause-tests')
            module_args.extend(args.clause_tests)
        return module_args

    if action == 'go':
        module_args = [
            project_name,
            args.workspace_name,
            '--config-file',
            args.config_file,
            '--chunk-size',
            str(args.chunk_size)
        ]
        if args.n_cpu is not None:
            module_args.extend(['--n-cpu', str(args.n_cpu)])
        if args.pre_validate:
            module_args.append('--pre-validate')
        if args.skip_font_fix:
            module_args.append('--skip-font-fix')
        if args.wcag_and_ua1_must_pass:
            module_args.append('--wcag-and-ua1-must-pass')
        if args.verbose:
            module_args.append('--verbose')
        if args.debug:
            module_args.append('--debug')
        return module_args

    if action == 'fix_target':
        module_args = [
            project_name,
            args.workspace_name,
            args.workspace_folder,
            '--targets',
            *args.targets,
            '--n-cpu',
            str(args.n_cpu)
        ]
        if args.verbose:
            module_args.append('--verbose')
        if args.debug:
            module_args.append('--debug')
        return module_args

    if action == 'get_latest_files':
        return [project_name, args.workspace_name]

    if action == 'reprocess':
        return [project_name, args.workspace_name, args.workspace_folder]

    if action == 'validate':
        module_args = [
            project_name,
            args.workspace_name,
            args.workspace_folder,
            args.directory
        ]
        if args.full:
            module_args.append('--full')
        if args.skip_page_count:
            module_args.append('--skip-page-count')
        return module_args

    return [project_name]


def _run_action(action: str, args: argparse.Namespace, project_name: str) -> int:
    '''
    Run one action module for a single project.
    '''
    module = f'pdf_remediation.{action}'
    command = [
        sys.executable,
        '-m',
        module,
        *_build_module_args(action, args, project_name)
    ]
    print_console_command(command)
    result = subprocess.run(command, check=False)
    return result.returncode


def _remove_existing_project_aggregates(project_name: str, debug_files_base_path: Path) -> None:
    '''
    Remove existing aggregated debug output for a project from all clause folders.
    '''
    if not debug_files_base_path.exists():
        return

    for clause_path in debug_files_base_path.iterdir():
        if not clause_path.is_dir():
            continue

        project_path = clause_path / project_name
        if project_path.is_dir():
            shutil.rmtree(project_path)

        if clause_path.is_dir() and not any(clause_path.iterdir()):
            clause_path.rmdir()


def _move_project_debug_files(
        project_name: str,
        workspace_name: str,
        debug_files_base_path: Path) -> tuple[int, int]:
    '''
    Move workspace debug files into resources/debug/_files/<clause-test>/<project>.
    '''
    workspace_debug_path = (
        Path(PROJECT_BASE_PATH)
        / project_name
        / 'workspace'
        / workspace_name
        / 'debug'
    )
    if not workspace_debug_path.exists():
        return 0, 0

    _remove_existing_project_aggregates(project_name, debug_files_base_path)

    moved_clause_folders = 0
    moved_files = 0
    for clause_folder_path in sorted(workspace_debug_path.iterdir(), key=lambda path: path.name):
        if not clause_folder_path.is_dir():
            continue

        destination_project_path = (
            debug_files_base_path
            / clause_folder_path.name
            / project_name
        )
        destination_project_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(clause_folder_path), str(destination_project_path))

        moved_clause_folders += 1
        moved_files += len([path for path in destination_project_path.rglob('*') if path.is_file()])

    return moved_clause_folders, moved_files


def _add_project_names_argument(subparser: argparse.ArgumentParser) -> None:
    '''
    Add shared project name fleet argument.
    '''
    subparser.add_argument(
        'project_names',
        nargs='*',
        help=(
            'Optional project names. Omit to run across every project directory in '
            'PROJECT_BASE_PATH.'
        )
    )


def _add_exclude_sites_argument(subparser: argparse.ArgumentParser) -> None:
    '''
    Add shared exclude sites argument.
    '''
    subparser.add_argument(
        '--exclude-sites',
        '--exclude-projects',
        nargs='+',
        default=[],
        help=(
            'Optional project/site names to exclude. Supports space-separated and '
            'comma-separated values.'
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    '''
    Build CLI parser for all fleet actions.
    '''
    parser = argparse.ArgumentParser(
        description=(
            'Run project modules sequentially across all projects in PROJECT_BASE_PATH, '
            'or only selected projects.'
        )
    )
    subparsers = parser.add_subparsers(dest='action', required=True)

    go_parser = subparsers.add_parser(
        'go',
        help='Run go.py sequentially across projects.'
    )
    _add_project_names_argument(go_parser)
    _add_exclude_sites_argument(go_parser)
    go_parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to go.py (default: %(default)s).'
    )
    go_parser.add_argument(
        '--config-file',
        '--c',
        default='default.json',
        help='Configuration file name for go.py (default: %(default)s).'
    )
    go_parser.add_argument(
        '--chunk-size',
        type=int,
        default=500,
        help='Chunk size for go.py font-fix stages (default: %(default)s).'
    )
    go_parser.add_argument(
        '--n-cpu',
        type=int,
        default=None,
        help='CPU count for go.py font_fix_pdfix stage (--n-cpu).'
    )
    go_parser.add_argument(
        '--pre-validate',
        action='store_true',
        help='Run pre-fix validate step.'
    )
    go_parser.add_argument(
        '--skip-font-fix',
        action='store_true',
        help='Skip go.py font_fix and font_fix_pdfix steps.'
    )
    go_parser.add_argument(
        '--wcag-and-ua1-must-pass',
        action='store_true',
        help='Require go.py to pass --wcag-and-ua1-must-pass through to fix.py.'
    )
    go_parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output in go.py.'
    )
    go_parser.add_argument(
        '--debug',
        '-d',
        action='store_true',
        help='Enable debug output in go.py.'
    )

    get_latest_parser = subparsers.add_parser(
        'get_latest_files',
        help='Run get_latest_files.py sequentially across projects.'
    )
    _add_project_names_argument(get_latest_parser)
    _add_exclude_sites_argument(get_latest_parser)
    get_latest_parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to get_latest_files.py (default: %(default)s).'
    )

    debug_parser = subparsers.add_parser(
        'debug',
        help='Run debug.py across all projects and aggregate clause folders.'
    )
    _add_project_names_argument(debug_parser)
    _add_exclude_sites_argument(debug_parser)
    debug_parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to debug.py (default: %(default)s).'
    )
    debug_parser.add_argument(
        '--clause-tests',
        nargs='+',
        default=[],
        help=(
            'Optional clause-test ids to pass through to debug.py. '
            'Example: --clause-tests 6.2.4-1 7.1.3-2'
        )
    )

    fix_target_parser = subparsers.add_parser(
        'fix_target',
        help='Run fix_target.py sequentially across all projects.'
    )
    _add_project_names_argument(fix_target_parser)
    _add_exclude_sites_argument(fix_target_parser)
    fix_target_parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to fix_target.py (default: %(default)s).'
    )
    fix_target_parser.add_argument(
        '--workspace-folder',
        default='active',
        help='Workspace folder to pass to fix_target.py (default: %(default)s).'
    )
    fix_target_parser.add_argument(
        '--targets',
        nargs='+',
        required=True,
        help=(
            'Clause-test to action.json mappings for fix_target.py. '
            'Example: --targets 7.1-9:action1.json 5.2-3:action2.json'
        )
    )
    fix_target_parser.add_argument(
        '--n-cpu',
        type=int,
        default=4,
        help='Worker count to pass to fix_target.py (default: %(default)s).'
    )
    fix_target_parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output in fix_target.py.'
    )
    fix_target_parser.add_argument(
        '--debug',
        '-d',
        action='store_true',
        help='Enable debug output in fix_target.py.'
    )

    reprocess_parser = subparsers.add_parser(
        'reprocess',
        help='Run reprocess.py sequentially across all projects.'
    )
    _add_project_names_argument(reprocess_parser)
    _add_exclude_sites_argument(reprocess_parser)
    reprocess_parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to reprocess.py (default: %(default)s).'
    )
    reprocess_parser.add_argument(
        '--workspace-folder',
        default='all',
        help='Workspace folder to pass to reprocess.py (default: %(default)s).'
    )

    init_parser = subparsers.add_parser(
        'init',
        help='Run init.py sequentially across selected projects.'
    )
    _add_project_names_argument(init_parser)
    _add_exclude_sites_argument(init_parser)

    status_parser = subparsers.add_parser(
        'status',
        help='Run status.py sequentially across projects.'
    )
    _add_project_names_argument(status_parser)
    _add_exclude_sites_argument(status_parser)

    validate_parser = subparsers.add_parser(
        'validate',
        help='Run validate.py sequentially across projects.'
    )
    _add_project_names_argument(validate_parser)
    _add_exclude_sites_argument(validate_parser)
    validate_parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to validate.py (default: %(default)s).'
    )
    validate_parser.add_argument(
        '--workspace-folder',
        default='active',
        help='Workspace folder to pass to validate.py (default: %(default)s).'
    )
    validate_parser.add_argument(
        '--directory',
        default='files',
        help='Workspace directory to pass to validate.py (default: %(default)s).'
    )
    validate_parser.add_argument(
        '--full',
        action='store_true',
        help='Validate every workspace folder files/processed directories.'
    )
    validate_parser.add_argument(
        '--skip-page-count',
        action='store_true',
        help='Skip page counting and run only validation.'
    )

    return parser


def _print_action_context(args: argparse.Namespace, project_names: list[str]) -> None:
    '''
    Print context details before running fleet action.
    '''
    rows: list[tuple[str, object]] = [
        ("Projects Path", Path(PROJECT_BASE_PATH).resolve()),
        ("Action", args.action),
        ("Projects", len(project_names)),
    ]
    if args.action in {'go', 'get_latest_files', 'debug', 'fix_target', 'reprocess', 'validate'}:
        rows.append(("Workspace", args.workspace_name))
    if args.action == 'go':
        rows.extend([
            ("Config File", args.config_file),
            ("Chunk Size", args.chunk_size),
            ("N CPU", args.n_cpu),
            ("Pre Validate", args.pre_validate),
            ("Skip Font Fix", args.skip_font_fix),
            ("Verbose", args.verbose),
            ("Debug", args.debug),
        ])
    if args.action in {'fix_target', 'reprocess', 'validate'}:
        rows.append(("Folder", args.workspace_folder))
    if args.action == 'fix_target':
        rows.extend([
            ("Targets", ', '.join(args.targets)),
            ("N CPU", 1 if args.debug else args.n_cpu),
            ("Verbose", args.verbose or args.debug),
            ("Debug", args.debug),
        ])
    if args.action == 'validate':
        rows.extend([
            ("Directory", args.directory),
            ("Full", args.full),
            ("Skip Page Count", args.skip_page_count),
        ])
    if args.action == 'debug':
        rows.extend([
            ("Debug Aggregate Path", DEBUG_FILES_BASE_PATH.resolve()),
            ("Clause-Test Filters", ', '.join(args.clause_tests) if args.clause_tests else 'all'),
        ])
    if args.excluded_sites:
        rows.append(("Excluded Sites", ', '.join(sorted(args.excluded_sites))))

    print_console_banner("FLEET")
    print_console_key_value_rows(rows)


def main(argv: list[str] | None = None) -> int:
    '''
    Execute a fleet action across selected projects or all projects.
    '''
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.excluded_sites = parse_cli_filters(args.exclude_sites)

    allow_missing_projects = args.action in {'init', 'go', 'get_latest_files'}
    project_names, missing_project_names = _collect_project_names(
        args.project_names,
        excluded_project_names=args.excluded_sites,
        allow_missing=allow_missing_projects
    )

    if not project_names:
        print_console_section("NO PROJECTS", "warn")
        print_console_message("warn", f"No projects found under: {Path(PROJECT_BASE_PATH).resolve()}") # pylint: disable=line-too-long
        if missing_project_names:
            print_console_message(
                "warn",
                f"Missing project names: {', '.join(missing_project_names)}",
                indent=2
            )
        return 1

    if missing_project_names:
        print_console_message(
            "warn",
            f"Skipping missing projects: {', '.join(missing_project_names)}"
        )

    if args.action == 'debug':
        DEBUG_FILES_BASE_PATH.mkdir(parents=True, exist_ok=True)

    _print_action_context(args, project_names)

    failed_projects: list[tuple[str, int]] = []
    total_moved_clause_folders = 0
    total_moved_files = 0
    total_projects = len(project_names)
    for index, project_name in enumerate(project_names, start=1):
        print_processing_banner(project_name, index, total_projects)

        rc = _run_action(args.action, args, project_name)
        if rc != 0:
            if args.action == 'debug':
                print_console_message(
                    "warn",
                    f"fleet warning: debug.py failed for '{project_name}' "
                    f"with exit code {rc}."
                )
                failed_projects.append((project_name, rc))
                continue

            print_console_message(
                "error",
                f"fleet stopped: {args.action}.py failed for '{project_name}' "
                f"with exit code {rc}."
            )
            return rc

        if args.action == 'debug':
            moved_clause_folders, moved_files = _move_project_debug_files(
                project_name=project_name,
                workspace_name=args.workspace_name,
                debug_files_base_path=DEBUG_FILES_BASE_PATH
            )
            total_moved_clause_folders += moved_clause_folders
            total_moved_files += moved_files
            print_console_message(
                "success",
                f"MOVED: clause folders={moved_clause_folders}, files={moved_files} "
                f"for project '{project_name}'."
            )

    if args.action == 'debug':
        print_console_section("FLEET DEBUG SUMMARY", "info")
        print_console_key_value_rows([
            ("Projects Attempted", total_projects),
            ("Projects Failed", len(failed_projects)),
            ("Clause Folders Moved", total_moved_clause_folders),
            ("Files Moved", total_moved_files),
        ])

        if failed_projects:
            print_console_message("error", "Failed projects:", indent=2)
            print_console_list(
                [f"{project_name} (exit code {rc})" for project_name, rc in failed_projects],
                indent=4
            )
            return 1

    print_console_message("success", f"Fleet completed successfully ({args.action}).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
