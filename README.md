# PDF Remediation Tool

```mermaid
flowchart LR
  A[Ingest] --> B[Validate] --> C[Fix] --> D[Re-Validate] --> E[Publish + Report]
```

Think of this as a production line for accessibility: you feed it a sprawling PDF archive, and it spits back a compliant set without wrecking your folder structure. Under the hood it wires veraPDF and PDFix together to validate and remediate thousands of files fast, with the original layout intact.

![Demo of PDF Remediation](resources/images/pdf_remediation_process_flow_presentation.gif)

## Quickstart

```mermaid
flowchart TD
  A[PDF Archive Input<br/>Preserve folder structure] --> B[Baseline Compliance Check<br/>PDF/UA + WCAG]
  B --> C[Automated Remediation Pass]
  C --> D[Re-Validate Compliance]
  D --> E{Outcome}

  E -->|Pass| F[Compliant Library Output<br/>Ready to publish / distribute]
  E -->|Font issues| G[Targeted Font Repair]
  G --> D

  E -->|Cannot validate / blocked| H[Exception Queue<br/>Manual review required]

  D --> I[Reports & Audit Trail<br/>Per-file status + evidence]
```

1) Install uv
   - macOS/Linux:
     `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows PowerShell:
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

     (For Windows, you may need to install VC++ Redistributable: https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170#latest-supported-redistributable-version)
2) Install Java (required for veraPDF validation).
3) Set the PDFix license in `.env`:
   ```
   PDFIX_LICENSE_NAME="your-name"
   PDFIX_LICENSE_KEY="your-key"
   ```

   Check if the license is valid:
   ```
   uv run license
   ```

4) Install Docker Desktop (required for Callas/PDFix Docker font-fix steps).
   The tool now attempts to launch Docker Desktop automatically if it is not running.
5) Save the Callas license in `resources/font/.env`

## Run the full workflow with one command

```bash
uv run go delnorte
```

`go.py` orchestrates a required pre-fix `validate --skip-page-count` on
`active/files`, immediately moves files that meet the configured compliance
gate to `remediated/files`, then runs `fix`, optional `font_fix` +
`font_fix_pdfix` (`--skip-font-fix`), reprocesses all workspace folders back
into `active/files`, runs `fix_target` on `active` with
`5-1:restore_metadata.json` and `7.1-9:restore_metadata.json`, and finishes
with `validate --full --skip-page-count`.
It also initializes missing projects automatically. Pass
`--wcag-and-ua1-must-pass` to make the pre-fix gate and remediation stages
move files to `remediated/` only when both veraPDF `wcag` and `ua1` pass. By
default, those stages still route files based on `wcag` only. The
`--pre-validate` flag is accepted only for backwards compatibility because
pre-fix validation now always runs.

If `source/` is empty, `go.py` can automatically download and extract the
live files backup from Pantheon into `source/`.

Requirement: Terminus must be installed and already configured/authenticated.

### To run the same pipeline for multiple projects sequentially:

```bash
uv run readyset delnorte alameda sonoma
```

`readyset` runs `fleet.py go`, which runs `go.py` once per project in the
order provided, prints a high-visibility banner for each project, and stops on
the first non-zero exit code. It also accepts `--wcag-and-ua1-must-pass` and
forwards that strict pre-fix and remediated-file requirement to each project run.

### To run reprocess across all projects sequentially:

```bash
uv run fleet reprocess
```

`fleet.py` runs `reprocess.py` once per project under
`resources/projects`, prints a high-visibility banner for each project, and
stops on the first non-zero exit code.

### To run debug triage across all projects and aggregate clause folders:

```bash
uv run fleet debug
```

`fleet.py` runs `debug.py` for each project and moves each project's
clause folders into `resources/debug/_files/<clause-test>/<project>/`.

## Walkthrough

Here's an example walkthrough of remediating the Del Norte trial court.

```mermaid
flowchart LR

%% LANES
subgraph ORCH["Orchestrator (go.py / CLI)"]
    A[Init Project]
    B[Seed active/files from source]
    C["Required Pre-Fix Validate"]
    D[Run fix.py]
    E["Run font_fix.py (optional; skipped by --skip-font-fix)"]
    F["Run font_fix_pdfix.py (optional; skipped by --skip-font-fix)"]
    G[Final validate --full]
end

subgraph PDFIX[PDFix Engine]
    P1[Page Count]
    P2[Core Remediation<br/>default.json profile]
    P3["Font Fix Pass (missing unicode)"]
end

subgraph VERAPDF[veraPDF Validator]
    V1[Validate UA1 + WCAG 2.2]
    V2[Re-Validate after Fix]
    V3[Re-Validate after FontFix]
    V4[Re-Validate after PDFix FontFix]
    V5[Full Workspace Validation]
end

subgraph CALLAS["Callas pdfToolbox (Docker)"]
    C1[Font Remediation]
end

subgraph WORKSPACE[Workspace Routing]
    W1[source/]
    W2[active/files]
    W3[active/processed]
    W4[remediated/files]
    W5[font-issues/files]
    W6[font-issues-missing-unicode/files]
    W7[unable-to-validate/files]
    W8[secured-*]
    W9[pdfix-unable-to-open]
    W10[reports/<timestamp>-*]
end


%% FLOW

W1 --> B
B --> W2

C --> V1
W2 --> V1
V1 --> W10
V1 -->|Already compliant| W4
V1 -->|Needs remediation| D

D --> P1
P1 --> P2
W2 --> P2
P2 --> W3

W3 --> V2
V2 --> W10

V2 -->|Compliant| W4
V2 -->|Font Violations| W5
V2 -->|Validation Errors| W7
V2 -->|Secured| W8
V2 -->|Cannot Open| W9

E --> C1
W5 --> C1
C1 --> V3
V3 --> W10

V3 -->|Compliant| W4
V3 -->|Missing Unicode| W6
V3 -->|Still Failing| W7

F --> P3
W6 --> P3
P3 --> V4
V4 --> W10

V4 -->|Compliant| W4
V4 -->|Still Failing| W7

G --> V5
V5 --> W10
```

1) Initialize a project:
   ```
   uv run init delnorte
   ```
2) Copy PDFs into `resources/projects/delnorte/source`.
3) Validate the PDFs to establish a baseline.
   ```
   uv run validate delnorte
   ```
4) Remediate PDFs:
   ```
   uv run fix delnorte
   ```
5) If font issues are flagged, run Callas font remediation:
   ```
   uv run font_fix delnorte
   ```
6) After Callas, run the PDFix missing-unicode font fix on any remaining font issues:
   ```
   uv run font_fix_pdfix delnorte
   ```
7) Run the fallback remediation on pdf's that were not remediated in #4.

   a. Queue the files for re-processing (default scans all workspace
   subfolders that contain a `processed/` directory):
   ```
   uv run reprocess delnorte
   ```

   b. Remediate with the fallback configuration.
   ```
   uv run fix delnorte --config-file=default-fallback.json
   ```
8) Run the fallback remediation on the files with remaining font issues.
   
   a. Queue the files for re-processing:
   ```
   uv run reprocess delnorte default font-issues
   ```
   Use `font-issues-missing-unicode` instead of `font-issues` if you are
   reprocessing the PDFix font pass.

   b. Remediate with the fallback configuration (`reprocess` returns files to
   `active/files`, so run `fix` on `active`, which is the default):
   ```
   uv run fix delnorte --config-file=default-fallback.json
   ```

9) Check the workspace status:
   ```
   uv run status delnorte
   ```

10) Review the reports:
    - Standard validation/remediation runs:
      `resources/projects/<project>/workspace/<workspace>/<folder>/reports/<timestamp>-<directory>`
    - Full workspace validation runs (`validate --full`):
      `resources/projects/<project>/workspace/<workspace>/reports/<timestamp>-full`

11) Remediated files will be located in:
    `resources/projects/<project>/workspace/remediated/<folder>`

## Process Flow

High-level view of the end-to-end pipeline: initialize, validate, remediate, re-validate, and route results into the right workspace folders.

### Initialize and Validate

Bootstrap a project and get a clean baseline before remediation begins.

#### 1) Initialize a project
```
uv run init <project_name>
```

Copy PDFs into the printed `resources/projects/<project>/source` directory.

#### 2) Validate PDFs
```
uv run validate <project_name> [workspace] [folder] [directory] [--full] [--skip-page-count]
```

For ad-hoc validation of a single PDF, use [pdfaudit.org](https://www.pdfaudit.org/).
It explains accessibility issues in plain language and is useful for quick spot checks outside the full pipeline.

Defaults:
- `workspace` = `default`
- `folder` = `active`
- `directory` = `files`
You can target any workspace/subfolder/directory by passing these arguments.
By default, validation runs against `<workspace>/<folder>/<directory>`.

Use `--full` to validate all PDFs in every workspace subfolder's `files/` and
`processed/` directories in one pass. This mode writes reports to
`workspace/<workspace>/reports/<timestamp>-full` and prints the scanned folders.
`--full` skips these operational subfolders: `pdfix-cannot-process`,
`secured-cannot-process`, `secured-needs-approval`, `reports`,
`pdfix-unable-to-open`, `unable-to-validate`, and `unable-to-process`.
Use `--skip-page-count` to skip the PDFix page count pass and run only veraPDF.

Validation runs both PDF/UA (veraPDF `ua1`) and the modified WCAG 2.2
profile `WCAG-2-2-Complete-JCC.xml` by default.
Results include `ua1` and `wcag` columns in `vera_validation_results.csv`, and
per-profile report folders under `reports/<timestamp>-<directory>` (for example,
`xml/ua1`, `xml/wcag`, `summary/ua1`, `summary/wcag`). To change profiles, edit
the `profiles` list in `src/pdf_remediation/utilities/verapdf.py`.

If the `active/files` folder is empty, the system copies PDFs from `source/`
into `active/files` once and creates `.remediation.lock`.

### Fix and Reprocess

Run remediation, then loop back for another pass when you have a better config.

```mermaid
flowchart LR
  A[Non-compliant outputs<br/>active/processed, font-issues/processed, etc.] --> B[Reprocess<br/>reprocess.py moves processed -> active/files]
  B --> C[Update Config<br/>resources/configuration/*.json]
  C --> D[Re-run Fix<br/>fix.py --config-file <new-config.json>]
  D --> E[Validate + Route]
  E -->|Compliant| F[remediated/files]
  E -->|Still failing| A
```

#### 1) Remediate PDFs
```
uv run fix <project_name> [workspace] [folder]
```

Use `workspace` and `folder` to remediate a specific subfolder in the project.

For verbose progress and file-level visibility (useful for spotting blocking files), run:
`uv run fix <project_name> [workspace] [folder] --verbose`
Tune processing with:
- `--chunk-size <n>` to control batch size (default: 500)
- `--n-cpu <n>` to control parallel workers (default: 4)
- `--debug` to set `--verbose` and `--chunk-size 1` so you can spot a slow file
- `--wcag-and-ua1-must-pass` to move files to `remediated/` only when both
  `wcag` and `ua1` pass validation (default remains `wcag` only)

Steps executed:
1. Apply the skip lists (`skipped_files.txt` and `pdfix-cannot-process-files.csv`) to exclude problematic files.
2. Count pages for each PDF (PDFix).
3. Check for secured PDFs; classify and route them, then exclude them from remediation.
   - `secured-cannot-process/files`: secured PDFs with font violations that cannot be remediated.
   - `secured-needs-approval/files`: secured PDFs without blocking font violations (manual approval needed).
   - `pdfix-unable-to-open/files`: PDFs that PDFix cannot open.
4. Split files into size buckets for parallel remediation.
5. Remediate with PDFix, write to `active/processed/`.
6. Validate all processed files with veraPDF.
7. Move compliant files into `remediated/files`.
   - Default: move files when `wcag` passes.
   - Optional: pass `--wcag-and-ua1-must-pass` to require both `wcag` and `ua1`.
8. Move validation-error files into `unable-to-validate/files` and log them to
   `unable-to-validate.csv` in the project root.
9. Move font-violation failures into `font-issues/files`.

If remediation is interrupted, rerunning `Fix` resumes from the remaining files.
Runs end with a workspace summary showing totals plus `files`/`processed`
breakdowns.

For clause-test-specific remediation, use `fix_target`:
```
uv run -m pdf_remediation.fix_target <project_name> [workspace] [folder] --targets <clause-test:action.json> [<clause-test:action.json> ...] [--skip-final-full-validation] [--wcag-and-ua1-must-pass]
```

Example:
```
uv run -m pdf_remediation.fix_target delnorte --targets 3.1-42:action1.json 4.2-2:action1.json 7.1-9:action2.json
```

`fix_target` does the following:
1. Validate every PDF in `<workspace>/<folder>/files`.
2. Match each file's failing VERA clause-test IDs against `--targets`.
3. Run the matching PDFix action JSONs in CLI order.
4. If multiple matched clause-tests point to the same action JSON, run that action once for that PDF.
5. Write remediation output to `<workspace>/<folder>/processed`.
6. Validate every PDF currently in `<workspace>/<folder>/processed`.
7. Move validation-passing files to `remediated/files`; files that still fail remain in `processed`.
   By default, `wcag` passing is enough. Pass `--wcag-and-ua1-must-pass` to require both `wcag` and `ua1`.
8. Unless `--skip-final-full-validation` is passed, run a final `validate --full --skip-page-count` pass for the workspace.

Target action files must exist under `resources/configuration/`.

#### 2) Fix font issues (Callas)
```
uv run font_fix <project_name> [workspace] [folder]
```

`FontFix` targets the `font-issues` folder by default, runs Callas pdfToolbox
inside Docker on those files, then re-validates and routes results into
`remediated/` or `unable-to-validate/`.
Runs end with a workspace summary showing totals plus `files`/`processed`
breakdowns.
Missing-unicode violations detected after validation are moved to
`font-issues-missing-unicode/` for the PDFix pass.
By default, `wcag` passing is enough to move files to `remediated/`. Pass
`--wcag-and-ua1-must-pass` to require both `wcag` and `ua1`.

Callas file-level failures (error codes 104-107) are logged to
`callas_font_fix_errors.csv` in the project root.

Options:
- `--chunk-size <n>` to control batch size (default: 500)
- `--verbose` to list files in each chunk
- `--debug` to set `--verbose` and `--chunk-size 1` so you can spot a slow file
- `--wcag-and-ua1-must-pass` to move files to `remediated/` only when both
  `wcag` and `ua1` pass

#### 3) Fix missing-unicode font issues (PDFix)
```
uv run font_fix_pdfix <project_name> [workspace] [folder]
```

Run this after `FontFix` to process files moved into `font-issues-missing-unicode`.
It uses PDFix font remediation via Docker, re-validates, and routes results into
`remediated/` or `unable-to-validate/`.
By default, `wcag` passing is enough to move files to `remediated/`. Pass
`--wcag-and-ua1-must-pass` to require both `wcag` and `ua1`.

PDFix file-level failures are logged to `pdfix-font-errors.csv` in the project root.

Options:
- `--chunk-size <n>` to control batch size (default: 500)
- `--n-cpu <n>` to control parallel workers (default: all cores)
- `--verbose` to list files in each chunk
- `--debug` to set `--verbose` and `--chunk-size 1` so you can spot a slow file
- `--wcag-and-ua1-must-pass` to move files to `remediated/` only when both
  `wcag` and `ua1` pass

#### 4) Reprocess with a new configuration
```
uv run reprocess <project_name> [workspace] [folder]
```

Defaults:
- `workspace` = `default`
- `folder` = `all`

`reprocess` scans `<workspace>/<folder>/processed` and moves any PDFs back to
`active/files`. When `folder` is `all`, it scans every workspace subfolder with
a `processed/` directory.

Update
`resources/configuration/default.json` (or swap in a new config),
then re-run `Fix`.

```
uv run fix <project_name> [workspace] active --config-file [new-config.json]
```

`new-config.json` is located in `resources/configuration`

For font-issue retries, run reprocess with `font-issues` as the folder,
update the config, then re-run `Fix` on `active` (default folder). Run `FontFix` to
attempt automatic font remediation with Callas pdfToolbox, then follow with
`font_fix_pdfix` on `font-issues-missing-unicode`.

To skip a blocking file before reprocessing, run:
`uv run -m pdf_remediation.skip <project_name> <relative_file_path>`

### Workspace Control

Use these controls to reset or fork clean workspaces without touching your originals.

#### 1) Get latest source PDFs into `active/files`
```
uv run -m pdf_remediation.get_latest_files <project_name> [workspace]
uv run fleet get_latest_files <project_name> [project_name ...] [--workspace-name <workspace>] [--exclude-sites <site> [<site> ...]]
```

`get_latest_files` does the following:
- If Terminus is installed, it clears `<project>/source` and downloads the latest live files backup.
- If Terminus is not installed, it skips download and uses the current `source/` contents.
- Scans only PDF files from `source/`.
- Compares by relative path against all workspace `<folder>/files` and `<folder>/processed` PDFs.
- Ignores workspace `debug/` and `reports/` folders during this comparison.
- Copies only new PDFs into `<workspace>/active/files`, preserving relative paths.

#### 2) Reset workspace
```
uv run reset <project_name> [workspace] [folder]
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
- `pandas` is used to summarize validation results and write CSV reports.
- Callas pdfToolbox runs in Docker for `FontFix` font remediation.

### External tools and assets
- `lib/greenfield-apps-1.28.0.jar`: veraPDF validation tool invoked by
  `src/pdf_remediation/utilities/verapdf.py`.
- `resources/configuration/default.json`: PDFix command profile applied
  during remediation.
- `resources/configuration/WCAG-2-2-Complete-JCC.xml`: default veraPDF WCAG
  profile used alongside `ua1` (see the section below for the JCC-specific
  rule changes; adjust the `profiles` list in
  `src/pdf_remediation/utilities/verapdf.py` to change this).
- `resources/configuration/UA1-Font.xml`: optional narrowed veraPDF profile for
  font-only checks.
- `resources/font/.env`: Callas pdfToolbox license config for `FontFix`.

### WCAG-2-2-Complete-JCC.xml
`resources/configuration/WCAG-2-2-Complete-JCC.xml` is a modified version of
the veraPDF WCAG Validation profile. This repository uses it as the default
`wcag` profile in `src/pdf_remediation/utilities/verapdf.py`.

Compared with the base WCAG Validation profile, this JCC variant removes:
- `1.3.4-1`: Pages shall have the same orientation.
- `1.4.8-1`: Document should not contain illegible font.

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
The workspace structure is created on demand by `resources.py`:

```
resources/projects/<project>/
  source/                # user-provided original PDFs
  workspace/<workspace>/ # defaults to "default"
    reports/<ts>-full    # optional consolidated reports from "validate --full"
    active/
      files/             # working set copied from source
      processed/         # remediation output
      reports/<ts>-<directory>  # validation reports for a run
      .remediation.lock  # semaphore to avoid repeated copy
    remediated/
      files/             # validated, compliant PDFs
    font-issues/
      files/             # font-related validation failures
    font-issues-missing-unicode/
      files/             # missing-unicode font issues after Callas validation
    unable-to-validate/
      files/             # PDFs that failed validation after remediation
    debug/
      <clause>/...       # copies of failed active/files PDFs grouped by clause
    secured-cannot-process/
      files/             # secured PDFs with blocking font violations
    secured-needs-approval/
      files/             # secured PDFs without blocking font violations
    pdfix-unable-to-open/
      files/             # PDFs that PDFix cannot open
```
Subfolder names are not fixed. `Fix` and `Validate` accept a `workspace_folder`
argument so you can run separate workflows in different subfolders (for example,
`active`, `remediated`, or a custom name).

## Commands

Project scripts can be run directly with `uv run <script>`:
- `uv run license`
- `uv run init ...`
- `uv run validate ...`
- `uv run fix ...`
- `uv run font_fix ...`
- `uv run font_fix_pdfix ...`
- `uv run go ...`
- `uv run readyset ...`
- `uv run fleet ...`
- `uv run reprocess ...`
- `uv run reset ...`
- `uv run status ...`

### Pipeline orchestration
- `go.py` runs the remediation pipeline in sequence:
  1) required pre-fix validate (`--skip-page-count`) on `active/files`; files
     that meet the configured compliance gate move immediately to `remediated/files`
  2) `fix` on `active`
  3) optional `font_fix` on `font-issues` (skipped by `--skip-font-fix`)
  4) optional `font_fix_pdfix` on `font-issues-missing-unicode` (skipped by `--skip-font-fix`)
  5) `reprocess` on all folders back to `active/files`
  6) `fix_target` on `active` with `--targets 5-1:restore_metadata.json 7.1-9:restore_metadata.json`
  7) final `validate --full --skip-page-count`
- Syntax:
  `uv run go <project_name> [workspace] [--config-file <file>] [--chunk-size <n>] [--n-cpu <n>] [--pre-validate] [--skip-font-fix] [--wcag-and-ua1-must-pass] [--verbose] [--debug]`
- If the project does not exist, `go.py` runs `init` automatically.
- If `source/` is empty and Terminus is installed/configured, `go.py` can
  download and extract the live files backup into `source/`.
- `--wcag-and-ua1-must-pass` is forwarded to `fix.py`, `font_fix.py`,
  `font_fix_pdfix.py`, and the `go.py` `fix_target` stage. It also controls
  the required pre-fix validation gate. By default, files move to
  `remediated/files` when `wcag` passes; with the flag they require both
  `wcag` and `ua1`.
- `--pre-validate` is deprecated and has no effect; pre-fix validation is
  required and always runs.
- `readyset` runs `fleet.py go`, which runs `go.py` sequentially across
  multiple projects.
- Syntax:
  `uv run readyset <project_name> [project_name ...] [--workspace-name <workspace>] [--config-file <file>] [--chunk-size <n>] [--n-cpu <n>] [--pre-validate] [--skip-font-fix] [--wcag-and-ua1-must-pass] [--verbose] [--debug] [--exclude-sites <site> [<site> ...]]`
- `fleet.py go` is the explicit fleet subcommand for the same workflow.
- Syntax:
  `uv run fleet go <project_name> [project_name ...] [--workspace-name <workspace>] [--config-file <file>] [--chunk-size <n>] [--n-cpu <n>] [--pre-validate] [--skip-font-fix] [--wcag-and-ua1-must-pass] [--verbose] [--debug] [--exclude-sites <site> [<site> ...]]`
- `fleet.py go` exits immediately if any project run fails and returns that
  same exit code.
- `fleet.py get_latest_files` runs `get_latest_files.py` sequentially across all
  projects (or selected projects).
- Syntax:
  `uv run fleet get_latest_files [project_name ...] [--workspace-name <workspace>] [--exclude-sites <site> [<site> ...]]`
- `fleet.py fix_target` runs `fix_target.py` sequentially across all projects
  (or selected projects).
- Syntax:
  `uv run fleet fix_target [project_name ...] [--workspace-name <workspace>] [--workspace-folder <folder>] --targets <clause-test:action.json> [<clause-test:action.json> ...] [--n-cpu <n>] [--verbose] [--debug] [--skip-final-full-validation] [--wcag-and-ua1-must-pass] [--exclude-sites <site> [<site> ...]]`
- `fleet.py` runs `get_latest_files.py`, `init.py`, `status.py`, `validate.py`,
  `reprocess.py`, `debug.py`, or `fix_target.py` sequentially across all
  projects (or selected projects).
- Syntax:
  `uv run fleet <action> [project_name ...] [action options]`
- Shared filter:
  `--exclude-sites <site> [<site> ...]` (alias: `--exclude-projects`; comma-separated values also supported)
- Actions:
  `go`, `get_latest_files`, `init`, `status`, `validate`, `reprocess`, `debug`, `fix_target`
- `reprocess` exits immediately if any project run fails and returns that same
  exit code.
- `debug` runs across all projects (or selected projects), then moves each
  project's clause folders into
  `resources/debug/_files/<clause-test>/<project>/`.

### Initialization
- `init.py` bootstraps a project workspace and prints the source path for ingest.

### Validation
- `validate.py` runs page counting (PDFix) and veraPDF validation for PDF/UA
  (`ua1`) plus the modified WCAG 2.2 JCC profile
  (`WCAG-2-2-Complete-JCC.xml`).
- Default mode validates one directory (`<workspace>/<folder>/<directory>`).
- `--full` mode validates every `<subfolder>/files` and `<subfolder>/processed`
  directory in the workspace and writes a consolidated report under
  `workspace/<workspace>/reports/<timestamp>-full`.
- In `--full` mode, these subfolders are ignored:
  `pdfix-cannot-process`, `secured-cannot-process`, `secured-needs-approval`,
  `reports`, `pdfix-unable-to-open`, `unable-to-validate`, and
  `unable-to-process`.
- `--full` prints a `FOLDERS SCANNED` list before validation starts.
- `--skip-page-count` skips PDFix page counting and runs only veraPDF validation.
- Results feed the reporting pipeline in `reports/<timestamp>-<directory>`.

### Debug triage
- `debug.py` validates `active/files`, then copies every non-compliant file into
  clause-specific folders under `workspace/<workspace>/debug/<clause>/`.
- Debug copies are flattened by filename (source relative folders are not preserved).
- Files with multiple failing clauses are copied into each matching clause folder.
- Files with validation errors but no clause metadata are copied into
  `workspace/<workspace>/debug/unknown/`.
- Existing contents of `workspace/<workspace>/debug/` are cleared before each run.
- Syntax:
  `uv run -m pdf_remediation.debug <project_name> [workspace]`
- `fleet.py debug` runs `debug.py` for each project and aggregates outputs under
  `resources/debug/_files/<clause-test>/<project>/`.
- Syntax:
  `uv run fleet debug [project_name ...] [--workspace-name <workspace>] [--clause-tests <clause-test> [<clause-test> ...]] [--exclude-sites <site> [<site> ...]]`

### Remediation
- `fix.py` runs the PDFix remediation profile (e.g., `default.json`) with
  multiprocessing and preserves folder structure.
- Post-validation routes outputs to `remediated/` and moves font-issue files to
  `font-issues/`.
- By default, `fix.py` moves files to `remediated/` when `wcag` passes.
- Pass `--wcag-and-ua1-must-pass` to require both `wcag` and `ua1` before a
  file is moved to `remediated/`.
- `fix_target.py` validates `<workspace>/<folder>/files`, applies clause-test-
  specific PDFix action JSONs from `--targets`, validates `<workspace>/<folder>/processed`,
  moves validation-passing files to `remediated/`, then optionally runs `validate --full --skip-page-count`.
- By default, `fix_target.py` moves files to `remediated/` when `wcag` passes.
- Pass `--wcag-and-ua1-must-pass` to require both `wcag` and `ua1` before a
  file is moved to `remediated/`.
- If multiple matched clause-tests use the same action JSON, `fix_target.py`
  runs that action once per PDF.
- File-level remediation failures in `fix_target.py` are logged to
  `pdfix-cannot-process-files.csv` and reported in the console, but they do not
  make the whole workflow exit non-zero.
- `fleet.py fix_target` runs that workflow sequentially across every selected
  project and exits immediately only when a project has a fatal workflow error.

### Font remediation
- `font_fix.py` runs Callas pdfToolbox via Docker on `font-issues/`, re-validates,
  then moves results to `remediated/` or `unable-to-validate/`. Missing-unicode
  files move to `font-issues-missing-unicode/`. By default, `wcag` passing is
  enough to move files to `remediated/`; `--wcag-and-ua1-must-pass` requires
  both `wcag` and `ua1`.
- `font_fix_pdfix.py` runs PDFix font remediation via Docker on
  `font-issues-missing-unicode/`, re-validates, then moves results to
  `remediated/` or `unable-to-validate/`. By default, `wcag` passing is enough
  to move files to `remediated/`; `--wcag-and-ua1-must-pass` requires both
  `wcag` and `ua1`.

### Reporting (internal function, part of Validate)
- `utilities/report.py` generates CSV/TXT/HTML report artifacts from veraPDF XML output.
- Every Validate and Fix run generates reports under `reports/<timestamp>-<directory>`
  (or `workspace/<workspace>/reports/<timestamp>-full` for `validate --full`).
- Report outputs include:
  - `vera_validation_results.csv`: per-file `ua1`/`wcag` pass/fail status and rule counts.
  - `xml/<profile>/`: raw veraPDF XML reports per file (for example, `xml/ua1`).
  - `summary/<profile>/verapdf-compliance-report.txt`: compliant vs non-compliant file list.
  - `summary/<profile>/verapdf-clause-summary.csv`: clause-level rollup across the run.
  - `summary/<profile>/verapdf-file-summary.csv`: per-file summary of violations.
  - `summary/<profile>/output.txt`: synthetic log used by HTML report generation.
  - `summary/<profile>/*.html`: human-readable compliance report.

### Reprocess
- `reprocess.py` returns processed PDFs to `active/files` so you can iterate with
  a revised configuration file.
- Defaults: `workspace=default`, `folder=all`.
- You can target one source subfolder (for example, `font-issues`) or scan all
  subfolders with `processed/` and return them to `active/files`.
- `fleet.py reprocess` runs `reprocess.py` across every project in
  `resources/projects` (or only selected projects).
- Syntax:
  `uv run fleet reprocess [project_name ...] [--workspace-name <workspace>] [--workspace-folder <folder>] [--exclude-sites <site> [<site> ...]]`

### Skip
- `skip.py` appends a problematic file to `skipped_files.txt` so it is ignored
  during processing.
- Syntax: `uv run -m pdf_remediation.skip <project_name> <relative_file_path>`
  
### Auto-skip (PDFix failures)
- Files that PDFix cannot open/process are recorded in
  `pdfix-cannot-process-files.csv` at the project root and are skipped on
  subsequent runs.

### Secured and unreadable files
- Secured PDFs are logged to `secured-files.csv` with a status column
  (`secured-cannot-process` or `secured-needs-approval`).
- PDFs that PDFix cannot open are logged to `pdfix-unable-to-open.csv`.
- PDFs that cannot be validated after remediation are logged to
  `unable-to-validate.csv` and moved to `unable-to-validate/files`.
- Secured classification runs an in-memory veraPDF pass using the WCAG 2.2
  profile and treats font violations (`7.21.4.1`, `7.21.3.2`, `7.21.4.2`) as
  blocking.

### Status
- `status.py` prints a summary of the source PDF count and per-workspace file
  counts, including totals plus `files`/`processed` breakdowns.
- Workspace totals and summaries skip the workspace-level `reports/` folder.
- Syntax: `uv run status <project_name>`

### Tally
- `tally.py` scans all project folders in `resources/projects`, reads each
  project's latest `workspace/default/reports/<timestamp-*>/summary/ua1/california-report.html`,
  extracts `Clause-Test` and `Files Affected`, and builds a Clause-Test x
  Project pivot table with summed file totals.
- Default output is `resources/artifacts/tally/YYYY-MM-DD/tally.csv`.
- It also writes `resources/artifacts/tally/YYYY-MM-DD/tally-summary.csv` from each
  project's latest `workspace/default/reports/<timestamp-*>/summary-total.csv`
  with columns: `project`, `processed total`, `passed`, `fail`, `success %`.
- It also writes `resources/artifacts/tally/YYYY-MM-DD/tally-processing-errors.csv`
  from each project's `pdfix-cannot-process-files.csv`, pivoted on the second
  column (error message) with `Total` and per-project totals.
- It also writes `resources/artifacts/tally/YYYY-MM-DD/tally-progress-report.csv`
  from each project's latest
  `workspace/default/reports/<timestamp-*>/workspace-file-count.csv`
  with columns:
  `project`, `total`, `remediated`, `Remediation %`, `partially remediated`, `broken`.
  Formulas:
  `total = Total Files - pdfix-unable-to-open`;
  `Remediation % = round((remediated / total) * 100)`;
  `partially remediated = active + font-issues + font-issues-missing-unicode + secured-cannot-process + secured-needs-approval`;
  `broken = pdfix-unable-to-open + unable-to-validate`.
- It also writes `resources/artifacts/tally/YYYY-MM-DD/tally-progress-report-pivot.csv`
  from `tally-progress-report.csv` with projects as columns, metrics as rows,
  and an `aggregate` second column. Metric labels include aggregation function:
  `total (sum)`, `remediated (sum)`, `Remediation % (avg)`,
  `partially remediated (sum)`, `broken (sum)`.
- Syntax:
  `uv run -m pdf_remediation.tally [--projects-path <path>] [--workspace <name>] [--profile <name>] [--report-file <file>] [--output <path>] [--summary-output <path>] [--processing-errors-output <path>] [--progress-report-output <path>] [--progress-report-pivot-output <path>]`

### Utility scripts
- `scripts/check_pdf_headers.py` recursively checks file headers for `%PDF-`.
- It prints total valid/invalid/unreadable counts plus up to 3 sample valid and
  3 sample invalid files.
- Invalid samples include the first 32 bytes (printable + hex) to aid triage.
- Syntax: `python3 scripts/check_pdf_headers.py <folder_path>`

### Reset
- `reset.py` refreshes a workspace from `source/` and resets the copy semaphore.

### Latest source sync
- `get_latest_files.py` refreshes source from Terminus when available, then copies
  only new PDF files from `source/` into `workspace/<workspace>/active/files`.
- New-file detection is based on each PDF's path relative to `source/`, compared
  against all `<workspace>/<folder>/files` and `<workspace>/<folder>/processed` PDFs.
- Ignores `debug/` and `reports/` workspace folders while checking existing files.
- Syntax:
  `uv run -m pdf_remediation.get_latest_files <project_name> [workspace]`
- Fleet syntax:
  `uv run fleet get_latest_files [project_name ...] [--workspace-name <workspace>] [--exclude-sites <site> [<site> ...]]`

### Licensing
- `license.py` reads license state from PDFix.
<!-- - `license_activate.py` activates a license key.
- `license_deactivate.py` deactivates an active license. -->
- `.env` supports `PDFIX_LICENSE_NAME` and `PDFIX_LICENSE_KEY` for remediation.

## Troubleshooting

### Find slow files in a batch
1) Press Ctrl+C to stop the current run.
2) Re-run the fix command with `--debug` (or `-d`).
3) When a file hangs, copy the file path and press Ctrl+C again.
4) Skip the file:
   ```
   uv run -m pdf_remediation.skip <project_name> <file_path>
   ```
5) Run `fix` again without `--debug`/`-d`.

## Notes and Considerations
- Remediation deletes the original file in `active/files` after successful save
  (see `PDFix.fix`), so `Reset` is the canonical way to restore originals.
- Validation and remediation use multiprocessing; `fix.py` sets spawn mode for
  compatibility.
