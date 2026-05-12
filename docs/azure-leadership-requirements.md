# PDF Remediation Web Service: Requirements

## Summary

Build a secure Azure web service that allows authorized court staff to upload PDFs, automatically remediate them for accessibility, validate the results, and download the completed files and reports. Each PDF is handled as its own job, which keeps status, errors, and downloads simple to understand.

## Goals

- Provide secure Azure Entra ID sign-in for authorized staff.
- Let staff upload PDFs directly and safely.
- Create one processing job per PDF.
- Allow staff to choose an approved remediation configuration.
- Return three downloadable results for each PDF:
  - Pre-remediation validation report.
  - Remediated PDF.
  - Post-remediation validation report.
- Keep remediation capacity controlled with a shared limit of four active remediation processes.
- Let validation work scale separately so reports can be generated quickly.
- Show clear job status, success, failure, and follow-up needs.

## Users

- Judicial Council Staff.
- Supreme Court Staff.
- Appellate Court Staff.
- Trial Court Staff.

## Simplified Flow

1. Staff sign in with Azure Entra ID.
2. Staff select one or more PDFs and choose an approved remediation configuration.
3. The system creates one job for each PDF.
4. PDFs upload directly to Azure storage.
5. The service validates each original PDF.
6. The service remediates each PDF using the selected configuration.
7. The service validates the remediated PDF.
8. Staff download the pre-validation report, remediated PDF, and post-validation report for each job.
9. Any failed job clearly shows what happened and whether manual follow-up is needed.

## Azure Resources and Quantity

Baseline resources per Azure environment:

| Azure resource | Quantity | Leadership purpose |
| --- | ---: | --- |
| Azure sign-in application | 1 | Secure staff access. |
| Azure web application | 1 | Staff portal for jobs, uploads, status, and downloads. |
| Azure background processing jobs | 6 | One dispatcher, one validation worker, and four remediation worker types. |
| Azure queue service | 1 | Coordinates work without tying processing to the web app. |
| Work queues | 7 | Separates validation, remediation, orchestration, and capacity control. |
| Remediation capacity slots | 4 | Limits active remediation work to four processes. |
| Azure storage account | 1 | Stores PDFs, reports, remediated files, manifests, and job history. |
| Azure secrets vault | 1 | Protects licenses and service secrets. |
| Azure container registry | 1 | Stores application and worker images. |
| Azure monitoring workspace | 1 | Provides logs, alerts, and operational visibility. |

## Schedule

| Phase | Timeline | Outcome |
| --- | --- | --- |
| Requirements confirmation | Week 1 | Confirm users, roles, retention, approved configurations, upload limits, and success criteria. |
| Azure foundation | Week 2 | Establish secure Azure environment, identity, storage, queues, secrets, and monitoring. |
| Web experience | Weeks 3-4 | Staff can sign in, create jobs, upload PDFs directly, and view job status. |
| Processing pipeline | Weeks 5-7 | Validation, remediation, worker orchestration, and the four-process remediation limit are operational. |
| Results and reporting | Week 8 | Staff can download the three required artifacts for each PDF. |
| Security, monitoring, and resilience | Week 9 | Alerts, logs, retry handling, and operational support paths are ready. |
| User acceptance and launch readiness | Week 10 | End-to-end testing with representative court users confirms readiness or identifies approved exceptions. |

## Success Criteria

- Authorized staff can process PDFs without technical assistance.
- Each PDF has its own trackable job.
- Each completed job returns the required reports and remediated PDF.
- Users cannot access another user's jobs or downloads unless explicitly authorized.
- The service remains stable when many PDFs are submitted.
- Failures are visible, explainable, and do not block unrelated jobs.
