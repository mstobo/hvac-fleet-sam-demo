#!/bin/bash
# Script to diagnose why corporate LAN cannot access the Schema Registry
# Run this from BOTH a corporate LAN machine AND a VDI session

echo "=================================================="
echo "Schema Registry Network Diagnostics"
echo "=================================================="
echo ""

# Configuration
SCHEMA_REGISTRY_HOSTNAME="apis.3.14.49.98.nip.io"
SCHEMA_REGISTRY_IP="3.14.49.98"
SCHEMA_REGISTRY_PORT="443"

echo "Target: https://$SCHEMA_REGISTRY_HOSTNAME"
echo "IP: $SCHEMA_REGISTRY_IP"
echo ""

# Determine OS
if [[ "$OSTYPE" == "darwin"* ]]; then
  OS="Mac"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
  OS="Linux"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
  OS="Windows"
else
  OS="Unknown"
fi

echo "Detected OS: $OS"
echo ""

# Test 1: DNS Resolution
echo "Test 1: DNS Resolution"
echo "----------------------"
if command -v nslookup &> /dev/null; then
  nslookup $SCHEMA_REGISTRY_HOSTNAME
  echo ""
elif command -v dig &> /dev/null; then
  dig $SCHEMA_REGISTRY_HOSTNAME
  echo ""
fi

# Test 2: Your Public IP (to understand network path)
echo "Test 2: Your Public IP"
echo "----------------------"
if command -v curl &> /dev/null; then
  MY_PUBLIC_IP=$(curl -s --max-time 5 ifconfig.me)
  if [ -n "$MY_PUBLIC_IP" ]; then
    echo "Your public IP: $MY_PUBLIC_IP"
  else
    echo "Could not determine public IP (might be blocked)"
  fi
else
  echo "curl not available"
fi
echo ""

# Test 3: Ping (may be blocked)
echo "Test 3: ICMP Ping"
echo "----------------------"
if command -v ping &> /dev/null; then
  if [[ "$OS" == "Windows" ]]; then
    ping -n 4 $SCHEMA_REGISTRY_IP 2>&1 | head -n 10
  else
    ping -c 4 $SCHEMA_REGISTRY_IP 2>&1 | head -n 10
  fi
else
  echo "ping not available"
fi
echo ""

# Test 4: TCP Port 443 Connectivity
echo "Test 4: TCP Port 443 Connectivity"
echo "----------------------------------"
if [[ "$OS" == "Windows" ]]; then
  # Windows PowerShell command
  echo "Run this in PowerShell:"
  echo "Test-NetConnection -ComputerName $SCHEMA_REGISTRY_IP -Port $SCHEMA_REGISTRY_PORT"
elif command -v nc &> /dev/null; then
  echo "Testing with netcat..."
  timeout 5 nc -zv $SCHEMA_REGISTRY_IP $SCHEMA_REGISTRY_PORT 2>&1
elif command -v telnet &> /dev/null; then
  echo "Testing with telnet..."
  timeout 5 telnet $SCHEMA_REGISTRY_IP $SCHEMA_REGISTRY_PORT 2>&1
else
  echo "No tools available for port testing"
fi
echo ""

# Test 5: HTTP/HTTPS Request
echo "Test 5: HTTPS Request"
echo "---------------------"
if command -v curl &> /dev/null; then
  echo "Attempting HTTPS connection..."
  curl -k -v --max-time 10 "https://$SCHEMA_REGISTRY_HOSTNAME/apis/registry/v3/system/info" 2>&1 | head -n 30
else
  echo "curl not available"
fi
echo ""

# Test 6: Traceroute
echo "Test 6: Network Path (Traceroute)"
echo "----------------------------------"
if command -v traceroute &> /dev/null; then
  echo "Tracing route to $SCHEMA_REGISTRY_IP..."
  timeout 30 traceroute -m 15 $SCHEMA_REGISTRY_IP 2>&1 | head -n 20
elif command -v tracert &> /dev/null; then
  echo "Tracing route to $SCHEMA_REGISTRY_IP..."
  tracert -h 15 $SCHEMA_REGISTRY_IP 2>&1 | head -n 20
else
  echo "traceroute not available"
fi
echo ""

# Test 7: Route Table
echo "Test 7: Default Gateway"
echo "-----------------------"
if [[ "$OS" == "Mac" || "$OS" == "Linux" ]]; then
  echo "Default route:"
  netstat -rn | grep -E "^default|^0.0.0.0" | head -n 5
elif [[ "$OS" == "Windows" ]]; then
  echo "Run this in PowerShell:"
  echo "route print | findstr 0.0.0.0"
fi
echo ""

echo "=================================================="
echo "Diagnostics Summary"
echo "=================================================="
echo ""
echo "Common Issues & Solutions:"
echo ""
echo "1. DNS Fails to Resolve:"
echo "   → Corporate DNS may not support nip.io"
echo "   → Try adding to /etc/hosts:"
echo "     $SCHEMA_REGISTRY_IP $SCHEMA_REGISTRY_HOSTNAME"
echo ""
echo "2. Port 443 Connection Refused/Timeout:"
echo "   → Corporate firewall blocking AWS IP ranges"
echo "   → Check with network team to whitelist:"
echo "     $SCHEMA_REGISTRY_IP/32"
echo ""
echo "3. Different Public IP between LAN and VDI:"
echo "   → VDI uses different internet gateway (works)"
echo "   → Corporate LAN uses restrictive proxy/firewall (blocked)"
echo "   → Solution: Use Internal NLB + VPN"
echo ""
echo "4. SSL/TLS Errors:"
echo "   → Corporate proxy intercepting SSL"
echo "   → May need to add corporate proxy CA to truststore"
echo ""
echo "5. Traceroute stops at corporate gateway:"
echo "   → Traffic not leaving corporate network"
echo "   → No route to AWS (need VPN/Direct Connect)"
echo ""
echo "=================================================="
echo ""
echo "Next Steps:"
echo ""
echo "Run this script from BOTH locations:"
echo "  - Corporate LAN workstation"
echo "  - VDI session"
echo ""
echo "Compare the results to identify the difference."
echo ""
echo "Share results with network team to determine:"
echo "  - Is outbound HTTPS to $SCHEMA_REGISTRY_IP blocked?"
echo "  - Is there a proxy server that needs configuration?"
echo "  - Should we switch to internal NLB + VPN?"
echo ""






