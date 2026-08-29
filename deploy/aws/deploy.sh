#!/usr/bin/env bash
set -euo pipefail

for argument in "$@"; do
  case "$argument" in
    ACCESS_POINT_ID=*) ACCESS_POINT_ID="${argument#*=}" ;;
    APP_IMAGE=*) APP_IMAGE="${argument#*=}" ;;
    AWS_REGION=*) AWS_REGION="${argument#*=}" ;;
    FILE_SYSTEM_ID=*) FILE_SYSTEM_ID="${argument#*=}" ;;
    SECRET_ARN=*) SECRET_ARN="${argument#*=}" ;;
    WEB_HOSTNAME=*) WEB_HOSTNAME="${argument#*=}" ;;
    PDF_CALLAS_FONT_IMAGE=*) PDF_CALLAS_FONT_IMAGE="${argument#*=}" ;;
    PDF_PDFIX_FONT_IMAGE=*) PDF_PDFIX_FONT_IMAGE="${argument#*=}" ;;
    PDF_WEB_MAX_CONCURRENT_JOBS=*) PDF_WEB_MAX_CONCURRENT_JOBS="${argument#*=}" ;;
    *) echo "Unknown deployment parameter: ${argument%%=*}" >&2; exit 2 ;;
  esac
done

: "${ACCESS_POINT_ID:?ACCESS_POINT_ID is required}"
: "${APP_IMAGE:?APP_IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${FILE_SYSTEM_ID:?FILE_SYSTEM_ID is required}"
: "${SECRET_ARN:?SECRET_ARN is required}"
: "${WEB_HOSTNAME:?WEB_HOSTNAME is required}"

PDF_CALLAS_FONT_IMAGE="${PDF_CALLAS_FONT_IMAGE:-pdfix/font-fix-callas:v1.0.11}"
PDF_PDFIX_FONT_IMAGE="${PDF_PDFIX_FONT_IMAGE:-pdfix/font-fix-pdfix:v1.0.9}"
PDF_WEB_MAX_CONCURRENT_JOBS="${PDF_WEB_MAX_CONCURRENT_JOBS:-4}"

DEPLOY_DIR=/opt/pdf-remediation-aws
DATA_ROOT=/mnt/pdf-data
SCRATCH_ROOT=/srv/pdf-remediation/scratch
ENV_FILE="$DEPLOY_DIR/.env"

retry() {
  local attempts=0
  until "$@"; do
    attempts=$((attempts + 1))
    if (( attempts >= 60 )); then
      return 1
    fi
    sleep 5
  done
}

registry="${APP_IMAGE%%/*}"
retry aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$registry" >/dev/null

install -d -m 0755 "$DATA_ROOT" "$SCRATCH_ROOT" "$DEPLOY_DIR"
fstab_entry="$FILE_SYSTEM_ID:/ $DATA_ROOT efs _netdev,noresvport,tls,iam,accesspoint=$ACCESS_POINT_ID 0 0"
if ! grep -Fq "$FILE_SYSTEM_ID:/ $DATA_ROOT " /etc/fstab; then
  printf '%s\n' "$fstab_entry" >> /etc/fstab
fi
if ! mountpoint -q "$DATA_ROOT"; then
  retry mount "$DATA_ROOT"
fi

install -d -o 10001 -g 10001 -m 0770 \
  "$DATA_ROOT/web" "$DATA_ROOT/caddy/data" "$DATA_ROOT/caddy/config" \
  "$SCRATCH_ROOT"

docker pull "$APP_IMAGE" >/dev/null
asset_container="pdf-remediation-aws-assets-$$"
docker create --name "$asset_container" "$APP_IMAGE" >/dev/null
trap 'docker rm -f "$asset_container" >/dev/null 2>&1 || true' EXIT
docker cp "$asset_container:/opt/pdf-remediation-deploy/aws/." "$DEPLOY_DIR/"
docker rm "$asset_container" >/dev/null
trap - EXIT

secret_json="$(retry aws secretsmanager get-secret-value \
  --region "$AWS_REGION" --secret-id "$SECRET_ARN" \
  --query SecretString --output text)"

required_secret_keys=(
  ACME_EMAIL
  COGNITO_CLIENT_ID
  COGNITO_CLIENT_SECRET
  COGNITO_ISSUER_URL
  COGNITO_LOGOUT_URL
  ENV_CALLAS_LICENSE
  ENV_CALLAS_SECRET
  OAUTH2_PROXY_COOKIE_SECRET
  PDFIX_LICENSE_KEY
  PDFIX_LICENSE_NAME
  PDF_WEB_PROXY_SECRET
)
for name in "${required_secret_keys[@]}"; do
  value="$(jq -r --arg name "$name" '.[$name] // empty' <<<"$secret_json")"
  if [[ -z "$value" || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Secrets Manager value $name is missing or invalid." >&2
    exit 2
  fi
done

previous_image=""
if [[ -f "$ENV_FILE" ]]; then
  previous_image="$(sed -n 's/^APP_IMAGE=//p' "$ENV_FILE" | head -n 1)"
fi

umask 077
temporary_env="$(mktemp "$DEPLOY_DIR/.env.XXXXXX")"
{
  printf 'APP_IMAGE=%s\n' "$APP_IMAGE"
  printf 'DOCKER_GID=%s\n' "$(stat -c '%g' /var/run/docker.sock)"
  printf 'PDF_CALLAS_FONT_IMAGE=%s\n' "$PDF_CALLAS_FONT_IMAGE"
  printf 'PDF_PDFIX_FONT_IMAGE=%s\n' "$PDF_PDFIX_FONT_IMAGE"
  printf 'PDF_WEB_MAX_CONCURRENT_JOBS=%s\n' "$PDF_WEB_MAX_CONCURRENT_JOBS"
  printf 'WEB_HOSTNAME=%s\n' "$WEB_HOSTNAME"
  jq -r '
    to_entries
    | sort_by(.key)[]
    | select(.key | IN(
        "ACME_EMAIL", "COGNITO_CLIENT_ID", "COGNITO_CLIENT_SECRET",
        "COGNITO_ISSUER_URL", "COGNITO_LOGOUT_URL", "ENV_CALLAS_LICENSE",
        "ENV_CALLAS_SECRET", "OAUTH2_PROXY_COOKIE_SECRET", "PDFIX_LICENSE_KEY",
        "PDFIX_LICENSE_NAME", "PDF_WEB_PROXY_SECRET"
      ))
    | "\(.key)=\(.value | @sh)"
  ' <<<"$secret_json"
} > "$temporary_env"
chmod 0600 "$temporary_env"
mv "$temporary_env" "$ENV_FILE"
unset secret_json value

docker pull "$PDF_CALLAS_FONT_IMAGE" >/dev/null
docker pull "$PDF_PDFIX_FONT_IMAGE" >/dev/null

cd "$DEPLOY_DIR"

rollback() {
  docker compose --env-file "$ENV_FILE" logs --tail 100 >&2 || true
  if [[ -n "$previous_image" && "$previous_image" != "$APP_IMAGE" ]]; then
    sed -i "s|^APP_IMAGE=.*$|APP_IMAGE=$previous_image|" "$ENV_FILE"
    docker compose --env-file "$ENV_FILE" up -d --remove-orphans
    echo "AWS deployment failed health checks; restored $previous_image." >&2
  fi
  exit 1
}

if ! docker compose --env-file "$ENV_FILE" config --quiet; then
  rollback
fi
if ! docker compose --env-file "$ENV_FILE" up -d --remove-orphans; then
  rollback
fi

healthy=false
for _ in $(seq 1 30); do
  running_services="$(docker compose --env-file "$ENV_FILE" ps \
    --services --status running | wc -l | tr -d ' ')"
  if [[ "$running_services" == 3 ]] \
    && docker compose --env-file "$ENV_FILE" exec -T pdf-web \
      python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=10)" \
      >/dev/null 2>&1 \
    && curl --fail --silent --show-error --max-time 15 \
      --resolve "$WEB_HOSTNAME:443:127.0.0.1" \
      "https://$WEB_HOSTNAME/healthz" >/dev/null \
    && curl --fail --silent --show-error --max-time 15 \
      --resolve "$WEB_HOSTNAME:443:127.0.0.1" \
      "https://$WEB_HOSTNAME/readyz" >/dev/null; then
    edge_status="$(curl --silent --show-error --max-time 15 \
      --resolve "$WEB_HOSTNAME:443:127.0.0.1" \
      --output /dev/null --write-out '%{http_code}' \
      "https://$WEB_HOSTNAME/" || true)"
    if [[ "$edge_status" == 302 ]]; then
      healthy=true
      break
    fi
  fi
  sleep 10
done

if [[ "$healthy" != true ]]; then
  rollback
fi

if ! docker compose --env-file "$ENV_FILE" exec -T pdf-web python -c \
    "import os, urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/api/config-files', headers={'X-PDF-Web-Proxy-Secret': os.environ['PDF_WEB_PROXY_SECRET'], 'X-Forwarded-Email': 'deployment@aws.local'}); urllib.request.urlopen(request, timeout=10).read()" \
    >/dev/null; then
  rollback
fi

docker image prune -f >/dev/null
echo "pdf_web is ready on AWS with image $APP_IMAGE"
