# pylint: disable=too-many-branches, too-many-locals
'''
Aggregate Clause-Test totals from latest California reports across projects.
'''
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import re

import pandas as pd

from .utilities.resources import PROJECT_BASE_PATH

TIMESTAMP_PATTERN = re.compile(r'(\d{8}_\d{6})')
CLAUSE_HEADER = 'clausetest'
FILES_AFFECTED_HEADER = 'filesaffected'
DEFAULT_OUTPUT_DIR = Path('resources/artifacts/tally')
PROCESSING_ERRORS_FILENAME = 'pdfix-cannot-process-files.csv'


def _normalize_whitespace(value: str) -> str:
    '''
    Collapse repeated whitespace in a string.
    '''
    return ' '.join(value.split())


def _normalize_header(value: str) -> str:
    '''
    Normalize table headers for case-insensitive matching.
    '''
    return re.sub(r'[^a-z0-9]+', '', value.lower())


def _parse_timestamp(folder_name: str) -> datetime | None:
    '''
    Parse timestamp from report folder names like 20260228_204858-full.
    '''
    match = TIMESTAMP_PATTERN.search(folder_name)
    if match is None:
        return None

    try:
        return datetime.strptime(match.group(1), '%Y%m%d_%H%M%S')
    except ValueError:
        return None


def _build_timestamped_output_path(
        suffix: str,
        timestamp_format: str = '%Y%m%d%H%M%S') -> Path:
    '''
    Build a default tally artifact path using a timestamped filename.
    '''
    timestamp = datetime.now().strftime(timestamp_format)
    return DEFAULT_OUTPUT_DIR / f'tally-{timestamp}{suffix}'


def _parse_int(value: str) -> int | None:
    '''
    Parse integer values from strings like "7,918".
    '''
    cleaned = re.sub(r'[^0-9-]+', '', value)
    if cleaned in {'', '-'}:
        return None

    try:
        return int(cleaned)
    except ValueError:
        return None


def _clause_test_sort_key(value: str) -> tuple:
    '''
    Sort clause-test values numerically where possible.
    '''
    text = (value or '').strip()
    clause_part, test_part = text, ''
    if '-' in text:
        clause_part, test_part = text.split('-', 1)

    clause_key: list[tuple[int, int | str]] = []
    for token in clause_part.split('.'):
        if token.isdigit():
            clause_key.append((0, int(token)))
        else:
            clause_key.append((1, token))

    if test_part.isdigit():
        test_key = (0, int(test_part))
    else:
        test_key = (1, test_part)

    return (clause_key, test_key, text)


class _TableParser(HTMLParser):
    '''
    Extract plain-text rows from all HTML tables.
    '''
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # pylint: disable=unused-argument
        if tag == 'table':
            self._in_table = True
            self._current_table = []
            return

        if self._in_table and tag == 'tr':
            self._in_row = True
            self._current_row = []
            return

        if self._in_row and tag in {'th', 'td'}:
            self._in_cell = True
            self._current_cell_chunks = []
            return

        if self._in_cell and tag == 'br':
            self._current_cell_chunks.append(' ')

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_cell and tag in {'th', 'td'}:
            value = _normalize_whitespace(''.join(self._current_cell_chunks))
            self._current_row.append(value)
            self._in_cell = False
            self._current_cell_chunks = []
            return

        if self._in_row and tag == 'tr':
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = []
            self._in_row = False
            return

        if self._in_table and tag == 'table':
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False


def _extract_clause_totals(report_path: Path) -> dict[str, int]:
    '''
    Return {Clause-Test: Files Affected} extracted from the report table.
    '''
    parser = _TableParser()
    parser.feed(report_path.read_text(encoding='utf-8', errors='ignore'))

    for table in parser.tables:
        for row_index, row in enumerate(table):
            normalized_headers = [_normalize_header(cell) for cell in row]
            if CLAUSE_HEADER not in normalized_headers:
                continue
            if FILES_AFFECTED_HEADER not in normalized_headers:
                continue

            clause_idx = normalized_headers.index(CLAUSE_HEADER)
            files_idx = normalized_headers.index(FILES_AFFECTED_HEADER)
            clause_totals: defaultdict[str, int] = defaultdict(int)

            for data_row in table[row_index + 1:]:
                expected_width = max(clause_idx, files_idx)
                if len(data_row) <= expected_width:
                    continue

                clause_test = data_row[clause_idx].strip()
                files_affected = _parse_int(data_row[files_idx])
                if not clause_test or files_affected is None:
                    continue

                clause_totals[clause_test] += files_affected

            if clause_totals:
                return dict(clause_totals)

    raise ValueError(
        'Could not find a table with "Clause-Test" and "Files Affected" columns'
    )


def _find_latest_report_run(
        project_path: Path,
        workspace_name: str,
        required_relative_path: Path | None = None) -> Path | None:
    '''
    Return the latest reports run folder for a project.
    When required_relative_path is provided, return the latest matching artifact path instead.
    '''
    reports_path = project_path / 'workspace' / workspace_name / 'reports'
    if not reports_path.exists():
        return None

    candidates: list[tuple[datetime, float, str, Path]] = []
    for report_run_path in reports_path.iterdir():
        if not report_run_path.is_dir():
            continue

        candidate_path = report_run_path
        if required_relative_path is not None:
            required_path = report_run_path / required_relative_path
            if not required_path.exists():
                continue
            candidate_path = required_path

        timestamp = _parse_timestamp(report_run_path.name)
        sort_timestamp = timestamp if timestamp is not None else datetime.min
        modified_time = report_run_path.stat().st_mtime
        candidates.append((sort_timestamp, modified_time, report_run_path.name, candidate_path))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][3]


def _extract_summary_totals(summary_total_path: Path) -> dict[str, str | int]:
    '''
    Parse summary-total.csv and return the first row's totals.
    '''
    with open(summary_total_path, newline='', encoding='utf-8') as summary_file:
        reader = csv.DictReader(summary_file)
        first_row = next(reader, None)

    if first_row is None:
        raise ValueError('summary-total.csv is empty')

    processed_total = _parse_int(str(first_row.get('processed total', '')))
    passed_total = _parse_int(str(first_row.get('passed', '')))
    failed_total = _parse_int(str(first_row.get('fail', '')))
    success_percent = _normalize_whitespace(str(first_row.get('success %', '')).strip())

    if processed_total is None or passed_total is None or failed_total is None:
        raise ValueError('summary-total.csv has invalid numeric totals')
    if not success_percent:
        raise ValueError('summary-total.csv is missing success %')

    if not success_percent.endswith('%'):
        success_percent = f'{success_percent}%'

    return {
        'processed total': processed_total,
        'passed': passed_total,
        'fail': failed_total,
        'success %': success_percent
    }


def _collect_summary_rows(
        projects_path: Path,
        workspace_name: str) -> tuple[list[dict[str, str | int]], list[tuple[str, str]], int]:
    '''
    Collect project summary rows from summary-total.csv files.
    '''
    rows: list[dict[str, str | int]] = []
    skipped_projects: list[tuple[str, str]] = []
    scanned_projects = 0

    for project_path in sorted(projects_path.iterdir(), key=lambda path: path.name):
        if not project_path.is_dir():
            continue

        scanned_projects += 1
        summary_total_path = _find_latest_report_run(
            project_path=project_path,
            workspace_name=workspace_name,
            required_relative_path=Path('summary-total.csv')
        )
        if summary_total_path is None:
            skipped_projects.append((project_path.name, 'missing summary-total.csv'))
            continue

        try:
            totals = _extract_summary_totals(summary_total_path)
        except ValueError as error:
            skipped_projects.append((project_path.name, str(error)))
            continue

        rows.append(
            {
                'project': project_path.name,
                'processed total': totals['processed total'],
                'passed': totals['passed'],
                'fail': totals['fail'],
                'success %': totals['success %']
            }
        )

    return rows, skipped_projects, scanned_projects


def _collect_rows(
        projects_path: Path,
        workspace_name: str,
        profile_name: str,
        report_filename: str) -> tuple[list[dict[str, str | int]], list[tuple[str, str]], int]:
    '''
    Collect normalized rows for pivot generation.
    '''
    rows: list[dict[str, str | int]] = []
    skipped_projects: list[tuple[str, str]] = []
    scanned_projects = 0

    for project_path in sorted(projects_path.iterdir(), key=lambda path: path.name):
        if not project_path.is_dir():
            continue

        scanned_projects += 1
        report_path = _find_latest_report_run(
            project_path=project_path,
            workspace_name=workspace_name,
            required_relative_path=Path('summary') / profile_name / report_filename
        )

        if report_path is None:
            skipped_projects.append((project_path.name, 'missing report'))
            continue

        try:
            clause_totals = _extract_clause_totals(report_path)
        except ValueError as error:
            skipped_projects.append((project_path.name, str(error)))
            continue

        for clause_test, files_affected in clause_totals.items():
            rows.append(
                {
                    'Clause-Test': clause_test,
                    'Project': project_path.name,
                    'Files Affected': files_affected
                }
            )

    return rows, skipped_projects, scanned_projects


def _collect_processing_error_rows(
        projects_path: Path) -> tuple[list[dict[str, str | int]], list[tuple[str, str]], int]:
    '''
    Collect rows from <project>/pdfix-cannot-process-files.csv for pivoting.
    '''
    rows: list[dict[str, str | int]] = []
    skipped_projects: list[tuple[str, str]] = []
    scanned_projects = 0

    for project_path in sorted(projects_path.iterdir(), key=lambda path: path.name):
        if not project_path.is_dir():
            continue

        scanned_projects += 1
        processing_error_path = project_path / PROCESSING_ERRORS_FILENAME
        if not processing_error_path.exists():
            skipped_projects.append((project_path.name, f'missing {PROCESSING_ERRORS_FILENAME}'))
            continue

        with open(processing_error_path, newline='', encoding='utf-8', errors='ignore') as csv_file:
            reader = csv.reader(csv_file)
            for csv_row in reader:
                if not csv_row or all(not cell.strip() for cell in csv_row):
                    continue
                if len(csv_row) < 2:
                    continue

                error_value = _normalize_whitespace(','.join(csv_row[1:]).strip())
                if not error_value:
                    continue

                rows.append(
                    {
                        'Processing Error': error_value,
                        'Project': project_path.name,
                        'Count': 1
                    }
                )

    return rows, skipped_projects, scanned_projects


def _build_pivot(rows: list[dict[str, str | int]]) -> pd.DataFrame:
    '''
    Build Clause-Test x Project pivot table with summed file totals.
    '''
    if not rows:
        return pd.DataFrame(columns=['Clause-Test', 'Total'])

    data_frame = pd.DataFrame(rows)
    pivot_table = (
        data_frame
        .pivot_table(
            index='Clause-Test',
            columns='Project',
            values='Files Affected',
            aggfunc='sum',
            fill_value=0
        )
        .reset_index()
    )
    pivot_table.columns.name = None
    project_columns = [
        column_name for column_name in pivot_table.columns
        if column_name != 'Clause-Test'
    ]
    pivot_table.insert(1, 'Total', pivot_table[project_columns].sum(axis=1).astype(int))
    pivot_table = pivot_table.sort_values(
        by='Clause-Test',
        key=lambda column: column.map(_clause_test_sort_key),
        kind='mergesort'
    )
    pivot_table = pivot_table.sort_values(
        by='Total',
        ascending=False,
        kind='mergesort',
        ignore_index=True
    )
    return pivot_table


def _build_processing_errors_pivot(rows: list[dict[str, str | int]]) -> pd.DataFrame:
    '''
    Build processing-error x project pivot table with totals.
    '''
    if not rows:
        return pd.DataFrame(columns=['Processing Error', 'Total'])

    data_frame = pd.DataFrame(rows)
    pivot_table = (
        data_frame
        .pivot_table(
            index='Processing Error',
            columns='Project',
            values='Count',
            aggfunc='sum',
            fill_value=0
        )
        .reset_index()
    )
    pivot_table.columns.name = None
    project_columns = [
        column_name for column_name in pivot_table.columns
        if column_name != 'Processing Error'
    ]
    pivot_table.insert(1, 'Total', pivot_table[project_columns].sum(axis=1).astype(int))
    pivot_table = pivot_table.sort_values(
        by='Processing Error',
        kind='mergesort'
    )
    pivot_table = pivot_table.sort_values(
        by='Total',
        ascending=False,
        kind='mergesort',
        ignore_index=True
    )
    return pivot_table


def _write_spreadsheet(pivot_table: pd.DataFrame, output_path: Path) -> Path:
    '''
    Write the pivot table to CSV or XLSX depending on output extension.
    '''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    extension = output_path.suffix.lower()

    if extension == '.xlsx':
        try:
            pivot_table.to_excel(output_path, index=False)
            return output_path
        except (ImportError, ModuleNotFoundError, ValueError) as error:
            fallback_path = output_path.with_suffix('.csv')
            print()
            print(f'Unable to write XLSX ({error}). Writing CSV instead: {fallback_path}')
            pivot_table.to_csv(fallback_path, index=False)
            return fallback_path

    pivot_table.to_csv(output_path, index=False)
    return output_path


def _write_summary_spreadsheet(rows: list[dict[str, str | int]], output_path: Path) -> Path:
    '''
    Write project summary totals to CSV.
    '''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_frame = pd.DataFrame(
        rows,
        columns=['project', 'processed total', 'passed', 'fail', 'success %']
    )
    summary_frame = summary_frame.sort_values('project', ignore_index=True)
    summary_frame.to_csv(output_path, index=False)
    return output_path


def _write_processing_errors_spreadsheet(
        pivot_table: pd.DataFrame,
        output_path: Path) -> Path:
    '''
    Write processing errors pivot output to CSV.
    '''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pivot_table.to_csv(output_path, index=False)
    return output_path


def main() -> int:
    '''
    Generate a Clause-Test by Project tally spreadsheet.
    '''
    parser = argparse.ArgumentParser(
        description=(
            'Scan project California reports and build a Clause-Test x Project '
            'pivot table of Files Affected totals.'
        )
    )
    parser.add_argument(
        '--projects-path',
        type=Path,
        default=PROJECT_BASE_PATH,
        help='Projects base path (default: %(default)s).'
    )
    parser.add_argument(
        '--workspace',
        default='default',
        help='Workspace name (default: %(default)s).'
    )
    parser.add_argument(
        '--profile',
        default='ua1',
        help='Report profile folder under summary/ (default: %(default)s).'
    )
    parser.add_argument(
        '--report-file',
        default='california-report.html',
        help='Report file name inside summary/<profile>/ (default: %(default)s).'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=_build_timestamped_output_path('-clauses.csv'),
        help='Output spreadsheet path (.csv or .xlsx).'
    )
    parser.add_argument(
        '--summary-output',
        type=Path,
        default=_build_timestamped_output_path('-summary.csv', timestamp_format='%Y%m%d%H%S'),
        help='Output path for project summary totals CSV.'
    )
    parser.add_argument(
        '--processing-errors-output',
        type=Path,
        default=_build_timestamped_output_path('-processing-errors.csv'),
        help='Output path for processing errors pivot CSV.'
    )
    args = parser.parse_args()

    projects_path = args.projects_path
    if not projects_path.exists():
        print(f'Projects path not found: {projects_path}')
        return 1

    rows, skipped_projects, scanned_projects = _collect_rows(
        projects_path=projects_path,
        workspace_name=args.workspace,
        profile_name=args.profile,
        report_filename=args.report_file
    )

    if not rows:
        print('No report data found.')
        print(f'Scanned projects: {scanned_projects}')
        if skipped_projects:
            print('Skipped projects:')
            for project_name, reason in skipped_projects:
                print(f'  - {project_name}: {reason}')
        return 1

    pivot_table = _build_pivot(rows)
    written_path = _write_spreadsheet(pivot_table, args.output)
    summary_rows, skipped_summary_projects, _ = _collect_summary_rows(
        projects_path=projects_path,
        workspace_name=args.workspace
    )
    if not summary_rows:
        print('No summary-total data found.')
        if skipped_summary_projects:
            print('Skipped projects for summary output:')
            for project_name, reason in skipped_summary_projects:
                print(f'  - {project_name}: {reason}')
        return 1

    written_summary_path = _write_summary_spreadsheet(summary_rows, args.summary_output)
    processing_error_rows, skipped_processing_error_projects, _ = _collect_processing_error_rows(
        projects_path=projects_path
    )
    processing_errors_pivot = _build_processing_errors_pivot(processing_error_rows)
    written_processing_errors_path = _write_processing_errors_spreadsheet(
        processing_errors_pivot,
        args.processing_errors_output
    )

    projects_included = len({row['Project'] for row in rows})
    print(f'Scanned projects: {scanned_projects}')
    print(f'Included projects: {projects_included}')
    print(f'Clause-Test rows: {len(pivot_table)}')
    if skipped_projects:
        print(f'Skipped projects: {len(skipped_projects)}')
        for project_name, reason in skipped_projects:
            print(f'  - {project_name}: {reason}')
    if skipped_summary_projects:
        print(f'Skipped projects for summary output: {len(skipped_summary_projects)}')
        for project_name, reason in skipped_summary_projects:
            print(f'  - {project_name}: {reason}')
    if skipped_processing_error_projects:
        print(
            f'Skipped projects for processing errors output: '
            f'{len(skipped_processing_error_projects)}'
        )
        for project_name, reason in skipped_processing_error_projects:
            print(f'  - {project_name}: {reason}')
    print(f'Output: {written_path.resolve()}')
    print(f'Summary output: {written_summary_path.resolve()}')
    print(f'Processing errors output: {written_processing_errors_path.resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
