# Development reference

This guide covers repository architecture, configuration assets, reporting,
tests, and maintainer utilities. Product operators should begin with the
[documentation index](README.md).

## Package architecture

The project targets Python 3.14 and builds all packages under `src`:

| Package | Responsibility |
|---|---|
| `pdf_remediation` | Bulk CLI commands, project workspaces, orchestration, validation, routing, and reports |
| `pdf_api` | Shared single-PDF pipeline, result models, capability checks, and asynchronous HTTP API |
| `pdf_web` | Browser application, owner-scoped persistent jobs, queueing, downloads, and proxy trust |
| `pdf_worker` | Isolated single-file worker commands |

`pdf_web` calls `pdf_api.pipeline.process_pdf` in-process. The HTTP API is an
optional integration surface, not an internal dependency of the portal.

## Runtime dependencies

- `fastapi`, `uvicorn`, and `python-multipart` provide the HTTP services.
- `pdfix-sdk` performs PDF remediation, page counting, license operations, and
  selected security handling.
- Java runs the bundled veraPDF JAR for validation.
- `pandas` builds validation and progress summaries.
- `parallelbar` supports bulk multiprocessing progress and dispatch.
- `python-on-whales` integrates Docker-based font repair.
- `python-dotenv` loads local environment configuration.
- `plotext` supports terminal reporting utilities.

See `pyproject.toml` and `uv.lock` for authoritative versions.

## Important assets

| Path | Purpose |
|---|---|
| `lib/greenfield-apps-1.28.0.jar` | veraPDF validation CLI invoked by the Python utilities |
| `resources/configuration/default.json` | Primary PDFix remediation profile |
| `resources/configuration/default-slim.json` | Smaller, conservative remediation profile |
| `resources/configuration/WCAG-2-2-Complete-JCC.xml` | Default modified WCAG 2.2 validation profile |
| `resources/configuration/UA1-Font.xml` | Optional font-focused validation profile |
| `resources/configuration/*.json` | Presets and clause-specific PDFix actions |
| `resources/font/.env` | Callas pdfToolbox license configuration |

The web portal exposes only the configuration files listed in
`pdf_web.config.ALLOWED_CONFIG_FILES`. The standalone HTTP API currently allows
only `default.json` and `default-slim.json`. Treat those allowlists as public
input policy, not merely UI labels.

## Validation policy

`src/pdf_remediation/utilities/verapdf.py` defines the active veraPDF profiles.
The default run includes:

- `ua1` for PDF/UA-1
- `wcag` for `WCAG-2-2-Complete-JCC.xml`

The JCC profile removes checks `1.3.4-1` and `1.4.8-1` from the base profile.
Changing the profile or pass-gate behavior changes product policy and should be
accompanied by documentation, fixtures, and report-compatibility review.

## Remediation configurations

PDFix action files live under `resources/configuration`. Bulk commands accept a
configuration filename; the single-PDF pipeline validates names against its
allowed configurations before processing.

Clause-specific actions are supplied as `<clause-test>:<action.json>` mappings.
When multiple failing tests map to the same action, the targeted pipeline runs
that action once for the PDF. The default single-PDF mappings are defined in
`pdf_api.models.DEFAULT_TARGETS` so the API remains aligned with the bulk
orchestrator.

## Reporting internals

`src/pdf_remediation/utilities/report.py` converts veraPDF XML into CSV, text,
and HTML evidence. Standard runs write beneath the processed folder's `reports`
directory; full workspace validation writes beneath
`workspace/<workspace>/reports`.

Generated report artifacts include:

- Per-file profile pass/fail results
- Raw veraPDF XML by profile
- Clause-level and file-level violation summaries
- Human-readable HTML reports
- Workspace counts and remediation progress
- Exception CSVs for PDFix, Callas, security, and validation failures

`tally.py` reads the latest project reports and produces dated portfolio-level
outputs under `resources/artifacts/tally`:

- `tally.csv`: clause-test by project pivot
- `tally-summary.csv`: processed totals, pass/fail counts, and success rate
- `tally-processing-errors.csv`: PDFix error-message pivot
- `tally-progress-report.csv`: total, remediated, partially remediated, and broken counts
- `tally-progress-report-pivot.csv`: project metrics plus aggregate values

Use `uv run -m pdf_remediation.tally --help` for input and output overrides.

## Tests

Run the unit and integration suite:

```bash
uv run python -m unittest discover -s tests -t .
```

Run linting with the project's installed Pylint:

```bash
uv run pylint src tests
```

The test suite covers, among other behavior:

- Single-PDF pipeline options, stages, cancellation, scratch cleanup, and artifacts
- Upload sanitization, path traversal, interior-dot collapsing, and deduplication
- Portal identities, proxy trust, owner scoping, remote-bind refusal, and legacy jobs
- Queueing, cancellation, retention, job persistence, downloads, and retries
- Browser application rendering and client-side job behavior
- veraPDF report filename reconstruction and validation status parsing
- Subprocess environment isolation, including project-path override and removal
  of Pantheon credentials from web jobs
- Azure runtime and readiness behavior
- Single-file security removal and signature warnings

CI runs tests and linting on each push. When changing console banners or veraPDF
path handling, expect parsing tests to fail intentionally if the observable
contract drifts.

## Utility commands

### Check PDF headers

```bash
python3 scripts/check_pdf_headers.py <folder_path>
```

The script recursively checks for the `%PDF-` signature and prints valid,
invalid, and unreadable totals plus representative byte samples.

### Development identity proxy

```bash
uv run python scripts/dev_identity_proxy.py <identity> \
  --port <port> --secret <secret>
```

This local-only tool injects an identity and shared secret so multi-user portal
behavior can be tested. It does not authenticate the supplied identity and must
never be used as a production proxy.

### Debug aggregation

```bash
uv run -m pdf_remediation.debug <project_name> [workspace]
uv run fleet debug [<project_name> ...]
```

Project debug mode copies failures into clause-test directories. Fleet mode
aggregates them under `resources/debug/_files/<clause-test>/<project>`.

### License inspection

```bash
uv run license
```

The command reads PDFix license state. Runtime activation uses
`PDFIX_LICENSE_NAME` and `PDFIX_LICENSE_KEY` from the environment.

## Service implementation constraints

- The web portal and HTTP API must use one Uvicorn process because their job
  registries live in memory.
- Concurrency is provided by bounded thread pools inside that process.
- The API registry and artifacts are temporary; the portal persists metadata
  and artifacts under its job root.
- Java and the veraPDF JAR are hard validation dependencies.
- Docker and font licenses are capability-dependent for ordinary portal use,
  but production `/readyz` deliberately requires the complete deployment stack.
- A header-supplied identity is valid only when the request is proven to come
  through the configured authenticating proxy.

## Data-handling notes

- Single-PDF processing never overwrites its input.
- Bulk processing works from a source-backed workspace; successful remediation
  removes the corresponding working copy from `active/files`, not `source`.
- Saving an unsecured version of a digitally signed PDF invalidates the
  signatures; the security-removal utility reports this condition.
- User-supplied filenames are treated as untrusted input and sanitized before
  becoming paths or report identifiers.
- Web job ownership checks intentionally return 404 for another user's job so
  identifiers cannot be used to test for document existence.

## Adding or changing commands

Console entry points are declared in `[project.scripts]` in `pyproject.toml`.
When adding or changing one:

1. Keep the entry point and module `main` function aligned.
2. Update the relevant product guide rather than expanding the root README.
3. Add tests for argument handling and observable output.
4. Re-run the suite and verify `uv run <command> --help`.

## Documentation conventions

The root README is the product overview. Operational detail belongs in:

- [Getting started](getting-started.md)
- [Bulk remediation](bulk-remediation.md)
- [Web portal](web-portal.md)
- [API and Python integration](api.md)

Deployment-specific detail remains in the AWS, Azure, and Entra guides. New
screenshots should use synthetic documents and identities and must include
descriptive Markdown alt text.
