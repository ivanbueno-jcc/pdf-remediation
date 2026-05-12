# Azure PDF Remediation Web Service Requirements

## Purpose

This document defines requirements for a standalone Azure web service that lets authorized court staff upload PDFs, run automated PDF remediation as one job per PDF, and download validation reports plus remediated files.

The requirements are based on the architecture in `docs/azure-deployment-plan.md`.

## Users and Goals

### Primary Users

- Judicial Council Staff.
- Supreme Court Staff.
- Appellate Court Staff.
- Trial Court Staff.

### User Goal

Authorized staff need a secure, reliable, multi-user web service that creates one processing job per PDF, remediates each PDF for accessibility, validates the result, and returns downloadable artifacts that can be reviewed, shared, and retained according to policy.

### User Outcomes

- Staff can sign in with Azure Entra ID.
- Staff can upload PDFs directly to Azure Blob Storage without routing file bytes through the Container App.
- If staff select multiple PDFs, the service creates one independent job per file.
- Staff can select an approved remediation configuration, such as `default.json` or `default-slim.json`.
- Staff can monitor each job's progress.
- Staff can download:
  - A pre-remediation veraPDF validation report artifact for the job's PDF.
  - A remediated PDF artifact for the job's PDF.
  - A post-remediation veraPDF validation report artifact for the job's PDF.
- Staff can see which jobs succeeded, failed, or require manual follow-up.

## Functional Requirements

### Authentication and Authorization

- The service must require Azure Entra ID sign-in before any job, upload, status, or download operation.
- The service must associate each job with the Entra user or group that submitted it.
- A user must only access jobs and artifacts they are authorized to view.
- Admin access, if enabled, must be role-based and auditable.
- Download URLs must be short-lived and issued only after authorization checks.

### Job Creation and Direct Upload

- The user must be able to create a job draft for one PDF and choose an allowlisted remediation configuration.
- If the user selects multiple PDFs, the web API must create one independent job draft per PDF.
- The web API must reject config names that are not explicitly allowlisted.
- The web API must create short-lived, job-scoped Blob upload URLs or equivalent direct-upload credentials.
- The browser must upload PDFs directly to Blob Storage under the job input prefix.
- PDF bytes must not pass through the public Container App.
- The browser must finalize each upload session by sending the file name, blob path, size, and checksum to the web API.
- The web API must verify each expected blob exists before submitting its job for processing.

### Processing Behavior

- Each job's uploaded PDF must receive a pre-remediation veraPDF validation pass.
- The selected base PDFix remediation configuration must run for the job's PDF.
- Optional Callas, PDFix font-fix, and targeted PDFix stages must run when configured validation clause matches require them.
- The job's final candidate PDF must receive a post-remediation veraPDF validation pass.
- Validation work must not consume remediation slots.
- Remediation work must be globally limited to 4 concurrent processes total across:
  - `pdf_worker.solo_fix`
  - `pdf_worker.solo_fix_target`
  - `pdf_worker.solo_font_callas`
  - `pdf_worker.solo_font_pdfix`

### Worker Contracts

- Worker scripts must process exactly one PDF per invocation.
- Worker scripts must accept local input and output paths.
- Worker scripts must emit machine-readable JSON.
- Worker scripts must write operational logs to stderr.
- Worker scripts must use non-zero exit codes for operational failures.
- Missing worker wrappers for `solo_fix_target`, `solo_font_callas`, and `solo_font_pdfix` must be implemented before Azure deployment.

### Job Status and Results

- The user must see job-level status, including queued, running, completed, completed with errors, and failed states.
- Because each job represents one PDF, the job detail page must show stage-level status for that PDF.
- The service must show operational errors in user-readable language without exposing secrets or internal stack traces.
- The final job must provide three downloadable artifact types for its PDF:
  - A pre-remediation validation report artifact.
  - A remediated PDF artifact.
  - A post-remediation validation report artifact.
- The job must also produce `manifest.json` with stage status, output path, report paths, and errors.

### Operations and Auditability

- The service must record structured logs with `jobId`, `stage`, `attempt`, `workerKind`, and `correlationId`.
- Remediation workers must also log `slotId`.
- The system must expose operational metrics for queue depth, dead-letter count, worker failures, job duration, and job artifact publication failures.
- Secrets must be stored in Azure Key Vault.
- Service access to Blob Storage, Table Storage, Service Bus, Key Vault, and Azure Container Registry must use managed identity and Azure RBAC.

## Expected Behavior

1. A signed-in staff user starts a new job.
2. The user selects `default.json`, `default-slim.json`, or another approved config.
3. The service creates one job and upload target per selected PDF.
4. The browser uploads each file directly to Blob Storage.
5. The service confirms each upload and creates job metadata for each PDF.
6. The dispatcher starts pre-remediation validation for each job.
7. The validation workers produce veraPDF reports for each job.
8. The dispatcher queues remediation stages.
9. Remediation workers acquire one of the four shared remediation slots before starting PDFix, Callas, or PDFix font-fix work.
10. No more than four remediation processes run at the same time across all remediation worker types.
11. Validation workers may continue running independently while remediation slots are full.
12. The dispatcher queues post-remediation validation for the job's final candidate.
13. The dispatcher writes the manifest with the job's pre-validation report, remediated PDF, and post-validation report artifact paths.
14. The user downloads the job's artifacts from the job page.
15. Jobs that fail processing show the failure state and any available report artifacts without blocking other jobs.

## Azure Resources and Quantity

Baseline resources per Azure environment:

| Azure resource | Quantity | Requirement supported |
| --- | ---: | --- |
| Azure Entra ID app registration | 1 | Staff sign-in and role-based access. |
| Azure Container Apps environment | 1 | Hosts the public app and background processing jobs. |
| Public web/API Container App | 1 | Job creation, direct-upload setup, job status, and downloads. |
| Dispatcher job definition | 1 | Moves each PDF job through the workflow. |
| Validation job definition | 1 | Runs pre-remediation and post-remediation validation. |
| Remediation job definitions | 4 | Runs PDFix, targeted PDFix, Callas font, and PDFix font-fix processing. |
| Azure Service Bus namespace | 1 | Supports queue-driven processing and job orchestration. |
| Service Bus queues | 7 | Separates dispatcher, validation, remediation, and slot-control work. |
| Remediation slot messages | 4 | Enforces the shared four-process remediation limit. |
| Azure Storage account | 1 | Stores PDF files, reports, manifests, and job metadata. |
| Blob container | 1 | Stores uploaded PDFs, outputs, reports, and manifests. |
| Table Storage tables | 2 | Stores job records and stage history. |
| Azure Key Vault | 1 | Stores licenses and secrets. |
| Azure Container Registry | 1 | Stores application and worker container images. |
| Log Analytics workspace | 1 | Centralized logs and operational metrics. |
| Application Insights resource | 1 | Application and workflow telemetry. |
| Managed identities | 1 or more | Secure access to Azure resources without stored credentials. |

## Azure Monthly Cost

These are planning estimates for one production Azure environment. They are not a Microsoft quote. Re-price before procurement using the Azure Pricing Calculator, the Azure Retail Prices API, the selected Azure region, and any government, enterprise, reservation, or savings-plan rates available to the Judicial Council.

### Cost Model Assumptions

| Cost driver | Planning assumption |
| --- | --- |
| Monthly volume scenarios | 5,000 PDFs, 10,000 PDFs, and 20,000 PDFs per month. |
| Azure region and currency | Commercial Azure, West US 2, USD pay-as-you-go retail pricing. |
| Job model | One job per PDF. |
| Container Apps plan | Consumption plan. Worker jobs scale to zero between executions. |
| Public web/API baseline | One always-available 0.5 vCPU / 1 GiB replica for 730 hours per month. |
| Validation compute per PDF | Two veraPDF validation passes at 120 seconds each, 1 vCPU, and 2 GiB. |
| Remediation compute per PDF | Weighted average of 72 seconds of remediation work at 2 vCPU and 4 GiB. This assumes 60 seconds of base PDFix remediation for every PDF, plus 30 seconds of Callas and 30 seconds of PDFix font-fix work on 20% of PDFs. |
| Average stored footprint per PDF | 60 MB retained for 30 days, including input PDF, remediated PDF, pre-validation report, post-validation report, manifest, and working metadata. |
| Average downloaded footprint per PDF | 30 MB of user downloads per completed job, assuming each PDF's three artifacts are downloaded once. |
| Blob transactions per PDF | 30 writes, 20 reads, and 2 list/create operations. |
| Table Storage transactions per PDF | 100 table operations and 0.002 GB of metadata. |
| Service Bus usage | Standard tier with fewer than 13 million monthly messaging operations. |
| Log Analytics ingestion | 1 MB of billable logs per PDF. |
| Container Registry | Standard ACR with 20 GB of image storage. |
| Excluded from estimate | PDFix and Callas licenses, Azure Support, taxes, private endpoints, NAT Gateway, Azure Firewall, Application Gateway/WAF, Defender plans, disaster recovery replicas, and staff labor. |

### Azure Rates Used

Rates were checked against the Azure Retail Prices API on April 30, 2026, using West US 2 meters where region-specific pricing applies.

| Azure service | Meter used | Rate used |
| --- | --- | ---: |
| Azure Container Apps | Standard vCPU Active Usage | $0.000034 per vCPU-second |
| Azure Container Apps | Standard Memory Active Usage | $0.000004 per GiB-second |
| Azure Container Apps | Standard vCPU Idle Usage | $0.000004 per vCPU-second |
| Azure Container Apps | Standard Memory Idle Usage | $0.000004 per GiB-second |
| Azure Container Apps | Monthly free grant | First 180,000 vCPU-seconds and 360,000 GiB-seconds |
| Azure Container Apps | Standard Requests | First 2 million requests included, then $0.40 per 1 million requests |
| Azure Service Bus | Standard Base Unit | $10.00 per month |
| Azure Service Bus | Standard Messaging Operations | First 13 million operations included |
| Azure Blob Storage | Hot LRS Data Stored | $0.0184 per GB-month |
| Azure Blob Storage | Hot LRS Write Operations | $0.05 per 10,000 operations |
| Azure Blob Storage | Hot Read Operations | $0.004 per 10,000 operations |
| Azure Blob Storage | LRS List and Create Container Operations | $0.05 per 10,000 operations |
| Azure Table Storage | Standard LRS Data Stored | $0.045 per GB-month |
| Azure Table Storage | Standard LRS operations | $0.00036 per 10,000 operations |
| Azure Key Vault | Standard operations | $0.03 per 10,000 operations |
| Azure Container Registry | Standard Registry Unit | $0.6666 per day |
| Azure Container Registry | Data Stored | $0.10 per GB-month |
| Log Analytics | Analytics Logs Data Ingestion | First 5 GB included, then $2.30 per GB |
| Azure Bandwidth | Standard Data Transfer Out | First 100 GB included, then $0.087 per GB |
| Azure Monitor | Alert resources | $0.10 per monitored resource per month; estimate assumes 10 alert resources |

Pricing source: [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices), [Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/), and [Azure Monitor Logs cost guidance](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/cost-logs).

### Estimated Monthly Cost

| Monthly PDFs | Container Apps compute | Storage and transactions | Fixed platform services | Observability | Download egress | Estimated monthly Azure cost | Estimated cost per PDF |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5,000 | $89 | $7 | $33 | $0 | $4 | $135 | $0.027 |
| 10,000 | $169 | $14 | $33 | $12 | $17 | $250 | $0.025 |
| 20,000 | $331 | $27 | $33 | $35 | $44 | $470 | $0.023 |

Fixed platform services include Service Bus Standard, ACR Standard with 20 GB image storage, Key Vault operations, and 10 Azure Monitor alert resources. The public web/API idle replica is included in the Container Apps compute column.

### Show Your Work

Container Apps active worker usage per PDF:

```text
validation vCPU-seconds = 2 validation passes * 120 seconds * 1 vCPU = 240
validation GiB-seconds = 2 validation passes * 120 seconds * 2 GiB = 480
base PDFix remediation = 60 seconds * 100% of PDFs = 60 weighted seconds
Callas font work = 30 seconds * 20% of PDFs = 6 weighted seconds
PDFix font-fix work = 30 seconds * 20% of PDFs = 6 weighted seconds
weighted remediation seconds per PDF = 60 + 6 + 6 = 72
remediation vCPU-seconds = 72 seconds * 2 vCPU = 144
remediation GiB-seconds = 72 seconds * 4 GiB = 288
total active worker vCPU-seconds per PDF = 384
total active worker GiB-seconds per PDF = 768
```

Container Apps monthly worker compute:

```text
vCPU charge = max(PDF count * 384 - 180,000 free vCPU-seconds, 0) * $0.000034
memory charge = max(PDF count * 768 - 360,000 free GiB-seconds, 0) * $0.000004
web/API idle charge =
  (0.5 vCPU * 730 hours * 3,600 seconds * $0.000004)
  + (1 GiB * 730 hours * 3,600 seconds * $0.000004)
  = $15.77 per month
Container Apps estimate = vCPU charge + memory charge + web/API idle charge
```

Storage and transaction estimate:

```text
Blob storage = PDF count * 0.06 GB * $0.0184
Blob writes = PDF count * 30 / 10,000 * $0.05
Blob reads = PDF count * 20 / 10,000 * $0.004
Blob list/create = PDF count * 2 / 10,000 * $0.05
Table Storage = (PDF count * 0.002 GB * $0.045) + (PDF count * 100 / 10,000 * $0.00036)
```

Observability, egress, and fixed service estimate:

```text
Log Analytics = max(PDF count * 0.001 GB - 5 GB free, 0) * $2.30
Download egress = max(PDF count * 0.03 GB - 100 GB free, 0) * $0.087
Service Bus Standard = $10.00 per month, assuming operations stay below included 13 million
ACR Standard = (30 days * $0.6666) + (20 GB * $0.10) = $22.00
Key Vault = 50,000 operations / 10,000 * $0.03 = $0.15
Azure Monitor alerts = 10 alert resources * $0.10 = $1.00
```

Scenario calculations:

| Monthly PDFs | Worker vCPU charge | Worker memory charge | Web/API idle | Blob and Table Storage | Logs | Egress | Fixed services | Total |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5,000 | $59.16 | $13.92 | $15.77 | $6.83 | $0.00 | $4.35 | $33.15 | $133.17 |
| 10,000 | $124.44 | $29.28 | $15.77 | $13.66 | $11.50 | $17.40 | $33.15 | $245.19 |
| 20,000 | $255.00 | $60.00 | $15.77 | $27.31 | $34.50 | $43.50 | $33.15 | $469.23 |

### Remediation Slot Capacity Check

The estimate assumes a weighted average of 1.2 remediation minutes per PDF. Because the architecture allows only four remediation processes at once, monthly remediation capacity is:

```text
monthly remediation slot capacity = 4 slots * 730 hours = 2,920 slot-hours
slot-hours needed = PDF count * 1.2 minutes / 60
```

| Monthly PDFs | Slot-hours needed | Percent of 4-slot monthly capacity |
| ---: | ---: | ---: |
| 5,000 | 100 | 3% |
| 10,000 | 200 | 7% |
| 20,000 | 400 | 14% |

At 20,000 PDFs per month, the 4-slot limit can support an average remediation duration up to about 8.76 minutes per PDF if remediation runs continuously across the month. If pilot data shows the average remediation duration is closer to 10 minutes per PDF, the team must either reduce volume, extend the processing window, optimize workers, or revisit the four-process limit.

## Edge Cases to Identify Early

### Authentication and Access

- User signs out or token expires during upload.
- User tries to finalize or download another user's job.
- User is removed from an Entra group while a job is running.
- Admin role assignment is missing or misconfigured.

### Uploads

- Upload URL expires before upload completes.
- Browser upload is interrupted.
- Uploaded blob is missing at finalize time.
- Uploaded blob size or checksum does not match the finalize request.
- Duplicate filenames are selected in the same browser batch and must still map to separate jobs.
- File extension is `.pdf` but content is not a valid PDF.
- PDF is zero bytes.
- PDF exceeds the agreed maximum file size or page count.
- User selects more PDFs than the system allows in one bulk submission.
- A finalize request attempts to attach more than one PDF to a single job.

### Validation

- Java or the veraPDF JAR is missing from the validation image.
- veraPDF returns an operational error instead of pass or fail.
- Validation generates XML but summary report generation fails.
- Validation reports are large enough to affect storage, download, or manifest generation time.

### Remediation

- PDFix license is missing, expired, or rejected.
- Callas license is missing, expired, or rejected.
- PDFix cannot open the PDF.
- PDFix hangs and must be killed by timeout handling.
- Callas or PDFix font-fix command fails.
- A file passes WCAG but fails UA1, or the reverse.
- Optional target mappings reference missing config files.
- A remediation stage produces no output file.

### Queueing and Concurrency

- A worker crashes after acquiring a remediation slot.
- A worker completes work but crashes before Service Bus settlement.
- A slot message is duplicated, lost, or not replaced.
- A poison message reaches maximum delivery count and moves to DLQ.
- A duplicate message is delivered after a stage already completed.
- Validation queue spikes while all remediation slots are occupied.

### Artifacts and Retention

- Per-job artifact publication fails after processing completes.
- A bulk submission creates some jobs that succeed and some jobs that fail.
- Artifact download URL expires during download.
- Retention policy deletes inputs or outputs earlier than expected.
- Manifest and Table Storage disagree after a retry.

## Assumptions to Confirm Before Development Starts

- Confirm the Entra tenant, app registration ownership, and whether access is single-tenant, multi-tenant, or B2B.
- Confirm user roles: submitter, viewer, admin, and operations support.
- Confirm whether job visibility is user-owned, group-owned, court-owned, or admin-only.
- Confirm allowed remediation configs for launch, including `default.json` and `default-slim.json`.
- Confirm maximum PDFs per bulk submission.
- Confirm maximum PDF file size and maximum total job size.
- Confirm whether duplicate filenames should be preserved, renamed, or rejected.
- Confirm whether final pass requires WCAG only or both WCAG and UA1.
- Confirm retention periods for inputs, work files, per-job report and PDF artifacts, logs, and failed jobs.
- Confirm whether direct upload uses user delegation SAS, service-generated SAS, or another approved Azure pattern.
- Confirm Service Bus tier. Standard or Premium is required for the planned transaction pattern.
- Confirm deployment environments, regions, private networking expectations, and disaster recovery needs.
- Confirm PDFix and Callas license terms for Azure-hosted container workers.
- Confirm whether Callas and PDFix font-fix worker images can be built without Docker-in-Docker.
- Confirm expected support process for files that fail remediation or validation.
- Confirm accessibility, branding, and language requirements for the web UI.

## Acceptance Criteria and Requirement Traceability

| Original Requirement | Acceptance Test |
| --- | --- |
| Azure Entra ID sign-in | Unauthenticated users cannot access the app; authenticated users can create jobs. |
| One job per PDF | Each uploaded PDF creates exactly one independent processing job. |
| Multiple PDFs selected | A multi-file browser selection creates multiple independent jobs, one per selected PDF. |
| Select remediation config | `default.json` and `default-slim.json` are accepted; non-allowlisted config names are rejected. |
| Pre-remediation validation report | Completed job includes a pre-remediation validation report artifact for its PDF. |
| Remediated PDF | Completed job includes a remediated PDF artifact when processing succeeds and a manifest error when it fails. |
| Post-remediation validation report | Completed job includes a post-remediation validation report artifact for its final candidate PDF. |
| Shared limit of 4 remediation processes | Load test proves no more than 4 total PDFix, Callas, and PDFix font-fix processes run concurrently. |
| Uncapped validation capacity | Validation workers can scale independently and continue while all remediation slots are occupied. |
| Direct Blob upload | Network and application logs show PDF bytes upload directly from browser to Blob Storage, not through `web-api`. |
| Queue-driven orchestration | Submitted jobs move through dispatcher and worker queues without manual intervention. |
| Failure tolerance | Worker crash, duplicate message, and DLQ scenarios produce recoverable or visible failure states. |
| Secure artifact access | A user cannot download another user's artifacts; authorized downloads use short-lived URLs. |

## Epics, Tasks, and Schedule

This schedule assumes a small implementation team and a first production-ready release target of 10 weeks. The team should confirm staffing, procurement, security review, and environment lead times before committing dates.

| Week | Epic | Tasks | Exit Criteria |
| --- | --- | --- | --- |
| 1 | Requirements and Architecture Confirmation | Review this requirements document with stakeholders; confirm assumptions; finalize user roles, retention, config allowlist, upload limits, and validation pass criteria. | Signed-off requirements and confirmed launch assumptions. |
| 2 | Azure Foundation | Provision dev Azure resources; create Container Apps environment, Storage, Service Bus, Key Vault, ACR, Log Analytics, and managed identities; define RBAC. | Infrastructure deploys repeatably and identities can access required resources. |
| 3 | Web API and Entra ID | Implement Entra ID auth, user identity extraction, role checks, job draft APIs, config allowlist API, and job status APIs. | Signed-in users can create and view authorized job drafts. |
| 4 | Direct Blob Upload | Implement one-PDF job upload sessions, direct browser-to-Blob upload, finalize endpoint, blob existence checks, size/checksum validation, and duplicate filename handling across jobs. | Each selected PDF uploads directly to Blob and produces one verified job record. |
| 5 | Worker Script Readiness | Implement missing `pdf_worker` wrappers; standardize JSON outputs and exit codes; add local contract tests for all one-PDF worker scripts. | All five worker contracts pass local tests. |
| 6 | Queue Runners and Dispatcher Core | Implement Service Bus message schemas, dispatcher state machine, validation queue runner, Table Storage updates, idempotent message handling, and DLQ handling. | A job can run through pre-validation in Azure dev. |
| 7 | Remediation Workers and Slot Gate | Implement PDFix, target, Callas, and PDFix-font worker runners; implement `remediation-slots` acquisition, lock renewal, transactional release, and crash behavior. | Load test confirms the 4-process global remediation limit. |
| 8 | Artifacts and UI Workflow | Implement post-validation, per-job artifact publication, `manifest.json`, download authorization, and job detail UI. | A user can submit a job and download that PDF's required artifacts. |
| 9 | Observability, Security, and Resilience | Add dashboards, alerts, structured logs, secret validation, retry policies, timeout handling, and failure-state UX. Run security and privacy review. | Operational scenarios are visible, auditable, and documented. |
| 10 | End-to-End Testing and UAT | Run acceptance tests against original requirements; perform multi-user testing, worker crash tests, DLQ tests, artifact verification, and stakeholder UAT. | Release candidate meets all acceptance criteria or has approved exceptions. |

## Detailed Epic Task List

### Epic 1: Requirements and Governance

- Confirm stakeholder goals for Judicial Council, Supreme Court, Appellate Court, and Trial Court staff.
- Confirm role model and ownership model.
- Confirm retention, privacy, and audit requirements.
- Confirm support workflow for failed PDFs.

### Epic 2: Infrastructure

- Create infrastructure-as-code for Azure resources.
- Configure managed identities and RBAC.
- Configure Service Bus queues and DLQs.
- Seed `remediation-slots` with four slot messages.
- Configure Blob lifecycle policies.

### Epic 3: Web Application

- Implement Entra ID sign-in.
- Implement job draft creation.
- Implement direct upload session APIs.
- Implement upload finalization.
- Implement job status and artifact download APIs.
- Implement user-facing job list and job detail views.

### Epic 4: Worker Platform

- Add missing one-PDF worker wrappers.
- Build validation and remediation worker images.
- Implement queue runners.
- Implement scratch file handling.
- Implement blob download/upload logic.
- Implement per-stage Table Storage updates.

### Epic 5: Orchestration

- Implement dispatcher state transitions.
- Fan out pre-validation work.
- Decide remediation stages from validation results.
- Fan out remediation and post-validation work.
- Handle duplicate completion events.
- Mark jobs completed, completed with errors, or failed.

### Epic 6: Concurrency Control

- Implement remediation slot acquisition.
- Renew work and slot locks during processing.
- Release and replace slots transactionally.
- Handle crashes, lock expiry, and retries.
- Add tests proving no more than four remediation processes run globally.

### Epic 7: Reporting and Artifacts

- Upload pre-validation reports.
- Upload post-validation reports.
- Publish per-job report and PDF artifacts.
- Create `manifest.json`.
- Authorize and issue short-lived download URLs.

### Epic 8: Testing and Release

- Add worker contract tests.
- Add API integration tests.
- Add dispatcher and queue idempotency tests.
- Add end-to-end job tests.
- Add security and authorization tests.
- Run UAT with representative court staff.

## Final Feature Test Against Original Requirement

Before release, run one complete test using representative PDFs from each target user group:

1. Sign in as an authorized staff user.
2. Select multiple PDFs and confirm the service creates one independent job per PDF.
3. Select `default.json`.
4. Upload each PDF directly to Blob Storage.
5. Finalize each upload session.
6. Confirm each job starts without PDF bytes passing through the Container App.
7. Confirm pre-remediation validation runs for each job.
8. Confirm remediation stages run with no more than four total active remediation processes.
9. Confirm validation workers continue scaling independently.
10. Confirm post-remediation validation runs for each job.
11. Download one job's pre-remediation validation report artifact.
12. Download the same job's remediated PDF artifact.
13. Download the same job's post-remediation validation report artifact.
14. Review `manifest.json` for the job.
15. Attempt cross-user artifact access and confirm it is blocked.
16. Confirm logs and metrics contain the expected correlation fields.

The feature is ready only when this final test passes or any exceptions are explicitly accepted by stakeholders.
