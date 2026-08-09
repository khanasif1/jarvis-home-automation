// Jarvis home-automation infrastructure — subscription-scope entry point.
//
// Provisions a resource group plus all Azure resources required to run the
// azure-backend Function App: storage, Key Vault, Log Analytics/App Insights,
// Azure AI Speech, Azure OpenAI, the Function App itself, and the RBAC role
// assignments that wire its managed identity to every dependency.
//
// Deploy with either:
//   az deployment sub create --location <region> --template-file main.bicep --parameters main.parameters.json
// or:
//   azd provision   (azd resolves this template via azure.yaml at the _src root)
targetScope = 'subscription'

@description('Unique short environment name (for example jarvis-a1b2). Used to derive resource names and as the azd environment name.')
@minLength(1)
@maxLength(16)
param environmentName string

@description('Stable seed used to derive globally unique resource names. Lifecycle tooling rotates it after uninstall so retained soft-deleted names do not block reinstall.')
@minLength(1)
@maxLength(64)
param resourceNameSeed string = environmentName

@description('Azure region for all resources.')
param location string

@description('Optional explicit resource group name. Defaults to rg-<environmentName>-jarvis.')
param resourceGroupName string = 'rg-${environmentName}-jarvis'

@description('Common tags applied to every resource.')
param tags object = {
  application: 'jarvis-home-automation'
  'azd-env-name': environmentName
  environment: environmentName
  managedBy: 'bicep'
}

@description('Hosting plan SKU for the Function App. New deployments use Linux Flex Consumption.')
@allowed([
  'FC1'
])
param functionAppPlanSku string = 'FC1'

@description('Maximum number of Flex Consumption instances.')
@minValue(1)
@maxValue(1000)
param functionMaximumInstanceCount int = 20

@description('Memory allocated to each Flex Consumption instance in MB.')
@allowed([
  512
  2048
  4096
])
param functionInstanceMemoryMB int = 2048

@description('Python runtime version for the Function App.')
@allowed([
  '3.10'
  '3.11'
  '3.12'
])
param pythonVersion string = '3.11'

@description('Pricing tier for the Azure AI Speech resource.')
@allowed([
  'F0'
  'S0'
])
param speechSku string = 'S0'

@description('Azure OpenAI chat-completion model deployment name.')
param openAiDeploymentName string = 'gpt-4.1-mini'

@description('Azure OpenAI underlying model name.')
param openAiModelName string = 'gpt-4.1-mini'

@description('Azure OpenAI model version.')
param openAiModelVersion string = '2025-04-14'

@description('Azure OpenAI data-plane API version used by the backend SDK.')
param openAiApiVersion string = '2024-10-21'

@description('Azure OpenAI deployment capacity in units of 1,000 tokens-per-minute.')
param openAiDeploymentCapacity int = 10

@description('Whether to enable Key Vault purge protection. Keep true for production; may be set false for short-lived dev/test environments.')
param enableKeyVaultPurgeProtection bool = true

@description('Log Analytics data retention in days.')
@minValue(30)
@maxValue(730)
param logRetentionInDays int = 30

@description('High-entropy key used to authorize backend administrative routes and sign Google OAuth state. Store it only in secure deployment inputs.')
@secure()
@minLength(32)
param adminApiKey string

@description('Optional Google OAuth web client ID. Leave blank to keep Google integrations disabled.')
param googleOAuthClientId string = ''

@description('Optional Google OAuth web client secret. Required when googleOAuthClientId is set.')
@secure()
param googleOAuthClientSecret string = ''

@description('Optional Google OAuth callback URL, for example https://<function-app>.azurewebsites.net/api/google/oauth/callback.')
param googleOAuthRedirectUri string = ''

// Deterministic, globally-unique-safe suffix derived from the lifecycle seed.
// The seed is stable across updates and rotated only after uninstall.
var uniqueSuffix = uniqueString(subscription().id, resourceNameSeed, location)
var baseName = 'jarvis-${environmentName}-${uniqueSuffix}'
// Keep the complete unique suffix in globally unique names while respecting
// the 24-character Storage account and Key Vault name limits.
var storageAccountName = 'st${uniqueSuffix}'
var keyVaultName = 'kv-${uniqueSuffix}'
var speechAccountName = 'speech-jarvis-${environmentName}-${uniqueSuffix}'
var openAiAccountName = 'aoai-jarvis-${environmentName}-${uniqueSuffix}'

var speechKeySecretName = 'speech-account-key'
var openAiKeySecretName = 'openai-account-key'
var adminApiKeySecretName = 'admin-api-key'
var googleOAuthClientSecretName = 'google-oauth-client-secret'
var googleOAuthEnabled = !empty(googleOAuthClientId) || !empty(googleOAuthClientSecret) || !empty(googleOAuthRedirectUri)
var googleOAuthComplete = !googleOAuthEnabled
  ? false
  : (!empty(googleOAuthClientId) && !empty(googleOAuthClientSecret) && !empty(googleOAuthRedirectUri)
      ? true
      : fail('Google OAuth configuration is incomplete. Set client ID, client secret, and redirect URI together.'))

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: union(tags, {
    jarvisResourceNameSeed: resourceNameSeed
  })
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    baseName: baseName
    tags: tags
    logRetentionInDays: logRetentionInDays
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    storageAccountName: storageAccountName
    tags: tags
  }
}

module speech 'modules/speech.bicep' = {
  name: 'speech'
  scope: rg
  params: {
    location: location
    speechAccountName: speechAccountName
    tags: tags
    sku: speechSku
  }
}

module openai 'modules/openai.bicep' = {
  name: 'openai'
  scope: rg
  params: {
    location: location
    openAiAccountName: openAiAccountName
    tags: tags
    deploymentName: openAiDeploymentName
    modelName: openAiModelName
    modelVersion: openAiModelVersion
    deploymentCapacity: openAiDeploymentCapacity
  }
}

// Key Vault is created and seeded with secrets derived from the Speech and
// OpenAI module outputs in a single module deployment so that
// account keys pass directly from ARM resource outputs into vault secrets
// without ever being written to logs, app settings, or source control.
module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  scope: rg
  params: {
    location: location
    keyVaultName: keyVaultName
    tags: tags
    enablePurgeProtection: enableKeyVaultPurgeProtection
    secretsToSet: union({
      '${speechKeySecretName}': speech.outputs.speechKey
      '${openAiKeySecretName}': openai.outputs.openAiKey
      '${adminApiKeySecretName}': adminApiKey
    }, googleOAuthComplete ? {
      '${googleOAuthClientSecretName}': googleOAuthClientSecret
    } : {})
  }
}

module functionApp 'modules/function-app.bicep' = {
  name: 'function-app'
  scope: rg
  params: {
    location: location
    baseName: baseName
    tags: tags
    planSkuName: functionAppPlanSku
    pythonVersion: pythonVersion
    maximumInstanceCount: functionMaximumInstanceCount
    instanceMemoryMB: functionInstanceMemoryMB
    storageAccountName: storage.outputs.storageAccountName
    storageBlobEndpoint: storage.outputs.blobEndpoint
    storageQueueEndpoint: storage.outputs.queueEndpoint
    storageTableEndpoint: storage.outputs.tableEndpoint
    deploymentContainerName: storage.outputs.deploymentContainerName
    keyVaultUri: keyVault.outputs.keyVaultUri
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    speechEndpoint: speech.outputs.speechEndpoint
    speechRegion: speech.outputs.speechRegion
    speechKeySecretName: speechKeySecretName
    openAiEndpoint: openai.outputs.openAiEndpoint
    openAiDeploymentName: openai.outputs.openAiDeploymentName
    openAiApiVersion: openAiApiVersion
    openAiKeySecretName: openAiKeySecretName
    additionalAppSettings: concat([
      {
        name: 'APP_ENVIRONMENT'
        value: 'production'
      }
      {
        name: 'ADMIN_API_KEY'
        value: '@Microsoft.KeyVault(SecretUri=${keyVault.outputs.keyVaultUri}secrets/${adminApiKeySecretName})'
      }
    ], googleOAuthComplete ? [
      {
        name: 'GOOGLE_OAUTH_CLIENT_ID'
        value: googleOAuthClientId
      }
      {
        name: 'GOOGLE_OAUTH_CLIENT_SECRET'
        value: '@Microsoft.KeyVault(SecretUri=${keyVault.outputs.keyVaultUri}secrets/${googleOAuthClientSecretName})'
      }
      {
        name: 'GOOGLE_OAUTH_REDIRECT_URI'
        value: googleOAuthRedirectUri
      }
    ] : [])
  }
}

module roleAssignments 'modules/role-assignments.bicep' = {
  name: 'role-assignments'
  scope: rg
  params: {
    functionAppPrincipalId: functionApp.outputs.functionAppPrincipalId
    keyVaultName: keyVault.outputs.keyVaultName
    storageAccountName: storage.outputs.storageAccountName
    speechAccountName: speech.outputs.speechAccountName
    openAiAccountName: openai.outputs.openAiAccountName
  }
}

// ---------------------------------------------------------------------------
// Outputs consumed by the azure-backend deployment and by the Raspberry Pi
// device provisioning/config flow (in particular apiBaseUrl).
// ---------------------------------------------------------------------------

@description('Name of the resource group all resources were deployed into.')
output resourceGroupName string = rg.name

@description('Name of the Function App running the backend.')
output functionAppName string = functionApp.outputs.functionAppName

@description('Base API URL the Pi client and other consumers should call, e.g. https://<app>.azurewebsites.net/api.')
output apiBaseUrl string = functionApp.outputs.apiBaseUrl

@description('Name of the storage account backing the Function App and its data tables.')
output storageAccountName string = storage.outputs.storageAccountName

@description('Name of the Key Vault holding backend secrets.')
output keyVaultName string = keyVault.outputs.keyVaultName

@description('URI of the Key Vault holding backend secrets.')
output keyVaultUri string = keyVault.outputs.keyVaultUri

@description('Name of the Azure AI Speech account.')
output speechAccountName string = speech.outputs.speechAccountName

@description('Endpoint of the Azure AI Speech account.')
output speechEndpoint string = speech.outputs.speechEndpoint

@description('Region of the Azure AI Speech account.')
output speechRegion string = speech.outputs.speechRegion

@description('Name of the Azure OpenAI account.')
output openAiAccountName string = openai.outputs.openAiAccountName

@description('Endpoint of the Azure OpenAI account.')
output openAiEndpoint string = openai.outputs.openAiEndpoint

@description('Name of the Azure OpenAI chat-completion deployment.')
output openAiDeploymentName string = openai.outputs.openAiDeploymentName

@description('Name of the Log Analytics workspace collecting backend telemetry.')
output logAnalyticsWorkspaceName string = monitoring.outputs.logAnalyticsWorkspaceName

@description('Name of the Application Insights component collecting backend telemetry.')
output appInsightsName string = monitoring.outputs.appInsightsName

@description('Principal ID of the Function App system-assigned managed identity.')
output functionAppPrincipalId string = functionApp.outputs.functionAppPrincipalId

// Conventional azd outputs persisted into the environment after provisioning.
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_BACKEND_NAME string = functionApp.outputs.functionAppName
output RESOURCE_NAME_SEED string = resourceNameSeed
