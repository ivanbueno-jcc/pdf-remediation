# Getting started

This guide prepares a local machine for bulk remediation, the web portal, and
the single-PDF API. All three functions share the same validation and
remediation dependencies.

## Requirements

- Python 3.14 or newer, as declared in `pyproject.toml`
- [uv](https://docs.astral.sh/uv/) for dependency management and command entry points
- Java for veraPDF validation
- PDFix SDK license credentials for automated remediation
- Docker Desktop and a Callas pdfToolbox license for the optional Callas font-repair stage

The core pipeline can still complete without Docker; it records why the
Callas-based font stage was skipped. Validation cannot run without Java and the
bundled veraPDF JAR.

## 1. Install uv and dependencies

On macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Windows may also require the latest [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).

From the repository root, create or refresh the environment:

```bash
uv sync --all-groups
```

## 2. Install Java

Install a current Java runtime and confirm it is on `PATH`:

```bash
java -version
```

The application invokes `lib/greenfield-apps-1.28.0.jar` for veraPDF WCAG and
PDF/UA validation. The portal disables submission when Java or the JAR is
missing because a run without validation cannot produce a trustworthy result.

## 3. Configure the PDFix license

Create or update `.env` in the repository root:

```dotenv
PDFIX_LICENSE_NAME="your-name"
PDFIX_LICENSE_KEY="your-key"
```

Check the configured license:

```bash
uv run license
```

The remediation pipeline passes these credentials to the PDFix SDK. Keep `.env`
out of version control and provide the same values through your secret manager
in hosted environments.

## 4. Configure optional font repair

Install Docker Desktop and make sure the Docker daemon is running. Bulk
workflows attempt to launch Docker Desktop automatically when it is installed
but not running.

Save the Callas pdfToolbox license configuration in:

```text
resources/font/.env
```

The pipeline uses Callas in Docker for its first font-repair pass and PDFix for
the missing-Unicode follow-up pass. Without Docker or the relevant license, the
single-PDF pipeline skips unavailable font stages and records that decision.

## 5. Check the environment

For command-line workflows:

```bash
uv run license
java -version
docker info
```

For the portal:

```bash
uv run web
```

Open <http://127.0.0.1:8000>. The environment banner checks Java, the veraPDF
JAR, repair configurations, Docker, disk readiness, and the PDFix and Callas
licenses. Detailed capability information is available to the signed-in user
at `GET /api/health`; `GET /readyz` is intended for readiness probes.

For the integration API:

```bash
uv run pdf-api
curl http://127.0.0.1:8100/healthz
curl http://127.0.0.1:8100/api/capabilities
```

## Choose a product function

- Continue to [bulk remediation](bulk-remediation.md) for archives and project workspaces.
- Continue to the [web portal](web-portal.md) for on-demand browser processing.
- Continue to the [API guide](api.md) for Python or HTTP integration.

## Storage overrides

Bulk projects default to `resources/projects`. Use an absolute path when the
working collection belongs on another disk:

```dotenv
PROJECT_BASE_PATH="/Volumes/ExternalDrive/pdf-remediation-projects"
```

The portal and API use temporary scratch space for intermediate files. Set
`PDF_SCRATCH_ROOT` to an appropriate local volume when the operating-system
temporary directory is too small. Portal job persistence can be moved with
`PDF_WEB_JOBS_ROOT`; see [Web portal configuration](web-portal.md#configuration-reference).
