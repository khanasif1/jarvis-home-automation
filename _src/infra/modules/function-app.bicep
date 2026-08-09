// Function App module: Linux Azure Functions app (Python) hosting the
// home_assistant_api backend, fronted by a system-assigned managed identity
// used for Key Vault secret access and RBAC-based access to Storage, Speech,
// and Azure OpenAI.
metadata description = 'Provisions the Linux Function App (Python) that runs the Jarvis home-automation backend.'

@description('Azure region for the Function App and its hosting plan.')
param location string

@description('Base name used to derive the Function App and hosting-plan names.')
param baseName string

@description('Tags applied to the Function App resources.')
param tags object = {}

@description('Hosting plan SKU. New deployments use Linux Flex Consumption.')
@allowed([
  'FC1'
])
param planSkuName string = 'FC1'

@description('Maximum number of Flex Consumption instances.')
@minValue(1)
@maxValue(1000)
param maximumInstanceCount int = 20

@description('Memory allocated to each Flex Consumption instance in MB.')
@allowed([
  512
  2048
  4096
])
param instanceMemoryMB int = 2048

@description('Python runtime version for the Function App.')
@allowed([
  '3.10'
  '3.11'
  '3.12'
])
param pythonVersion string = '3.11'

@description('Storage account name backing AzureWebJobsStorage and application data tables.')
param storageAccountName string

@description('Blob endpoint of the storage account (used for identity-based data access app settings).')
param storageBlobEndpoint string

@description('Queue endpoint of the storage account (used by the Functions host identity-based connection).')
param storageQueueEndpoint string

@description('Table endpoint of the storage account (used for identity-based data access app settings).')
param storageTableEndpoint string

@description('Blob container used by Flex Consumption for deployment packages.')
param deploymentContainerName string

@description('Key Vault URI, used to build Key Vault reference app settings.')
param keyVaultUri string

@description('Application Insights connection string.')
@secure()
param appInsightsConnectionString string

@description('Azure AI Speech endpoint.')
param speechEndpoint string

@description('Azure AI Speech region.')
param speechRegion string

@description('Name of the Key Vault secret holding the Speech account key.')
param speechKeySecretName string

@description('Azure OpenAI endpoint.')
param openAiEndpoint string

@description('Azure OpenAI chat-completion deployment name.')
param openAiDeploymentName string

@description('Azure OpenAI data-plane API version used by the backend SDK.')
param openAiApiVersion string = '2024-10-21'

@description('Name of the Key Vault secret holding the Azure OpenAI account key.')
param openAiKeySecretName string

@description('Additional application settings to merge in, e.g. Google OAuth client ids or feature flags. Values that are secrets should be Key Vault references, not raw values.')
param additionalAppSettings array = []

var planName = '${baseName}-plan'
var functionAppName = '${baseName}-func'

resource hostingPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: planSkuName
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'azure-backend'
  })
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    clientAffinityEnabled: false
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageBlobEndpoint}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: maximumInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }
      runtime: {
        name: 'python'
        version: pythonVersion
      }
    }
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      http20Enabled: true
      appSettings: concat([
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: storageBlobEndpoint
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: storageQueueEndpoint
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: storageTableEndpoint
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storageAccountName
        }
        {
          name: 'STORAGE_BLOB_ENDPOINT'
          value: storageBlobEndpoint
        }
        {
          name: 'STORAGE_TABLE_ENDPOINT'
          value: storageTableEndpoint
        }
        {
          name: 'KEY_VAULT_URI'
          value: keyVaultUri
        }
        {
          name: 'SPEECH_ENDPOINT'
          value: speechEndpoint
        }
        {
          name: 'SPEECH_REGION'
          value: speechRegion
        }
        {
          name: 'SPEECH_API_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/${speechKeySecretName})'
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAiEndpoint
        }
        {
          name: 'AZURE_OPENAI_DEPLOYMENT'
          value: openAiDeploymentName
        }
        {
          name: 'AZURE_OPENAI_API_VERSION'
          value: openAiApiVersion
        }
        {
          name: 'AZURE_OPENAI_API_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/${openAiKeySecretName})'
        }
        {
          name: 'AZURE_CLIENT_USE_MANAGED_IDENTITY'
          value: 'true'
        }
      ], additionalAppSettings)
    }
  }
}

resource scmBasicAuthPolicy 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: functionApp
  name: 'scm'
  properties: {
    allow: false
  }
}

resource ftpBasicAuthPolicy 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: functionApp
  name: 'ftp'
  properties: {
    allow: false
  }
}

@description('Resource ID of the Function App.')
output functionAppId string = functionApp.id

@description('Name of the Function App.')
output functionAppName string = functionApp.name

@description('Default hostname of the Function App (no scheme).')
output functionAppHostName string = functionApp.properties.defaultHostName

@description('Base API URL the Pi client and other consumers should call.')
output apiBaseUrl string = 'https://${functionApp.properties.defaultHostName}/api'

@description('Principal ID of the Function App system-assigned managed identity, used for role assignments.')
output functionAppPrincipalId string = functionApp.identity.principalId

@description('Resource ID of the hosting plan.')
output hostingPlanId string = hostingPlan.id
