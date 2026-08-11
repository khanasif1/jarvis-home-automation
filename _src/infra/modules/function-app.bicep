// Python 3.11 Flex Consumption Function with one always-ready HTTP instance.

param location string
param baseName string
param tags object = {}

@minValue(40)
@maxValue(1000)
param maximumInstanceCount int = 40

@allowed([
  512
  2048
  4096
])
param instanceMemoryMB int = 2048

param storageAccountName string
param storageBlobEndpoint string
param storageQueueEndpoint string
param storageTableEndpoint string
param deploymentContainerName string
param virtualNetworkSubnetId string

@secure()
param appInsightsConnectionString string

@secure()
param deviceGuid string

param foundryEndpoint string
param foundryDeploymentName string
param foundryVoice string = 'alloy'

var functionAppName = '${baseName}-func'

resource hostingPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: '${baseName}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    clientAffinityEnabled: false
    virtualNetworkSubnetId: virtualNetworkSubnetId
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
        alwaysReady: [
          {
            name: 'http'
            instanceCount: 1
          }
        ]
        triggers: {
          http: {
            perInstanceConcurrency: 4
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      http20Enabled: true
    }
  }
}

resource appSettings 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: functionApp
  name: 'appsettings'
  properties: {
    AzureWebJobsStorage__credential: 'managedidentity'
    AzureWebJobsStorage__blobServiceUri: storageBlobEndpoint
    AzureWebJobsStorage__queueServiceUri: storageQueueEndpoint
    AzureWebJobsStorage__tableServiceUri: storageTableEndpoint
    PYTHON_ENABLE_INIT_INDEXING: '1'
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'Authorization=AAD'
    STORAGE_ACCOUNT_NAME: storageAccountName
    DEVICE_GUID: deviceGuid
    AZURE_OPENAI_ENDPOINT: foundryEndpoint
    AZURE_OPENAI_DEPLOYMENT_NAME: foundryDeploymentName
    AZURE_OPENAI_VOICE: foundryVoice
    AZURE_CLIENT_USE_MANAGED_IDENTITY: 'true'
    FOUNDRY_RESPONSE_TIMEOUT_SECONDS: '60'
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

output functionAppName string = functionApp.name
output apiBaseUrl string = 'https://${functionApp.properties.defaultHostName}/api'
output functionAppPrincipalId string = functionApp.identity.principalId
