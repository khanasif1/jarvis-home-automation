// Microsoft Foundry resource with local/key authentication disabled.

param location string
param foundryAccountName string
param tags object = {}
param deploymentName string = 'gpt-realtime-2'
param modelName string = 'gpt-realtime-2'
param modelVersion string = '2026-05-06'

@minValue(1)
param deploymentCapacity int = 1

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryAccountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: foundryAccountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource realtimeDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundryAccount
  name: deploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: deploymentCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    raiPolicyName: 'Microsoft.Default'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

output foundryAccountName string = foundryAccount.name
output foundryEndpoint string = 'https://${foundryAccount.name}.services.ai.azure.com'
output deploymentName string = realtimeDeployment.name
