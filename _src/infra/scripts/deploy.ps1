#Requires -Version 7.0
<#
.SYNOPSIS
    Deploys the Jarvis home-automation Azure infrastructure (infra/main.bicep)
    using a subscription-scope az deployment. Does not build, test, or
    package the Pi client or the azure-backend application code.

.DESCRIPTION
    This script only provisions infrastructure. To deploy backend application
    code afterwards, use `azd deploy azure-backend` or the azure-backend
    project's own deployment tooling.

.PARAMETER EnvironmentName
    Short environment name (e.g. dev, test, prod). Used to derive resource
    names and the default resource group name.

.PARAMETER Location
    Azure region to deploy into, e.g. eastus2.

.PARAMETER ParametersFile
    Path to a Bicep parameters file. Defaults to infra/main.parameters.json.

.PARAMETER WhatIf
    Run `az deployment sub what-if` instead of a real deployment.

.PARAMETER ValidateOnly
    Run `az deployment sub validate` instead of a real deployment.

.EXAMPLE
    ./deploy.ps1 -EnvironmentName dev -Location eastus2

.EXAMPLE
    ./deploy.ps1 -EnvironmentName dev -Location eastus2 -WhatIf

.EXAMPLE
    ./deploy.ps1 -EnvironmentName prod -Location eastus2 -ValidateOnly
#>
[CmdletBinding(DefaultParameterSetName = 'Deploy')]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,14}[a-z0-9]$')]
    [string]$EnvironmentName,

    [Parameter(Mandatory = $true)]
    [string]$Location,

    [Parameter()]
    [string]$ParametersFile,

    [Parameter(ParameterSetName = 'WhatIf')]
    [switch]$WhatIf,

    [Parameter(ParameterSetName = 'Validate')]
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($env:ADMIN_API_KEY) -or $env:ADMIN_API_KEY.Length -lt 32) {
    Write-Error 'Set ADMIN_API_KEY to a high-entropy value of at least 32 characters before deploying.'
    exit 1
}

$googleValues = @(
    $env:GOOGLE_OAUTH_CLIENT_ID
    $env:GOOGLE_OAUTH_CLIENT_SECRET
    $env:GOOGLE_OAUTH_REDIRECT_URI
)
$googleValueCount = @($googleValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
if ($googleValueCount -ne 0 -and $googleValueCount -ne 3) {
    Write-Error 'Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI together.'
    exit 1
}

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$infraDir = Join-Path $sourceRoot 'infra'
$templateFile = Join-Path $infraDir 'main.bicep'

if (-not $ParametersFile) {
    $ParametersFile = Join-Path $infraDir 'main.parameters.json'
}

if (-not (Test-Path $templateFile)) {
    Write-Error "Template not found: $templateFile"
    exit 1
}

if (-not (Test-Path $ParametersFile)) {
    Write-Error "Parameters file not found: $ParametersFile"
    exit 1
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error 'Azure CLI (az) is required but was not found on PATH. Install it from https://learn.microsoft.com/cli/azure/install-azure-cli'
    exit 1
}

$deploymentName = "jarvis-infra-$EnvironmentName-$(Get-Date -Format 'yyyyMMddHHmmss')"

Write-Host "Source root     : $sourceRoot"
Write-Host "Template        : $templateFile"
Write-Host "Parameters      : $ParametersFile"
Write-Host "Environment     : $EnvironmentName"
Write-Host "Location        : $Location"
Write-Host "Deployment name : $deploymentName"

$commonArgs = @(
    '--name', $deploymentName
    '--location', $Location
    '--template-file', $templateFile
    '--parameters', $ParametersFile
    '--parameters', "environmentName=$EnvironmentName"
    '--parameters', "location=$Location"
    '--parameters', "adminApiKey=$($env:ADMIN_API_KEY)"
)

if ($googleValueCount -eq 3) {
    $commonArgs += @(
        '--parameters', "googleOAuthClientId=$($env:GOOGLE_OAUTH_CLIENT_ID)"
        '--parameters', "googleOAuthClientSecret=$($env:GOOGLE_OAUTH_CLIENT_SECRET)"
        '--parameters', "googleOAuthRedirectUri=$($env:GOOGLE_OAUTH_REDIRECT_URI)"
    )
}

if ($ValidateOnly) {
    Write-Host "`nValidating deployment (no resources will be created)..." -ForegroundColor Cyan
    az deployment sub validate @commonArgs
}
elseif ($WhatIf) {
    Write-Host "`nRunning what-if analysis (no resources will be created)..." -ForegroundColor Cyan
    az deployment sub what-if @commonArgs
}
else {
    Write-Host "`nStarting deployment..." -ForegroundColor Cyan
    $deployJson = az deployment sub create @commonArgs --output json

    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Deployment failed. See az CLI output above for details.'
        exit 1
    }

    $result = ($deployJson -join "`n") | ConvertFrom-Json
    $outputs = $result.properties.outputs

    Write-Host "`nDeployment succeeded." -ForegroundColor Green
    Write-Host "Resource group : $($outputs.resourceGroupName.value)"
    Write-Host "Function App   : $($outputs.functionAppName.value)"
    Write-Host "API base URL   : $($outputs.apiBaseUrl.value)"
    Write-Host "Key Vault      : $($outputs.keyVaultName.value)"
    Write-Host "Storage account: $($outputs.storageAccountName.value)"
    Write-Host "Speech account : $($outputs.speechAccountName.value)"
    Write-Host "OpenAI account : $($outputs.openAiAccountName.value)"

    Write-Host "`nNext steps:" -ForegroundColor Yellow
    Write-Host '  Deploy backend code : azd deploy azure-backend'
    Write-Host '  Provision a device  : infra/scripts/provision-device.ps1 -DeviceName <name> -StorageAccountName <name>'
}
