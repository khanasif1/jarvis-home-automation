// Key Vault module: RBAC-authorized vault holding backend secrets (Speech key,
// OpenAI key, Google OAuth client secret, etc.).
// Access is granted exclusively through Azure RBAC role assignments (see
// role-assignments.bicep) -- no access policies and no secrets are ever
// written into source control or app settings in plain text.
metadata description = 'Provisions an RBAC-authorized Key Vault for the Jarvis home-automation backend secrets.'

@description('Azure region for the Key Vault.')
param location string

@description('Name of the Key Vault (must be globally unique, 3-24 chars).')
@minLength(3)
@maxLength(24)
param keyVaultName string

@description('Tags applied to the Key Vault.')
param tags object = {}

@description('Azure AD tenant ID that the Key Vault trusts.')
param tenantId string = subscription().tenantId

@description('Enable purge protection. Recommended true for production; can be set false for ephemeral dev/test environments.')
param enablePurgeProtection bool = true

@description('Soft-delete retention in days.')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 90

@description('Secrets to seed into the vault as ARM control-plane resources. Keys are secret names, values are secret values. Values flow only through the deployment engine and are never persisted to logs or outputs.')
@secure()
param secretsToSet object = {}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

@batchSize(1)
resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for secretName in items(secretsToSet): {
  parent: keyVault
  name: secretName.key
  properties: {
    value: secretName.value
  }
}]

@description('Resource ID of the Key Vault.')
output keyVaultId string = keyVault.id

@description('Name of the Key Vault.')
output keyVaultName string = keyVault.name

@description('URI of the Key Vault, used to build Key Vault reference app settings.')
output keyVaultUri string = keyVault.properties.vaultUri
