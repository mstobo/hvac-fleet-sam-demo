# Align Schema Registry with Solace Cloud Architecture

## Current Working Setup

You currently have:
- ✅ **Solace Cloud**: `mr-connection-yfx6c4y9zy1.messaging.solace.cloud`
- ✅ **Working from both**: Corporate LAN and VDI can access Solace Cloud
- ✅ **Schema Registry on AWS EKS**: But using **public NLB** (doesn't match Solace architecture)

## Goal

Configure Schema Registry to match your Solace Cloud access pattern:
- ✅ Accessible from Corporate LAN
- ✅ Accessible from VDI  
- ✅ Internal/private connectivity (not public internet)
- ✅ Same network path as Solace Cloud broker

---

## Step-by-Step Implementation

Since you already have VPN or Direct Connect to AWS (otherwise Solace Cloud Web UI wouldn't work), we just need to convert the Schema Registry NLB from **public** to **internal**.

### Step 1: Understand Current Solace Cloud Connectivity

First, let's verify how you're connecting to Solace Cloud:

```bash
# From your corporate LAN workstation, check connectivity
nslookup mr-connection-yfx6c4y9zy1.messaging.solace.cloud

# Check if it resolves to private IPs or public IPs
# If public IPs, then you have internet access (with whitelisting)
# If private IPs, then you have VPN/Direct Connect
```

**Likely scenarios:**

1. **Solace Cloud with PrivateLink** (most secure)
   - Solace Cloud uses AWS PrivateLink
   - You have VPC Endpoint configured
   - Traffic stays within AWS network

2. **Solace Cloud with VPN** (common)
   - Corporate network has AWS VPN or Direct Connect
   - All AWS traffic routes through VPN
   - Broker accessible via VPN tunnel

3. **Solace Cloud Public with Whitelist** (less secure)
   - Broker has public endpoint
   - Corporate firewall whitelists Solace Cloud IPs
   - Internet-based access

---

### Step 2: Convert Schema Registry to Internal NLB

This will make Schema Registry use the **same network path** as Solace Cloud.

```bash
cd /Users/matthewstobo/Documents/mqtt5SRDemo

# Run the conversion script
./switch-to-internal-nlb.sh
```

**What this does:**
1. Updates NGINX Ingress Controller service to request an internal NLB
2. AWS deletes the old public NLB (`3.14.49.98`)
3. AWS creates a new internal NLB (private IP: `10.0.x.x`)
4. Takes 3-5 minutes

**Wait for completion:**
```bash
# Watch for new internal LB to be assigned
kubectl get svc ingress-nginx-controller -n ingress-nginx -w
```

---

### Step 3: Configure Internal DNS

Once the internal NLB is created, set up DNS resolution.

#### Option A: Route53 Private Hosted Zone (Recommended)

**If you have Route53 already configured for Solace Cloud**, add Schema Registry to the same zone:

```bash
# Get the internal LB address
INTERNAL_LB=$(kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "Internal LB: $INTERNAL_LB"

# Resolve to IP
INTERNAL_IP=$(dig +short "$INTERNAL_LB" | head -1)
echo "Internal IP: $INTERNAL_IP"

# Assuming you have a private hosted zone (check with your AWS admin)
# Get your hosted zone ID
aws route53 list-hosted-zones --query 'HostedZones[?Name==`internal.yourcompany.com.`].Id' --output text

# Add DNS records
HOSTED_ZONE_ID="Z1234567890ABC"  # Replace with your zone ID

# Create A record for APIs
aws route53 change-resource-record-sets \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "apis.schema-registry.internal.yourcompany.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z3AADJGX6KTTL2",
          "DNSName": "'"$INTERNAL_LB"'",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'

# Repeat for UI and IDP
aws route53 change-resource-record-sets \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "ui.schema-registry.internal.yourcompany.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z3AADJGX6KTTL2",
          "DNSName": "'"$INTERNAL_LB"'",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'

aws route53 change-resource-record-sets \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "idp.schema-registry.internal.yourcompany.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z3AADJGX6KTTL2",
          "DNSName": "'"$INTERNAL_LB"'",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'
```

#### Option B: Corporate DNS

**If corporate DNS already resolves internal AWS resources**, add these entries:

```bash
# Contact your DNS team to add these records:
apis.schema-registry.internal.yourcompany.com  IN A  10.0.x.x
ui.schema-registry.internal.yourcompany.com    IN A  10.0.x.x
idp.schema-registry.internal.yourcompany.com   IN A  10.0.x.x

# Where 10.0.x.x is the internal NLB IP from Step 2
```

#### Option C: Use nip.io with Internal IP (Quick Test)

**For quick testing** (not recommended for production):

```bash
# Get internal IP
INTERNAL_IP=$(dig +short "$INTERNAL_LB" | head -1)

# Use nip.io for DNS
# URLs will be: apis.10.0.1.100.nip.io (example)
echo "Use this IP for nip.io: $INTERNAL_IP"
```

---

### Step 4: Update TLS Certificates

Generate new certificates for the internal hostname:

```bash
# For Route53 private zone (Option A)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=*.schema-registry.internal.yourcompany.com" \
  -addext "subjectAltName=DNS:*.schema-registry.internal.yourcompany.com,DNS:schema-registry.internal.yourcompany.com"

# For nip.io with internal IP (Option C)
# openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
#   -keyout tls.key -out tls.crt \
#   -subj "/CN=*.$INTERNAL_IP.nip.io" \
#   -addext "subjectAltName=DNS:*.$INTERNAL_IP.nip.io,DNS:$INTERNAL_IP.nip.io"

# Update Kubernetes secret
kubectl -n solace delete secret schema-registry-tls-secret
kubectl -n solace create secret tls schema-registry-tls-secret \
  --cert=tls.crt \
  --key=tls.key

echo "TLS secret updated!"
```

---

### Step 5: Update Kubernetes Ingress

Update the ingress to use the new internal hostname:

```bash
# For Route53 private zone (Option A)
HOSTNAME_SUFFIX="schema-registry.internal.yourcompany.com"

# For nip.io with internal IP (Option C)
# HOSTNAME_SUFFIX="$INTERNAL_IP.nip.io"

# Delete old ingress
kubectl delete ingress schema-registry-ingress -n solace

# Create new ingress
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: schema-registry-ingress
  namespace: solace
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - ui.$HOSTNAME_SUFFIX
        - apis.$HOSTNAME_SUFFIX
        - idp.$HOSTNAME_SUFFIX
      secretName: schema-registry-tls-secret
  rules:
    - host: ui.$HOSTNAME_SUFFIX
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: schema-registry-ui-service
                port:
                  number: 8888
    - host: apis.$HOSTNAME_SUFFIX
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: schema-registry-backend-service
                port:
                  number: 8081
    - host: idp.$HOSTNAME_SUFFIX
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: schema-registry-idp-service
                port:
                  number: 3000
EOF

echo "Ingress updated!"
```

---

### Step 6: Update Helm Values (for future deployments)

Update `infra/values-override.yaml`:

```yaml
ingress:
  enabled: true
  # Use internal hostname
  hostNameSuffix: "schema-registry.internal.yourcompany.com"
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/use-forwarded-headers: "true"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/enable-cors: "true"
    nginx.ingress.kubernetes.io/cors-allow-origin: "*"
    nginx.ingress.kubernetes.io/cors-allow-methods: "GET, POST, PUT, DELETE, OPTIONS"
    nginx.ingress.kubernetes.io/cors-allow-headers: "Content-Type, Authorization, Accept, Origin, User-Agent, X-Requested-With, Cache-Control, Pragma, Expires"
    nginx.ingress.kubernetes.io/cors-allow-credentials: "true"
    nginx.ingress.kubernetes.io/cors-max-age: "86400"
  tls:
    enabled: true
    secretName: schema-registry-tls-secret
```

---

### Step 7: Update MqttConfig.java

Update your Java application configuration:

```java
// Before (public IP)
public static final String SCHEMA_REGISTRY_URL = "https://apis.3.14.49.98.nip.io/apis/registry/v3";

// After (internal hostname)
public static final String SCHEMA_REGISTRY_URL = "https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3";
```

Save the file:

```bash
# Edit the file
nano src/main/java/MqttConfig.java

# Or use this quick update
sed -i '' 's|https://apis.3.14.49.98.nip.io/apis/registry/v3|https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3|g' src/main/java/MqttConfig.java
```

---

### Step 8: Update Java Truststore

If using corporate certificates, import them:

```bash
# Download the new certificate
openssl s_client -showcerts -connect apis.schema-registry.internal.yourcompany.com:443 </dev/null 2>/dev/null | openssl x509 -outform PEM > schema-registry-internal.crt

# Import to Java truststore
sudo keytool -import \
  -trustcacerts \
  -alias schema-registry-internal \
  -file schema-registry-internal.crt \
  -keystore $JAVA_HOME/lib/security/cacerts \
  -storepass changeit \
  -noprompt

echo "Certificate imported to Java truststore"
```

---

### Step 9: Restart Schema Registry Pods

Restart pods to pick up new configuration:

```bash
# Restart backend pods (one at a time for zero downtime)
kubectl rollout restart deployment schema-registry-backend -n solace

# Restart UI pods
kubectl rollout restart deployment schema-registry-ui -n solace

# Restart IDP pods
kubectl rollout restart deployment schema-registry-idp -n solace

# Wait for rollout to complete
kubectl rollout status deployment schema-registry-backend -n solace
kubectl rollout status deployment schema-registry-ui -n solace
kubectl rollout status deployment schema-registry-idp -n solace

echo "All pods restarted!"
```

---

### Step 10: Test Access

#### From Corporate LAN:

```bash
# Test DNS resolution
nslookup apis.schema-registry.internal.yourcompany.com

# Test API access
curl -k -u sr-developer:admin \
  https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info

# Expected output:
# {"name":"Solace Schema Registry","description":"High performance, runtime registry for schemas.","version":"1.0.0",...}

# Test UI access (open in browser)
open https://ui.schema-registry.internal.yourcompany.com
```

#### From VDI:

```bash
# Same tests as corporate LAN
nslookup apis.schema-registry.internal.yourcompany.com
curl -k -u sr-developer:admin \
  https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info

# Open UI in browser
# https://ui.schema-registry.internal.yourcompany.com
```

#### Test Java Application:

```bash
cd /Users/matthewstobo/Documents/mqtt5SRDemo

# Rebuild with new URL
mvn clean compile

# Test publisher
mvn exec:java -Dexec.mainClass="MQTT5Publisher"

# Should see:
# "Schema validation successful"
# "Published message X to test/mqtt5/messages"
```

---

## Architecture Comparison

### Before (Current - Public NLB)

```
Corporate LAN ──X (blocked)
                
VDI ───────────✓ (works via internet)
                │
                ▼
            Internet
                │
                ▼
    ┌───────────────────────┐
    │  AWS Public NLB       │
    │  3.14.49.98          │
    │  (0.0.0.0/0 rules)   │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │  Schema Registry      │
    └───────────────────────┘
```

### After (Aligned with Solace Cloud - Internal NLB)

```
Corporate LAN ────┐
                  │
VDI ──────────────┤
                  │
                  ▼
        ┌─────────────────┐
        │  VPN / Direct   │
        │  Connect        │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │  AWS VPC        │
        │  (Internal)     │
        │                 │
        │  ┌──────────┐   │
        │  │Internal  │   │
        │  │NLB       │   │
        │  │10.0.x.x  │   │
        │  └────┬─────┘   │
        │       │         │
        │       ▼         │
        │  ┌──────────┐   │
        │  │ Schema   │   │
        │  │ Registry │   │
        │  └──────────┘   │
        │                 │
        │  ┌──────────┐   │
        │  │ Solace   │   │
        │  │ Cloud    │   │
        │  └──────────┘   │
        └─────────────────┘
```

**Now both Solace Cloud and Schema Registry use the same network path!**

---

## Troubleshooting

### Issue: Can't resolve internal hostname

**Check:**
```bash
# Verify Route53 private hosted zone
aws route53 list-hosted-zones

# Verify VPC association
aws route53 list-hosted-zones-by-vpc --vpc-id vpc-xxxxxxxxx --vpc-region us-east-2

# Test DNS from EC2 instance in VPC
nslookup apis.schema-registry.internal.yourcompany.com
```

**Solution:**
Associate private hosted zone with your VPC:
```bash
aws route53 associate-vpc-with-hosted-zone \
  --hosted-zone-id Z1234567890ABC \
  --vpc VPCRegion=us-east-2,VPCId=vpc-xxxxxxxxx
```

---

### Issue: Connection timeout from corporate LAN

**Check:**
```bash
# Verify internal NLB is created
kubectl get svc ingress-nginx-controller -n ingress-nginx

# Should show EXTERNAL-IP as internal-xxx.elb.amazonaws.com

# Check security groups
aws ec2 describe-security-groups --filters "Name=tag:kubernetes.io/service-name,Values=ingress-nginx/ingress-nginx-controller"
```

**Solution:**
Ensure security groups allow your corporate network CIDR:
```bash
kubectl annotate svc ingress-nginx-controller -n ingress-nginx \
  "service.beta.kubernetes.io/aws-load-balancer-source-ranges=10.0.0.0/8,172.16.0.0/12" \
  --overwrite
```

---

### Issue: VDI can't access after switching to internal

**Cause:** VDI may not have VPN access

**Solution 1:** Ensure VDI is in a VPC that's peered with Schema Registry VPC

**Solution 2:** Configure VDI to use VPN for AWS access

**Solution 3:** If VDI must use public access, keep a separate public ingress for UI-only (read-only)

---

## Summary

After completing these steps:

✅ Schema Registry accessible from **Corporate LAN** (same as Solace Cloud)
✅ Schema Registry accessible from **VDI** (same as Solace Cloud)
✅ Uses **internal networking** (same as Solace Cloud)
✅ No public internet exposure (secure)
✅ Aligned architecture across all components

---

## Quick Command Reference

```bash
# 1. Convert to internal NLB
./switch-to-internal-nlb.sh

# 2. Get new internal LB
INTERNAL_LB=$(kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
INTERNAL_IP=$(dig +short "$INTERNAL_LB" | head -1)

# 3. Update certificates
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=*.schema-registry.internal.yourcompany.com"
kubectl -n solace delete secret schema-registry-tls-secret
kubectl -n solace create secret tls schema-registry-tls-secret --cert=tls.crt --key=tls.key

# 4. Update ingress (see Step 5 above)

# 5. Update MqttConfig.java
sed -i '' 's|https://apis.3.14.49.98.nip.io|https://apis.schema-registry.internal.yourcompany.com|g' src/main/java/MqttConfig.java

# 6. Test
curl -k -u sr-developer:admin https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info
```

---

## Next Steps

1. **Contact AWS/Network team** to confirm:
   - VPN or Direct Connect details
   - Private hosted zone name (e.g., `internal.yourcompany.com`)
   - VPC ID where Schema Registry is deployed

2. **Run Step 1** to convert NLB to internal

3. **Choose DNS option** (A, B, or C) based on your infrastructure

4. **Follow steps 2-10** to complete configuration

5. **Test from both** corporate LAN and VDI

Your Schema Registry will then have the same access pattern as your Solace Cloud deployment! 🎉






