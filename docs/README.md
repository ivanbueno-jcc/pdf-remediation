# PDF Remediation Platform documentation

The platform exposes one remediation pipeline through three product functions:
bulk processing, an on-demand web portal, and a single-PDF integration API.

## Start here

| Guide | Audience | Contents |
|---|---|---|
| [Getting started](getting-started.md) | Everyone | Installation, Java, Docker, licenses, environment variables, and readiness checks |
| [Bulk remediation](bulk-remediation.md) | Operators and accessibility programs | Projects, orchestration, validation, routing, reports, fleet commands, and troubleshooting |
| [Web portal](web-portal.md) | Portal operators and administrators | Local use, authentication, privacy, queueing, retention, health checks, and configuration |
| [API and Python](api.md) | Integrators and developers | In-process use, asynchronous HTTP endpoints, options, statuses, artifacts, limits, and security |
| [Development reference](development.md) | Contributors and maintainers | Architecture, configuration assets, reports, tests, utilities, and implementation notes |

## Deployment guides

- [Deploy the web portal to AWS](aws-web-deployment.md)
- [Deploy the web portal to Azure](azure-web-deployment.md)
- [Publish the portal with Microsoft Entra Application Proxy](deployment-entra-app-proxy.md)

The AWS and Azure deployments run `pdf_web`; the single-PDF pipeline remains
an in-process library. Deploy `pdf_api` separately only when an integrated
system needs the HTTP surface, and place it behind authentication before
allowing remote access.

## Product overview

For the concise product story, screenshots, and quickstarts, return to the
[project README](../README.md).
