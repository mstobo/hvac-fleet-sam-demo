# Fix Schema Registry UI Backend Configuration
# This updates the UI's configuration to point to the correct backend URL

param(
    [switch]$SkipConfirmation
)

$ErrorActionPreference = "Stop"

$HOSTNAME_SUFFIX = "schema-registry.your-domain.com"

Write-Host "=========================================================================="
Write-Host "Fix Schema Registry UI Backend Configuration"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "This will update the UI configuration to use the correct backend URL:"
Write-Host "  https://apis.$HOSTNAME_SUFFIX"
Write-Host ""

if (-not $SkipConfirmation) {
    $confirm = Read-Host "Press Enter to continue, or Ctrl+C to cancel"
}

# Step 1: Check current UI configuration
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 1: Checking Current UI Configuration"
Write-Host "=========================================================================="

$uiPods = kubectl get pods -n solace -l app=schema-registry-ui -o jsonpath='{.items[0].metadata.name}' 2>$null

if ([string]::IsNullOrWhiteSpace($uiPods)) {
    Write-Host "[ERROR] No UI pods found"
    exit 1
}

Write-Host "[OK] Found UI pod: $uiPods"

# Check the UI's environment variables
Write-Host ""
Write-Host "Checking UI environment variables..."
kubectl exec -n solace $uiPods -- env | Select-String "REGISTRY" | ForEach-Object { Write-Host "  $_" }

# Step 2: Update Helm values to fix UI backend URL
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 2: Updating Helm Values"
Write-Host "=========================================================================="

$valuesPath = "infra\values-override.yaml"

if (Test-Path $valuesPath) {
    $content = Get-Content $valuesPath -Raw
    
    # Check if ui section exists and update it
    if ($content -match "ui:") {
        Write-Host "[INFO] UI section found in values file"
        
        # Add or update registryUrl in ui section
        if ($content -notmatch "registryUrl:") {
            Write-Host "[INFO] Adding registryUrl to ui section"
            $content = $content -replace "(ui:\s*\n)", "`$1  registryUrl: `"https://apis.$HOSTNAME_SUFFIX`"`n"
        } else {
            Write-Host "[INFO] Updating existing registryUrl"
            $content = $content -replace 'registryUrl:\s*"[^"]*"', "registryUrl: `"https://apis.$HOSTNAME_SUFFIX`""
        }
        
        Set-Content $valuesPath $content
        Write-Host "[OK] values-override.yaml updated"
    } else {
        Write-Host "[INFO] No ui section in values file, will add it"
        $content += @"

ui:
  registryUrl: "https://apis.$HOSTNAME_SUFFIX"
"@
        Set-Content $valuesPath $content
        Write-Host "[OK] UI configuration added to values-override.yaml"
    }
} else {
    Write-Host "[WARNING] values-override.yaml not found"
    Write-Host "[INFO] Creating minimal values file"
    
    $minimalValues = @"
ingress:
  hostNameSuffix: "$HOSTNAME_SUFFIX"

ui:
  registryUrl: "https://apis.$HOSTNAME_SUFFIX"
"@
    
    New-Item -Path "infra" -ItemType Directory -Force | Out-Null
    Set-Content $valuesPath $minimalValues
    Write-Host "[OK] Created values-override.yaml"
}

# Step 3: Apply updated configuration via Helm
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 3: Applying Configuration via Helm"
Write-Host "=========================================================================="

try {
    # Find the Helm chart
    $chartPath = ".serdes\schema-registry-v1.0.0\helm-chart\solace-schema-registry-1.0.0.tgz"
    
    if (-not (Test-Path $chartPath)) {
        Write-Host "[ERROR] Helm chart not found at: $chartPath"
        Write-Host "[INFO] Skipping Helm upgrade, will restart pods manually"
    } else {
        Write-Host "[INFO] Running Helm upgrade..."
        helm upgrade schema-registry $chartPath `
            -n solace `
            -f $valuesPath | Out-Null
        
        Write-Host "[OK] Helm upgrade completed"
    }
} catch {
    Write-Host "[WARNING] Helm upgrade failed, will restart pods manually"
}

# Step 4: Restart UI pods to pick up new configuration
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 4: Restarting UI Pods"
Write-Host "=========================================================================="

kubectl rollout restart deployment schema-registry-ui -n solace | Out-Null

Write-Host "[WAIT] Waiting for UI pods to restart..."
kubectl rollout status deployment schema-registry-ui -n solace --timeout=3m | Out-Null

Write-Host "[OK] UI pods restarted"

# Step 5: Restart IDP and backend pods (may also need new config)
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 5: Restarting IDP and Backend Pods"
Write-Host "=========================================================================="

kubectl rollout restart deployment schema-registry-idp -n solace | Out-Null
kubectl rollout restart deployment schema-registry-backend -n solace | Out-Null

Write-Host "[WAIT] Waiting for all pods to restart..."
kubectl rollout status deployment schema-registry-idp -n solace --timeout=3m | Out-Null
kubectl rollout status deployment schema-registry-backend -n solace --timeout=3m | Out-Null

Write-Host "[OK] All pods restarted"

# Step 6: Verify configuration
Write-Host ""
Write-Host "=========================================================================="
Write-Host "Step 6: Verifying Configuration"
Write-Host "=========================================================================="

Start-Sleep -Seconds 10

$newUiPod = kubectl get pods -n solace -l app=schema-registry-ui -o jsonpath='{.items[0].metadata.name}' 2>$null

Write-Host "[INFO] Checking new UI pod configuration..."
$registryUrlCheck = kubectl exec -n solace $newUiPod -- env 2>$null | Select-String "REGISTRY_URL"

if ($registryUrlCheck) {
    Write-Host "[OK] UI configuration:"
    Write-Host "     $registryUrlCheck"
} else {
    Write-Host "[WARNING] Could not verify REGISTRY_URL environment variable"
}

# Check UI config.js file
Write-Host ""
Write-Host "[INFO] Checking UI config.js..."
$configJs = kubectl exec -n solace $newUiPod -- cat /opt/app-root/src/config.js 2>$null

if ($configJs -match "registryUrl") {
    Write-Host "[OK] Found registryUrl in config.js"
    $configJs | Select-String "registryUrl" | ForEach-Object { Write-Host "     $_" }
} else {
    Write-Host "[WARNING] Could not find registryUrl in config.js"
}

# Summary
Write-Host ""
Write-Host "=========================================================================="
Write-Host "FIX COMPLETE"
Write-Host "=========================================================================="
Write-Host ""
Write-Host "UI Backend Configuration Updated:"
Write-Host "  Backend URL: https://apis.$HOSTNAME_SUFFIX"
Write-Host ""
Write-Host "Next Steps:"
Write-Host "  1. Wait 1-2 minutes for all pods to fully start"
Write-Host "  2. Clear browser cache or open incognito window"
Write-Host "  3. Access UI: https://ui.$HOSTNAME_SUFFIX"
Write-Host "  4. The UI should now connect to the backend successfully"
Write-Host ""
Write-Host "If the issue persists:"
Write-Host "  - Open browser Developer Tools (F12)"
Write-Host "  - Check Console for errors"
Write-Host "  - Check Network tab for failed API calls"
Write-Host "  - Verify the API URL being used"
Write-Host ""
Write-Host "=========================================================================="

$summary = @"
UI Configuration Fix Applied
Generated: $(Get-Date)

Updated Configuration:
  Backend URL: https://apis.$HOSTNAME_SUFFIX
  Values File: $valuesPath

Pods Restarted:
  - schema-registry-ui
  - schema-registry-idp
  - schema-registry-backend

Testing:
  1. Access UI: https://ui.$HOSTNAME_SUFFIX
  2. Login with: sr-developer / admin
  3. Should now connect successfully

Troubleshooting:
  - Check pod logs: kubectl logs -n solace -l app=schema-registry-ui --tail=50
  - Check backend: curl -u sr-developer:admin https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info
  - Verify DNS: nslookup apis.$HOSTNAME_SUFFIX
"@

Set-Content "ui-fix-summary.txt" $summary

Write-Host "[OK] Summary saved to: ui-fix-summary.txt"
Write-Host ""

