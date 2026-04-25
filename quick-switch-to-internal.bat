@echo off
REM Quick Switch to Internal NLB - Windows Batch Version
REM Since Solace Cloud (private VPC) is already accessible, this will make
REM Schema Registry use the same network path

echo ==========================================================================
echo Quick Switch: Schema Registry to Internal NLB
echo ==========================================================================
echo.
echo Since your Solace Cloud (private VPC) is already accessible from
echo corporate LAN and VDI, this will align Schema Registry to use the
echo same network path (VPN/Direct Connect).
echo.
echo This will:
echo   1. Convert NLB from public to internal
echo   2. Generate new TLS certificate
echo   3. Update Kubernetes resources
echo   4. Provide DNS configuration commands
echo.
echo Estimated time: 30 minutes
echo.
echo Press any key to continue, or Ctrl+C to cancel...
pause >nul

REM Step 1: Convert to internal NLB
echo.
echo ==========================================================================
echo Step 1: Converting NGINX Ingress to Internal NLB
echo ==========================================================================

kubectl annotate svc ingress-nginx-controller -n ingress-nginx "service.beta.kubernetes.io/aws-load-balancer-scheme=internal" --overwrite
if errorlevel 1 (
    echo ERROR: Failed to update service annotation
    pause
    exit /b 1
)

kubectl annotate svc ingress-nginx-controller -n ingress-nginx "service.beta.kubernetes.io/aws-load-balancer-internal=true" --overwrite

kubectl annotate svc ingress-nginx-controller -n ingress-nginx "service.beta.kubernetes.io/aws-load-balancer-type=nlb" --overwrite

echo [OK] Annotations updated
echo.
echo Waiting for AWS to create new internal NLB (this takes 3-5 minutes)...
echo Old public NLB will be deleted automatically.
echo.

REM Wait for load balancer
timeout /t 30 /nobreak >nul
echo Checking load balancer status...
timeout /t 30 /nobreak >nul
echo Still waiting...
timeout /t 30 /nobreak >nul
echo Almost there...
timeout /t 30 /nobreak >nul

REM Step 2: Get internal LB details
echo.
echo ==========================================================================
echo Step 2: Getting Internal Load Balancer Details
echo ==========================================================================

for /f "delims=" %%i in ('kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath^="{.status.loadBalancer.ingress[0].hostname}"') do set INTERNAL_LB=%%i

if "%INTERNAL_LB%"=="" (
    echo WARNING: Internal LB not ready yet. Wait a bit longer and check with:
    echo    kubectl get svc ingress-nginx-controller -n ingress-nginx
    pause
    exit /b 1
)

echo [OK] Internal Load Balancer: %INTERNAL_LB%

REM Resolve to IP using nslookup
for /f "tokens=2 delims=:" %%a in ('nslookup %INTERNAL_LB% ^| findstr /C:"Address"') do (
    set TEMP_IP=%%a
    if not "!TEMP_IP!"=="" (
        set INTERNAL_IP=!TEMP_IP: =!
    )
)

if "%INTERNAL_IP%"=="" (
    echo WARNING: Could not resolve IP yet. DNS may still be propagating.
    set INTERNAL_IP=UNKNOWN
) else (
    echo [OK] Internal IP: %INTERNAL_IP%
)

REM Step 3: DNS Configuration
echo.
echo ==========================================================================
echo Step 3: DNS Configuration Options
echo ==========================================================================
echo.
echo Choose your DNS approach:
echo.
echo Option A: Use existing corporate DNS (Recommended)
echo   Contact your DNS team to add these records:
echo.
echo   apis.schema-registry.internal.yourcompany.com  IN A  %INTERNAL_IP%
echo   ui.schema-registry.internal.yourcompany.com    IN A  %INTERNAL_IP%
echo   idp.schema-registry.internal.yourcompany.com   IN A  %INTERNAL_IP%
echo.
echo   (Replace 'internal.yourcompany.com' with your actual internal domain)
echo.
echo Option B: Use Route53 Private Hosted Zone
echo   See the PowerShell script for AWS CLI commands
echo.
echo Option C: Use nip.io for testing (NOT for production)
echo   Use hostname: %INTERNAL_IP%.nip.io
echo   Note: nip.io may not resolve from corporate network
echo.
set /p DNS_CHOICE="Enter A, B, or C: "

if /i "%DNS_CHOICE%"=="A" (
    echo.
    echo Selected: Corporate DNS
    set HOSTNAME_SUFFIX=schema-registry.internal.yourcompany.com
    echo.
    echo WARNING: Remember to contact your DNS team to add the records!
) else if /i "%DNS_CHOICE%"=="B" (
    echo.
    echo Selected: Route53 Private Hosted Zone
    set HOSTNAME_SUFFIX=schema-registry.internal.yourcompany.com
    echo.
    echo WARNING: Run the PowerShell script for Route53 commands!
) else if /i "%DNS_CHOICE%"=="C" (
    echo.
    echo Selected: nip.io (testing only)
    set HOSTNAME_SUFFIX=%INTERNAL_IP%.nip.io
) else (
    echo Invalid choice. Defaulting to corporate DNS.
    set HOSTNAME_SUFFIX=schema-registry.internal.yourcompany.com
)

REM Step 4: Generate TLS Certificate
echo.
echo ==========================================================================
echo Step 4: Generating TLS Certificate
echo ==========================================================================

REM Check if OpenSSL is available (Git Bash includes it)
where openssl >nul 2>&1
if errorlevel 1 (
    echo ERROR: OpenSSL not found. Please install Git for Windows or OpenSSL.
    echo Download from: https://git-scm.com/download/win
    echo.
    echo After installing, run this script from Git Bash instead.
    pause
    exit /b 1
)

openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout tls.key -out tls.crt -subj "/CN=*.%HOSTNAME_SUFFIX%" -addext "subjectAltName=DNS:*.%HOSTNAME_SUFFIX%,DNS:%HOSTNAME_SUFFIX%" 2>nul

if errorlevel 1 (
    echo ERROR: Failed to generate certificate
    pause
    exit /b 1
)

echo [OK] Certificate generated for *.%HOSTNAME_SUFFIX%

REM Update Kubernetes secret
kubectl -n solace delete secret schema-registry-tls-secret 2>nul
kubectl -n solace create secret tls schema-registry-tls-secret --cert=tls.crt --key=tls.key

echo [OK] TLS secret updated in Kubernetes

REM Step 5: Update Ingress
echo.
echo ==========================================================================
echo Step 5: Updating Kubernetes Ingress
echo ==========================================================================

kubectl delete ingress schema-registry-ingress -n solace 2>nul

REM Create temporary YAML file
(
echo apiVersion: networking.k8s.io/v1
echo kind: Ingress
echo metadata:
echo   name: schema-registry-ingress
echo   namespace: solace
echo   annotations:
echo     nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
echo     nginx.ingress.kubernetes.io/ssl-redirect: "true"
echo spec:
echo   ingressClassName: nginx
echo   tls:
echo     - hosts:
echo         - ui.%HOSTNAME_SUFFIX%
echo         - apis.%HOSTNAME_SUFFIX%
echo         - idp.%HOSTNAME_SUFFIX%
echo       secretName: schema-registry-tls-secret
echo   rules:
echo     - host: ui.%HOSTNAME_SUFFIX%
echo       http:
echo         paths:
echo           - path: /
echo             pathType: Prefix
echo             backend:
echo               service:
echo                 name: schema-registry-ui-service
echo                 port:
echo                   number: 8888
echo     - host: apis.%HOSTNAME_SUFFIX%
echo       http:
echo         paths:
echo           - path: /
echo             pathType: Prefix
echo             backend:
echo               service:
echo                 name: schema-registry-backend-service
echo                 port:
echo                   number: 8081
echo     - host: idp.%HOSTNAME_SUFFIX%
echo       http:
echo         paths:
echo           - path: /
echo             pathType: Prefix
echo             backend:
echo               service:
echo                 name: schema-registry-idp-service
echo                 port:
echo                   number: 3000
) > ingress-temp.yaml

kubectl apply -f ingress-temp.yaml
del ingress-temp.yaml

echo [OK] Ingress updated

REM Step 6: Update MqttConfig.java
echo.
echo ==========================================================================
echo Step 6: Updating MqttConfig.java
echo ==========================================================================

REM Backup original
copy src\main\java\MqttConfig.java src\main\java\MqttConfig.java.backup >nul

REM Update URL (using PowerShell for regex replacement)
powershell -Command "(Get-Content src\main\java\MqttConfig.java) -replace 'public static final String SCHEMA_REGISTRY_URL = \"https://apis\.[^\"]*\";', 'public static final String SCHEMA_REGISTRY_URL = \"https://apis.%HOSTNAME_SUFFIX%/apis/registry/v3\";' | Set-Content src\main\java\MqttConfig.java"

echo [OK] MqttConfig.java updated
echo    Old config backed up to: src\main\java\MqttConfig.java.backup
echo    New URL: https://apis.%HOSTNAME_SUFFIX%/apis/registry/v3

REM Step 7: Update Java Truststore
echo.
echo ==========================================================================
echo Step 7: Updating Java Truststore
echo ==========================================================================
echo.
echo NOTE: This requires administrator privileges.
echo If it fails, run this command manually as Administrator:
echo.
echo keytool -import -trustcacerts -alias schema-registry-internal -file tls.crt -keystore "%JAVA_HOME%\lib\security\cacerts" -storepass changeit -noprompt
echo.

keytool -import -trustcacerts -alias schema-registry-internal -file tls.crt -keystore "%JAVA_HOME%\lib\security\cacerts" -storepass changeit -noprompt 2>nul

if errorlevel 1 (
    echo WARNING: Certificate import may have failed. You may need to run as Administrator.
) else (
    echo [OK] Certificate imported to Java truststore
)

REM Step 8: Restart Pods
echo.
echo ==========================================================================
echo Step 8: Restarting Schema Registry Pods
echo ==========================================================================

kubectl rollout restart deployment schema-registry-backend -n solace
kubectl rollout restart deployment schema-registry-ui -n solace
kubectl rollout restart deployment schema-registry-idp -n solace

echo Waiting for pods to restart...
kubectl rollout status deployment schema-registry-backend -n solace --timeout=3m
kubectl rollout status deployment schema-registry-ui -n solace --timeout=3m
kubectl rollout status deployment schema-registry-idp -n solace --timeout=3m

echo [OK] All pods restarted

REM Summary
echo.
echo ==========================================================================
echo [OK] Conversion Complete!
echo ==========================================================================
echo.
echo Your Schema Registry is now using INTERNAL NLB
echo Same network path as your Solace Cloud (private VPC)
echo.
echo Configuration Summary:
echo ----------------------
echo Internal LB:  %INTERNAL_LB%
echo Internal IP:  %INTERNAL_IP%
echo Hostname:     %HOSTNAME_SUFFIX%
echo.
echo URLs:
echo   UI:   https://ui.%HOSTNAME_SUFFIX%
echo   API:  https://apis.%HOSTNAME_SUFFIX%/apis/registry/v3
echo   IDP:  https://idp.%HOSTNAME_SUFFIX%
echo.
echo ==========================================================================
echo Next Steps:
echo ==========================================================================
echo.

if /i "%DNS_CHOICE%"=="A" (
    echo 1. Contact DNS team to add these records:
    echo    apis.%HOSTNAME_SUFFIX%  IN A  %INTERNAL_IP%
    echo    ui.%HOSTNAME_SUFFIX%    IN A  %INTERNAL_IP%
    echo    idp.%HOSTNAME_SUFFIX%   IN A  %INTERNAL_IP%
    echo.
)

if /i "%DNS_CHOICE%"=="B" (
    echo 1. Add Route53 DNS records using PowerShell script
    echo.
)

echo 2. Test from Corporate LAN:
echo    nslookup apis.%HOSTNAME_SUFFIX%
echo    curl -k -u sr-developer:admin https://apis.%HOSTNAME_SUFFIX%/apis/registry/v3/system/info
echo.
echo 3. Test from VDI:
echo    (Same commands as above)
echo.
echo 4. Test Java application:
echo    mvn clean compile
echo    mvn exec:java -Dexec.mainClass="MQTT5Publisher"
echo.
echo 5. Open UI in browser:
echo    https://ui.%HOSTNAME_SUFFIX%
echo.
echo ==========================================================================
echo Architecture:
echo ==========================================================================
echo.
echo   Corporate LAN --^>
echo                   +--^> VPN/Direct Connect --^> AWS VPC (Private)
echo   VDI -----------^>                              ^|
echo                                                   +--^> Solace Cloud
echo                                                   +--^> Schema Registry
echo.
echo Both services now use the SAME network path!
echo.
echo ==========================================================================

REM Save configuration for reference
(
echo Schema Registry Internal NLB Configuration
echo Generated: %date% %time%
echo.
echo Internal Load Balancer: %INTERNAL_LB%
echo Internal IP: %INTERNAL_IP%
echo Hostname Suffix: %HOSTNAME_SUFFIX%
echo.
echo URLs:
echo   UI:  https://ui.%HOSTNAME_SUFFIX%
echo   API: https://apis.%HOSTNAME_SUFFIX%/apis/registry/v3
echo   IDP: https://idp.%HOSTNAME_SUFFIX%
echo.
echo DNS Records Needed:
echo   apis.%HOSTNAME_SUFFIX%  IN A  %INTERNAL_IP%
echo   ui.%HOSTNAME_SUFFIX%    IN A  %INTERNAL_IP%
echo   idp.%HOSTNAME_SUFFIX%   IN A  %INTERNAL_IP%
echo.
echo Test Commands:
echo   nslookup apis.%HOSTNAME_SUFFIX%
echo   curl -k -u sr-developer:admin https://apis.%HOSTNAME_SUFFIX%/apis/registry/v3/system/info
) > internal-nlb-config.txt

echo Configuration saved to: internal-nlb-config.txt
echo.
pause






