// stock-tools-api — Azure Container App infrastructure
//
// Resources deployed:
//   - Log Analytics Workspace          (diagnostics)
//   - Azure Container Registry (ACR)   (image store)
//   - Container Apps Environment       (shared compute plane)
//   - User-Assigned Managed Identity   (ACR pull + future RBAC)
//   - Container App                    (the FastAPI service)
//
// Usage:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/azure/container-app.bicep \
//     --parameters infra/azure/container-app.bicepparam

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Short environment tag appended to every resource name (e.g. dev, prod)')
param environmentName string = 'dev'

@description('Container image tag to deploy (e.g. latest or a specific SHA)')
param imageTag string = 'latest'

@description('Finnhub API key injected as a Container App secret')
@secure()
param finnhubApiKey string = ''

// ── Derived names ─────────────────────────────────────────────────────────────

var appName    = 'stock-tools-api'
var prefix     = '${appName}-${environmentName}'
var acrName    = replace('acr${appName}${environmentName}', '-', '')  // ACR names: alphanumeric only
var imageName  = '${acrName}.azurecr.io/${appName}:${imageTag}'

// ── Log Analytics Workspace ───────────────────────────────────────────────────

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ── Azure Container Registry ──────────────────────────────────────────────────

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false   // use managed identity pull, not admin credentials
  }
}

// ── User-Assigned Managed Identity ────────────────────────────────────────────

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-identity'
  location: location
}

// Grant the identity the built-in AcrPull role on the registry
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Container Apps Environment ────────────────────────────────────────────────

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Container App ─────────────────────────────────────────────────────────────

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-app'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: finnhubApiKey != '' ? [
        {
          name: 'finnhub-key'
          value: finnhubApiKey
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: appName
          image: imageName
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: finnhubApiKey != '' ? [
            {
              name: 'FINNHUB_KEY'
              secretRef: 'finnhub-key'
            }
          ] : []
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0   // scale to zero when idle
        maxReplicas: 3
        rules: [
          {
            name: 'http-scale'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

@description('Public FQDN of the Container App')
output appFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Full ACR login server (e.g. acrstock...dev.azurecr.io)')
output acrLoginServer string = acr.properties.loginServer

@description('Resource ID of the user-assigned managed identity')
output identityId string = identity.id

@description('Client ID of the user-assigned managed identity (use in GitHub OIDC)')
output identityClientId string = identity.properties.clientId
