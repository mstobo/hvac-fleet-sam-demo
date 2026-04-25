# Quick Switch to Internal NLB - PowerShell Version
# Since Solace Cloud (private VPC) is already accessible, this will make
# Schema Registry use the same network path

param(
    [switch]$SkipConfirmation
)

# Enable strict error handling
$ErrorActionPreference = "Stop"

Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Quick Switch: Schema Registry to Internal NLB" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Since your Solace Cloud (private VPC) is already accessible from"
Write-Host "corporate LAN and VDI, this will align Schema Registry to use the"
Write-Host "same network path (VPN/Direct Connect)."
Write-Host ""
Write-Host "This will:"
Write-Host "  1. Convert NLB from public to internal"
Write-Host "  2. Generate new TLS certificate"
Write-Host "  3. Update Kubernetes resources"
Write-Host "  4. Provide DNS configuration commands"
Write-Host ""
Write-Host "⏱️  Estimated time: 30 minutes" -ForegroundColor Yellow
Write-Host ""

if (-not $SkipConfirmation) {
    $confirm = Read-Host "Press Enter to continue, or Ctrl+C to cancel"
}

# Step 1: Convert to internal NLB
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 1: Converting NGINX Ingress to Internal NLB" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

try {
    kubectl annotate svc ingress-nginx-controller -n ingress-nginx `
        "service.beta.kubernetes.io/aws-load-balancer-scheme=internal" `
        --overwrite | Out-Null

    kubectl annotate svc ingress-nginx-controller -n ingress-nginx `
        "service.beta.kubernetes.io/aws-load-balancer-internal=true" `
        --overwrite | Out-Null

    kubectl annotate svc ingress-nginx-controller -n ingress-nginx `
        "service.beta.kubernetes.io/aws-load-balancer-type=nlb" `
        --overwrite | Out-Null

    Write-Host "✅ Annotations updated" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: Failed to update service annotations" -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host ""
Write-Host "⏳ AWS is creating new internal NLB (this takes 3-5 minutes)..." -ForegroundColor Yellow
Write-Host "   Old public NLB will be deleted automatically."
Write-Host ""

# Wait for load balancer with progress
for ($i = 1; $i -le 4; $i++) {
    Start-Sleep -Seconds 30
    Write-Host "⏳ Waiting... ($($i * 30) seconds)" -ForegroundColor Yellow
}

# Step 2: Get internal LB details
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 2: Getting Internal Load Balancer Details" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

$INTERNAL_LB = kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>$null

if ([string]::IsNullOrWhiteSpace($INTERNAL_LB)) {
    Write-Host "⚠️  Internal LB not ready yet. Wait a bit longer and check with:" -ForegroundColor Yellow
    Write-Host "   kubectl get svc ingress-nginx-controller -n ingress-nginx"
    exit 1
}

Write-Host "✅ Internal Load Balancer: $INTERNAL_LB" -ForegroundColor Green

# Resolve to IP
try {
    $dnsResult = Resolve-DnsName -Name $INTERNAL_LB -Type A -ErrorAction SilentlyContinue | Where-Object { $_.Type -eq 'A' } | Select-Object -First 1
    $INTERNAL_IP = $dnsResult.IPAddress
    Write-Host "✅ Internal IP: $INTERNAL_IP" -ForegroundColor Green
} catch {
    Write-Host "⚠️  Could not resolve IP yet. DNS may still be propagating." -ForegroundColor Yellow
    $INTERNAL_IP = "UNKNOWN"
}

# Step 3: DNS Configuration
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 3: DNS Configuration Options" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Choose your DNS approach:"
Write-Host ""
Write-Host "Option A: Use existing corporate DNS (Recommended)"
Write-Host "  Contact your DNS team to add these records:"
Write-Host ""
Write-Host "  apis.schema-registry.internal.yourcompany.com  IN A  $INTERNAL_IP" -ForegroundColor Yellow
Write-Host "  ui.schema-registry.internal.yourcompany.com    IN A  $INTERNAL_IP" -ForegroundColor Yellow
Write-Host "  idp.schema-registry.internal.yourcompany.com   IN A  $INTERNAL_IP" -ForegroundColor Yellow
Write-Host ""
Write-Host "  (Replace 'internal.yourcompany.com' with your actual internal domain)"
Write-Host ""
Write-Host "Option B: Use Route53 Private Hosted Zone"
Write-Host "  Script will generate AWS CLI commands for you"
Write-Host ""
Write-Host "Option C: Use nip.io for testing (NOT for production)"
Write-Host "  Use hostname: $INTERNAL_IP.nip.io"
Write-Host "  Note: nip.io may not resolve from corporate network"
Write-Host ""

$DNS_CHOICE = Read-Host "Enter A, B, or C"

switch ($DNS_CHOICE.ToUpper()) {
    "A" {
        Write-Host ""
        Write-Host "Selected: Corporate DNS" -ForegroundColor Green
        $HOSTNAME_SUFFIX = "schema-registry.internal.yourcompany.com"
        Write-Host ""
        Write-Host "⚠️  Remember to contact your DNS team to add the records!" -ForegroundColor Yellow
    }
    "B" {
        Write-Host ""
        Write-Host "Selected: Route53 Private Hosted Zone" -ForegroundColor Green
        $HOSTNAME_SUFFIX = "schema-registry.internal.yourcompany.com"
        Write-Host ""
        Write-Host "Route53 Commands (run these after getting your Hosted Zone ID):" -ForegroundColor Yellow
        Write-Host ""
        Write-Host @"
# Get your hosted zone ID
`$HOSTED_ZONE_ID = aws route53 list-hosted-zones --query 'HostedZones[?Name==``internal.yourcompany.com.``].Id' --output text

# Create A record for APIs
aws route53 change-resource-record-sets --hosted-zone-id `$HOSTED_ZONE_ID --change-batch '{
  "Changes": [{
    "Action": "CREATE",
    "ResourceRecordSet": {
      "Name": "apis.$HOSTNAME_SUFFIX",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "Z3AADJGX6KTTL2",
        "DNSName": "$INTERNAL_LB",
        "EvaluateTargetHealth": false
      }
    }
  }]
}'

# Repeat for UI
aws route53 change-resource-record-sets --hosted-zone-id `$HOSTED_ZONE_ID --change-batch '{
  "Changes": [{
    "Action": "CREATE",
    "ResourceRecordSet": {
      "Name": "ui.$HOSTNAME_SUFFIX",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "Z3AADJGX6KTTL2",
        "DNSName": "$INTERNAL_LB",
        "EvaluateTargetHealth": false
      }
    }
  }]
}'

# Repeat for IDP
aws route53 change-resource-record-sets --hosted-zone-id `$HOSTED_ZONE_ID --change-batch '{
  "Changes": [{
    "Action": "CREATE",
    "ResourceRecordSet": {
      "Name": "idp.$HOSTNAME_SUFFIX",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "Z3AADJGX6KTTL2",
        "DNSName": "$INTERNAL_LB",
        "EvaluateTargetHealth": false
      }
    }
  }]
}'
"@ -ForegroundColor Cyan
    }
    "C" {
        Write-Host ""
        Write-Host "Selected: nip.io (testing only)" -ForegroundColor Yellow
        $HOSTNAME_SUFFIX = "$INTERNAL_IP.nip.io"
    }
    default {
        Write-Host "Invalid choice. Defaulting to corporate DNS." -ForegroundColor Yellow
        $HOSTNAME_SUFFIX = "schema-registry.internal.yourcompany.com"
    }
}

# Step 4: Generate TLS Certificate
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 4: Generating TLS Certificate" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

# Check for OpenSSL
$opensslPath = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $opensslPath) {
    Write-Host "❌ ERROR: OpenSSL not found. Please install one of:" -ForegroundColor Red
    Write-Host "   - Git for Windows (includes OpenSSL): https://git-scm.com/download/win"
    Write-Host "   - OpenSSL for Windows: https://slproweb.com/products/Win32OpenSSL.html"
    Write-Host ""
    Write-Host "After installing, restart PowerShell and run this script again."
    exit 1
}

try {
    & openssl req -x509 -nodes -days 365 -newkey rsa:2048 `
        -keyout tls.key -out tls.crt `
        -subj "/CN=*.$HOSTNAME_SUFFIX" `
        -addext "subjectAltName=DNS:*.$HOSTNAME_SUFFIX,DNS:$HOSTNAME_SUFFIX" 2>$null

    Write-Host "✅ Certificate generated for *.$HOSTNAME_SUFFIX" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: Failed to generate certificate" -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}

# Update Kubernetes secret
kubectl -n solace delete secret schema-registry-tls-secret 2>$null | Out-Null
kubectl -n solace create secret tls schema-registry-tls-secret --cert=tls.crt --key=tls.key | Out-Null

Write-Host "✅ TLS secret updated in Kubernetes" -ForegroundColor Green

# Step 5: Update Ingress
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 5: Updating Kubernetes Ingress" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

kubectl delete ingress schema-registry-ingress -n solace 2>$null | Out-Null

$ingressYaml = @"
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
"@

$ingressYaml | kubectl apply -f - | Out-Null

Write-Host "✅ Ingress updated" -ForegroundColor Green

# Step 6: Update MqttConfig.java
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 6: Updating MqttConfig.java" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

$configPath = "src\main\java\MqttConfig.java"

# Backup original
Copy-Item $configPath "$configPath.backup" -Force

# Update URL
$content = Get-Content $configPath -Raw
$newContent = $content -replace 'public static final String SCHEMA_REGISTRY_URL = "https://apis\.[^"]*";', "public static final String SCHEMA_REGISTRY_URL = `"https://apis.$HOSTNAME_SUFFIX/apis/registry/v3`";"
Set-Content $configPath $newContent

Write-Host "✅ MqttConfig.java updated" -ForegroundColor Green
Write-Host "   Old config backed up to: $configPath.backup"
Write-Host "   New URL: https://apis.$HOSTNAME_SUFFIX/apis/registry/v3"

# Step 7: Update Java Truststore
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 7: Updating Java Truststore" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

if (-not $env:JAVA_HOME) {
    Write-Host "⚠️  WARNING: JAVA_HOME not set. Please set it and run this command manually:" -ForegroundColor Yellow
    Write-Host "   keytool -import -trustcacerts -alias schema-registry-internal -file tls.crt -keystore `"`$env:JAVA_HOME\lib\security\cacerts`" -storepass changeit -noprompt"
} else {
    $keystorePath = Join-Path $env:JAVA_HOME "lib\security\cacerts"
    
    # Check if running as Administrator
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    
    if (-not $isAdmin) {
        Write-Host "⚠️  WARNING: Not running as Administrator. Certificate import may fail." -ForegroundColor Yellow
        Write-Host "   If it fails, run this script as Administrator or run this command manually:"
        Write-Host "   keytool -import -trustcacerts -alias schema-registry-internal -file tls.crt -keystore `"$keystorePath`" -storepass changeit -noprompt"
    }
    
    try {
        & keytool -import -trustcacerts -alias schema-registry-internal -file tls.crt -keystore $keystorePath -storepass changeit -noprompt 2>$null | Out-Null
        Write-Host "✅ Certificate imported to Java truststore" -ForegroundColor Green
    } catch {
        Write-Host "⚠️  WARNING: Certificate import may have failed. Run the manual command if needed." -ForegroundColor Yellow
    }
}

# Step 8: Restart Pods
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Step 8: Restarting Schema Registry Pods" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

kubectl rollout restart deployment schema-registry-backend -n solace | Out-Null
kubectl rollout restart deployment schema-registry-ui -n solace | Out-Null
kubectl rollout restart deployment schema-registry-idp -n solace | Out-Null

Write-Host "⏳ Waiting for pods to restart..." -ForegroundColor Yellow
kubectl rollout status deployment schema-registry-backend -n solace --timeout=3m | Out-Null
kubectl rollout status deployment schema-registry-ui -n solace --timeout=3m | Out-Null
kubectl rollout status deployment schema-registry-idp -n solace --timeout=3m | Out-Null

Write-Host "✅ All pods restarted" -ForegroundColor Green

# Summary
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Green
Write-Host "✅ Conversion Complete!" -ForegroundColor Green
Write-Host "==========================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Your Schema Registry is now using INTERNAL NLB"
Write-Host "Same network path as your Solace Cloud (private VPC)"
Write-Host ""
Write-Host "Configuration Summary:" -ForegroundColor Cyan
Write-Host "----------------------"
Write-Host "Internal LB:  $INTERNAL_LB"
Write-Host "Internal IP:  $INTERNAL_IP"
Write-Host "Hostname:     $HOSTNAME_SUFFIX"
Write-Host ""
Write-Host "URLs:" -ForegroundColor Cyan
Write-Host "  UI:   https://ui.$HOSTNAME_SUFFIX"
Write-Host "  API:  https://apis.$HOSTNAME_SUFFIX/apis/registry/v3"
Write-Host "  IDP:  https://idp.$HOSTNAME_SUFFIX"
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host ""

if ($DNS_CHOICE -eq "A") {
    Write-Host "1. Contact DNS team to add these records:" -ForegroundColor Yellow
    Write-Host "   apis.$HOSTNAME_SUFFIX  IN A  $INTERNAL_IP"
    Write-Host "   ui.$HOSTNAME_SUFFIX    IN A  $INTERNAL_IP"
    Write-Host "   idp.$HOSTNAME_SUFFIX   IN A  $INTERNAL_IP"
    Write-Host ""
}

if ($DNS_CHOICE -eq "B") {
    Write-Host "1. Add Route53 DNS records (see commands above)" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "2. Test from Corporate LAN or VDI:" -ForegroundColor Yellow
Write-Host "   nslookup apis.$HOSTNAME_SUFFIX"
Write-Host "   curl -k -u sr-developer:admin https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info"
Write-Host ""
Write-Host "3. Test Java application:" -ForegroundColor Yellow
Write-Host "   mvn clean compile"
Write-Host "   mvn exec:java -Dexec.mainClass=`"MQTT5Publisher`""
Write-Host ""
Write-Host "4. Open UI in browser:" -ForegroundColor Yellow
Write-Host "   https://ui.$HOSTNAME_SUFFIX"
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Architecture:" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Corporate LAN ──┐"
Write-Host "                  ├──> VPN/Direct Connect ──> AWS VPC (Private)"
Write-Host "  VDI ───────────┘                              │"
Write-Host "                                                 ├──> Solace Cloud"
Write-Host "                                                 └──> Schema Registry"
Write-Host ""
Write-Host "Both services now use the SAME network path! ✅" -ForegroundColor Green
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Cyan

# Save configuration for reference
$configContent = @"
Schema Registry Internal NLB Configuration
Generated: $(Get-Date)

Internal Load Balancer: $INTERNAL_LB
Internal IP: $INTERNAL_IP
Hostname Suffix: $HOSTNAME_SUFFIX

URLs:
  UI:  https://ui.$HOSTNAME_SUFFIX
  API: https://apis.$HOSTNAME_SUFFIX/apis/registry/v3
  IDP: https://idp.$HOSTNAME_SUFFIX

DNS Records Needed:
  apis.$HOSTNAME_SUFFIX  IN A  $INTERNAL_IP
  ui.$HOSTNAME_SUFFIX    IN A  $INTERNAL_IP
  idp.$HOSTNAME_SUFFIX   IN A  $INTERNAL_IP

Test Commands:
  nslookup apis.$HOSTNAME_SUFFIX
  curl -k -u sr-developer:admin https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info
  
PowerShell Test:
  Resolve-DnsName apis.$HOSTNAME_SUFFIX
  Invoke-WebRequest -Uri https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info -Credential (Get-Credential) -SkipCertificateCheck
"@

Set-Content "internal-nlb-config.txt" $configContent

Write-Host "Configuration saved to: internal-nlb-config.txt" -ForegroundColor Green
Write-Host ""






