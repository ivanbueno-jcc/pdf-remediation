# Web portal

The PDFix SDK Portal provides on-demand remediation in a browser. Users can
upload one or many PDFs, monitor each file independently, compare baseline and
final validation, and download the processed PDF with its reports.

The portal calls the shared `pdf_api` pipeline in-process; it does not shell out
to the bulk CLI and does not require a separate API deployment.

## Run locally

Complete the [shared setup](getting-started.md), then start the portal:

```bash
uv run web
```

Open <http://127.0.0.1:8000>. Useful development options are:

```text
--port <port>
--host <host>
--allow-remote
--reload
```

With no proxy configuration, the portal runs in single-user mode. It binds to
loopback, uses the local identity, and rejects non-loopback clients.

## User workflow

1. Drop or choose one or more PDFs.
2. Select a repair preset and, when needed, strict validation or font options.
3. Submit the collection. Each accepted PDF becomes its own job.
4. Watch queued, running, and completed states in the workspace.
5. Compare WCAG and UA1 before/after results.
6. Download the processed PDF, baseline report, final report, or ZIP bundle.

One submission accepts at most 200 files, 200 MiB per file, and 2 GiB total.
Invalid files are reported individually so one bad upload does not discard the
other accepted PDFs.

Each job receives a durable URL such as `/#job=<job-id>`. Refreshing or
bookmarking the URL reopens that job while its retained directory exists. Jobs
are stored under `resources/web-jobs` by default and expire after 72 hours.

## Outcomes and artifacts

The portal reports pipeline outcomes rather than internal workspace folder
names:

- `already_compliant`
- `remediated`
- `improved`
- `unchanged`
- `failed`
- `cancelled`

Expandable job details show the pipeline's actual stage list and the failing
veraPDF rule identifiers and descriptions for both the WCAG and UA1 profiles.
When both reports are present, the UI distinguishes resolved, persisting, and
new violations.

A running or queued job can be cancelled. The runner stops work at a safe
boundary, frees the queue slot, and retains whatever artifacts the pipeline can
honestly expose. Failed, cancelled, improved, or unchanged jobs can be retried
when a processed PDF is available.

## Queueing and capacity

The portal uses a bounded in-process worker pool:

- `PDF_WEB_MAX_CONCURRENT_JOBS` defaults to 4 jobs for the host.
- `PDF_WEB_MAX_RUNNING_JOBS_PER_USER` defaults to 4 running jobs per user.
- Further submissions wait in a fair queue; the UI shows how many jobs are ahead.
- Queue summaries reveal counts, never the owners of other jobs.
- `PDF_WEB_JOB_TIMEOUT_SECONDS` defaults to four hours per PDF.

Each pipeline run may hold a veraPDF JVM, a PDFix authorization, and optional
Docker work. Set concurrency according to CPU, memory, Docker capacity, and the
licensed PDFix width. The web process must run with one Uvicorn worker because
job state and the queue live in that process.

Every job uses an isolated directory and scratch area. Web jobs never touch
`resources/projects` and remove `PANTHEON_EMAIL` from the subprocess environment
so a browser upload cannot trigger a Terminus source download.

## Multi-user mode

The application delegates authentication to an authenticating reverse proxy
such as oauth2-proxy, Cloudflare Access, or Microsoft Entra Application Proxy.
The proxy terminates TLS and sign-in, then forwards a verified identity header.
The application does not implement passwords, OAuth flows, or browser sessions.

Start a shared deployment with proof that every request came through the
trusted proxy:

```bash
export PDF_WEB_PROXY_SECRET="$(openssl rand -hex 32)"
uv run web --host 0.0.0.0 --allow-remote
```

Configure the proxy to send the same secret in
`x-pdf-web-proxy-secret` and the signed-in identity in
`x-forwarded-email`, unless the corresponding header variables are changed.

### The proxy trust boundary

The identity header is trustworthy only when clients cannot set it themselves.
Configure one or both supported proofs:

- **Shared secret:** use when the proxy can inject a header that clients cannot
  supply, such as oauth2-proxy.
- **Trusted source IPs:** use when only known connector hosts can reach the
  application, such as Entra Application Proxy.

If both proofs are configured, both must pass. The server refuses a non-loopback
bind unless `--allow-remote` is supplied and at least one proof is configured.

Jobs are private to the submitting identity. Another user's job returns 404,
not 403, so identifiers cannot be probed for existence. Sharing a job URL does
not grant another user access.

Persisted jobs created before ownership was recorded are unreachable in
multi-user mode unless `PDF_WEB_LEGACY_JOB_OWNER` explicitly assigns them. In
single-user mode they are assigned to the local development identity. Startup
logs report how many unowned jobs were discovered.

## Diagnose a proxy deployment

Temporarily enable the redacted header diagnostic:

```bash
export PDF_WEB_HEADER_DIAGNOSTIC=1
```

`GET /api/proxy-headers` then reports the source address, checked identity
headers, whether a secret header arrived, and whether the request would
authenticate. Credential-bearing values are redacted. The route is deliberately
available before authentication so it can diagnose broken authentication;
disable it immediately after testing.

For a local multi-user exercise, use the development identity proxy. It asserts
whatever identity is supplied and must never be exposed as real authentication:

```bash
PDF_WEB_PROXY_SECRET=s3cret uv run web --port 8000

uv run python scripts/dev_identity_proxy.py alice@example.com \
  --port 8101 --secret s3cret

uv run python scripts/dev_identity_proxy.py bob@example.com \
  --port 8102 --secret s3cret
```

Open <http://127.0.0.1:8101> and <http://127.0.0.1:8102> in separate browser
windows. The proxy streams uploads and logs in both directions, but it
authenticates nobody.

## Health and readiness

| Endpoint | Authentication | Purpose |
|---|---|---|
| `GET /healthz` | None | Liveness and worker-thread state; returns 503 if the runner stopped |
| `GET /readyz` | None | Deployment readiness without dependency details |
| `GET /api/health` | Required | Detailed tools, licenses, disk, queue, user, and auth mode |
| `GET /api/proxy-headers` | None while enabled | Temporary, redacted proxy diagnostic |

The in-app environment banner uses detailed health data. Missing Java, veraPDF,
or configuration files blocks submission. Missing optional font capabilities
enables “Skip font repair” automatically so the remaining pipeline can run.

Production readiness is intentionally stricter: it checks PDFix, Docker, both
font images, the Callas license, writable job and scratch volumes, and scratch
free space in addition to the validation dependencies.

## Configuration reference

### Job execution and storage

| Variable | Default | Purpose |
|---|---|---|
| `PDF_WEB_JOBS_ROOT` | `resources/web-jobs` | Persisted job directories and artifacts |
| `PDF_SCRATCH_ROOT` | operating-system temp directory | Intermediate single-PDF pipeline data |
| `PDF_WEB_JOB_TTL_HOURS` | `72` | Retention window; `0` disables sweeping |
| `PDF_WEB_JOB_TIMEOUT_SECONDS` | `14400` | Wall-clock limit for one PDF |
| `PDF_WEB_MAX_CONCURRENT_JOBS` | `4` | Host-wide worker-pool width |
| `PDF_WEB_MAX_RUNNING_JOBS_PER_USER` | `4` | Per-user running-job cap |
| `PDF_WEB_MIN_READY_DISK_BYTES` | `1073741824` | Minimum scratch space for `/readyz` |

Uploads also require at least 5 GiB of free space before a new submission is
accepted.

### Identity and proxy trust

| Variable | Default | Purpose |
|---|---|---|
| `PDF_WEB_PROXY_SECRET` | unset | Secret proving the request passed through the proxy |
| `PDF_WEB_TRUSTED_PROXY_IPS` | unset | Comma-separated source addresses or CIDRs allowed to assert identity |
| `PDF_WEB_PROXY_SECRET_HEADER` | `x-pdf-web-proxy-secret` | Header carrying the shared secret |
| `PDF_WEB_IDENTITY_HEADER` | `x-forwarded-email` | Comma-separated identity headers; first usable value wins |
| `PDF_WEB_DEV_USER` | `local` | Identity used in single-user mode |
| `PDF_WEB_LEGACY_JOB_OWNER` | unset | Owner assigned to pre-ownership job directories |
| `PDF_WEB_HEADER_DIAGNOSTIC` | unset | Enables the temporary `/api/proxy-headers` route |

## Deployment

- [Deploy the portal to AWS](aws-web-deployment.md)
- [Deploy the portal to Azure](azure-web-deployment.md)
- [Publish through Microsoft Entra Application Proxy](deployment-entra-app-proxy.md)

All deployment models should keep the Uvicorn process count at one and scale
capacity through the configured job pool or separate, isolated instances with
their own job ownership and storage strategy.
