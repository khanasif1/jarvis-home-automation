# Jarvis home-automation — Infrastructure as Code

This folder contains **all** Azure Infrastructure as Code for the Jarvis
home-automation solution. It is independently deployable and validated: it
does not require building, testing, or downloading the `pi-client` or
`azure-backend` source.

> Component boundary: `infra/` provisions Azure resources only. It never
> contains Azure Functions application code (that lives in `azure-backend/`),
> and it never contains Pi runtime code (that lives in `pi-client/`).

## Contents

```
infra/
  README.md                    This file
  main.bicep                   Subscription-scope entry point
  main.parameters.json         azd environment-variable parameter bindings
  modules/
    monitoring.bicep           Log Analytics workspace + Application Insights
    storage.bicep              Storage account, app tables, and deployment container
    key-vault.bicep            RBAC-authorized Key Vault, seeded with generated secrets
    speech.bicep                Azure AI Speech account (STT/TTS)
    openai.bicep                Azure OpenAI account + chat-completion model deployment
    function-app.bicep          Linux Python Function App + hosting plan, system-assigned identity
    role-assignments.bicep      RBAC grants from the Function App identity to Key Vault/Storage/Speech/OpenAI
  scripts/
    backend_lifecycle.py        Idempotent create/deploy/delete command for the complete backend
    deploy.ps1 / deploy.sh      Advanced infrastructure-only wrappers around `az deployment sub`
    provision-device.ps1/.sh    Generates a Pi device ID + token, stores only a token hash
  tests/
    test_backend_lifecycle.py   Lifecycle idempotency tests with fake Azure commands
```

## Architecture summary

`main.bicep` deploys at **subscription scope**: it creates the resource group
itself, then deploys each module scoped to that resource group.

Dependency order (enforced by Bicep via output references):

1. `monitoring` (Log Analytics, App Insights) — no dependencies.
2. `storage` (Storage account, containers, tables) — no dependencies.
3. `speech`, `openai` — no dependencies; each provisions its own Cognitive
   Services account (and, for OpenAI, a model deployment).
4. `key-vault` — depends on `speech` and `openai` outputs (their account keys
   are written directly into vault secrets as part of the same deployment, so
   keys never appear in app settings, logs, or source control).
5. `function-app` — depends on `storage`, `key-vault`, `monitoring`, `speech`,
   and `openai` outputs; provisions the Linux Python Function App with a
   system-assigned managed identity and app settings that reference secrets
   via Key Vault references (`@Microsoft.KeyVault(SecretUri=...)`) rather than
   embedding plaintext keys.
6. `role-assignments` — depends on the Function App's managed identity
   (`functionAppPrincipalId`) and grants it least-privilege RBAC roles on
   Key Vault, Storage, Speech, and OpenAI:
   - **Key Vault Secrets User** — read the seeded secrets (also required for
     Key Vault reference app settings to resolve).
   - **Storage Blob Data Owner**, **Storage Queue Data Contributor**, and
     **Storage Table Data Contributor** — run the Flex Consumption host,
     retrieve deployment packages, and read/write application data (todos,
     reminders, sessions, devices, idempotency records, and Google OAuth
     credentials) using managed identity, without a storage key.
   - **Cognitive Services User** (Speech) / **Cognitive Services OpenAI User**
     (Azure OpenAI) — enables Azure AD (managed identity) authentication as an
     alternative to the key-based app settings, for code paths that support it.

Secrets (`speech-account-key`, `openai-account-key`, and optional Google OAuth
credentials) are set once during the Key Vault module deployment
using ARM control-plane writes driven by the deploying principal's
`Microsoft.KeyVault/vaults/secrets/write` permission (standard Contributor
role) — no Key Vault data-plane access is required by the deployer, and no
key ever appears as a plain deployment output.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) 2.60+
- Azure CLI Bicep tooling: `az bicep install` (or `az bicep upgrade`)
- An Azure subscription and `az login` / `az account set --subscription <id>`
- Python 3.11+
- [Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)

Create a high-entropy backend administrator key before validating a live
deployment or provisioning resources. It is passed as a secure Bicep parameter,
stored in Key Vault, and never emitted as an output:

```powershell
$bytes = [byte[]]::new(48)
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$env:ADMIN_API_KEY = [Convert]::ToBase64String($bytes)
```

```bash
export ADMIN_API_KEY="$(openssl rand -base64 48)"
```

## Validate only (no deployment)

```powershell
New-Item -ItemType Directory -Force .test-artifacts\bicep | Out-Null
az bicep build --file infra\main.bicep --outfile .test-artifacts\bicep\main.json
az bicep lint --file infra\main.bicep
```

```bash
mkdir -p .test-artifacts/bicep
az bicep build --file infra/main.bicep --outfile .test-artifacts/bicep/main.json
az bicep lint --file infra/main.bicep
```

Both commands compile every module referenced by `main.bicep` and report
type/schema errors without contacting Azure or touching `pi-client` /
`azure-backend`. The compiled ARM JSON stays under the source root's disposable
`.test-artifacts/` folder and must not be committed.

To validate against a real subscription (dry run, no resources created):

```powershell
az deployment sub validate `
  --location eastus2 `
  --template-file infra\main.bicep `
  --parameters infra\main.parameters.json `
  --parameters environmentName=jarvis-a1b2 location=eastus2 adminApiKey="$env:ADMIN_API_KEY"

az deployment sub what-if `
  --location eastus2 `
  --template-file infra\main.bicep `
  --parameters infra\main.parameters.json `
  --parameters environmentName=jarvis-a1b2 location=eastus2 adminApiKey="$env:ADMIN_API_KEY"
```

Or use the wrapper scripts:

```powershell
infra\scripts\deploy.ps1 -EnvironmentName jarvis-a1b2 -Location eastus2 -ValidateOnly
infra\scripts\deploy.ps1 -EnvironmentName jarvis-a1b2 -Location eastus2 -WhatIf
```

```bash
infra/scripts/deploy.sh --environment-name jarvis-a1b2 --location eastus2 --validate-only
infra/scripts/deploy.sh --environment-name jarvis-a1b2 --location eastus2 --what-if
```

## Install or update the complete backend

The recommended lifecycle command creates or updates every Azure service,
deploys the Function code, and verifies the health endpoint. It creates the
local azd environment and administrator key on first use, then reuses both on
later runs:

```bash
az login
azd auth login
python3 infra/scripts/backend_lifecycle.py install \
  --environment-name home \
  --location australiaeast
```

```powershell
az login
azd auth login
python infra\scripts\backend_lifecycle.py install `
  --environment-name home `
  --location australiaeast
```

To delete the resource group and every contained service:

```bash
python3 infra/scripts/backend_lifecycle.py uninstall \
  --environment-name home \
  --yes
```

Both operations are idempotent. Install reconciles the same Bicep deployment
and redeploys the same backend; uninstall returns success when the resource
group is already absent. Managed environments are removed with
`azd down --purge`, including soft-deleted Key Vault and Cognitive Services
resources where Azure permits purge. The resource-name seed rotates after
uninstall, so purge-protected retention cannot block reinstalling the same
environment.

## Advanced infrastructure-only deployment

### Option A — `az deployment sub create`

```powershell
infra\scripts\deploy.ps1 -EnvironmentName jarvis-a1b2 -Location eastus2
```

```bash
infra/scripts/deploy.sh --environment-name jarvis-a1b2 --location eastus2
```

Or call the Azure CLI directly:

```bash
az deployment sub create \
  --name jarvis-infra-jarvis-a1b2 \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters environmentName=jarvis-a1b2 resourceNameSeed=jarvis-a1b2 \
    location=eastus2 adminApiKey="$ADMIN_API_KEY"
```

### Option B — `azd provision`

`azd` discovers `infra/main.bicep` via `_src/azure.yaml`. From the `_src/`
source root:

```bash
azd env set RESOURCE_NAME_SEED jarvis-a1b2
azd provision
```

Set `ADMIN_API_KEY` in the current environment first. `azd` resolves it, along
with `AZURE_ENV_NAME`, `AZURE_LOCATION`, and `RESOURCE_NAME_SEED`, through
`main.parameters.json`, then runs the equivalent of
`az deployment sub create` against this template.

Deploying infrastructure never builds or requires `pi-client` or
`azure-backend` source; deploying backend **code** afterwards is a separate,
explicit step:

```bash
azd deploy azure-backend
```

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `environmentName` | *(required)* | Unique short environment name (for example `jarvis-a1b2`); used to derive all resource names. |
| `resourceNameSeed` | `environmentName` | Stable input for globally unique resource names. Lifecycle tooling persists it across updates and rotates it after uninstall. |
| `location` | *(required)* | Azure region for every resource. |
| `resourceGroupName` | `rg-<environmentName>-jarvis` | Override the generated resource group name. |
| `tags` | see `main.bicep` | Tags applied to every resource. |
| `functionAppPlanSku` | `FC1` | Linux Flex Consumption hosting plan SKU. |
| `functionMaximumInstanceCount` | `20` | Maximum burst scale-out for Flex Consumption. |
| `functionInstanceMemoryMB` | `2048` | Memory allocated per Flex Consumption instance. |
| `pythonVersion` | `3.11` | Python runtime version for the Function App. |
| `speechSku` | `S0` | Azure AI Speech pricing tier (`F0` free tier also available). |
| `openAiDeploymentName` | `gpt-4.1-mini` | Azure OpenAI chat-completion deployment name the backend targets. |
| `openAiModelName` | `gpt-4.1-mini` | Underlying Azure OpenAI model. |
| `openAiModelVersion` | `2025-04-14` | Azure OpenAI model version. |
| `openAiApiVersion` | `2024-10-21` | Stable Azure OpenAI API version used by the backend SDK. |
| `openAiDeploymentCapacity` | `10` | Deployment capacity in units of 1,000 TPM. |
| `enableKeyVaultPurgeProtection` | `true` | Protects deleted vaults from purge; lifecycle seed rotation still permits immediate reinstall. |
| `logRetentionInDays` | `30` | Log Analytics workspace retention. |
| `adminApiKey` | *(required, secure)* | High-entropy key for administrative routes and OAuth state signing; stored in Key Vault. |
| `googleOAuthClientId` | blank | Optional Google web OAuth client ID. |
| `googleOAuthClientSecret` | blank (secure) | Optional Google web OAuth client secret; set with the client ID and redirect URI. |
| `googleOAuthRedirectUri` | blank | Optional backend Google OAuth callback URL. |

Keep secrets in environment variables or a secure deployment system. Use
`azd env set` for non-secret per-environment values, or pass explicit
`--parameters key=value` overrides through the deployment scripts.

> **Region note:** Azure AI Speech and Azure OpenAI are available only in a
> subset of regions and Azure OpenAI model/deployment availability varies by
> subscription. If deployment fails with a region or quota error, choose a
> region/model combination your subscription has access to (see
> `az cognitiveservices account list-skus` and the Azure OpenAI model
> availability documentation) and re-run.

## Outputs

`main.bicep` outputs the values the backend and the Raspberry Pi need to
integrate with the deployed infrastructure:

| Output | Consumed by | Purpose |
|---|---|---|
| `apiBaseUrl` | Pi client, backend clients | `https://<function-app>.azurewebsites.net/api` — the base URL every voice-turn request targets. |
| `functionAppName` | Deployment tooling, operators | Name of the Function App to target with `azd deploy azure-backend` / `func azure functionapp publish`. |
| `resourceGroupName` | Deployment tooling, operators | Resource group all resources live in. |
| `storageAccountName` | Backend config, device provisioning scripts | Storage account backing `AzureWebJobsStorage` and the data tables. |
| `keyVaultName` / `keyVaultUri` | Backend config, operators | Where backend secrets (Speech key, OpenAI key, admin key, and optional Google OAuth credentials) live. |
| `speechAccountName` / `speechEndpoint` / `speechRegion` | Backend config | Azure AI Speech connection details. |
| `openAiAccountName` / `openAiEndpoint` / `openAiDeploymentName` | Backend config | Azure OpenAI connection details. |
| `logAnalyticsWorkspaceName` / `appInsightsName` | Operators | Telemetry resource names for dashboards/alerts. |
| `functionAppPrincipalId` | Operators, auditing | Managed identity principal ID granted RBAC access. |

Retrieve outputs after a deployment:

```bash
az deployment sub show --name jarvis-infra-jarvis-a1b2 --query properties.outputs
```

## Device provisioning

Once infrastructure is deployed, issue a credential for each Raspberry Pi
device. The script generates the device ID and token **locally**, stores only
a SHA-256 hash of the token in the `Devices` table (via Azure AD data-plane
auth — no storage key is ever used or embedded), and prints the plaintext
token exactly once.

```powershell
infra\scripts\provision-device.ps1 `
  -DeviceName kitchen-pi `
  -StorageAccountName <storageAccountName output>
```

```bash
infra/scripts/provision-device.sh \
  --device-name kitchen-pi \
  --storage-account <storageAccountName output>
```

Copy the printed `HAP_DEVICE_ID` and `HAP_DEVICE_TOKEN` lines into the Pi's
`/etc/home-assistant-pi/config.env` (see `pi-client/README.md`). The token
cannot be retrieved again after this — re-run the script to issue a new
credential if it is lost. The operator must hold the **Storage Table Data
Contributor** role (or higher) on the storage account to run this script;
`az login` must be completed first.

Never commit the printed token, the optional `-OutputFile`/`--output-file`
snippet, or any `config.env` file to source control.

## Security notes

- Key Vault uses **RBAC authorization** (no access policies); the Function
  App identity is granted only `Key Vault Secrets User`.
- No account keys are stored in app settings as plain text — every secret app
  setting is a Key Vault reference (`@Microsoft.KeyVault(SecretUri=...)`).
- The storage account, Speech, and OpenAI account keys exist only transiently
  as Bicep module outputs consumed by the Key Vault module in the same
  deployment; they are never printed by the deploy scripts or written to
  `.test-artifacts/`.
- TLS 1.2 minimum and HTTPS-only are enforced on the storage account and the
  Function App; public blob access is disabled.
- Device tokens are generated client-side and only their SHA-256 hash is
  persisted; the plaintext token exists only in the operator's terminal (and
  optionally a locally-secured file they must delete after transfer to the
  Pi).

## Manual validation

Run these checks manually whenever infrastructure changes:

```bash
az bicep build --file infra/main.bicep
az bicep lint --file infra/main.bicep
```

These commands validate `infra/**` without building, testing, or packaging
`pi-client/**` or `azure-backend/**`.
