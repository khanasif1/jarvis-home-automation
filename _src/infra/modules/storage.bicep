// Storage module: general-purpose v2 storage account backing the Function App
// runtime (AzureWebJobsStorage) and application data tables (todos, reminders,
// sessions, devices, idempotency, and Google OAuth credentials).
metadata description = 'Provisions the storage account and data tables/containers used by the Jarvis home-automation backend.'

@description('Azure region for the storage account.')
param location string

@description('Base name used to derive the storage account name (must be globally unique, lowercase, alphanumeric).')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('Tags applied to the storage account.')
param tags object = {}

@description('Storage account SKU. Standard_LRS is sufficient for a single-region deployment.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
])
param skuName string = 'Standard_LRS'

@description('Names of the Azure Table Storage tables to create for backend repositories.')
param tableNames array = [
  'Todos'
  'Reminders'
  'Sessions'
  'Devices'
  'Idempotency'
  'GoogleCredentials'
]

@description('Name of the blob container used for Function App deployment packages / azcopy releases.')
param deploymentContainerName string = 'app-package'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: skuName
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource tables 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = [for tableName in tableNames: {
  parent: tableService
  name: tableName
}]

@description('Resource ID of the storage account.')
output storageAccountId string = storageAccount.id

@description('Name of the storage account.')
output storageAccountName string = storageAccount.name

@description('Primary blob endpoint of the storage account.')
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob

@description('Primary table endpoint of the storage account.')
output tableEndpoint string = storageAccount.properties.primaryEndpoints.table

@description('Primary queue endpoint of the storage account.')
output queueEndpoint string = storageAccount.properties.primaryEndpoints.queue

@description('Name of the blob container used for Function App deployment packages.')
output deploymentContainerName string = deploymentContainer.name
