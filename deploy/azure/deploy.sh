#!/usr/bin/env bash
set -euo pipefail

# Azure VM Run Command passes parameters as key=value arguments. Keeping all
# deployment logic here makes the same operation usable by CI and operators.
for argument in "$@"; do
  case "$argument" in
    APP_IMAGE=*) APP_IMAGE="${argument#*=}" ;;
    KEY_VAULT_NAME=*) KEY_VAULT_NAME="${argument#*=}" ;;
    STORAGE_ACCOUNT=*) STORAGE_ACCOUNT="${argument#*=}" ;;
    STORAGE_SHARE=*) STORAGE_SHARE="${argument#*=}" ;;
    WEB_HOSTNAME=*) WEB_HOSTNAME="${argument#*=}" ;;
    PDF_CALLAS_FONT_IMAGE=*) PDF_CALLAS_FONT_IMAGE="${argument#*=}" ;;
    PDF_PDFIX_FONT_IMAGE=*) PDF_PDFIX_FONT_IMAGE="${argument#*=}" ;;
    *) echo "Unknown deployment parameter: ${argument%%=*}" >&2; exit 2 ;;
  esac
done

: "${APP_IMAGE:?APP_IMAGE is required}"
: "${KEY_VAULT_NAME:?KEY_VAULT_NAME is required}"
: "${STORAGE_ACCOUNT:?STORAGE_ACCOUNT is required}"
: "${STORAGE_SHARE:?STORAGE_SHARE is required}"
: "${WEB_HOSTNAME:?WEB_HOSTNAME is required}"

PDF_CALLAS_FONT_IMAGE="${PDF_CALLAS_FONT_IMAGE:-pdfix/font-fix-callas:v1.0.11}"
PDF_PDFIX_FONT_IMAGE="${PDF_PDFIX_FONT_IMAGE:-pdfix/font-fix-pdfix:v1.0.9}"

DEPLOY_DIR=/opt/pdf-remediation
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

az login --identity --allow-no-subscriptions --output none
ACR_NAME="${APP_IMAGE%%.*}"
retry az acr login --name "$ACR_NAME" --output none

storage_key="$(retry az keyvault secret show \
  --vault-name "$KEY_VAULT_NAME" --name azure-files-key \
  --query value --output tsv)"

install -d -m 0755 "$DATA_ROOT" "$SCRATCH_ROOT" "$DEPLOY_DIR"
credential_dir=/etc/smbcredentials
credential_file="$credential_dir/$STORAGE_ACCOUNT.cred"
install -d -m 0700 "$credential_dir"
umask 077
printf 'username=%s\npassword=%s\n' "$STORAGE_ACCOUNT" "$storage_key" > "$credential_file"
unset storage_key

share_source="//$STORAGE_ACCOUNT.file.core.windows.net/$STORAGE_SHARE"
fstab_entry="$share_source $DATA_ROOT cifs nofail,_netdev,credentials=$credential_file,serverino,nosharesock,actimeo=30,mfsymlinks,uid=10001,gid=10001,file_mode=0660,dir_mode=0770 0 0"
if ! grep -Fq "$share_source $DATA_ROOT " /etc/fstab; then
  printf '%s\n' "$fstab_entry" >> /etc/fstab
fi
if ! mountpoint -q "$DATA_ROOT"; then
  retry mount "$DATA_ROOT"
fi

install -d -o 10001 -g 10001 -m 0770 \
  "$DATA_ROOT/web" "$DATA_ROOT/caddy/data" "$DATA_ROOT/caddy/config" \
  "$SCRATCH_ROOT"

docker pull "$APP_IMAGE" >/dev/null
asset_container="pdf-remediation-assets-$$"
docker create --name "$asset_container" "$APP_IMAGE" >/dev/null
trap 'docker rm -f "$asset_container" >/dev/null 2>&1 || true' EXIT
docker cp "$asset_container:/opt/pdf-remediation-deploy/." "$DEPLOY_DIR/"
docker rm "$asset_container" >/dev/null
trap - EXIT

secret() {
  retry az keyvault secret show \
    --vault-name "$KEY_VAULT_NAME" --name "$1" --query value --output tsv
}

validate_env_value() {
  if [[ "$2" == *$'\n'* || "$2" == *$'\r'* ]]; then
    echo "Key Vault secret $1 contains a newline and cannot be written to Compose env." >&2
    exit 2
  fi
}

previous_image=""
if [[ -f "$ENV_FILE" ]]; then
  previous_image="$(sed -n 's/^APP_IMAGE=//p' "$ENV_FILE" | head -n 1)"
fi

declare -A values
values[ACME_EMAIL]="$(secret acme-email)"
values[ENTRA_CLIENT_ID]="$(secret entra-client-id)"
values[ENTRA_CLIENT_SECRET]="$(secret entra-client-secret)"
values[ENTRA_TENANT_ID]="$(secret entra-tenant-id)"
values[ENV_CALLAS_LICENSE]="$(secret callas-license)"
values[ENV_CALLAS_SECRET]="$(secret callas-secret)"
values[OAUTH2_PROXY_COOKIE_SECRET]="$(secret oauth2-cookie-secret)"
values[PDFIX_LICENSE_KEY]="$(secret pdfix-license-key)"
values[PDFIX_LICENSE_NAME]="$(secret pdfix-license-name)"
values[PDF_WEB_PROXY_SECRET]="$(secret pdf-web-proxy-secret)"

umask 077
temporary_env="$(mktemp "$DEPLOY_DIR/.env.XXXXXX")"
{
  printf 'APP_IMAGE=%s\n' "$APP_IMAGE"
  printf 'DOCKER_GID=%s\n' "$(stat -c '%g' /var/run/docker.sock)"
  printf 'PDF_CALLAS_FONT_IMAGE=%s\n' "$PDF_CALLAS_FONT_IMAGE"
  printf 'PDF_PDFIX_FONT_IMAGE=%s\n' "$PDF_PDFIX_FONT_IMAGE"
  printf 'WEB_HOSTNAME=%s\n' "$WEB_HOSTNAME"
  for name in "${!values[@]}"; do
    validate_env_value "$name" "${values[$name]}"
    printf '%s=%s\n' "$name" "${values[$name]}"
  done
} > "$temporary_env"
chmod 0600 "$temporary_env"
mv "$temporary_env" "$ENV_FILE"

docker pull "$PDF_CALLAS_FONT_IMAGE" >/dev/null
docker pull "$PDF_PDFIX_FONT_IMAGE" >/dev/null

cd "$DEPLOY_DIR"

rollback() {
  docker compose --env-file "$ENV_FILE" logs --tail 100 >&2 || true
  if [[ -n "$previous_image" && "$previous_image" != "$APP_IMAGE" ]]; then
    sed -i "s|^APP_IMAGE=.*$|APP_IMAGE=$previous_image|" "$ENV_FILE"
    docker compose --env-file "$ENV_FILE" up -d --remove-orphans
    echo "Deployment failed health checks; restored $previous_image." >&2
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
  if docker compose --env-file "$ENV_FILE" exec -T pdf-web \
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

# Exercise the same proxy proof and identity contract used for real requests,
# without exposing a CI-only bypass through the public edge.
if ! docker compose --env-file "$ENV_FILE" exec -T pdf-web python -c \
    "import os, urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/api/config-files', headers={'X-PDF-Web-Proxy-Secret': os.environ['PDF_WEB_PROXY_SECRET'], 'X-Forwarded-Email': 'deployment@azure.local'}); urllib.request.urlopen(request, timeout=10).read()" \
    >/dev/null; then
  rollback
fi

docker image prune -f >/dev/null
echo "pdf_web is ready with image $APP_IMAGE"
