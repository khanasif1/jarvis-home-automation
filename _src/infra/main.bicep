// Minimal Jarvis infrastructure: Entra-only storage and Foundry plus one
// always-ready HTTP-streaming Function App.
targetScope = 'subscription'

@description('Short lowercase environment name used in resource names.')
@minLength(2)
@maxLength(16)
param environmentName string

@description('Stable seed used for globally unique resource names.')
@minLength(2)
@maxLength(64)
param resourceNameSeed string = environmentName

@description('Region for the Function App, storage, and monitoring.')
param location string = 'australiaeast'

@description('Region that currently supports the selected GPT Realtime model.')
param foundryLocation string = 'southindia'

@description('Optional resource group name.')
param resourceGroupName string = 'rg-${environmentName}-jarvis'

@description('Fixed canonical lowercase UUID configured on the Raspberry Pi.')
@secure()
param deviceGuid string

@description('Foundry Realtime deployment name.')
param foundryDeploymentName string = 'gpt-realtime-2'

@description('Foundry Realtime model name.')
param foundryModelName string = 'gpt-realtime-2'

@description('Foundry Realtime model version.')
param foundryModelVersion string = '2026-05-06'

@description('Global Standard deployment capacity in thousands of tokens per minute.')
@minValue(1)
param foundryDeploymentCapacity int = 1

@description('Voice returned by the Realtime model.')
param foundryVoice string = 'alloy'

@description('Maximum Flex Consumption scale-out.')
@minValue(40)
@maxValue(1000)
param functionMaximumInstanceCount int = 40

@description('Memory allocated to each Flex Consumption instance.')
@allowed([
  512
  2048
  4096
])
param functionInstanceMemoryMB int = 2048

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param logRetentionInDays int = 30

param tags object = {
  application: 'jarvis-home-automation'
  environment: environmentName
  managedBy: 'bicep'
}

var suffix = uniqueString(subscription().id, resourceNameSeed)
var baseName = 'jarvis-${environmentName}-${suffix}'
var storageAccountName = 'st${suffix}'
var foundryAccountName = 'foundry-${environmentName}-${suffix}'

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

module networking 'modules/networking.bicep' = {
  name: 'networking'
  scope: rg
  params: {
    location: location
    baseName: baseName
    storageAccountName: storage.outputs.storageAccountName
    tags: tags
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    location: foundryLocation
    foundryAccountName: foundryAccountName
    tags: tags
    deploymentName: foundryDeploymentName
    modelName: foundryModelName
    modelVersion: foundryModelVersion
    deploymentCapacity: foundryDeploymentCapacity
  }
}

module functionApp 'modules/function-app.bicep' = {
  name: 'function-app'
  scope: rg
  params: {
    location: location
    baseName: baseName
    tags: tags
    maximumInstanceCount: functionMaximumInstanceCount
    instanceMemoryMB: functionInstanceMemoryMB
    storageAccountName: storage.outputs.storageAccountName
    storageBlobEndpoint: storage.outputs.blobEndpoint
    storageQueueEndpoint: storage.outputs.queueEndpoint
    storageTableEndpoint: storage.outputs.tableEndpoint
    deploymentContainerName: storage.outputs.deploymentContainerName
    virtualNetworkSubnetId: networking.outputs.appSubnetId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    deviceGuid: deviceGuid
    foundryEndpoint: foundry.outputs.foundryEndpoint
    foundryDeploymentName: foundry.outputs.deploymentName
    foundryVoice: foundryVoice
  }
}

module roleAssignments 'modules/role-assignments.bicep' = {
  name: 'role-assignments'
  scope: rg
  params: {
    functionAppPrincipalId: functionApp.outputs.functionAppPrincipalId
    storageAccountName: storage.outputs.storageAccountName
    foundryAccountName: foundry.outputs.foundryAccountName
    appInsightsName: monitoring.outputs.appInsightsName
  }
}

output resourceGroupName string = rg.name
output functionAppName string = functionApp.outputs.functionAppName
output apiBaseUrl string = functionApp.outputs.apiBaseUrl
output storageAccountName string = storage.outputs.storageAccountName
output virtualNetworkName string = networking.outputs.virtualNetworkName
output foundryAccountName string = foundry.outputs.foundryAccountName
output foundryEndpoint string = foundry.outputs.foundryEndpoint
output foundryDeploymentName string = foundry.outputs.deploymentName
output functionAppPrincipalId string = functionApp.outputs.functionAppPrincipalId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_BACKEND_NAME string = functionApp.outputs.functionAppName
output RESOURCE_NAME_SEED string = resourceNameSeed
