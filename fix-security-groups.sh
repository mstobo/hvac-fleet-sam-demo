#!/bin/bash
# Script to restrict NGINX Ingress Load Balancer to specific CIDR ranges
# This will update the NLB to only allow traffic from corporate network and VDI

set -e

echo "=================================================="
echo "Restricting Schema Registry Access"
echo "=================================================="

# Configuration
NAMESPACE="ingress-nginx"
SERVICE_NAME="ingress-nginx-controller"

# Get your current public IP (for testing)
MY_PUBLIC_IP=$(curl -s ifconfig.me)
echo "Your current public IP: $MY_PUBLIC_IP"

# Define your allowed CIDR ranges
# IMPORTANT: Update these with your actual corporate and VDI network CIDRs
CORPORATE_CIDR="10.0.0.0/8"          # Example: Your corporate LAN
VDI_CIDR="172.16.0.0/12"              # Example: Your VDI network
MY_IP_CIDR="$MY_PUBLIC_IP/32"         # Your current IP for testing

# Combine CIDRs (comma-separated, no spaces)
ALLOWED_CIDRS="$CORPORATE_CIDR,$VDI_CIDR,$MY_IP_CIDR"

echo ""
echo "Allowed CIDR ranges:"
echo "  - Corporate LAN: $CORPORATE_CIDR"
echo "  - VDI Network: $VDI_CIDR"
echo "  - Your IP (testing): $MY_IP_CIDR"
echo ""
echo "This will restrict the NLB to ONLY allow traffic from these ranges."
echo "Press Ctrl+C to cancel, or Enter to continue..."
read

# Update the service annotation
echo "Updating NGINX Ingress Controller service annotations..."
kubectl annotate svc $SERVICE_NAME -n $NAMESPACE \
  "service.beta.kubernetes.io/aws-load-balancer-source-ranges=$ALLOWED_CIDRS" \
  --overwrite

echo ""
echo "✅ Service annotation updated!"
echo ""
echo "⏳ Waiting for AWS to update the security groups (this takes 2-3 minutes)..."
echo ""

# Wait for the change to propagate
sleep 30

echo "Checking current service configuration..."
kubectl get svc $SERVICE_NAME -n $NAMESPACE -o jsonpath='{.metadata.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-source-ranges}'
echo ""
echo ""

echo "=================================================="
echo "Next Steps:"
echo "=================================================="
echo ""
echo "1. Wait 2-3 minutes for AWS to update the NLB security groups"
echo ""
echo "2. Verify in AWS Console:"
echo "   - Go to EC2 → Load Balancers"
echo "   - Find your NLB (search for 'schema-registry')"
echo "   - Check Security Groups → Inbound rules"
echo "   - Should now show only your specified CIDR ranges"
echo ""
echo "3. Test access:"
echo "   From Corporate LAN:"
echo "     curl -k https://apis.3.14.49.98.nip.io/apis/registry/v3/system/info"
echo ""
echo "   From VDI:"
echo "     Open browser to: https://ui.3.14.49.98.nip.io"
echo ""
echo "4. If corporate LAN still can't access, check:"
echo "   - Corporate firewall rules"
echo "   - Routing to AWS"
echo "   - NAT gateway configuration"
echo ""
echo "=================================================="






