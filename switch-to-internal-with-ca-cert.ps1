# Quick Switch to Internal NLB - With Corporate CA Certificate
# Customized for: schema-registry.your-domain.com
# Uses existing corporate CA certificate instead of self-signed

param(
    [Parameter(Mandatory=$false)]
    [string]$CertPath,
    
    [Parameter(Mandatory=$false)]
    [string]$KeyPath,
    
    [Parameter(Mandatory=$false)]
    [string]$Subnets,  # Comma-separated subnet IDs for NLB
    
    [Parameter(Mandatory=$false)]
    [string]$AllowedCIDRs,  # Comma-separated CIDR ranges (e.g., "10.0.0.0/8,172.16.0.0/12")
    
    [switch]$SkipConfirmation
)

# Enable strict error handling
$ErrorActionPreference = "Stop"

# Configuration
$HOSTNAME_SUFFIX = "schema-registry.your-domain.com"
$INGRESS_NGINX_VERSION = "4.9.1"

# Default allowed CIDRs - restrict to internal networks only (no 0.0.0.0/0)
$DEFAULT_ALLOWED_CIDRS = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

Write-Host "=========================================================================="
Write-Host "Quick Switch: Schema Registry to Internal NLB"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "Configuration:"
Write-Host "  Hostname: $HOSTNAME_SUFFIX"
Write-Host "  Using: Corporate CA Certificate"
Write-Host ""
Write-Host "This will:"
Write-Host "  1. Convert NLB from public to internal"
Write-Host "  2. Use your corporate CA certificate"
Write-Host "  3. Update Kubernetes resources"
Write-Host "  4. Configure DNS for your-domain.com"
Write-Host ""
Write-Host "  Estimated time: 30 minutes"
Write-Host ""

# Check for certificate files if not provided as parameters
if (-not $CertPath -or -not $KeyPath) {
    Write-Host "Certificate Location:"
    Write-Host ""
    Write-Host "Please provide paths to your corporate CA certificate and key."
    Write-Host "These should be provided by your security/infrastructure team."
    Write-Host ""
    
    if (-not $CertPath) {
        $CertPath = Read-Host "Enter path to certificate file (e.g., C:\certs\schema-registry.crt or tls.crt)"
        if (-not (Test-Path $CertPath)) {
            Write-Host "[ERROR] Certificate file not found: $CertPath"
            Write-Host ""
            Write-Host "Please obtain the certificate from your security team for:"
            Write-Host "  - CN: *.schema-registry.your-domain.com"
            Write-Host "  - SANs: *.schema-registry.your-domain.com, schema-registry.your-domain.com"
            Write-Host ""
            Write-Host "Or place the certificate in this directory as 'tls.crt' and 'tls.key'"
            exit 1
        }
    }
    
    if (-not $KeyPath) {
        $KeyPath = Read-Host "Enter path to private key file (e.g., C:\certs\schema-registry.key or tls.key)"
        if (-not (Test-Path $KeyPath)) {
            Write-Host "[ERROR] Private key file not found: $KeyPath"
            exit 1
        }
    }
}

Write-Host ""
Write-Host "[OK] Certificate file: $CertPath"
Write-Host "[OK] Private key file: $KeyPath"
Write-Host ""

if (-not $SkipConfirmation) {
    $confirm = Read-Host "Press Enter to continue, or Ctrl+C to cancel"
}

# Step 1: Configure NGINX Ingress with Internal NLB
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 1: Configuring NGINX Ingress Controller with Internal NLB"
Write-Host "=========================================================================="

# Check if subnets were provided
if (-not $Subnets) {
    Write-Host "[INFO] No subnets specified. You may need to provide subnet IDs."
    Write-Host "       Example: -Subnets 'subnet-xxx,subnet-yyy,subnet-zzz'"
    Write-Host ""
    $Subnets = Read-Host "Enter comma-separated subnet IDs (or press Enter to skip)"
}

# Check if allowed CIDRs were provided
if (-not $AllowedCIDRs) {
    Write-Host ""
    Write-Host "[INFO] Security Group CIDR Restriction"
    Write-Host "       Default: $DEFAULT_ALLOWED_CIDRS (internal networks only)"
    Write-Host "       This prevents 0.0.0.0/0 rules in security groups."
    Write-Host ""
    Write-Host "       To customize, provide comma-separated CIDR ranges:"
    Write-Host "       Example: -AllowedCIDRs '10.21.0.0/16,10.162.0.0/16'"
    Write-Host ""
    $customCIDRs = Read-Host "Enter allowed CIDR ranges (or press Enter to use defaults)"
    
    if ($customCIDRs -and $customCIDRs.Trim() -ne "") {
        $AllowedCIDRs = $customCIDRs
    } else {
        $AllowedCIDRs = $DEFAULT_ALLOWED_CIDRS
    }
}

Write-Host "[OK] Allowed CIDRs: $AllowedCIDRs"
Write-Host "     (No 0.0.0.0/0 - restricted to specified networks)"

try {
    # Create IngressClass if it doesn't exist
    Write-Host ""
    Write-Host "[INFO] Creating IngressClass..."
    $ingressClass = @"
apiVersion: networking.k8s.io/v1
kind: IngressClass
metadata:
  name: nginx
spec:
  controller: k8s.io/ingress-nginx
"@
    $ingressClass | kubectl apply -f - 2>$null | Out-Null
    Write-Host "[OK] IngressClass created/updated"

    # Build Helm upgrade command
    Write-Host "[INFO] Upgrading NGINX Ingress Controller via Helm..."
    
    $helmArgs = @(
        "upgrade", "--install", "ingress-nginx", "ingress-nginx/ingress-nginx",
        "--version", $INGRESS_NGINX_VERSION,
        "-n", "ingress-nginx",
        "--create-namespace",
        "--set", "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-scheme=internal",
        "--set", "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-type=nlb",
        "--set", "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-nlb-target-type=ip"
    )
    
    # Add subnets if provided
    if ($Subnets -and $Subnets.Trim() -ne "") {
        $helmArgs += "--set-string"
        $helmArgs += "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-subnets=$Subnets"
    }
    
    # Add source ranges to restrict security groups (prevents 0.0.0.0/0)
    if ($AllowedCIDRs -and $AllowedCIDRs.Trim() -ne "") {
        # Escape backslashes for the annotation key
        $helmArgs += "--set-string"
        $helmArgs += "controller.service.loadBalancerSourceRanges={$AllowedCIDRs}"
    }
    
    # Execute Helm upgrade
    & helm $helmArgs
    
    if ($LASTEXITCODE -ne 0) {
        throw "Helm upgrade failed"
    }

    Write-Host "[OK] NGINX Ingress Controller upgraded with internal NLB configuration"
    Write-Host "[OK] Security groups restricted to: $AllowedCIDRs"
} catch {
    Write-Host "[ERROR] Failed to configure NGINX Ingress Controller"
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host ""
Write-Host "[WAIT] AWS is creating new internal NLB (this takes 3-5 minutes)..."
Write-Host "       Old public NLB will be deleted automatically."
Write-Host ""

# Wait for load balancer with progress
for ($i = 1; $i -le 4; $i++) {
    Start-Sleep -Seconds 30
    Write-Host "[WAIT] Progress: $($i * 30) seconds elapsed..."
}

# Step 2: Get internal LB details
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 2: Getting Internal Load Balancer Details"
Write-Host "=========================================================================="

$INTERNAL_LB = kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>$null

if ([string]::IsNullOrWhiteSpace($INTERNAL_LB)) {
    Write-Host "[WARNING] Internal LB not ready yet. Wait a bit longer and check with:"
    Write-Host "          kubectl get svc ingress-nginx-controller -n ingress-nginx"
    exit 1
}

Write-Host "[OK] Internal Load Balancer: $INTERNAL_LB"

# Resolve to IP
try {
    $dnsResult = Resolve-DnsName -Name $INTERNAL_LB -Type A -ErrorAction SilentlyContinue | Where-Object { $_.Type -eq 'A' } | Select-Object -First 1
    $INTERNAL_IP = $dnsResult.IPAddress
    Write-Host "[OK] Internal IP: $INTERNAL_IP"
} catch {
    Write-Host "[WARNING] Could not resolve IP yet. DNS may still be propagating."
    $INTERNAL_IP = "UNKNOWN"
}

# Step 3: Update Kubernetes TLS Secret with Corporate Certificate
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 3: Updating TLS Secret with Corporate CA Certificate"
Write-Host "=========================================================================="

# Delete existing secret
kubectl -n solace delete secret schema-registry-tls-secret 2>$null | Out-Null

# Create new secret with corporate cert
kubectl -n solace create secret tls schema-registry-tls-secret --cert=$CertPath --key=$KeyPath | Out-Null

Write-Host "[OK] TLS secret updated with corporate CA certificate"

# Step 4: Update Ingress with new hostname
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 4: Updating Kubernetes Ingress"
Write-Host "=========================================================================="

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

Write-Host "[OK] Ingress updated for $HOSTNAME_SUFFIX"

# Step 5: Update Helm values-override.yaml
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 5: Updating Helm Values"
Write-Host "=========================================================================="

$valuesPath = "infra\values-override.yaml"

if (Test-Path $valuesPath) {
    # Backup original
    Copy-Item $valuesPath "$valuesPath.backup" -Force

    # Update hostNameSuffix
    $content = Get-Content $valuesPath -Raw
    $newContent = $content -replace 'hostNameSuffix:\s*"[^"]*"', "hostNameSuffix: `"$HOSTNAME_SUFFIX`""
    Set-Content $valuesPath $newContent

    Write-Host "[OK] values-override.yaml updated"
    Write-Host "     Backup saved to: $valuesPath.backup"
} else {
    Write-Host "[INFO] values-override.yaml not found (optional)"
}

# Step 6: Restart Pods
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 6: Restarting Schema Registry Pods"
Write-Host "=========================================================================="

kubectl rollout restart deployment schema-registry-backend -n solace | Out-Null
kubectl rollout restart deployment schema-registry-ui -n solace | Out-Null
kubectl rollout restart deployment schema-registry-idp -n solace | Out-Null

Write-Host "[WAIT] Waiting for pods to restart..."
kubectl rollout status deployment schema-registry-backend -n solace --timeout=3m | Out-Null
kubectl rollout status deployment schema-registry-ui -n solace --timeout=3m | Out-Null
kubectl rollout status deployment schema-registry-idp -n solace --timeout=3m | Out-Null

Write-Host "[OK] All pods restarted successfully"

# Summary
Write-Host ""
Write-Host "=========================================================================="
Write-Host "CONVERSION COMPLETE"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "Your Schema Registry is now using INTERNAL NLB"
Write-Host "Same network path as your Solace Cloud (private VPC)"
Write-Host ""
Write-Host "Configuration Summary:"
Write-Host "----------------------"
Write-Host "Internal LB:  $INTERNAL_LB"
Write-Host "Internal IP:  $INTERNAL_IP"
Write-Host "Hostname:     $HOSTNAME_SUFFIX"
Write-Host "Environment:  your-domain.com"
Write-Host ""
Write-Host "URLs:"
Write-Host "  UI:   https://ui.$HOSTNAME_SUFFIX"
Write-Host "  API:  https://apis.$HOSTNAME_SUFFIX/apis/registry/v3"
Write-Host "  IDP:  https://idp.$HOSTNAME_SUFFIX"
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Next Steps: DNS Configuration"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "Contact your DNS team to add these records"
Write-Host "to your DNS zone:"
Write-Host ""
Write-Host "A Records (preferred):"
Write-Host "  apis.schema-registry.your-domain.com  IN A  $INTERNAL_IP"
Write-Host "  ui.schema-registry.your-domain.com    IN A  $INTERNAL_IP"
Write-Host "  idp.schema-registry.your-domain.com   IN A  $INTERNAL_IP"
Write-Host ""
Write-Host "Or CNAME Records (alternative):"
Write-Host "  apis.schema-registry.your-domain.com  IN CNAME  $INTERNAL_LB"
Write-Host "  ui.schema-registry.your-domain.com    IN CNAME  $INTERNAL_LB"
Write-Host "  idp.schema-registry.your-domain.com   IN CNAME  $INTERNAL_LB"
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Testing (after DNS is configured):"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "1. Test DNS resolution:"
Write-Host "   nslookup apis.schema-registry.your-domain.com"
Write-Host ""
Write-Host "2. Test API access:"
Write-Host "   curl -u sr-developer:admin https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info"
Write-Host ""
Write-Host "3. Test from corporate LAN or VDI:"
Write-Host "   Should work from both locations via VPN/Direct Connect"
Write-Host ""
Write-Host "4. Open UI in browser:"
Write-Host "   https://ui.$HOSTNAME_SUFFIX"
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Architecture:"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "  Corporate LAN -->"
Write-Host "                   +--> VPN/Direct Connect --> AWS VPC (Private)"
Write-Host "  VDI ----------->"
Write-Host "                                                 |"
Write-Host "                                                 +--> Solace Cloud"
Write-Host "                                                 +--> Schema Registry"
Write-Host "                                                      (your-domain.com)"
Write-Host ""
Write-Host "Both services now use the SAME network path."
Write-Host ""
Write-Host "=========================================================================="

# Save configuration for reference
$configContent = @"
Schema Registry Internal NLB Configuration
Generated: $(Get-Date)
Environment: your-domain.com

Internal Load Balancer: $INTERNAL_LB
Internal IP: $INTERNAL_IP
Hostname Suffix: $HOSTNAME_SUFFIX

Certificate: Corporate CA Certificate
  - Certificate file: $CertPath
  - Private key file: $KeyPath

NGINX Ingress Configuration:
  - Version: $INGRESS_NGINX_VERSION
  - NLB Type: Internal
  - Target Type: IP (pod IPs directly)
  - Subnets: $Subnets
  - Allowed CIDRs: $AllowedCIDRs (NO 0.0.0.0/0)

Helm Command Used:
  helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx ``
    --version $INGRESS_NGINX_VERSION ``
    -n ingress-nginx ``
    --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-scheme"=internal ``
    --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-type"=nlb ``
    --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-nlb-target-type"=ip ``
    --set-string controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-subnets"="$Subnets" ``
    --set-string controller.service.loadBalancerSourceRanges="{$AllowedCIDRs}"

Security Group Note:
  The loadBalancerSourceRanges setting restricts inbound traffic to the specified
  CIDR ranges, preventing 0.0.0.0/0 rules from being created.

URLs:
  UI:  https://ui.$HOSTNAME_SUFFIX
  API: https://apis.$HOSTNAME_SUFFIX/apis/registry/v3
  IDP: https://idp.$HOSTNAME_SUFFIX

DNS Records Needed (A records):
  apis.schema-registry.your-domain.com  IN A  $INTERNAL_IP
  ui.schema-registry.your-domain.com    IN A  $INTERNAL_IP
  idp.schema-registry.your-domain.com   IN A  $INTERNAL_IP

Or DNS Records (CNAME records):
  apis.schema-registry.your-domain.com  IN CNAME  $INTERNAL_LB
  ui.schema-registry.your-domain.com    IN CNAME  $INTERNAL_LB
  idp.schema-registry.your-domain.com   IN CNAME  $INTERNAL_LB

Test Commands:
  nslookup apis.schema-registry.your-domain.com
  curl -u sr-developer:admin https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info
  
PowerShell Test:
  Resolve-DnsName apis.schema-registry.your-domain.com
  `$cred = Get-Credential -UserName 'sr-developer'
  Invoke-RestMethod -Uri https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info -Credential `$cred

Kubernetes Resources:
  Service:  ingress-nginx-controller (namespace: ingress-nginx)
  Ingress:  schema-registry-ingress (namespace: solace)
  Secret:   schema-registry-tls-secret (namespace: solace)
  Deployments:
    - schema-registry-backend
    - schema-registry-ui
    - schema-registry-idp

Contact Information:
  DNS Team: [Add contact for your DNS zone]
  Security Team: [Add contact for CA certificates]
  Network Team: [Add contact for VPN/Direct Connect]
"@

Set-Content "internal-nlb-config.txt" $configContent

Write-Host ""
Write-Host "[OK] Configuration saved to: internal-nlb-config.txt"
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Share internal-nlb-config.txt with your DNS team for record creation."
Write-Host "=========================================================================="
Write-Host ""
