targetScope = 'resourceGroup'

param dnsZoneName string
param recordName string
param ipv4Address string

resource dnsZone 'Microsoft.Network/dnsZones@2018-05-01' existing = {
  name: dnsZoneName
}

resource dnsRecord 'Microsoft.Network/dnsZones/A@2018-05-01' = {
  parent: dnsZone
  name: recordName
  properties: {
    TTL: 300
    ARecords: [
      {
        ipv4Address: ipv4Address
      }
    ]
  }
}
