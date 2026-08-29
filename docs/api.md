# API and Python integration

The `pdf_api` package exposes the single-PDF remediation pipeline as both a
Python function and a small asynchronous HTTP service. It is intended for CMS
workflows, publishing pipelines, background automation, and systems that need
the same remediation behavior without a bulk project workspace.

One call produces a processed PDF when possible, a baseline validation report,
a final validation report, stage outcomes, warnings, and diagnostics. The input
PDF is never modified.

## Python quickstart

```python
from pathlib import Path

from pdf_api.models import PipelineOptions
from pdf_api.pipeline import process_pdf

result = process_pdf(
    Path("document.pdf"),
    Path("output"),
    PipelineOptions(config_file="default.json"),
)

print(result.status)
print(result.output_pdf_path)
print(result.before)
print(result.after)
```

The function manages its intermediate files in a temporary directory and
removes them before returning. Only the requested output artifacts remain.

## Pipeline options

`PipelineOptions` supports:

| Option | Default | Purpose |
|---|---:|---|
| `config_file` | `default.json` | PDFix remediation configuration |
| `wcag_and_ua1_must_pass` | `False` | Require both profiles for the successful pass gate |
| `attempt_unlock` | `True` | Remove empty-password security when PDFix can do so |
| `attempt_font_fix` | `True` | Attempt available Callas and PDFix font repair |
| `attempt_targeted_fixes` | `True` | Apply matching clause-specific repair actions |
| `targets` | repository defaults | Clause-test to action-file mappings |
| `fix_timeout_seconds` | `500` | Primary remediation timeout |
| `font_fix_timeout_seconds` | `600` | Font-stage timeout |

The default targeted mappings repair metadata, role mapping, and language
issues defined by the repository's configuration files.

## Pipeline outcomes

`PipelineResult.status` is one of:

| Status | Meaning |
|---|---|
| `already_compliant` | The input already met the selected pass gate |
| `remediated` | The processed PDF met the selected pass gate |
| `improved` | Violations decreased but the result still requires review |
| `unchanged` | A usable PDF was produced without measurable validation improvement |
| `failed` | The pipeline could not produce a usable result |
| `cancelled` | Processing stopped at a cancellation boundary |

`PipelineResult.succeeded()` is true for every usable-PDF outcome, including
`improved` and `unchanged`. Callers should inspect both the outcome and final
validation report rather than treating every produced PDF as conformant.

## Processing sequence

The single-PDF pipeline performs the relevant parts of the bulk workflow
without project scaffolding:

1. Validate the input.
2. Return early when it already meets the selected pass gate.
3. Unlock eligible secured files.
4. Apply the selected PDFix configuration.
5. Attempt Callas and PDFix font repair for matching violations.
6. Apply configured clause-specific targeted fixes.
7. Validate the final PDF and classify the outcome.

The bulk-only reprocessing sweep has no single-PDF equivalent because an API
caller receives a result rather than files routed among workspace folders.
Unavailable Docker-based stages are skipped with a recorded reason.

## Run the HTTP API

```bash
uv run pdf-api
```

The service binds to <http://127.0.0.1:8100> by default. Options are:

```text
--host <host>
--port <port>
--allow-remote
```

Job state and artifacts are process-local and temporary, so the server always
runs with one Uvicorn process. The in-process thread pool handles two jobs by
default; change it with `PDF_API_MAX_CONCURRENT_JOBS` after validating host and
license capacity.

## HTTP quickstart

Submit a PDF:

```bash
curl -X POST http://127.0.0.1:8100/api/pdf \
  -F 'file=@document.pdf;type=application/pdf' \
  -F 'config_file=default.json' \
  -F 'wcag_and_ua1_must_pass=false' \
  -F 'attempt_font_fix=true' \
  -F 'attempt_unlock=true'
```

The service returns `202 Accepted`:

```json
{
  "job_id": "8e4f2a9b7c1d4e6f8a0b2c3d4e5f6a7b",
  "original_name": "document.pdf",
  "state": "queued",
  "created_at": "2026-08-28T12:00:00",
  "stages": [],
  "error": null
}
```

Poll the job:

```bash
curl http://127.0.0.1:8100/api/pdf/8e4f2a9b7c1d4e6f8a0b2c3d4e5f6a7b
```

When processing finishes, `state` is `done` and the payload includes a result:

```json
{
  "job_id": "8e4f2a9b7c1d4e6f8a0b2c3d4e5f6a7b",
  "original_name": "document.pdf",
  "state": "done",
  "created_at": "2026-08-28T12:00:00",
  "stages": [],
  "error": null,
  "result": {
    "status": "remediated",
    "input_pdf_path": "/tmp/document.pdf",
    "output_pdf_path": "/tmp/out/document.pdf",
    "before": {},
    "after": {},
    "stages": [],
    "warnings": [],
    "diagnostics": [],
    "error": null
  }
}
```

Download artifacts:

```bash
curl -OJ "http://127.0.0.1:8100/api/pdf/<job-id>/pdf"
curl -OJ "http://127.0.0.1:8100/api/pdf/<job-id>/before"
curl -OJ "http://127.0.0.1:8100/api/pdf/<job-id>/after"
```

The PDF download uses the original filename. Validation artifacts are JSON
files named `<stem>-before.json` and `<stem>-after.json`.

## Endpoint reference

| Method | Endpoint | Behavior |
|---|---|---|
| `GET` | `/healthz` | Validation readiness, font-fix availability, version, and concurrency |
| `GET` | `/api/capabilities` | Tooling details and allowed configurations |
| `POST` | `/api/pdf` | Validate the upload and create an asynchronous job |
| `GET` | `/api/pdf` | List jobs newest first |
| `GET` | `/api/pdf/{job_id}` | Read current state, stages, and final result |
| `GET` | `/api/pdf/{job_id}/pdf` | Download the processed PDF |
| `GET` | `/api/pdf/{job_id}/before` | Download baseline validation JSON |
| `GET` | `/api/pdf/{job_id}/after` | Download final validation JSON |
| `POST` | `/api/pdf/{job_id}/cancel` | Request cancellation at the next stage boundary |
| `DELETE` | `/api/pdf/{job_id}` | Forget the job and delete its temporary artifacts |

Job states are `queued`, `running`, `done`, `error`, and `cancelled`.

## HTTP inputs and limits

`POST /api/pdf` accepts multipart form data:

| Field | Type | Default |
|---|---|---|
| `file` | PDF file | required |
| `config_file` | string | `default.json` |
| `wcag_and_ua1_must_pass` | boolean | `false` |
| `attempt_font_fix` | boolean | `true` |
| `attempt_unlock` | boolean | `true` |

The HTTP service currently allows `default.json` and `default-slim.json` and
accepts one file up to 200 MiB per request. It verifies both the `.pdf` suffix
and `%PDF-` header.

## Health and capabilities

`GET /healthz` returns 200 only when Java and the veraPDF JAR are available.
The response also reports whether Callas font repair is available and the
configured concurrency. `GET /api/capabilities` provides the detailed probe,
including the allowed configuration files.

## Security and deployment boundary

The HTTP API has **no authentication, ownership isolation, persistence, or
TLS**. It binds to loopback by default and refuses a remote bind unless
`--allow-remote` is explicitly supplied.

Do not expose it directly to a network. A production integration must provide
authentication, authorization, TLS, request limits, and an appropriate durable
job strategy in front of or around this service. Restarting the process removes
its job registry and temporary artifacts.

Use the [web portal](web-portal.md) when the requirement is a multi-user browser
experience with persisted, owner-scoped jobs.

## Single-file command utilities

Remove empty-password PDF security without modifying the input:

```bash
uv run solo-remove-security input.pdf output.pdf
```

The command emits JSON, accepts `--compact`, and does not recover non-empty
passwords. Already-unsecured PDFs are copied unchanged. If signature fields are
present, it warns that saving an unsecured copy invalidates those signatures.

Additional worker entry points declared in `pyproject.toml` are:

```text
solo-validate
solo-fix
```

Run each command with `--help` for its isolated-file arguments and JSON output
contract.
