// Speech module: Azure AI Speech (Cognitive Services) account used by the
// backend for speech-to-text and text-to-speech.
metadata description = 'Provisions the Azure AI Speech resource for the Jarvis home-automation backend.'

@description('Azure region for the Speech resource. Speech services are available in a subset of regions.')
param location string

@description('Name of the Speech (Cognitive Services) account.')
param speechAccountName string

@description('Tags applied to the Speech resource.')
param tags object = {}

@description('Pricing tier for the Speech resource.')
@allowed([
  'F0'
  'S0'
])
param sku string = 'S0'

resource speechAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: speechAccountName
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: {
    name: sku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: speechAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

@description('Resource ID of the Speech account.')
output speechAccountId string = speechAccount.id

@description('Name of the Speech account.')
output speechAccountName string = speechAccount.name

@description('Region of the Speech account, required by the Speech SDK alongside the key/token.')
output speechRegion string = location

@description('Endpoint of the Speech account.')
output speechEndpoint string = speechAccount.properties.endpoint

@description('Primary key for the Speech account. Sensitive: only consumed to seed a Key Vault secret.')
@secure()
output speechKey string = speechAccount.listKeys().key1
