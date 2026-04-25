# Windows Setup Guide - Switch to Internal NLB

## Overview

You have **3 options** for running the switch script on Windows. Choose the one that works best for your environment.

---

## Option 1: PowerShell (Recommended) ⭐

**Best for**: Modern Windows 10/11 with PowerShell 5.1+

**Prerequisites:**
- PowerShell 5.1 or higher (check with: `$PSVersionTable.PSVersion`)
- kubectl installed and configured
- AWS CLI installed (if using Route53)
- OpenSSL installed (comes with Git for Windows)

**How to Run:**

```powershell
# Open PowerShell as Administrator (right-click PowerShell icon → "Run as Administrator")

# Navigate to project directory
cd C:\path\to\mqtt5SRDemo

# Allow script execution (one-time setup)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Run the script
.\quick-switch-to-internal.ps1
```

**Features:**
- ✅ Colored output
- ✅ Better error handling
- ✅ Progress indicators
- ✅ Automatic DNS resolution
- ✅ AWS CLI commands generation

---

## Option 2: Command Prompt / Batch File

**Best for**: Older Windows systems, restricted environments

**Prerequisites:**
- Command Prompt (cmd.exe)
- kubectl installed and configured
- OpenSSL installed (comes with Git for Windows)

**How to Run:**

```batch
REM Open Command Prompt as Administrator
REM Navigate to project directory
cd C:\path\to\mqtt5SRDemo

REM Run the batch file
quick-switch-to-internal.bat
```

**Note:** If you see errors about OpenSSL not found, install Git for Windows from: https://git-scm.com/download/win

---

## Option 3: Git Bash (Linux-like)

**Best for**: Users comfortable with Linux/Unix commands

**Prerequisites:**
- Git for Windows installed (includes Git Bash)

**How to Run:**

```bash
# Open Git Bash (right-click in folder → "Git Bash Here")

# Run the Linux/Mac version
./quick-switch-to-internal.sh
```

---

## Comparison

| Feature | PowerShell | Batch File | Git Bash |
|---------|-----------|------------|----------|
| Color output | ✅ Yes | ❌ No | ✅ Yes |
| Error handling | ✅ Excellent | ⚠️ Basic | ✅ Excellent |
| DNS resolution | ✅ Built-in | ⚠️ Basic | ✅ Built-in |
| Progress bars | ✅ Yes | ❌ No | ✅ Yes |
| AWS CLI support | ✅ Yes | ⚠️ Limited | ✅ Yes |
| Requires Admin | ✅ Yes | ✅ Yes | ✅ Yes |

---

## Prerequisites Installation

### Install kubectl

**Option A: Chocolatey (Recommended)**
```powershell
# Install Chocolatey first (if not installed)
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Install kubectl
choco install kubernetes-cli
```

**Option B: Manual Download**
1. Download: https://kubernetes.io/docs/tasks/tools/install-kubectl-windows/
2. Place `kubectl.exe` in `C:\Program Files\kubectl\`
3. Add to PATH: System Properties → Environment Variables → System Variables → Path → New → `C:\Program Files\kubectl`

### Install OpenSSL (via Git for Windows)

**Download and install:**
- Git for Windows: https://git-scm.com/download/win
- This includes OpenSSL and Git Bash

**Or install OpenSSL separately:**
- OpenSSL for Windows: https://slproweb.com/products/Win32OpenSSL.html

### Install AWS CLI (if using Route53)

**Download:**
- AWS CLI v2: https://awscli.amazonaws.com/AWSCLIV2.msi

**Verify installation:**
```powershell
aws --version
```

---

## Troubleshooting

### Issue: "execution of scripts is disabled on this system"

**Solution:**
```powershell
# Run PowerShell as Administrator
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Issue: "kubectl: command not found"

**Solution:**
```powershell
# Check if kubectl is installed
kubectl version --client

# If not installed, install via Chocolatey:
choco install kubernetes-cli

# Or download manually and add to PATH
```

### Issue: "openssl: command not found"

**Solution:**
Install Git for Windows (includes OpenSSL):
- Download: https://git-scm.com/download/win
- Run installer with default options
- Restart PowerShell/Command Prompt

### Issue: "Unable to connect to the server"

**Solution:**
Your AWS credentials may have expired. Refresh them:
```powershell
# Set AWS credentials
$env:AWS_ACCESS_KEY_ID="YOUR_KEY"
$env:AWS_SECRET_ACCESS_KEY="YOUR_SECRET"
$env:AWS_SESSION_TOKEN="YOUR_TOKEN"  # if using temporary credentials

# Update kubeconfig
aws eks update-kubeconfig --name sr-eks --region us-east-2
```

### Issue: Certificate import fails

**Solution:**
Run PowerShell/Command Prompt as Administrator, then run this command manually:
```powershell
keytool -import `
  -trustcacerts `
  -alias schema-registry-internal `
  -file tls.crt `
  -keystore "$env:JAVA_HOME\lib\security\cacerts" `
  -storepass changeit `
  -noprompt
```

---

## Post-Script Steps

After the script completes successfully:

### 1. Verify DNS Configuration

```powershell
# Test DNS resolution
nslookup apis.schema-registry.internal.yourcompany.com

# Or with PowerShell
Resolve-DnsName apis.schema-registry.internal.yourcompany.com
```

### 2. Test API Access

**Using curl (if available):**
```powershell
curl -k -u sr-developer:admin https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info
```

**Using PowerShell:**
```powershell
# Create credentials
$username = "sr-developer"
$password = ConvertTo-SecureString "admin" -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($username, $password)

# Test API (PowerShell 7+)
Invoke-RestMethod -Uri https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info -Credential $cred -SkipCertificateCheck

# For PowerShell 5.1 (skip cert check workaround)
[System.Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}
Invoke-RestMethod -Uri https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info -Credential $cred
```

### 3. Test UI Access

Open in browser:
```
https://ui.schema-registry.internal.yourcompany.com
```

### 4. Test Java Application

```powershell
# Rebuild application
mvn clean compile

# Run publisher
mvn exec:java -Dexec.mainClass="MQTT5Publisher"

# Run subscriber (in another terminal)
mvn exec:java -Dexec.mainClass="MQTT5Subscriber"
```

---

## DNS Configuration for Your Team

### If Using Corporate DNS

Contact your DNS team with this information:

**DNS Records Required:**
```
apis.schema-registry.internal.yourcompany.com  IN A  <INTERNAL_IP>
ui.schema-registry.internal.yourcompany.com    IN A  <INTERNAL_IP>
idp.schema-registry.internal.yourcompany.com   IN A  <INTERNAL_IP>
```

**Internal IP:** Found in `internal-nlb-config.txt` after running the script

### If Using Route53 Private Hosted Zone

The PowerShell script provides ready-to-run AWS CLI commands. After the script completes, you'll see commands like:

```powershell
# Get hosted zone ID
$HOSTED_ZONE_ID = aws route53 list-hosted-zones --query "HostedZones[?Name=='internal.yourcompany.com.'].Id" --output text

# Create DNS records (provided by script)
aws route53 change-resource-record-sets --hosted-zone-id $HOSTED_ZONE_ID --change-batch '{...}'
```

---

## What Happens During the Script

1. **Kubernetes Service Update** (~1 min)
   - Updates NGINX Ingress Controller to request internal NLB
   - Old public NLB is deleted by AWS

2. **AWS Load Balancer Creation** (~3-5 min)
   - AWS provisions new internal Network Load Balancer
   - Assigns private IP from VPC subnet

3. **TLS Certificate Generation** (~30 sec)
   - Creates self-signed certificate for internal hostname
   - Imports to Kubernetes secret

4. **Kubernetes Ingress Update** (~30 sec)
   - Deletes old ingress with public hostname
   - Creates new ingress with internal hostname

5. **Application Configuration** (~30 sec)
   - Updates `MqttConfig.java` with new URL
   - Imports certificate to Java truststore

6. **Pod Restart** (~2-3 min)
   - Restarts all Schema Registry pods
   - Picks up new configuration

**Total time: ~30 minutes**

---

## Architecture After Completion

```
┌─────────────────────────────────────────────────────────────┐
│                    Your Corporate Network                   │
│                                                             │
│   ┌──────────────┐              ┌──────────────┐          │
│   │ Corporate LAN│              │     VDI      │          │
│   │  Workstation │              │   Sessions   │          │
│   └──────┬───────┘              └──────┬───────┘          │
│          │                              │                  │
│          └──────────────┬───────────────┘                  │
│                         │                                  │
└─────────────────────────┼──────────────────────────────────┘
                          │
                          │ VPN/Direct Connect
                          │
┌─────────────────────────┼──────────────────────────────────┐
│                         │   AWS VPC (Private)              │
│                         ▼                                  │
│              ┌─────────────────────┐                       │
│              │  Internal NLB       │                       │
│              │  10.0.x.x           │                       │
│              └──────────┬──────────┘                       │
│                         │                                  │
│           ┌─────────────┴─────────────┐                   │
│           │                           │                   │
│           ▼                           ▼                   │
│  ┌─────────────────┐       ┌─────────────────┐           │
│  │ Schema Registry │       │  Solace Cloud   │           │
│  │  (Internal)     │       │  (Private VPC)  │           │
│  └─────────────────┘       └─────────────────┘           │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**Key Benefits:**
- ✅ Both services use same network path
- ✅ No public internet exposure
- ✅ Accessible from corporate LAN and VDI
- ✅ Consistent with Solace Cloud architecture

---

## Support

If you encounter issues:

1. **Check Prerequisites:**
   - kubectl installed and configured?
   - AWS credentials valid?
   - OpenSSL available?
   - Running as Administrator?

2. **Review Logs:**
   - Script output saved to console
   - Configuration saved to `internal-nlb-config.txt`

3. **Manual Verification:**
   ```powershell
   # Check service
   kubectl get svc ingress-nginx-controller -n ingress-nginx
   
   # Check pods
   kubectl get pods -n solace
   
   # Check ingress
   kubectl get ingress -n solace
   ```

4. **Contact Support:**
   - Share `internal-nlb-config.txt`
   - Share script output
   - Share kubectl output from verification commands

---

## Quick Reference Card

```powershell
# Run Script (PowerShell - Recommended)
.\quick-switch-to-internal.ps1

# Run Script (Batch File)
quick-switch-to-internal.bat

# Run Script (Git Bash)
./quick-switch-to-internal.sh

# Test DNS
nslookup apis.schema-registry.internal.yourcompany.com

# Test API
curl -k -u sr-developer:admin https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info

# Test UI
Start-Process https://ui.schema-registry.internal.yourcompany.com

# Check Status
kubectl get svc ingress-nginx-controller -n ingress-nginx
kubectl get pods -n solace
kubectl get ingress -n solace

# View Configuration
Get-Content internal-nlb-config.txt
```

---

## Next Steps After Completion

1. ✅ Verify access from corporate LAN workstation
2. ✅ Verify access from VDI session
3. ✅ Update any documentation with new URLs
4. ✅ Share new URLs with team members
5. ✅ Test MQTT5 publisher and subscriber applications
6. ✅ Verify ELK dashboard is receiving events
7. ✅ Update any CI/CD pipelines with new URLs

**You're all set!** 🎉






