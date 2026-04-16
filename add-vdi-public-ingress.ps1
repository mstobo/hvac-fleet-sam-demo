# Add Public Ingress for VDI Access
# This creates a second ingress controller (public) alongside the existing internal one
# Use this if VDI users need access but don't have VPN

param(
    [Parameter(Mandatory=$true)]
    [string]$VdiPublicIP,  # VDI public IP or CIDR range
    
    [switch]$SkipConfirmation
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================================================="
Write-Host "Add Public Ingress for VDI Access"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "This will create a SECOND NGINX Ingress Controller with:"
Write-Host "  - Public NLB (for VDI access)"
Write-Host "  - Restricted to VDI IP: $VdiPublicIP"
Write-Host "  - Separate from internal NLB (corporate LAN)"
Write-Host ""
Write-Host "WARNING: This exposes Schema Registry to the internet (restricted to VDI IP)"
Write-Host ""

if (-not $SkipConfirmation) {
    $confirm = Read-Host "Press Enter to continue, or Ctrl+C to cancel"
}

# Step 1: Install second NGINX ingress controller (public)
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 1: Installing Public NGINX Ingress Controller"
Write-Host "=========================================================================="

try {
    helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>$null | Out-Null
    helm repo update | Out-Null

    helm upgrade --install ingress-nginx-public ingress-nginx/ingress-nginx `
        --namespace ingress-nginx-public `
        --create-namespace `
        --set controller.ingressClass=nginx-public `
        --set controller.ingressClassResource.name=nginx-public `
        --set controller.ingressClassResource.controllerValue=k8s.io/ingress-nginx-public `
        --set "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-type=nlb" `
        --set "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-source-ranges=$VdiPublicIP" | Out-Null

    Write-Host "[OK] Public ingress controller installed"
} catch {
    Write-Host "[ERROR] Failed to install public ingress controller"
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host ""
Write-Host "[WAIT] Waiting for public load balancer to be created (2-3 minutes)..."
Start-Sleep -Seconds 120

# Step 2: Get public LB details
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 2: Getting Public Load Balancer Details"
Write-Host "=========================================================================="

$PUBLIC_LB = kubectl get svc ingress-nginx-public-controller -n ingress-nginx-public -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>$null

if ([string]::IsNullOrWhiteSpace($PUBLIC_LB)) {
    Write-Host "[WARNING] Public LB not ready yet. Check with:"
    Write-Host "          kubectl get svc ingress-nginx-public-controller -n ingress-nginx-public"
    exit 1
}

Write-Host "[OK] Public Load Balancer: $PUBLIC_LB"

# Resolve to IP
try {
    $dnsResult = Resolve-DnsName -Name $PUBLIC_LB -Type A -ErrorAction SilentlyContinue | Where-Object { $_.Type -eq 'A' } | Select-Object -First 1
    $PUBLIC_IP = $dnsResult.IPAddress
    Write-Host "[OK] Public IP: $PUBLIC_IP"
} catch {
    Write-Host "[WARNING] Could not resolve IP yet."
    $PUBLIC_IP = "UNKNOWN"
}

# Step 3: Create public ingress for VDI
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 3: Creating Public Ingress for VDI"
Write-Host "=========================================================================="

$HOSTNAME_SUFFIX = "schema-registry.your-domain.com"

$publicIngressYaml = @"
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: schema-registry-ingress-public
  namespace: solace
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx-public
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

$publicIngressYaml | kubectl apply -f - | Out-Null

Write-Host "[OK] Public ingress created"

# Summary
Write-Host ""
Write-Host "=========================================================================="
Write-Host "DUAL INGRESS CONFIGURATION COMPLETE"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "You now have TWO ingress controllers:"
Write-Host ""
Write-Host "1. INTERNAL NLB (Corporate LAN):"
Write-Host "   - Namespace: ingress-nginx"
Write-Host "   - Access: Via VPN/Direct Connect"
Write-Host "   - Users: Corporate LAN workstations"
Write-Host ""
Write-Host "2. PUBLIC NLB (VDI):"
Write-Host "   - Namespace: ingress-nginx-public"
Write-Host "   - Public IP: $PUBLIC_IP"
Write-Host "   - Access: Restricted to $VdiPublicIP"
Write-Host "   - Users: VDI sessions"
Write-Host ""
Write-Host "=========================================================================="
Write-Host "DNS Configuration Needed:"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "The SAME hostnames now resolve via TWO different paths:"
Write-Host ""
Write-Host "For Corporate LAN (internal DNS or Route53 Private Zone):"
Write-Host "  apis.schema-registry.your-domain.com  -> Internal IP"
Write-Host ""
Write-Host "For VDI (public DNS):"
Write-Host "  apis.schema-registry.your-domain.com  -> $PUBLIC_IP"
Write-Host ""
Write-Host "You'll need SPLIT-HORIZON DNS or different hostnames."
Write-Host ""
Write-Host "=========================================================================="
Write-Host "RECOMMENDED: Use Different Hostnames"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "Option A: Separate subdomains"
Write-Host "  Internal: apis.schema-registry-internal.your-domain.com"
Write-Host "  Public:   apis.schema-registry-vdi.your-domain.com"
Write-Host ""
Write-Host "Option B: Use nip.io for public (testing)"
Write-Host "  VDI users access: https://apis.$PUBLIC_IP.nip.io/apis/registry/v3"
Write-Host ""
Write-Host "=========================================================================="

$configContent = @"
Dual Ingress Configuration
Generated: $(Get-Date)

Internal NLB (Corporate LAN):
  Service: ingress-nginx-controller
  Namespace: ingress-nginx
  Access: VPN/Direct Connect only

Public NLB (VDI):
  Service: ingress-nginx-public-controller
  Namespace: ingress-nginx-public
  Public IP: $PUBLIC_IP
  Public LB: $PUBLIC_LB
  Restricted to: $VdiPublicIP

URLs (require split-horizon DNS or separate hostnames):
  https://ui.schema-registry.your-domain.com
  https://apis.schema-registry.your-domain.com/apis/registry/v3
  https://idp.schema-registry.your-domain.com

Test from VDI (using public IP directly):
  curl -u sr-developer:admin https://apis.$PUBLIC_IP.nip.io/apis/registry/v3/system/info

Cleanup (to remove public ingress):
  helm uninstall ingress-nginx-public -n ingress-nginx-public
  kubectl delete namespace ingress-nginx-public
  kubectl delete ingress schema-registry-ingress-public -n solace
"@

Set-Content "dual-ingress-config.txt" $configContent

Write-Host "[OK] Configuration saved to: dual-ingress-config.txt"
Write-Host ""






