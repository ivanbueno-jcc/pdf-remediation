# PDF Remediation Tool

Think of this as a production line for accessibility: you feed it a sprawling PDF archive, and it spits back a compliant set without wrecking your folder structure. Under the hood it wires veraPDF and PDFix together to validate and remediate thousands of files fast, with the original layout intact.

![Demo of PDF Remediation](resources/images/pdf_remediation_process_flow_presentation.gif)

## Quickstart

1) Install uv
   - macOS/Linux:
     `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows PowerShell:
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2) Install Java (required for veraPDF validation).
3) Set the PDFix license in `.env`:
   ```
   PDFIX_LICENSE_NAME="your-name"
   PDFIX_LICENSE_KEY="your-key"
   ```
4) Initialize a project:
   ```
   uv run -m pdf_remediation.init <project_name>
   ```
5) Copy PDFs into `resources/projects/<project>/source`.
6) Remediate PDFs:
   ```
   uv run -m pdf_remediation.fix <project_name>
   ```
7) If font issues are flagged, run Callas font remediation:
   ```
   uv run -m pdf_remediation.font_fix <project_name> [workspace]
   ```
8) Review reports in `resources/projects/<project>/workspace/<workspace>/active/reports/<timestamp>`.

## Process Flow

High-level view of the end-to-end pipeline: initialize, validate, remediate, re-validate, and route results into the right workspace folders.

![Process Flow Diagram of PDF Remediation Process](resources/images/pdf_remediation_process_flow_presentation.png)

### Initialize and Validate

Bootstrap a project and get a clean baseline before remediation begins.

![Initialize and Validate Diagram of PDF Remediation Process](resources/images/slide_1_init_validate.png)

#### 1) Initialize a project
```
uv run -m pdf_remediation.init <project_name>
```

Copy PDFs into the printed `resources/projects/<project>/source` directory.

#### 2) Validate PDFs
```
uv run -m pdf_remediation.validate <project_name> [workspace] [folder]
```

Defaults:
- `workspace` = `default`
- `folder` = `active`
You can target any workspace and subfolder by passing these arguments.

If the `active/files` folder is empty, the system copies PDFs from `source/`
into `active/files` once and creates `.remediation.lock`.

### Fix and Reprocess

Run remediation, then loop back for another pass when you have a better config.

![Fix and Reprocess Diagram of PDF Remediation Process](resources/images/slide_2_fix_reprocess.png)

#### 1) Remediate PDFs
```
uv run -m pdf_remediation.fix <project_name> [workspace] [folder]
```

Use `workspace` and `folder` to remediate a specific subfolder in the project.

For verbose progress and file-level visibility (useful for spotting blocking files), run:
`uv run -m pdf_remediation.fix <project_name> [workspace] [folder] --verbose`
Tune processing with:
- `--chunk-size <n>` to control batch size (default: 500)
- `--n-cpu <n>` to control parallel workers (default: 4)

Steps executed:
1. Apply the skip lists (`skipped_files.txt` and `pdfix_cannot_process_files.csv`) to exclude problematic files.
2. Count pages for each PDF (PDFix).
3. Split files into size buckets for parallel remediation.
4. Remediate with PDFix, write to `active/processed/`.
5. Validate all processed files with veraPDF.
6. Move compliant files into `remediated/files`.
7. Move error files into `unable-to-process/files`.
8. Move font-violation failures into `font-issues/files`.

If remediation is interrupted, rerunning `Fix` resumes from the remaining files.

#### 2) Fix font issues (Callas)
```
uv run -m pdf_remediation.font_fix <project_name> [workspace] [folder]
```

`FontFix` targets the `font-issues` folder by default, runs Callas pdfToolbox
inside Docker, then re-validates and routes results into `remediated/` or
`unable-to-process/`.

Options:
- `--chunk-size <n>` to control batch size (default: 500)
- `--verbose` to list files in each chunk

#### 3) Reprocess with a new configuration
```
uv run -m pdf_remediation.reprocess <project_name> [workspace] [folder]
```

This moves processed PDFs back to `active/files`. Update
`resources/configuration/default.json` (or swap in a new config),
then re-run `Fix`.

```
uv run -m pdf_remediation.fix <project_name> [workspace] [folder] --config-file [new-config.json]
```

`new-config.json` is located in `resources/configuration`

For font-issue retries, run reprocess with `font-issues` as the folder,
update the config, then re-run `Fix` on that subfolder. You can also run
`FontFix` to attempt automatic font remediation with Callas pdfToolbox.

To skip a blocking file before reprocessing, run:
`uv run -m pdf_remediation.skip <project_name> <relative_file_path>`

### Workspace Control

Use these controls to reset or fork clean workspaces without touching your originals.

![Workspace Control Diagram of PDF Remediation Process](resources/images/slide_3_workspace_control.png)

#### 1) Reset workspace
```
uv run -m pdf_remediation.reset <project_name> [workspace] [folder]
```

Clears `active/files` and `active/processed`, then re-copies files from `source/`
and resets `.remediation.lock`.

Use a new `workspace` name here to create a fresh workspace seeded from
`source/` without affecting existing workspaces.

## Infrastructure

### Runtime and dependencies
- Python package targeting `>=3.14` (see `pyproject.toml`).
- Java runtime is required for veraPDF validation (used by the JAR in `lib/`).
- PDFix SDK (`pdfix-sdk`) provides remediation and license operations.
- `parallelbar` is used for multiprocessing progress and job dispatch.
- Callas pdfToolbox runs in Docker for `FontFix` font remediation.

### External tools and assets
- `lib/greenfield-apps-1.27.0-SNAPSHOT.jar`: veraPDF validation tool invoked by
  `src/pdf_remediation/utilities/VeraPDF.py`.
- `resources/configuration/default.json`: PDFix command profile applied
  during remediation.
- `resources/configuration/WCAG-2-2-Complete.xml`: optional veraPDF profile
  (currently commented in code).
- `resources/font/.env`: Callas pdfToolbox license config for `FontFix`.

### Directory layout
- `src/pdf_remediation/`: CLI entry points and orchestration scripts.
- `src/pdf_remediation/utilities/`: shared functions for remediation, validation,
  project paths, and report generation.
- `resources/projects/`: per-project workspace root (default, can be overridden
  with `PROJECT_BASE_PATH`).

To store projects on a different disk, set `PROJECT_BASE_PATH` in `.env`:
```
PROJECT_BASE_PATH="/Volumes/ExternalDrive/pdf-remediation-projects"
```

### Project workspace structure
The workspace structure is created on demand by `Resources.py`:

```
resources/projects/<project>/
  source/                # user-provided original PDFs
  workspace/<workspace>/ # defaults to "default"
    active/
      files/             # working set copied from source
      processed/         # remediation output
      reports/<ts>/      # validation reports for a run
      .remediation.lock  # semaphore to avoid repeated copy
    remediated/
      files/             # validated, compliant PDFs
    font-issues/
      files/             # font-related validation failures
    unable-to-process/
      files/             # validation errors or unreadable PDFs
```
Subfolder names are not fixed. `Fix` and `Validate` accept a `workspace_folder`
argument so you can run separate workflows in different subfolders (for example,
`active`, `remediated`, or a custom name).

## Commands

### Initialization
- `init.py` bootstraps a project workspace and prints the source path for ingest.

### Validation
- `validate.py` runs page counting (PDFix) and PDF/UA validation via veraPDF (Java).
- Results feed the reporting pipeline in `reports/<timestamp>`.

### Remediation
- `fix.py` applies the PDFix remediation profile (e.g., `default.json`) with
  multiprocessing and keeps folder structure intact.
- Validation post-checks route outputs into `remediated/` and `font-issues/`.

### Font remediation
- `font_fix.py` runs Callas pdfToolbox via Docker on `font-issues/`, then
  re-validates and moves results to `remediated/` or `unable-to-process/`.

### Reporting (internal function, part of Validate)
- `report.py` generates CSV/TXT/HTML report artifacts from veraPDF XML output.
- Every Validate and Fix run generates a suite of reports under `reports/<timestamp>`.
- Report outputs include:
  - `vera_validation_results.csv`: per-file pass/fail status and rule counts.
  - `failed_rules.csv`: detailed list of failed rules with clauses and descriptions.
  - `xml/`: raw veraPDF XML reports per file.
  - `summary/verapdf-compliance-report.txt`: compliant vs non-compliant file list.
  - `summary/verapdf-clause-summary.csv`: clause-level rollup across the run.
  - `summary/verapdf-file-summary.csv`: per-file summary of violations.
  - `summary/output.txt`: synthetic log used by HTML report generation.
  - `summary/*.html`: human-readable compliance report.

### Reprocess
- `reprocess.py` returns processed PDFs to `active/files` so you can iterate with
  a revised configuration file.

### Skip
- `skip.py` appends a problematic file to `skipped_files.txt` so it is ignored
  during processing.
- Syntax: `uv run -m pdf_remediation.skip <project_name> <relative_file_path>`
  
### Auto-skip (PDFix failures)
- Files that PDFix cannot open/process are recorded in
  `pdfix_cannot_process_files.csv` at the project root and are skipped on
  subsequent runs.

### Status
- `status.py` prints a summary of the source PDF count and per-workspace file
  counts (including a remediated percentage).
- Syntax: `uv run -m pdf_remediation.status <project_name>`

### Reset
- `reset.py` refreshes a workspace from `source/` and resets the copy semaphore.

### Licensing
- `license.py` reads license state from PDFix.
- `license_activate.py` activates a license key.
- `license_deactivate.py` deactivates an active license.
- `.env` supports `PDFIX_LICENSE_NAME` and `PDFIX_LICENSE_KEY` for remediation.

## Notes and Considerations
- Remediation deletes the original file in `active/files` after successful save
  (see `PDFix.fix`), so `Reset` is the canonical way to restore originals.
- Validation and remediation use multiprocessing; `fix.py` sets spawn mode for
  compatibility.
