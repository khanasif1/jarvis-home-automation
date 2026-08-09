// OpenAI module: Azure OpenAI (Cognitive Services) account plus a single
// model deployment used by the backend's AI orchestrator.
metadata description = 'Provisions the Azure OpenAI resource and chat-model deployment for the Jarvis home-automation backend.'

@description('Azure region for the Azure OpenAI resource. Azure OpenAI is available in a subset of regions.')
param location string

@description('Name of the Azure OpenAI (Cognitive Services) account.')
param openAiAccountName string

@description('Tags applied to the Azure OpenAI resource.')
param tags object = {}

@description('Pricing tier for the Azure OpenAI resource.')
@allowed([
  'S0'
])
param sku string = 'S0'

@description('Name of the model deployment (used by the backend as the chat completion deployment id).')
param deploymentName string = 'gpt-4.1-mini'

@description('Underlying model name to deploy.')
param modelName string = 'gpt-4.1-mini'

@description('Model version to deploy.')
param modelVersion string = '2025-04-14'

@description('Deployment SKU capacity in units of 1,000 tokens-per-minute (TPM).')
param deploymentCapacity int = 10

@description('Deployment SKU name. Standard is pay-as-you-go; GlobalStandard spreads across Azure regions.')
@allowed([
  'Standard'
  'GlobalStandard'
])
param deploymentSkuName string = 'GlobalStandard'

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: openAiAccountName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: sku
  }
  properties: {
    customSubDomainName: openAiAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAiAccount
  name: deploymentName
  sku: {
    name: deploymentSkuName
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

@description('Resource ID of the Azure OpenAI account.')
output openAiAccountId string = openAiAccount.id

@description('Name of the Azure OpenAI account.')
output openAiAccountName string = openAiAccount.name

@description('Endpoint of the Azure OpenAI account.')
output openAiEndpoint string = openAiAccount.properties.endpoint

@description('Name of the chat-completion model deployment the backend should target.')
output openAiDeploymentName string = chatDeployment.name

@description('Primary key for the Azure OpenAI account. Sensitive: only consumed to seed a Key Vault secret.')
@secure()
output openAiKey string = openAiAccount.listKeys().key1
