targetScope = 'resourceGroup'

@description('Azure region for the deployment.')
param location string = resourceGroup().location

@description('Short lowercase prefix used in Azure resource names.')
@minLength(3)
@maxLength(12)
param namePrefix string = 'pdfremed'

@description('Resource ID of an existing public Azure DNS zone.')
param dnsZoneResourceId string

@description('Relative DNS label created in the existing zone.')
param webDnsLabel string = 'pdf'

@description('Linux VM size. Four concurrent PDF jobs are sized for eight vCPUs.')
param vmSize string = 'Standard_D8s_v5'

@description('Administrator username. Port 22 is not exposed by the NSG.')
param adminUsername string = 'azureadmin'

@secure()
@description('SSH public key retained for break-glass access through Azure Bastion or serial console.')
param adminSshPublicKey string

@description('Azure Files share quota in GiB.')
param fileShareQuotaGiB int = 1024

var suffix = uniqueString(resourceGroup().id)
var baseName = toLower('${namePrefix}-${suffix}')
var compactName = take(toLower('${namePrefix}${suffix}'), 18)
var storageName = take('st${compactName}', 24)
var acrName = take('acr${compactName}', 50)
var keyVaultName = take('kv-${baseName}', 24)
var vmName = take('vm-${baseName}', 64)
var fileShareName = 'pdf-data'
var backupFabric = 'Azure'
var backupManagementType = 'AzureStorage'
var backupScheduleTimes = [
  '2020-01-01T05:00:00Z'
]
var dnsParts = split(dnsZoneResourceId, '/')
var dnsSubscriptionId = dnsParts[2]
var dnsResourceGroupName = dnsParts[4]
var dnsZoneName = dnsParts[8]
var webHostname = '${webDnsLabel}.${dnsZoneName}'
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var storageFileDataSmbShareContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '0c867c2a-1d8c-454a-a3db-ab2ea1bdc8bb'
)
var monitoringMetricsPublisherRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${baseName}'
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-${baseName}'
  location: location
  properties: {
    securityRules: [
      {
        name: 'AllowHttp'
        properties: {
          priority: 100
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '80'
          sourceAddressPrefix: 'Internet'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'AllowHttps'
        properties: {
          priority: 110
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: 'Internet'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-${baseName}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.42.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'app'
        properties: {
          addressPrefix: '10.42.1.0/24'
          networkSecurityGroup: {
            id: nsg.id
          }
          serviceEndpoints: [
            {
              service: 'Microsoft.Storage'
            }
            {
              service: 'Microsoft.KeyVault'
            }
          ]
        }
      }
    ]
  }
}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-${baseName}'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-${baseName}'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'primary'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'app')
          }
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    shareDeleteRetentionPolicy: {
      enabled: true
      days: 30
    }
  }
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    accessTier: 'TransactionOptimized'
    enabledProtocols: 'SMB'
    shareQuota: fileShareQuotaGiB
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    dataEndpointEnabled: false
    policies: {
      retentionPolicy: {
        days: 30
        status: 'enabled'
      }
    }
    publicNetworkAccess: 'Enabled'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
    softDeleteRetentionInDays: 90
  }
}

resource backupVault 'Microsoft.RecoveryServices/vaults@2023-06-01' = {
  name: 'rsv-${baseName}'
  location: location
  sku: {
    name: 'RS0'
    tier: 'Standard'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
  }
}

resource backupPolicy 'Microsoft.RecoveryServices/vaults/backupPolicies@2022-02-01' = {
  parent: backupVault
  name: 'daily-30-days'
  properties: {
    backupManagementType: backupManagementType
    schedulePolicy: {
      schedulePolicyType: 'SimpleSchedulePolicy'
      scheduleRunFrequency: 'Daily'
      scheduleRunTimes: backupScheduleTimes
    }
    retentionPolicy: {
      dailySchedule: {
        retentionTimes: backupScheduleTimes
        retentionDuration: {
          count: 30
          durationType: 'Days'
        }
      }
      retentionPolicyType: 'LongTermRetentionPolicy'
    }
    timeZone: 'UTC'
    workLoadType: 'AzureFileShare'
  }
}

resource protectionContainer 'Microsoft.RecoveryServices/vaults/backupFabrics/protectionContainers@2022-02-01' = {
  name: '${backupVault.name}/${backupFabric}/storagecontainer;Storage;${resourceGroup().name};${storage.name}'
  properties: {
    backupManagementType: backupManagementType
    containerType: 'StorageContainer'
    sourceResourceId: storage.id
  }
  dependsOn: [
    backupPolicy
    fileShare
  ]
}

resource protectedFileShare 'Microsoft.RecoveryServices/vaults/backupFabrics/protectionContainers/protectedItems@2022-02-01' = {
  parent: protectionContainer
  name: 'AzureFileShare;${fileShare.name}'
  properties: {
    protectedItemType: 'AzureFileShareProtectedItem'
    sourceResourceId: storage.id
    policyId: backupPolicy.id
  }
}

resource storageKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-files-key'
  properties: {
    value: storage.listKeys().keys[0].value
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
          properties: {
            primary: true
          }
        }
      ]
    }
    osProfile: {
      adminUsername: adminUsername
      computerName: take(vmName, 15)
      customData: base64(loadTextContent('cloud-init.yaml'))
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [
            {
              keyData: adminSshPublicKey
              path: '/home/${adminUsername}/.ssh/authorized_keys'
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        caching: 'ReadWrite'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
        diskSizeGB: 128
      }
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
  }
  dependsOn: [
    storageKeySecret
  ]
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, vm.id, acrPullRoleId)
  scope: acr
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource keyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, vm.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

resource storageFileDataSmbShareContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(fileShare.id, vm.id, storageFileDataSmbShareContributorRoleId)
  scope: fileShare
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageFileDataSmbShareContributorRoleId
  }
}

module dnsRecord 'dns-record.bicep' = {
  name: 'pdf-web-dns-record'
  scope: resourceGroup(dnsSubscriptionId, dnsResourceGroupName)
  params: {
    dnsZoneName: dnsZoneName
    recordName: webDnsLabel
    ipv4Address: publicIp.properties.ipAddress
  }
}

resource monitorAgent 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AzureMonitorLinuxAgent'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Monitor'
    type: 'AzureMonitorLinuxAgent'
    typeHandlerVersion: '1.33'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
  }
}

resource dcr 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-${baseName}'
  location: location
  properties: {
    dataSources: {
      syslog: [
        {
          name: 'syslog'
          facilityNames: [
            'auth'
            'authpriv'
            'daemon'
            'syslog'
            'user'
          ]
          logLevels: [
            'Info'
            'Notice'
            'Warning'
            'Error'
            'Critical'
            'Alert'
            'Emergency'
          ]
          streams: [
            'Microsoft-Syslog'
          ]
        }
      ]
    }
    destinations: {
      logAnalytics: [
        {
          name: 'workspace'
          workspaceResourceId: logAnalytics.id
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Microsoft-Syslog'
        ]
        destinations: [
          'workspace'
        ]
      }
    ]
  }
}

resource monitoringMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dcr.id, vm.id, monitoringMetricsPublisherRoleId)
  scope: dcr
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringMetricsPublisherRoleId
  }
}

resource dcrAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = {
  name: 'configurationAccessEndpoint'
  scope: vm
  properties: {
    dataCollectionRuleId: dcr.id
    description: 'Collect VM and Docker journald messages through syslog.'
  }
}

resource availabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${baseName}-heartbeat'
  location: 'global'
  properties: {
    description: 'Alert when the VM stops reporting availability.'
    severity: 1
    enabled: true
    scopes: [
      vm.id
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'VmAvailability'
          metricName: 'VmAvailabilityMetric'
          metricNamespace: 'Microsoft.Compute/virtualMachines'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    targetResourceType: 'Microsoft.Compute/virtualMachines'
    targetResourceRegion: location
    actions: []
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = keyVault.name
output storageAccountName string = storage.name
output storageShareName string = fileShare.name
output vmName string = vm.name
output publicIpAddress string = publicIp.properties.ipAddress
output webHostname string = webHostname
output logAnalyticsWorkspaceId string = logAnalytics.id
output recoveryServicesVaultName string = backupVault.name
