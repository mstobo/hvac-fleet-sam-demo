# Production Access Architecture Guide
## Solace Schema Registry on AWS EKS

This guide covers production-grade network access patterns for Schema Registry deployed on AWS EKS.

## Table of Contents
1. [Access Patterns Overview](#access-patterns-overview)
2. [Architecture Options](#architecture-options)
3. [Implementation Details](#implementation-details)
4. [Security Considerations](#security-considerations)

---

## Access Patterns Overview

### Common Access Scenarios

| User Type | Access Method | Network Path | Authentication |
|-----------|--------------|--------------|----------------|
| Internal Developers | Corporate LAN | Direct to Private LB | SSO/LDAP |
| Remote Workers (VPN) | VPN Client | VPN → Private LB | SSO/LDAP + MFA |
| Remote Workers (VDI) | Virtual Desktop | VDI → Private LB | SSO/LDAP + MFA |
| CI/CD Pipelines | Service Accounts | VPC Peering/Transit Gateway | API Keys/IAM |
| MQTT Applications | Pub/Sub Clients | Direct to Solace Broker | Client Certs/OAuth |
| Operations Team | Monitoring/Admin | VPN/Bastion → Private LB | SSO + MFA |

---

## Architecture Options

### Option 1: Private Load Balancer + VPN/VDI (Recommended)

**Best for**: Enterprises with existing VPN/VDI infrastructure

```
┌─────────────────────────────────────────────────────────────────┐
│                        AWS Cloud (VPC)                          │
│                                                                 │
│  ┌────────────────────────────────────────────────────────┐   │
│  │              Private Subnet (10.0.1.0/24)              │   │
│  │                                                         │   │
│  │  ┌──────────────────────┐                             │   │
│  │  │  Internal NLB        │                             │   │
│  │  │  (No Public IP)      │                             │   │
│  │  └──────┬───────────────┘                             │   │
│  │         │                                              │   │
│  │         ▼                                              │   │
│  │  ┌──────────────────────┐                             │   │
│  │  │ NGINX Ingress        │                             │   │
│  │  │ Controller           │                             │   │
│  │  └──────┬───────────────┘                             │   │
│  │         │                                              │   │
│  │         ▼                                              │   │
│  │  ┌──────────────────────────────────────┐            │   │
│  │  │   Schema Registry Pods               │            │   │
│  │  │   - Backend (API)                    │            │   │
│  │  │   - UI                               │            │   │
│  │  │   - IDP                              │            │   │
│  │  └──────────────────────────────────────┘            │   │
│  └─────────────────────────────────────────────────────┘   │
│         ▲                           ▲                       │
└─────────┼───────────────────────────┼───────────────────────┘
          │                           │
          │                           │
  ┌───────┴────────┐         ┌────────┴──────────┐
  │  AWS VPN       │         │  AWS Direct Connect│
  │  (Site-to-Site)│         │  or VPN Gateway    │
  └───────┬────────┘         └────────┬──────────┘
          │                           │
  ┌───────┴────────────────────────────┴──────────┐
  │     Corporate Network (On-Prem)               │
  │                                                │
  │  ┌─────────────┐    ┌──────────────────────┐ │
  │  │ VPN Users   │    │  VDI Infrastructure  │ │
  │  │ (Laptop +   │    │  (Citrix/Horizon)    │ │
  │  │  VPN Client)│    │                      │ │
  │  └─────────────┘    └──────────────────────┘ │
  │                                                │
  │  ┌─────────────────────────────────────────┐  │
  │  │  Internal Users (Corporate LAN)         │  │
  │  └─────────────────────────────────────────┘  │
  └────────────────────────────────────────────────┘
```

**Configuration:**

```yaml
# NGINX Ingress Controller - Internal Load Balancer
controller:
  service:
    type: LoadBalancer
    annotations:
      # Make it internal (no public IP)
      service.beta.kubernetes.io/aws-load-balancer-scheme: "internal"
      service.beta.kubernetes.io/aws-load-balancer-internal: "true"
      
      # Restrict access to corporate network CIDR blocks
      service.beta.kubernetes.io/aws-load-balancer-source-ranges: "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
      
      # Use Network Load Balancer (better for long-lived connections)
      service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
      
      # Enable cross-zone load balancing
      service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled: "true"
      
      # Custom security groups (optional)
      service.beta.kubernetes.io/aws-load-balancer-security-groups: "sg-xxxxxxxxx"

ingress:
  enabled: true
  # Use private DNS hostname (via Route53 Private Hosted Zone)
  hostNameSuffix: "schema-registry.internal.yourcompany.com"
  tls:
    enabled: true
    secretName: schema-registry-tls-secret
```

**Access Methods:**

1. **Internal LAN Users**: Direct access via private DNS
2. **VPN Users**: Connect to corporate VPN → Access via private DNS
3. **VDI Users**: Log into VDI → Access via private DNS (VDI in corporate network)

---

### Option 2: AWS PrivateLink (Most Secure)

**Best for**: Multi-account AWS environments, strict security requirements

```
┌──────────────────────────────────────────────────────────────┐
│              Producer VPC (Schema Registry)                  │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │  VPC Endpoint Service                              │    │
│  │  └──────┬──────────────────────────────┘          │    │
│  │         ▼                                          │    │
│  │  ┌─────────────────────┐                          │    │
│  │  │  Network Load        │                          │    │
│  │  │  Balancer (Internal) │                          │    │
│  │  └──────┬──────────────┘                          │    │
│  │         ▼                                          │    │
│  │  ┌─────────────────────┐                          │    │
│  │  │  Schema Registry    │                          │    │
│  │  └─────────────────────┘                          │    │
│  └────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
                         ▲
                         │ AWS PrivateLink
                         │ (No Internet Gateway)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              Consumer VPC (Client Applications)              │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │  VPC Endpoint                                      │    │
│  │  (Interface Endpoint)                              │    │
│  │                                                     │    │
│  │  ┌─────────────────────┐                          │    │
│  │  │  Client App         │                          │    │
│  │  │  (MQTT Publisher/   │                          │    │
│  │  │   Subscriber)       │                          │    │
│  │  └─────────────────────┘                          │    │
│  └────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

**Setup Steps:**

```bash
# 1. Create VPC Endpoint Service (in Schema Registry VPC)
aws ec2 create-vpc-endpoint-service-configuration \
  --network-load-balancer-arns arn:aws:elasticloadbalancing:... \
  --acceptance-required

# 2. Create VPC Endpoint (in Consumer VPC)
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxxxxxxxx \
  --service-name com.amazonaws.vpce.us-east-2.vpce-svc-xxxxxxxxx \
  --vpc-endpoint-type Interface \
  --subnet-ids subnet-xxxxxxxxx subnet-yyyyyyyyy \
  --security-group-ids sg-xxxxxxxxx

# 3. Create Private Hosted Zone in Route53
aws route53 create-hosted-zone \
  --name schema-registry.internal \
  --vpc VPCRegion=us-east-2,VPCId=vpc-xxxxxxxxx \
  --caller-reference $(date +%s)
```

**Use Cases:**
- Multi-account AWS setups (dev/staging/prod accounts)
- Partner/vendor access without VPN
- Micro-segmentation between application tiers

---

### Option 3: Hybrid (Public UI + Private API)

**Best for**: Public documentation, private operations

```
                    Internet
                       │
                       ▼
            ┌──────────────────────┐
            │  Public ALB          │
            │  (WAF + CloudFront)  │
            └──────────┬───────────┘
                       │ (Read-only UI)
                       ▼
            ┌──────────────────────┐
            │  Schema Registry UI  │
            │  (Public - Read Only)│
            └──────────────────────┘

    Corporate Network          AWS Private
           │                        │
           └───────VPN──────────────┤
                                    ▼
                         ┌──────────────────────┐
                         │  Internal NLB        │
                         │  (Write API)         │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │  Schema Registry     │
                         │  Backend API + IDP   │
                         └──────────────────────┘
```

**Configuration:**

```yaml
# Two separate ingresses

# 1. Public UI (read-only)
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: schema-registry-ui-public
  namespace: solace
  annotations:
    nginx.ingress.kubernetes.io/auth-type: basic
    nginx.ingress.kubernetes.io/auth-secret: readonly-htpasswd
    # Rate limiting
    nginx.ingress.kubernetes.io/limit-rps: "10"
spec:
  ingressClassName: nginx-public
  rules:
    - host: docs.schema-registry.yourcompany.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: schema-registry-ui-service
                port:
                  number: 8888

# 2. Private API (full access)
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: schema-registry-api-private
  namespace: solace
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-scheme: "internal"
spec:
  ingressClassName: nginx-private
  rules:
    - host: apis.schema-registry.internal.yourcompany.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: schema-registry-backend-service
                port:
                  number: 8081
```

---

## Implementation Details

### 1. DNS Configuration

#### Option A: AWS Route53 Private Hosted Zone (Recommended)

```bash
# Create private hosted zone
aws route53 create-hosted-zone \
  --name internal.yourcompany.com \
  --vpc VPCRegion=us-east-2,VPCId=vpc-xxxxxxxxx \
  --caller-reference $(date +%s) \
  --hosted-zone-config PrivateZone=true

# Create alias record pointing to internal NLB
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567890ABC \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "apis.schema-registry.internal.yourcompany.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z3AADJGX6KTTL2",
          "DNSName": "internal-xxxx.elb.us-east-2.amazonaws.com",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'

# Associate hosted zone with VDI/VPN VPCs
aws route53 associate-vpc-with-hosted-zone \
  --hosted-zone-id Z1234567890ABC \
  --vpc VPCRegion=us-east-2,VPCId=vpc-vdi-network
```

**Update Helm values:**

```yaml
ingress:
  hostNameSuffix: "schema-registry.internal.yourcompany.com"
```

**Update MqttConfig.java:**

```java
public static final String SCHEMA_REGISTRY_URL = 
    "https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3";
```

#### Option B: Corporate DNS (On-Premises)

If your VPN/VDI uses on-prem DNS:

```bash
# On your corporate DNS server, add conditional forwarder
# Forward *.internal.yourcompany.com to Route53 Resolver

# Or add static A records:
apis.schema-registry.internal.yourcompany.com  IN A  10.0.1.100
ui.schema-registry.internal.yourcompany.com    IN A  10.0.1.100
idp.schema-registry.internal.yourcompany.com   IN A  10.0.1.100
```

---

### 2. VPN Configuration

#### AWS Client VPN Setup

```bash
# 1. Create VPN Endpoint
aws ec2 create-client-vpn-endpoint \
  --client-cidr-block 10.200.0.0/16 \
  --server-certificate-arn arn:aws:acm:... \
  --authentication-options Type=certificate-authentication,MutualAuthentication={ClientRootCertificateChainArn=arn:aws:acm:...} \
  --connection-log-options Enabled=true,CloudwatchLogGroup=vpn-logs \
  --vpc-id vpc-xxxxxxxxx \
  --security-group-ids sg-vpn-client \
  --split-tunnel

# 2. Associate with subnets
aws ec2 associate-client-vpn-target-network \
  --client-vpn-endpoint-id cvpn-endpoint-xxxxxxxxx \
  --subnet-id subnet-xxxxxxxxx

# 3. Add authorization rule
aws ec2 authorize-client-vpn-ingress \
  --client-vpn-endpoint-id cvpn-endpoint-xxxxxxxxx \
  --target-network-cidr 10.0.0.0/16 \
  --authorize-all-groups

# 4. Add route to Schema Registry subnet
aws ec2 create-client-vpn-route \
  --client-vpn-endpoint-id cvpn-endpoint-xxxxxxxxx \
  --destination-cidr-block 10.0.1.0/24 \
  --target-vpc-subnet-id subnet-xxxxxxxxx
```

#### Site-to-Site VPN (Corporate Network)

```bash
# 1. Create Customer Gateway
aws ec2 create-customer-gateway \
  --type ipsec.1 \
  --public-ip YOUR_CORPORATE_FIREWALL_IP \
  --bgp-asn 65000

# 2. Create Virtual Private Gateway
aws ec2 create-vpn-gateway --type ipsec.1

# 3. Attach to VPC
aws ec2 attach-vpn-gateway \
  --vpn-gateway-id vgw-xxxxxxxxx \
  --vpc-id vpc-xxxxxxxxx

# 4. Create VPN Connection
aws ec2 create-vpn-connection \
  --type ipsec.1 \
  --customer-gateway-id cgw-xxxxxxxxx \
  --vpn-gateway-id vgw-xxxxxxxxx \
  --options TunnelOptions=[{PreSharedKey=YOUR_PSK}]
```

---

### 3. VDI Integration

#### VDI Network Configuration

**For Citrix/VMware Horizon:**

1. **VDI VMs must be in same VPC** or have VPC peering:

```bash
# Create VPC peering connection
aws ec2 create-vpc-peering-connection \
  --vpc-id vpc-vdi \
  --peer-vpc-id vpc-schema-registry

# Accept peering connection
aws ec2 accept-vpc-peering-connection \
  --vpc-peering-connection-id pcx-xxxxxxxxx

# Update route tables
aws ec2 create-route \
  --route-table-id rtb-vdi \
  --destination-cidr-block 10.0.1.0/24 \
  --vpc-peering-connection-id pcx-xxxxxxxxx
```

2. **Update security groups**:

```bash
# Allow VDI network to access NLB
aws ec2 authorize-security-group-ingress \
  --group-id sg-schema-registry-nlb \
  --protocol tcp \
  --port 443 \
  --source-group sg-vdi-network
```

#### AWS WorkSpaces Configuration

```bash
# 1. Deploy WorkSpaces in same VPC or peered VPC
aws workspaces create-workspaces \
  --workspaces DirectoryId=d-xxxxxxxxx,UserName=user@example.com,BundleId=wsb-xxxxxxxxx,SubnetId=subnet-xxxxxxxxx

# 2. Ensure WorkSpaces subnet route table has route to Schema Registry subnet
aws ec2 create-route \
  --route-table-id rtb-workspaces \
  --destination-cidr-block 10.0.1.0/24 \
  --gateway-id igw-xxxxxxxxx  # or peering connection
```

---

### 4. TLS Certificate Management

#### Option A: AWS Certificate Manager (ACM) + Private CA

For internal domains:

```bash
# 1. Create Private CA
aws acm-pca create-certificate-authority \
  --certificate-authority-configuration file://ca-config.json \
  --certificate-authority-type ROOT

# 2. Request private certificate
aws acm request-certificate \
  --domain-name "*.schema-registry.internal.yourcompany.com" \
  --certificate-authority-arn arn:aws:acm-pca:... \
  --domain-validation-options DomainName=*.schema-registry.internal.yourcompany.com,ValidationDomain=yourcompany.com

# 3. Export certificate and import to Kubernetes
aws acm export-certificate \
  --certificate-arn arn:aws:acm:... \
  --passphrase $(openssl rand -base64 32) > cert-bundle.json

# Import to K8s
kubectl -n solace create secret tls schema-registry-tls-secret \
  --cert=certificate.pem \
  --key=private-key.pem
```

#### Option B: Corporate PKI Integration

```bash
# Generate CSR in K8s
openssl req -new -newkey rsa:2048 -nodes \
  -keyout tls.key -out tls.csr \
  -subj "/CN=*.schema-registry.internal.yourcompany.com"

# Submit CSR to your corporate CA
# (Process varies by organization - typically ServiceNow ticket)

# Once signed, create secret
kubectl -n solace create secret tls schema-registry-tls-secret \
  --cert=signed-certificate.pem \
  --key=tls.key
```

**Distribute Root CA to clients:**

```java
// For Java applications, add corporate root CA to truststore
keytool -import \
  -trustcacerts \
  -alias corporate-root-ca \
  -file corporate-root-ca.crt \
  -keystore $JAVA_HOME/lib/security/cacerts \
  -storepass changeit
```

---

### 5. Identity Provider (IDP) Integration

#### LDAP/Active Directory Integration

**Update Helm values:**

```yaml
idp:
  authType: ldap
  ldap:
    enabled: true
    url: "ldaps://ldap.yourcompany.com:636"
    baseDN: "ou=users,dc=yourcompany,dc=com"
    bindDN: "cn=schema-registry-bind,ou=service-accounts,dc=yourcompany,dc=com"
    bindPassword: "SECURE_PASSWORD"
    userFilter: "(uid={0})"
    groupSearchBase: "ou=groups,dc=yourcompany,dc=com"
    groupSearchFilter: "(member={0})"
    
  # Map LDAP groups to Schema Registry roles
  roleMapping:
    - ldapGroup: "cn=schema-admins,ou=groups,dc=yourcompany,dc=com"
      registryRole: "admin"
    - ldapGroup: "cn=schema-developers,ou=groups,dc=yourcompany,dc=com"
      registryRole: "developer"
    - ldapGroup: "cn=schema-readers,ou=groups,dc=yourcompany,dc=com"
      registryRole: "reader"
```

#### SAML/OIDC (SSO) Integration

```yaml
idp:
  authType: oidc
  oidc:
    enabled: true
    issuerUrl: "https://sso.yourcompany.com/auth/realms/corporate"
    clientId: "schema-registry"
    clientSecret: "SECURE_SECRET"
    redirectUri: "https://idp.schema-registry.internal.yourcompany.com/callback"
    scope: "openid profile email groups"
    
    # Map SSO groups to roles
    groupsClaim: "groups"
    roleMapping:
      - ssoGroup: "schema-registry-admins"
        registryRole: "admin"
      - ssoGroup: "developers"
        registryRole: "developer"
```

---

### 6. Security Group Configuration

**EKS Node Security Group:**

```bash
# Allow NLB health checks
aws ec2 authorize-security-group-ingress \
  --group-id sg-eks-nodes \
  --protocol tcp \
  --port 30000-32767 \
  --source-group sg-nlb

# Allow inter-pod communication
aws ec2 authorize-security-group-ingress \
  --group-id sg-eks-nodes \
  --protocol all \
  --source-group sg-eks-nodes
```

**NLB Security Group (if using NLB with SG):**

```bash
# Allow from corporate network
aws ec2 authorize-security-group-ingress \
  --group-id sg-nlb \
  --protocol tcp \
  --port 443 \
  --cidr 10.0.0.0/8  # Corporate network

# Allow from VDI network
aws ec2 authorize-security-group-ingress \
  --group-id sg-nlb \
  --protocol tcp \
  --port 443 \
  --cidr 172.16.0.0/12  # VDI network

# Deny all other traffic (implicit)
```

---

## Security Considerations

### Network Segmentation

```
┌─────────────────────────────────────────────────────────┐
│  Security Zones                                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Public Zone    │  │  DMZ            │             │
│  │  (Internet)     │→ │  (WAF/CloudFront│             │
│  └─────────────────┘  └────────┬────────┘             │
│                                 │                       │
│                         Firewall Rules                  │
│                                 ▼                       │
│                    ┌─────────────────────┐             │
│                    │  Private Zone       │             │
│                    │  (Schema Registry)  │             │
│                    │  - Internal NLB     │             │
│                    │  - Private Subnets  │             │
│                    └─────────┬───────────┘             │
│                              │                          │
│                      Only from VPN/VDI                  │
│                              ▼                          │
│                    ┌─────────────────────┐             │
│                    │  Data Zone          │             │
│                    │  (PostgreSQL)       │             │
│                    │  - No Internet      │             │
│                    │  - Encrypted at Rest│             │
│                    └─────────────────────┘             │
└─────────────────────────────────────────────────────────┘
```

### Access Control Matrix

| User Type | UI Access | API Access | Admin Access | MFA Required | Certificate Required |
|-----------|-----------|------------|--------------|--------------|----------------------|
| Internal Dev | ✅ Read/Write | ✅ Full | ❌ No | ✅ Yes | ❌ No |
| VPN User | ✅ Read/Write | ✅ Full | ❌ No | ✅ Yes | ❌ No |
| VDI User | ✅ Read/Write | ✅ Full | ❌ No | ✅ Yes | ❌ No |
| Admin | ✅ Full | ✅ Full | ✅ Yes | ✅ Yes | ✅ Yes |
| CI/CD | ❌ No | ✅ Full | ❌ No | ❌ No | ✅ Yes |
| MQTT Client | ❌ No | ✅ Read Only | ❌ No | ❌ No | ✅ Yes |

### Compliance Requirements

#### GDPR/Data Residency
```yaml
# Ensure data stays in specific region
global:
  region: "eu-central-1"
  dataResidency:
    enabled: true
    allowedRegions: ["eu-central-1", "eu-west-1"]
```

#### SOC2/PCI-DSS
- Enable audit logging for all API calls
- Encrypt all data in transit (TLS 1.3)
- Encrypt all data at rest (EBS encryption, PostgreSQL encryption)
- Rotate credentials every 90 days
- Implement network segmentation

```yaml
# Enable comprehensive audit logging
backend:
  auditLog:
    enabled: true
    destination: cloudwatch
    logGroup: /aws/eks/schema-registry/audit
    retentionDays: 2555  # 7 years for compliance
```

---

## Client Application Configuration

### For Internal Network / VPN / VDI Users

**Java MQTT Publisher/Subscriber:**

```java
public class MqttConfig {
    // Use internal DNS name
    public static final String SCHEMA_REGISTRY_URL = 
        "https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3";
    
    // Use LDAP/SSO credentials
    public static final String SCHEMA_REGISTRY_USERNAME = 
        System.getenv("LDAP_USERNAME");  // From user's login
    public static final String SCHEMA_REGISTRY_PASSWORD = 
        System.getenv("LDAP_PASSWORD");
    
    // Trust corporate CA certificates
    static {
        System.setProperty("javax.net.ssl.trustStore", 
            "/etc/pki/java/cacerts");  // Corporate truststore
        System.setProperty("javax.net.ssl.trustStorePassword", 
            "changeit");
    }
}
```

**For VDI deployments**, package the application with corporate truststore:

```bash
# Copy corporate truststore to Docker image
COPY corporate-cacerts /usr/local/openjdk-17/lib/security/cacerts

# Or use Java system property
java -Djavax.net.ssl.trustStore=/app/config/corporate-cacerts \
     -jar mqtt5-publisher.jar
```

---

## Monitoring & Alerting

### CloudWatch Dashboards

```bash
# Create dashboard for access monitoring
aws cloudwatch put-dashboard \
  --dashboard-name schema-registry-access \
  --dashboard-body file://dashboard.json
```

**Dashboard metrics:**
- NLB active connections by source IP/CIDR
- Failed authentication attempts
- API latency by endpoint
- Schema validation success/failure rates
- Certificate expiration warnings

### VPN/VDI Connection Monitoring

```bash
# CloudWatch Logs Insights query for VPN connections
fields @timestamp, @message
| filter @message like /Client connected/
| stats count() by bin(5m)
```

---

## Deployment Commands

### Deploy with Internal Load Balancer

```bash
# 1. Install NGINX Ingress with internal LB
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx \
  --create-namespace \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-scheme"="internal" \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-type"="nlb" \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-source-ranges"="10.0.0.0/8\,172.16.0.0/12"

# 2. Get internal load balancer address
kubectl get svc ingress-nginx-controller -n ingress-nginx

# 3. Create Route53 private hosted zone record (see DNS section above)

# 4. Deploy Schema Registry with internal hostname
helm upgrade --install schema-registry ./.serdes/schema-registry-v1.0.0/helm-chart/solace-schema-registry-1.0.0.tgz \
  -n solace \
  --create-namespace \
  -f infra/values-override.yaml \
  --set ingress.hostNameSuffix="schema-registry.internal.yourcompany.com"
```

---

## Testing Access

### From Internal Network

```bash
# Test DNS resolution
nslookup apis.schema-registry.internal.yourcompany.com

# Test connectivity
curl -k https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info

# Test authentication
curl -u ldap-username:password \
  https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/search/artifacts
```

### From VPN

```bash
# 1. Connect VPN client

# 2. Verify route to Schema Registry subnet
# Windows:
route print | findstr "10.0.1.0"
# Mac/Linux:
netstat -rn | grep 10.0.1.0

# 3. Test access (same as internal network)
curl -k https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info
```

### From VDI

```powershell
# 1. Log into VDI session

# 2. Test DNS from VDI
nslookup apis.schema-registry.internal.yourcompany.com

# 3. Test connectivity from VDI browser
# Open: https://ui.schema-registry.internal.yourcompany.com

# 4. Test from VDI command line
Test-NetConnection -ComputerName apis.schema-registry.internal.yourcompany.com -Port 443
```

---

## Troubleshooting

### Issue: VDI can't resolve internal DNS

**Solution:**
```bash
# Option 1: Configure VDI to use Route53 Resolver
aws route53resolver create-resolver-endpoint \
  --creator-request-id $(date +%s) \
  --direction INBOUND \
  --ip-addresses SubnetId=subnet-xxx,Ip=10.0.1.10 \
  --security-group-ids sg-resolver

# Configure VDI DNS to forward to 10.0.1.10
```

### Issue: VPN users can't access internal NLB

**Check:**
1. VPN route table includes Schema Registry subnet
2. Security groups allow VPN CIDR
3. VPN split-tunnel includes internal domain

```bash
# Verify routing
aws ec2 describe-client-vpn-routes \
  --client-vpn-endpoint-id cvpn-endpoint-xxx
```

### Issue: Certificate trust issues from VDI

**Solution:**
Distribute corporate root CA via Group Policy (Windows) or MDM (Mac)

```powershell
# Windows Group Policy
certutil -addstore -enterprise Root corporate-root-ca.cer
```

---

## Cost Optimization

### Internal NLB vs PrivateLink Cost Comparison

| Service | Monthly Cost (est.) | Best For |
|---------|---------------------|----------|
| Internal NLB | ~$20-30 | Single VPC access |
| AWS PrivateLink | ~$7/endpoint + $0.01/GB | Multi-VPC, partner access |
| Site-to-Site VPN | ~$36/connection | Corporate network |
| Client VPN | ~$73 + $0.05/hour | Remote workers |

**Recommendation**: Start with Internal NLB + Site-to-Site VPN for corporate network access.

---

## Summary

### Recommended Production Setup

1. **Internal Network + VPN/VDI Users**: 
   - ✅ Use Internal NLB
   - ✅ AWS Site-to-Site VPN for corporate network
   - ✅ Route53 Private Hosted Zone for DNS
   - ✅ LDAP/SSO integration
   - ✅ Corporate PKI certificates

2. **Multi-Account AWS**:
   - ✅ Add PrivateLink for cross-account access
   
3. **Security**:
   - ✅ No public internet exposure
   - ✅ MFA for all human users
   - ✅ Certificate-based auth for machines
   - ✅ Audit logging enabled

4. **DNS Strategy**:
   - ✅ Use `*.schema-registry.internal.yourcompany.com`
   - ✅ Avoid `nip.io` in production

This architecture provides enterprise-grade security while supporting diverse access patterns (internal, VPN, VDI).






