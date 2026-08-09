#!/usr/bin/env bash
# Deploys the Jarvis home-automation Azure infrastructure (infra/main.bicep)
# using a subscription-scope az deployment. Does not build, test, or package
# the Pi client or the azure-backend application code.
#
# Usage:
#   ./deploy.sh -e dev -l eastus2
#   ./deploy.sh -e dev -l eastus2 --what-if
#   ./deploy.sh -e prod -l eastus2 --validate-only
#
# Options:
#   -e, --environment-name   Short environment name (e.g. dev, test, prod). Required.
#   -l, --location           Azure region to deploy into, e.g. eastus2. Required.
#   -p, --parameters-file    Path to a Bicep parameters file. Defaults to infra/main.parameters.json.
#       --what-if            Run `az deployment sub what-if` instead of a real deployment.
#       --validate-only      Run `az deployment sub validate` instead of a real deployment.
#   -h, --help                Show this help text.
#
# Environment:
#   ADMIN_API_KEY             Required high-entropy backend administrator key.
#   GOOGLE_OAUTH_CLIENT_ID,
#   GOOGLE_OAUTH_CLIENT_SECRET,
#   GOOGLE_OAUTH_REDIRECT_URI Optional; set all three to enable Google tools.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INFRA_DIR="$SOURCE_ROOT/infra"
TEMPLATE_FILE="$INFRA_DIR/main.bicep"
PARAMETERS_FILE="$INFRA_DIR/main.parameters.json"

ENVIRONMENT_NAME=""
LOCATION=""
MODE="deploy"

print_help() {
    sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -e|--environment-name)
            ENVIRONMENT_NAME="$2"
            shift 2
            ;;
        -l|--location)
            LOCATION="$2"
            shift 2
            ;;
        -p|--parameters-file)
            PARAMETERS_FILE="$2"
            shift 2
            ;;
        --what-if)
            MODE="what-if"
            shift
            ;;
        --validate-only)
            MODE="validate"
            shift
            ;;
        -h|--help)
            print_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            print_help
            exit 1
            ;;
    esac
done

if [[ -z "$ENVIRONMENT_NAME" || -z "$LOCATION" ]]; then
    echo "Error: --environment-name and --location are required." >&2
    print_help
    exit 1
fi

ADMIN_KEY_VALUE="${ADMIN_API_KEY:-}"
if [[ ${#ADMIN_KEY_VALUE} -lt 32 ]]; then
    echo "Error: ADMIN_API_KEY must be set to a high-entropy value of at least 32 characters." >&2
    exit 1
fi

GOOGLE_CONFIG_COUNT=0
for value in "${GOOGLE_OAUTH_CLIENT_ID:-}" "${GOOGLE_OAUTH_CLIENT_SECRET:-}" "${GOOGLE_OAUTH_REDIRECT_URI:-}"; do
    [[ -n "$value" ]] && GOOGLE_CONFIG_COUNT=$((GOOGLE_CONFIG_COUNT + 1))
done
if [[ "$GOOGLE_CONFIG_COUNT" -ne 0 && "$GOOGLE_CONFIG_COUNT" -ne 3 ]]; then
    echo "Error: set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI together." >&2
    exit 1
fi

if ! [[ "$ENVIRONMENT_NAME" =~ ^[a-z0-9][a-z0-9-]{0,14}[a-z0-9]$ ]]; then
    echo "Error: environment name must be lowercase alphanumeric/hyphen, 2-16 chars." >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "Error: template not found: $TEMPLATE_FILE" >&2
    exit 1
fi

if [[ ! -f "$PARAMETERS_FILE" ]]; then
    echo "Error: parameters file not found: $PARAMETERS_FILE" >&2
    exit 1
fi

if ! command -v az >/dev/null 2>&1; then
    echo "Error: Azure CLI (az) is required but was not found on PATH." >&2
    echo "Install it from https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
    exit 1
fi

DEPLOYMENT_NAME="jarvis-infra-${ENVIRONMENT_NAME}-$(date +%Y%m%d%H%M%S)"

echo "Source root     : $SOURCE_ROOT"
echo "Template        : $TEMPLATE_FILE"
echo "Parameters      : $PARAMETERS_FILE"
echo "Environment     : $ENVIRONMENT_NAME"
echo "Location        : $LOCATION"
echo "Deployment name : $DEPLOYMENT_NAME"

COMMON_ARGS=(
    --name "$DEPLOYMENT_NAME"
    --location "$LOCATION"
    --template-file "$TEMPLATE_FILE"
    --parameters "$PARAMETERS_FILE"
    --parameters "environmentName=$ENVIRONMENT_NAME"
    --parameters "resourceNameSeed=$ENVIRONMENT_NAME"
    --parameters "location=$LOCATION"
    --parameters "adminApiKey=$ADMIN_KEY_VALUE"
)

if [[ "$GOOGLE_CONFIG_COUNT" -eq 3 ]]; then
    COMMON_ARGS+=(
        --parameters "googleOAuthClientId=$GOOGLE_OAUTH_CLIENT_ID"
        --parameters "googleOAuthClientSecret=$GOOGLE_OAUTH_CLIENT_SECRET"
        --parameters "googleOAuthRedirectUri=$GOOGLE_OAUTH_REDIRECT_URI"
    )
fi

case "$MODE" in
    validate)
        echo
        echo "Validating deployment (no resources will be created)..."
        az deployment sub validate "${COMMON_ARGS[@]}"
        ;;
    what-if)
        echo
        echo "Running what-if analysis (no resources will be created)..."
        az deployment sub what-if "${COMMON_ARGS[@]}"
        ;;
    deploy)
        echo
        echo "Starting deployment..."
        RESULT_TSV=$(az deployment sub create "${COMMON_ARGS[@]}" \
            --query "[properties.outputs.resourceGroupName.value, properties.outputs.functionAppName.value, properties.outputs.apiBaseUrl.value, properties.outputs.keyVaultName.value, properties.outputs.storageAccountName.value, properties.outputs.speechAccountName.value, properties.outputs.openAiAccountName.value]" \
            --output tsv)
        IFS=$'\t' read -r RESOURCE_GROUP FUNCTION_APP API_BASE_URL KEY_VAULT STORAGE_ACCOUNT SPEECH_ACCOUNT OPENAI_ACCOUNT <<< "$RESULT_TSV"

        echo
        echo "Deployment succeeded."
        echo "Resource group : ${RESOURCE_GROUP:-n/a}"
        echo "Function App   : ${FUNCTION_APP:-n/a}"
        echo "API base URL   : ${API_BASE_URL:-n/a}"
        echo "Key Vault      : ${KEY_VAULT:-n/a}"
        echo "Storage account: ${STORAGE_ACCOUNT:-n/a}"
        echo "Speech account : ${SPEECH_ACCOUNT:-n/a}"
        echo "OpenAI account : ${OPENAI_ACCOUNT:-n/a}"

        echo
        echo "Next steps:"
        echo "  Deploy backend code : azd deploy azure-backend"
        echo "  Provision a device  : infra/scripts/provision-device.sh --device-name <name> --storage-account <name>"
        ;;
esac
