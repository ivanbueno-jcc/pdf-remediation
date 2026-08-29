#!/usr/bin/env bash
set -euo pipefail

required=(
  ACME_EMAIL
  AWS_HOSTED_ZONE_ID
  AWS_REGION
  AWS_STACK_NAME
  COGNITO_INITIAL_USER_EMAIL
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

AWS_GITHUB_ENVIRONMENT="${AWS_GITHUB_ENVIRONMENT:-aws-production}"
AWS_INSTANCE_TYPE="${AWS_INSTANCE_TYPE:-m7i.2xlarge}"
AWS_ROOT_VOLUME_SIZE_GIB="${AWS_ROOT_VOLUME_SIZE_GIB:-256}"
CONFIGURE_GITHUB="${CONFIGURE_GITHUB:-false}"
NAME_PREFIX="${NAME_PREFIX:-pdfremed}"
PDF_CALLAS_FONT_IMAGE="${PDF_CALLAS_FONT_IMAGE:-pdfix/font-fix-callas:v1.0.11}"
PDF_PDFIX_FONT_IMAGE="${PDF_PDFIX_FONT_IMAGE:-pdfix/font-fix-pdfix:v1.0.9}"
PDF_WEB_MAX_CONCURRENT_JOBS="${PDF_WEB_MAX_CONCURRENT_JOBS:-4}"

hosted_zone_name="$(aws route53 get-hosted-zone \
  --id "$AWS_HOSTED_ZONE_ID" --query HostedZone.Name --output text)"
hosted_zone_name="${hosted_zone_name%.}"

existing_oidc_provider=""
managed_oidc_provider="$(aws cloudformation describe-stack-resource \
  --region "$AWS_REGION" --stack-name "$AWS_STACK_NAME" \
  --logical-resource-id GitHubOidcProvider \
  --query StackResourceDetail.PhysicalResourceId --output text 2>/dev/null || true)"
if [[ -z "$managed_oidc_provider" ]]; then
  while IFS= read -r provider_arn; do
    [[ -n "$provider_arn" ]] || continue
    provider_url="$(aws iam get-open-id-connect-provider \
      --open-id-connect-provider-arn "$provider_arn" \
      --query Url --output text 2>/dev/null || true)"
    if [[ "$provider_url" == token.actions.githubusercontent.com ]]; then
      existing_oidc_provider="$provider_arn"
      break
    fi
  done < <(aws iam list-open-id-connect-providers \
    --query 'OpenIDConnectProviderList[].Arn' --output text | tr '\t' '\n')
fi

parameters=(
  "AcmeEmail=$ACME_EMAIL"
  "GitHubEnvironment=$AWS_GITHUB_ENVIRONMENT"
  "GitHubRepository=$GITHUB_REPOSITORY"
  "HostedZoneId=$AWS_HOSTED_ZONE_ID"
  "HostedZoneName=$hosted_zone_name"
  "InstanceType=$AWS_INSTANCE_TYPE"
  "NamePrefix=$NAME_PREFIX"
  "RootVolumeSizeGiB=$AWS_ROOT_VOLUME_SIZE_GIB"
  "WebDnsLabel=$WEB_DNS_LABEL"
)
if [[ -n "$existing_oidc_provider" ]]; then
  parameters+=("ExistingGitHubOidcProviderArn=$existing_oidc_provider")
fi

aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name "$AWS_STACK_NAME" \
  --template-file infra/aws/main.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides "${parameters[@]}" \
  --no-fail-on-empty-changeset \
  --no-cli-pager >/dev/null

output() {
  aws cloudformation describe-stacks \
    --region "$AWS_REGION" --stack-name "$AWS_STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

cognito_client_id="$(output CognitoClientId)"
cognito_domain="$(output CognitoDomain)"
cognito_issuer_url="$(output CognitoIssuerUrl)"
cognito_user_pool_id="$(output CognitoUserPoolId)"
deployment_role_arn="$(output GitHubDeployRoleArn)"
execution_role_arn="$(output CloudFormationExecutionRoleArn)"
runtime_secret_arn="$(output RuntimeSecretArn)"
web_hostname="$(output WebHostname)"

cognito_client_secret="$(aws cognito-idp describe-user-pool-client \
  --region "$AWS_REGION" --user-pool-id "$cognito_user_pool_id" \
  --client-id "$cognito_client_id" \
  --query UserPoolClient.ClientSecret --output text)"

existing_secret="$(aws secretsmanager get-secret-value \
  --region "$AWS_REGION" --secret-id "$runtime_secret_arn" \
  --query SecretString --output text 2>/dev/null || printf '{}')"
cookie_secret="$(jq -r '.OAUTH2_PROXY_COOKIE_SECRET // empty' <<<"$existing_secret")"
proxy_secret="$(jq -r '.PDF_WEB_PROXY_SECRET // empty' <<<"$existing_secret")"
if [[ -z "$cookie_secret" ]]; then
  cookie_secret="$(openssl rand -base64 32 | tr -d '\n')"
fi
if [[ -z "$proxy_secret" ]]; then
  proxy_secret="$(openssl rand -hex 32)"
fi

logout_uri="$(jq -nr --arg value "https://$web_hostname/" '$value | @uri')"
cognito_logout_url="https://$cognito_domain/logout?client_id=$cognito_client_id&logout_uri=$logout_uri"

secret_file="$(mktemp)"
trap 'rm -f "$secret_file"' EXIT
jq -n \
  --arg acme_email "$ACME_EMAIL" \
  --arg callas_license "$ENV_CALLAS_LICENSE" \
  --arg callas_secret "$ENV_CALLAS_SECRET" \
  --arg client_id "$cognito_client_id" \
  --arg client_secret "$cognito_client_secret" \
  --arg cookie_secret "$cookie_secret" \
  --arg issuer_url "$cognito_issuer_url" \
  --arg logout_url "$cognito_logout_url" \
  --arg pdfix_key "$PDFIX_LICENSE_KEY" \
  --arg pdfix_name "$PDFIX_LICENSE_NAME" \
  --arg proxy_secret "$proxy_secret" \
  '{
    ACME_EMAIL: $acme_email,
    COGNITO_CLIENT_ID: $client_id,
    COGNITO_CLIENT_SECRET: $client_secret,
    COGNITO_ISSUER_URL: $issuer_url,
    COGNITO_LOGOUT_URL: $logout_url,
    ENV_CALLAS_LICENSE: $callas_license,
    ENV_CALLAS_SECRET: $callas_secret,
    OAUTH2_PROXY_COOKIE_SECRET: $cookie_secret,
    PDFIX_LICENSE_KEY: $pdfix_key,
    PDFIX_LICENSE_NAME: $pdfix_name,
    PDF_WEB_PROXY_SECRET: $proxy_secret
  }' > "$secret_file"
aws secretsmanager put-secret-value \
  --region "$AWS_REGION" --secret-id "$runtime_secret_arn" \
  --secret-string "file://$secret_file" --no-cli-pager >/dev/null
rm -f "$secret_file"
trap - EXIT
unset cognito_client_secret cookie_secret existing_secret proxy_secret

if ! aws cognito-idp admin-get-user \
    --region "$AWS_REGION" --user-pool-id "$cognito_user_pool_id" \
    --username "$COGNITO_INITIAL_USER_EMAIL" >/dev/null 2>&1; then
  aws cognito-idp admin-create-user \
    --region "$AWS_REGION" --user-pool-id "$cognito_user_pool_id" \
    --username "$COGNITO_INITIAL_USER_EMAIL" \
    --user-attributes \
      "Name=email,Value=$COGNITO_INITIAL_USER_EMAIL" \
      "Name=email_verified,Value=true" \
    --desired-delivery-mediums EMAIL --no-cli-pager >/dev/null
fi
aws cognito-idp admin-add-user-to-group \
  --region "$AWS_REGION" --user-pool-id "$cognito_user_pool_id" \
  --username "$COGNITO_INITIAL_USER_EMAIL" --group-name pdf-web-users

if [[ "$CONFIGURE_GITHUB" == true ]]; then
  command -v gh >/dev/null || {
    echo "CONFIGURE_GITHUB=true requires the GitHub CLI." >&2
    exit 2
  }
  gh api --method PUT \
    "repos/$GITHUB_REPOSITORY/environments/$AWS_GITHUB_ENVIRONMENT" \
    >/dev/null
  gh variable set AWS_DEPLOY_ROLE_ARN --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$deployment_role_arn"
  gh variable set AWS_CLOUDFORMATION_ROLE_ARN --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$execution_role_arn"
  gh variable set AWS_HOSTED_ZONE_ID --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$AWS_HOSTED_ZONE_ID"
  gh variable set AWS_REGION --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$AWS_REGION"
  gh variable set AWS_STACK_NAME --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$AWS_STACK_NAME"
  gh variable set AWS_WEB_HOSTNAME --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$web_hostname"
  gh variable set PDF_CALLAS_FONT_IMAGE --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$PDF_CALLAS_FONT_IMAGE"
  gh variable set PDF_PDFIX_FONT_IMAGE --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$PDF_PDFIX_FONT_IMAGE"
  gh variable set PDF_WEB_MAX_CONCURRENT_JOBS --env "$AWS_GITHUB_ENVIRONMENT" \
    --repo "$GITHUB_REPOSITORY" --body "$PDF_WEB_MAX_CONCURRENT_JOBS"
fi

echo "AWS bootstrap complete."
echo "Web URL: https://$web_hostname"
echo "Initial Cognito invitation: $COGNITO_INITIAL_USER_EMAIL"
echo "GitHub deployment role: $deployment_role_arn"
if [[ "$CONFIGURE_GITHUB" != true ]]; then
  echo "Configure the GitHub $AWS_GITHUB_ENVIRONMENT environment values documented in docs/aws-web-deployment.md."
fi
