// container-app.bicepparam — default parameter values for container-app.bicep
//
// Override imageTag at deploy time:
//
//   az deployment group create \
//     --resource-group rg-stock-tools \
//     --template-file infra/azure/container-app.bicep \
//     --parameters infra/azure/container-app.bicepparam \
//                  imageTag=sha-$(git rev-parse --short HEAD)

using './container-app.bicep'

param environmentName = 'dev'
param imageTag        = 'latest'
