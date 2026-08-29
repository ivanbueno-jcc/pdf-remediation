# Bulk remediation

Bulk remediation processes document collections while preserving their source
paths, tracking intermediate outcomes, and producing collection-level evidence.
It is intended for website archives, records programs, and repeatable
multi-project remediation work.

## Quickstart

Initialize a project:

```bash
uv run init acme
```

Copy PDFs into the path printed by the command, normally:

```text
resources/projects/acme/source/
```

Run the complete workflow:

```bash
uv run go acme
```

The final PDFs are written under:

```text
resources/projects/acme/workspace/default/remediated/files/
```

Reports remain with the workspace so the output can be traced back to its
baseline and final validation passes.

## Project and workspace model

A **project** is one source collection. A project may have multiple
**workspaces**, allowing operators to test a new configuration or restart a
campaign without changing the original source files.

The default layout is:

```text
resources/projects/<project>/
  source/                       # original, user-provided PDFs
  workspace/<workspace>/        # "default" unless another name is supplied
    reports/<timestamp>-full/   # consolidated full-workspace validation
    active/
      files/                    # current working set
      processed/                # output awaiting routing or another pass
      reports/<timestamp>-*/    # validation reports for this folder
      .remediation.lock         # prevents repeated source seeding
    remediated/files/           # files that met the selected validation gate
    font-issues/files/          # font-related validation failures
    font-issues-missing-unicode/files/
    unable-to-validate/files/
    secured-cannot-process/files/
    secured-needs-approval/files/
    pdfix-unable-to-open/files/
    debug/<clause-test>/
```

Workspace subfolder names are not hard-coded. Most commands accept a workspace
and folder so targeted or experimental workflows can operate beside `active`
and `remediated`.

## Full workflow orchestration

```bash
uv run go <project_name> [workspace]
```

`go` performs the production sequence:

1. Initializes a missing project and seeds `active/files` from `source`.
2. Runs required baseline validation on `active/files`; files that already meet
   the selected pass gate move directly to `remediated/files`.
3. Applies the selected PDFix remediation configuration.
4. Runs optional Callas and PDFix font-repair stages.
5. Returns remaining processed files to `active/files` for another pass.
6. Applies targeted metadata, role-mapping, and language fixes for configured
   clause tests.
7. Runs a final full-workspace validation and writes consolidated reports.

Common options:

```text
--config-file <file>
--chunk-size <n>
--n-cpu <n>
--skip-font-fix
--wcag-and-ua1-must-pass
--verbose
--debug
```

The compatibility flag `--pre-validate` is accepted but has no effect because
baseline validation is now always required.

By default, bulk routing treats the configured WCAG profile as the pass gate.
Use `--wcag-and-ua1-must-pass` when a file must pass both the WCAG and PDF/UA-1
profiles before it is routed to `remediated/files`. The option is forwarded to
the baseline, primary remediation, font repair, and targeted-fix stages.

If `source` is empty and Terminus is installed and already authenticated,
`go.py` can download and extract the live Pantheon files backup. Otherwise,
populate `source` directly.

## Multiple projects

Run the same pipeline sequentially across named projects:

```bash
uv run readyset delnorte alameda sonoma
```

`readyset` is the concise entry point for `fleet go`. It prints a clear project
banner, processes projects in the supplied order, and stops on the first
non-zero exit code.

The explicit fleet form supports the same workflow and shared filters:

```bash
uv run fleet go delnorte alameda sonoma \
  --workspace-name default \
  --exclude-sites archived-site
```

`--exclude-projects` is an alias for `--exclude-sites`; comma-separated values
are also accepted. Fleet actions include `go`, `get_latest_files`, `init`,
`status`, `validate`, `reprocess`, `debug`, and `fix_target`.

## Validate a collection

```bash
uv run validate <project_name> [workspace] [folder] [directory]
```

Defaults are `workspace=default`, `folder=active`, and `directory=files`.
Validation runs the veraPDF `ua1` profile and the repository's modified WCAG
2.2 profile, `WCAG-2-2-Complete-JCC.xml`.

Useful modes:

```bash
# Validate every eligible files/ and processed/ directory in the workspace.
uv run validate <project_name> --full

# Run veraPDF without the PDFix page-count pass.
uv run validate <project_name> --skip-page-count
```

Full validation writes to `workspace/<workspace>/reports/<timestamp>-full` and
prints every scanned directory. It excludes operational folders that are not
expected to validate, including reports, unreadable files, secured exceptions,
and unable-to-process results.

The JCC WCAG profile differs from the base profile by removing:

- `1.3.4-1`: pages shall have the same orientation
- `1.4.8-1`: document should not contain illegible font

Change the `profiles` list in `src/pdf_remediation/utilities/verapdf.py` only
when the program's validation policy is intentionally changing.

For an ad-hoc external check of one PDF, [pdfaudit.org](https://www.pdfaudit.org/)
can explain accessibility issues in plain language; it is separate from the
project pipeline and its reports.

## Run remediation stages manually

The orchestrated `go` command is the normal production entry point. Individual
commands are useful for diagnosis, reruns, and targeted experiments.

### Primary PDFix remediation

```bash
uv run fix <project_name> [workspace] [folder] \
  --config-file default.json
```

The command:

1. Applies project skip lists.
2. Counts pages and classifies secured or unreadable files.
3. Splits work into size-aware chunks.
4. Applies the selected PDFix configuration with multiprocessing.
5. Writes outputs to the selected folder's `processed` directory.
6. Validates the output and routes it to `remediated`, `font-issues`, or an
   exception folder.

Use `--chunk-size <n>` and `--n-cpu <n>` to tune a run. `--verbose` adds
file-level visibility; `--debug` enables verbose output and uses a chunk size of
one so a blocking file is easy to identify. Interrupted runs resume from the
files that remain.

### Callas font repair

```bash
uv run font_fix <project_name> [workspace] [folder]
```

The default source folder is `font-issues`. Callas pdfToolbox runs in Docker,
then the output is re-validated. Passing files move to `remediated`; remaining
missing-Unicode violations move to `font-issues-missing-unicode`; validation
failures move to `unable-to-validate`.

Callas error codes 104–107 are recorded in `callas_font_fix_errors.csv` at the
project root. Options include `--chunk-size`, `--verbose`, `--debug`, and the
strict two-profile pass gate.

### PDFix missing-Unicode repair

```bash
uv run font_fix_pdfix <project_name> [workspace] [folder]
```

Run this after the Callas stage. It processes
`font-issues-missing-unicode`, re-validates the results, and routes them to
`remediated` or `unable-to-validate`. File-level failures are recorded in
`pdfix-font-errors.csv`. Options include `--chunk-size`, `--n-cpu`,
`--verbose`, `--debug`, and the strict two-profile pass gate.

### Clause-specific targeted fixes

```bash
uv run fix_target <project_name> [workspace] [folder] \
  --targets 5-1:restore_metadata.json 7.1-9:restore_metadata.json
```

Target files live under `resources/configuration`. `fix_target` validates each
source PDF, matches failing clause-test identifiers to the supplied action
files, applies each matching action once per PDF, re-validates the processed
set, and routes passing files. Unless
`--skip-final-full-validation` is supplied, it finishes with a full workspace
validation.

Run the same targets across projects with:

```bash
uv run fleet fix_target <project_name> [<project_name> ...] \
  --targets <clause-test:action.json> [<clause-test:action.json> ...]
```

File-level PDFix failures are recorded in
`pdfix-cannot-process-files.csv` without failing the entire collection; fatal
workflow errors still produce a non-zero exit code.

## Reprocess and workspace control

Return processed files to `active/files` before trying a revised configuration:

```bash
uv run reprocess <project_name> [workspace] [folder]
```

Defaults are `workspace=default` and `folder=all`. `all` scans every workspace
subfolder that has a `processed` directory. After reprocessing, run `fix` on
`active` with the updated configuration:

```bash
uv run fix <project_name> default active \
  --config-file default-fallback.json
```

Across projects:

```bash
uv run fleet reprocess [<project_name> ...]
```

Refresh a workspace with source PDFs that are not already represented in any
`files` or `processed` directory:

```bash
uv run get_latest_files <project_name> [workspace]
uv run fleet get_latest_files [<project_name> ...] \
  --workspace-name <workspace>
```

When Terminus is available, `get_latest_files` first refreshes `source` from the
live files backup. It compares PDFs by source-relative path, ignores workspace
`debug` and `reports`, and copies only new PDFs into `active/files`.

Reset the working set from `source`:

```bash
uv run reset <project_name> [workspace] [folder]
```

`reset` clears `active/files` and `active/processed`, restores source files, and
resets the remediation lock. Supplying a new workspace name creates an
independent workspace without changing an existing run.

## Triage and status

Check project counts:

```bash
uv run status <project_name>
uv run fleet status [<project_name> ...]
```

Group current failures by clause-test for investigation:

```bash
uv run -m pdf_remediation.debug <project_name> [workspace]
uv run fleet debug [<project_name> ...]
```

`debug` validates `active/files` and copies each non-compliant PDF into every
matching `debug/<clause-test>` folder. Fleet mode aggregates clause folders
under `resources/debug/_files/<clause-test>/<project>`.

Skip a known blocking file:

```bash
uv run -m pdf_remediation.skip <project_name> <relative_file_path>
```

The file is appended to `skipped_files.txt`. PDFix open/process failures are
also auto-recorded in `pdfix-cannot-process-files.csv` and skipped on later
runs.

Build cross-project rollups with:

```bash
uv run -m pdf_remediation.tally
```

The default output directory is
`resources/artifacts/tally/YYYY-MM-DD/`. It includes clause-by-project counts,
success summaries, processing-error pivots, and remediation-progress reports.
Run `uv run -m pdf_remediation.tally --help` for path, workspace, profile, and
output overrides.

## Reports and audit evidence

Every validation and remediation run writes artifacts beside the directory it
processed. Full validation writes at the workspace level.

Key outputs include:

- `vera_validation_results.csv`: per-file WCAG and UA1 pass/fail status
- `xml/<profile>/`: raw veraPDF XML reports
- `summary/<profile>/verapdf-compliance-report.txt`: compliant and non-compliant file lists
- `summary/<profile>/verapdf-clause-summary.csv`: clause-level rollup
- `summary/<profile>/verapdf-file-summary.csv`: per-file violation summary
- `summary/<profile>/*.html`: human-readable compliance reports
- `workspace-file-count.csv`: workspace distribution used by progress reporting

Operational exception CSVs include secured files, PDFix open/process failures,
Callas failures, PDFix font failures, and files that could not be validated.

## Command reference

Project scripts declared in `pyproject.toml` can be run with `uv run`:

```text
init                 create a project
go                   run the complete project pipeline
readyset             run the complete pipeline across named projects
fleet                run supported actions across projects
validate             validate a directory or workspace
fix                  apply the primary PDFix configuration
fix_target           apply clause-test-specific configurations
font_fix             run Callas font remediation
font_fix_pdfix       run PDFix missing-Unicode remediation
reprocess            return processed files to active/files
get_latest_files     copy newly discovered source PDFs into a workspace
reset                restore the active working set from source
status               summarize project and workspace counts
license              inspect the PDFix license
```

Single-file utilities are documented in the [API and Python guide](api.md).
Contributor and reporting utilities are in the
[development reference](development.md).

## Troubleshooting

### Find a slow or blocking PDF

1. Press Ctrl+C to stop the current run.
2. Re-run `fix` with `--debug`.
3. Note the last file printed when processing stalls.
4. Stop the run and add the file to the skip list.
5. Re-run `fix` without `--debug`.

```bash
uv run fix <project_name> --debug
uv run -m pdf_remediation.skip <project_name> <relative_file_path>
uv run fix <project_name>
```

### Understand routed exceptions

- `font-issues`: requires a font repair pass
- `font-issues-missing-unicode`: requires the PDFix font follow-up
- `secured-cannot-process`: secured and contains blocking font violations
- `secured-needs-approval`: secured without the blocking font violations
- `pdfix-unable-to-open`: PDFix could not open the file
- `unable-to-validate`: final validation could not produce a usable result

Secured PDFs are recorded in `secured-files.csv`. Saving an unsecured copy of a
digitally signed PDF invalidates its signatures; the single-file security
utility warns when signature fields are present.

### Restore originals

Successful bulk remediation removes the corresponding working file from
`active/files` after writing its result. The original remains in `source`, and
`reset` is the canonical way to rebuild the working set.
