# syntax=docker/dockerfile:1.7
FROM docker:28.5.1-cli AS docker-cli
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv

FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PDF_WEB_JOBS_ROOT=/mnt/pdf-data/web \
    PDF_SCRATCH_ROOT=/srv/pdf-remediation/scratch

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        default-jre-headless \
        fontconfig \
        libgl1 \
        libgomp1 \
        libx11-6 \
        libxcb1 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 pdfweb \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin pdfweb \
    && mkdir -p /app /mnt/pdf-data/web /srv/pdf-remediation/scratch \
    && chown -R pdfweb:pdfweb /app /mnt/pdf-data /srv/pdf-remediation

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

COPY --chown=pdfweb:pdfweb pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=pdfweb:pdfweb src ./src
COPY --chown=pdfweb:pdfweb resources/configuration ./resources/configuration
COPY --chown=pdfweb:pdfweb lib/greenfield-apps-1.28.0.jar ./lib/greenfield-apps-1.28.0.jar
COPY --chown=pdfweb:pdfweb deploy/azure/compose.yaml deploy/azure/Caddyfile /opt/pdf-remediation-deploy/
RUN uv sync --frozen --no-dev

USER 10001:10001
EXPOSE 8000

CMD ["python", "-m", "pdf_web", "--host", "0.0.0.0", "--port", "8000", "--allow-remote"]
