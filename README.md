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
   uv run -m pdf_remediation.Init <project_name>
   ```
5) Copy PDFs into `resources/projects/<project>/source`.
6) Remediate PDFs:
   ```
   uv run -m pdf_remediation.Fix <project_name>
   ```
7) Review reports in `resources/projects/<project>/workspace/<workspace>/active/reports/<timestamp>`.

## Process Flow

![Process Flow Diagram of PDF Remediation Process](resources/images/pdf_remediation_process_flow_presentation.png)

### Initialize and Validate

![Initialize and Validate Diagram of PDF Remediation Process](resources/images/slide_1_init_validate.png)

#### 1) Initialize a project
```
uv run -m pdf_remediation.Init <project_name>
```
Copy PDFs into the printed `resources/projects/<project>/source` directory.

#### 2) Validate PDFs
```
uv run -m pdf_remediation.Validate <project_name> [workspace] [folder]
```
Defaults:
- `workspace` = `default`
- `folder` = `active`
You can target any workspace and subfolder by passing these arguments.

If the `active/files` folder is empty, the system copies PDFs from `source/`
into `active/files` once and creates `.remediation.lock`.

### Fix and ReProcess

![Fix and ReProcess Diagram of PDF Remediation Process](resources/images/slide_2_fix_reprocess.png)

#### 1) Remediate PDFs
```
uv run -m pdf_remediation.Fix <project_name> [workspace] [folder]
```
Use `workspace` and `folder` to remediate a specific subfolder in the project.
Steps executed:
1. Count pages for each PDF (PDFix).
2. Split files into size buckets for parallel remediation.
3. Remediate with PDFix, write to `active/processed/`.
4. Validate all processed files with veraPDF.
5. Move compliant files into `remediated/files`.
6. Move font-violation failures into `font-issues/files`.

#### 2) Reprocess with a new configuration
```
uv run -m pdf_remediation.ReProcess <project_name> [workspace] [folder]
```
This moves processed PDFs back to `active/files`. Update
`resources/configuration/default.json` (or swap in a new config),
then re-run `Fix`.
For font-issue retries, run ReProcess with `font-issues` as the folder,
update the config, then re-run `Fix` on that subfolder.

### Workspace Control

![Workspace Control Diagram of PDF Remediation Process](resources/images/slide_3_workspace_control.png)

#### 1) Reset workspace
```
uv run -m pdf_remediation.Reset <project_name> [workspace] [folder]
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

### External tools and assets
- `lib/greenfield-apps-1.27.0-SNAPSHOT.jar`: veraPDF validation tool invoked by
  `src/pdf_remediation/utilities/VeraPDF.py`.
- `resources/configuration/default.json`: PDFix command profile applied
  during remediation.
- `resources/configuration/WCAG-2-2-Complete.xml`: optional veraPDF profile
  (currently commented in code).

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
```
Subfolder names are not fixed. `Fix` and `Validate` accept a `workspace_folder`
argument so you can run separate workflows in different subfolders (for example,
`active`, `remediated`, or a custom name).

## Features

### Initialization
- `Init.py` creates the project structure and prints the source path for users
  to drop PDFs.

### Validation
- `Validate.py` counts pages (PDFix) and validates PDFs with veraPDF (Java).
- Validation reports are generated as:
  - `reports/<timestamp>/vera_validation_results.csv`
  - `reports/<timestamp>/failed_rules.csv`
  - `reports/<timestamp>/xml/*.xml`
  - `reports/<timestamp>/summary/*.csv` and HTML (via `Report.py`)

### Remediation
- `Fix.py` performs remediation with PDFix using `default.json`.
- PDFs are grouped by page count, then remediated in parallel.
- Outputs are written to `workspace/.../processed`.
- All processed PDFs are validated. Compliant PDFs are moved to
  `workspace/.../remediated/files`.
- PDFs that fail validation with font-related violations are moved to
  `workspace/.../font-issues/files` for follow-up remediation.

### Reporting (internal function, part of Validate)
- `Report.py` parses veraPDF XML results and produces:
  - `verapdf-compliance-report.txt`
  - `verapdf-clause-summary.csv`
  - `verapdf-file-summary.csv`
  - `output.txt` (synthetic log data)
  - HTML summary report

### Reprocess
- `ReProcess.py` moves PDFs from `active/processed` back into `active/files`.
- Use this to re-run remediation after updating the configuration file
  (`resources/configuration/default.json` or a replacement).
- Then re-run Fix with the new config:
  `uv run -m pdf_remediation.Fix <project_name> [workspace] [folder] --config_file <config_file>`

### Reset
- `Reset.py` clears `active/files` and `active/processed`, then re-copies files
  from `source/` into the workspace and resets `.remediation.lock`.
- Use a new workspace name with Reset to create a fresh workspace seeded from
  `source/` without affecting existing workspaces.

### Licensing
- `License.py` reads license state from PDFix.
- `LicenseActivate.py` activates a license key.
- `LicenseDeactivate.py` deactivates an active license.
- `.env` supports `PDFIX_LICENSE_NAME` and `PDFIX_LICENSE_KEY` for remediation.

## Notes and Considerations
- Remediation deletes the original file in `active/files` after successful save
  (see `PDFix.fix`), so `Reset` is the canonical way to restore originals.
- Validation and remediation use multiprocessing; `Fix.py` sets spawn mode for
  compatibility.
