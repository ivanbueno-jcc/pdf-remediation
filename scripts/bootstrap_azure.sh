#!/usr/bin/env bash
set -euo pipefail

required=(
  ACME_EMAIL
  ADMIN_SSH_PUBLIC_KEY
  AZURE_DNS_ZONE_RESOURCE_ID
  AZURE_LOCATION
  AZURE_RESOURCE_GROUP
  AZURE_SUBSCRIPTION_ID
  ENV_CALLAS_LICENSE
  ENV_CALLAS_SECRET
  GITHUB_REPOSITORY
  PDFIX_LICENSE_KEY
  PDFIX_LICENSE_NAME
  WEB_DNS_LABEL
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "$name is required." >&2
    exit 2
  fi
done

GITHUB_ENVIRONMENT="${GITHUB_ENVIRONMENT:-production}"
NAME_PREFIX="${NAME_PREFIX:-pdfremed}"
APP_DISPLAY_NAME="${APP_DISPLAY_NAME:-PDF Remediation Web}"
CONFIGURE_GITHUB="${CONFIGURE_GITHUB:-false}"

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

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
tenant_id="$(az account show --query tenantId --output tsv)"

az group create \
  --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" --output none

deployment_name="pdf-remediation-bootstrap"
az deployment group create \
  --name "$deployment_name" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file infra/azure/main.bicep \
  --parameters \
    location="$AZURE_LOCATION" \
    namePrefix="$NAME_PREFIX" \
    dnsZoneResourceId="$AZURE_DNS_ZONE_RESOURCE_ID" \
    webDnsLabel="$WEB_DNS_LABEL" \
    adminSshPublicKey="$ADMIN_SSH_PUBLIC_KEY" \
  --output none

outputs="$(az deployment group show \
  --name "$deployment_name" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query properties.outputs --output json)"
key_vault_name="$(jq -r '.keyVaultName.value' <<<"$outputs")"
web_hostname="$(jq -r '.webHostname.value' <<<"$outputs")"
callback_url="https://$web_hostname/oauth2/callback"

operator_object_id="$(az ad signed-in-user show --query id --output tsv 2>/dev/null || true)"
if [[ -z "$operator_object_id" ]]; then
  operator_client_id="$(az account show --query user.name --output tsv)"
  operator_object_id="$(az ad sp show --id "$operator_client_id" \
    --query id --output tsv 2>/dev/null || true)"
fi
if [[ -z "$operator_object_id" ]]; then
  echo "Unable to resolve the signed-in operator for Key Vault access." >&2
  exit 2
fi
key_vault_id="$(az keyvault show --name "$key_vault_name" --query id --output tsv)"
if [[ -z "$(az role assignment list --assignee-object-id "$operator_object_id" \
    --scope "$key_vault_id" --role "Key Vault Secrets Officer" \
    --query '[0].id' --output tsv)" ]]; then
  az role assignment create \
    --assignee-object-id "$operator_object_id" \
    --role "Key Vault Secrets Officer" --scope "$key_vault_id" --output none
fi

app_id="$(az ad app list --display-name "$APP_DISPLAY_NAME" \
  --query '[0].appId' --output tsv)"
if [[ -z "$app_id" ]]; then
  app_id="$(az ad app create \
    --display-name "$APP_DISPLAY_NAME" \
    --sign-in-audience AzureADMyOrg \
    --enable-id-token-issuance true \
    --web-redirect-uris "$callback_url" \
    --query appId --output tsv)"
else
  az ad app update --id "$app_id" \
    --enable-id-token-issuance true \
    --web-redirect-uris "$callback_url" --output none
fi

sp_object_id="$(az ad sp show --id "$app_id" --query id --output tsv 2>/dev/null || true)"
if [[ -z "$sp_object_id" ]]; then
  sp_object_id="$(az ad sp create --id "$app_id" --query id --output tsv)"
fi
az ad sp update --id "$sp_object_id" --set appRoleAssignmentRequired=true --output none

set_secret() {
  retry az keyvault secret set --vault-name "$key_vault_name" --name "$1" \
    --value "$2" --output none
}

secret_exists() {
  az keyvault secret show --vault-name "$key_vault_name" --name "$1" \
    --query id --output tsv >/dev/null 2>&1
}

set_secret acme-email "$ACME_EMAIL"
set_secret entra-client-id "$app_id"
set_secret entra-tenant-id "$tenant_id"
set_secret callas-license "$ENV_CALLAS_LICENSE"
set_secret callas-secret "$ENV_CALLAS_SECRET"
set_secret pdfix-license-key "$PDFIX_LICENSE_KEY"
set_secret pdfix-license-name "$PDFIX_LICENSE_NAME"

if ! secret_exists entra-client-secret; then
  entra_secret="$(az ad app credential reset --id "$app_id" --append \
    --display-name pdf-remediation-web --years 1 --query password --output tsv)"
  set_secret entra-client-secret "$entra_secret"
  unset entra_secret
fi
if ! secret_exists oauth2-cookie-secret; then
  set_secret oauth2-cookie-secret "$(openssl rand -base64 32 | tr -d '\n')"
fi
if ! secret_exists pdf-web-proxy-secret; then
  set_secret pdf-web-proxy-secret "$(openssl rand -hex 32)"
fi

github_display_name="PDF Remediation GitHub ${GITHUB_REPOSITORY//\//-}"
github_app_id="$(az ad app list --display-name "$github_display_name" \
  --query '[0].appId' --output tsv)"
if [[ -z "$github_app_id" ]]; then
  github_app_id="$(az ad app create --display-name "$github_display_name" \
    --sign-in-audience AzureADMyOrg --query appId --output tsv)"
fi
github_app_object_id="$(az ad app show --id "$github_app_id" --query id --output tsv)"
github_sp_object_id="$(az ad sp show --id "$github_app_id" --query id --output tsv 2>/dev/null || true)"
if [[ -z "$github_sp_object_id" ]]; then
  github_sp_object_id="$(az ad sp create --id "$github_app_id" --query id --output tsv)"
fi

credential_name="pdf-remediation-${GITHUB_ENVIRONMENT}"
if ! az ad app federated-credential list --id "$github_app_object_id" \
    --query "[?name=='$credential_name'].name | [0]" --output tsv | grep -q .; then
  credential_file="$(mktemp)"
  trap 'rm -f "$credential_file"' EXIT
  jq -n \
    --arg name "$credential_name" \
    --arg subject "repo:$GITHUB_REPOSITORY:environment:$GITHUB_ENVIRONMENT" \
    '{name:$name,issuer:"https://token.actions.githubusercontent.com",subject:$subject,description:"GitHub Actions production deployment",audiences:["api://AzureADTokenExchange"]}' \
    > "$credential_file"
  az ad app federated-credential create --id "$github_app_object_id" \
    --parameters "$credential_file" --output none
  rm -f "$credential_file"
  trap - EXIT
fi

subscription_scope="/subscriptions/$AZURE_SUBSCRIPTION_ID"
resource_group_scope="$subscription_scope/resourceGroups/$AZURE_RESOURCE_GROUP"
for role in Contributor "Role Based Access Control Administrator"; do
  az role assignment create \
    --assignee-object-id "$github_sp_object_id" \
    --assignee-principal-type ServicePrincipal \
    --role "$role" --scope "$resource_group_scope" --output none \
    2>/dev/null || true
done
az role assignment create \
  --assignee-object-id "$github_sp_object_id" \
  --assignee-principal-type ServicePrincipal \
  --role "DNS Zone Contributor" --scope "$AZURE_DNS_ZONE_RESOURCE_ID" \
  --output none 2>/dev/null || true

if [[ "$CONFIGURE_GITHUB" == true ]]; then
  command -v gh >/dev/null || {
    echo "CONFIGURE_GITHUB=true requires the GitHub CLI." >&2
    exit 2
  }
  gh secret set AZURE_CLIENT_ID --env "$GITHUB_ENVIRONMENT" --body "$github_app_id"
  gh secret set AZURE_TENANT_ID --env "$GITHUB_ENVIRONMENT" --body "$tenant_id"
  gh secret set AZURE_SUBSCRIPTION_ID --env "$GITHUB_ENVIRONMENT" \
    --body "$AZURE_SUBSCRIPTION_ID"
  gh secret set ADMIN_SSH_PUBLIC_KEY --env "$GITHUB_ENVIRONMENT" \
    --body "$ADMIN_SSH_PUBLIC_KEY"
  gh variable set AZURE_RESOURCE_GROUP --env "$GITHUB_ENVIRONMENT" \
    --body "$AZURE_RESOURCE_GROUP"
  gh variable set AZURE_LOCATION --env "$GITHUB_ENVIRONMENT" --body "$AZURE_LOCATION"
  gh variable set AZURE_DNS_ZONE_RESOURCE_ID --env "$GITHUB_ENVIRONMENT" \
    --body "$AZURE_DNS_ZONE_RESOURCE_ID"
  gh variable set WEB_DNS_LABEL --env "$GITHUB_ENVIRONMENT" --body "$WEB_DNS_LABEL"
  gh variable set NAME_PREFIX --env "$GITHUB_ENVIRONMENT" --body "$NAME_PREFIX"
fi

echo "Azure bootstrap complete."
echo "Web URL: https://$web_hostname"
echo "Entra application client ID: $app_id"
echo "Assign authorized users or groups to: $APP_DISPLAY_NAME"
echo "GitHub deployment client ID: $github_app_id"
if [[ "$CONFIGURE_GITHUB" != true ]]; then
  echo "Set the GitHub $GITHUB_ENVIRONMENT environment values documented in docs/azure-web-deployment.md."
fi
