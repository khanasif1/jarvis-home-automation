// Monitoring module: Log Analytics workspace + workspace-based Application Insights
// Used by the Function App for logs, traces, and metrics.
metadata description = 'Provisions Log Analytics workspace and Application Insights for the Jarvis home-automation backend.'

@description('Azure region for all monitoring resources.')
param location string

@description('Base name used to derive monitoring resource names.')
param baseName string

@description('Tags applied to all monitoring resources.')
param tags object = {}

@description('Log Analytics workspace daily ingestion cap in GB. -1 disables the cap.')
param dailyQuotaGb int = -1

@description('Number of days to retain Log Analytics data.')
@minValue(30)
@maxValue(730)
param logRetentionInDays int = 30

var logAnalyticsWorkspaceName = '${baseName}-law'
var appInsightsName = '${baseName}-appi'

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionInDays
    workspaceCapping: dailyQuotaGb == -1 ? null : {
      dailyQuotaGb: dailyQuotaGb
    }
    features: {
      disableLocalAuth: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

@description('Resource ID of the Log Analytics workspace.')
output logAnalyticsWorkspaceId string = logAnalyticsWorkspace.id

@description('Name of the Log Analytics workspace.')
output logAnalyticsWorkspaceName string = logAnalyticsWorkspace.name

@description('Resource ID of the Application Insights component.')
output appInsightsId string = appInsights.id

@description('Name of the Application Insights component.')
output appInsightsName string = appInsights.name

@description('Application Insights endpoint-identification string; ingestion is authenticated with Entra.')
@secure()
output appInsightsConnectionString string = appInsights.properties.ConnectionString
