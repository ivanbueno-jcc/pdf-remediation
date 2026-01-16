# PDF Remediation Tool - Technical Documentation

This document describes the infrastructure, features, and process flow for the
`pdf-remediation` project.

## Infrastructure

### Runtime and dependencies
- Python package targeting `>=3.14` (see `pyproject.toml`).
- Java runtime is required for veraPDF validation (used by the JAR in `lib/`).
- PDFix SDK (`pdfix-sdk`) provides remediation and license operations.
- `parallelbar` is used for multiprocessing progress and job dispatch.
- `python-dotenv` loads license credentials from `.env`.
- `pdfservices-sdk` is listed but not used in the current code path.

### External tools and assets
- `lib/greenfield-apps-1.27.0-SNAPSHOT.jar`: veraPDF validation tool invoked by
  `src/pdf_remediation/utilities/VeraPDF.py`.
- `resources/configuration/make-accessible.json`: PDFix command profile applied
  during remediation.
- `resources/configuration/WCAG-2-2-Complete.xml`: optional veraPDF profile
  (currently commented in code).

### Directory layout
- `src/pdf_remediation/`: CLI entry points and orchestration scripts.
- `src/pdf_remediation/utilities/`: shared functions for remediation, validation,
  project paths, and report generation.
- `resources/projects/`: per-project workspace root (default, can be overridden
  with `PROJECT_BASE_PATH`).

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
```

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
- `Fix.py` performs remediation with PDFix using `make-accessible.json`.
- PDFs are grouped by page count, then remediated in parallel.
- Outputs are written to `workspace/.../processed`.
- All processed PDFs are validated. Compliant PDFs are moved to
  `workspace/.../remediated/files`.

### Reporting
- `Report.py` parses veraPDF XML results and produces:
  - `verapdf-compliance-report.txt`
  - `verapdf-clause-summary.csv`
  - `verapdf-file-summary.csv`
  - `output.txt` (synthetic log data)
  - HTML summary report

### Licensing
- `License.py` reads license state from PDFix.
- `LicenseActivate.py` activates a license key.
- `LicenseDeactivate.py` deactivates an active license.
- `.env` supports `PDFIX_LICENSE_NAME` and `PDFIX_LICENSE_KEY` for remediation.

## Process Flow

### 1) Initialize a project
```
uv run -m pdf_remediation.Init <project_name>
```
Copy PDFs into the printed `resources/projects/<project>/source` directory.

### 2) Validate PDFs
```
uv run -m pdf_remediation.Validate <project_name> [workspace] [folder]
```
Defaults:
- `workspace` = `default`
- `folder` = `active`

If the `active/files` folder is empty, the system copies PDFs from `source/`
into `active/files` once and creates `.remediation.lock`.

### 3) Remediate PDFs
```
uv run -m pdf_remediation.Fix <project_name> [workspace] [folder]
```
Steps executed:
1. Count pages for each PDF (PDFix).
2. Split files into size buckets for parallel remediation.
3. Remediate with PDFix, write to `active/processed/`.
4. Validate all processed files with veraPDF.
5. Move compliant files into `remediated/files`.

### 4) Reset workspace
```
uv run -m pdf_remediation.Reset <project_name> [workspace] [folder]
```
Clears `active/files` and `active/processed`, then re-copies files from `source/`
and resets `.remediation.lock`.

## Notes and Considerations
- Remediation deletes the original file in `active/files` after successful save
  (see `PDFix.fix`), so `Reset` is the canonical way to restore originals.
- Validation and remediation use multiprocessing; `Fix.py` sets spawn mode for
  compatibility.
- The Java validator writes XML reports only when exit code is `0` or `1`.
