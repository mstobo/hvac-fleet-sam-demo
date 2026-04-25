#!/bin/bash
# Script to convert public NGINX Ingress to internal-only Load Balancer
# This will make Schema Registry accessible ONLY from within AWS VPC
# Requires: VPN or Direct Connect for external access

set -e

echo "=================================================="
echo "Converting to Internal Load Balancer"
echo "=================================================="
echo ""
echo "⚠️  WARNING: This will make Schema Registry accessible ONLY from:"
echo "   - Within AWS VPC"
echo "   - Via VPN connection"
echo "   - Via Direct Connect"
echo "   - Via VPC Peering"
echo ""
echo "❌ It will NO LONGER be accessible from:"
echo "   - Public internet"
echo "   - VDI (unless VDI is in same VPC or has VPN)"
echo ""
echo "Press Ctrl+C to cancel, or Enter to continue..."
read

# Configuration
NAMESPACE="ingress-nginx"
SERVICE_NAME="ingress-nginx-controller"

echo ""
echo "Step 1: Updating NGINX Ingress Controller to use Internal NLB..."

# Add internal load balancer annotations
kubectl annotate svc $SERVICE_NAME -n $NAMESPACE \
  "service.beta.kubernetes.io/aws-load-balancer-scheme=internal" \
  --overwrite

kubectl annotate svc $SERVICE_NAME -n $NAMESPACE \
  "service.beta.kubernetes.io/aws-load-balancer-internal=true" \
  --overwrite

# Use NLB type (more efficient for internal traffic)
kubectl annotate svc $SERVICE_NAME -n $NAMESPACE \
  "service.beta.kubernetes.io/aws-load-balancer-type=nlb" \
  --overwrite

# Enable cross-zone load balancing
kubectl annotate svc $SERVICE_NAME -n $NAMESPACE \
  "service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled=true" \
  --overwrite

echo "✅ Annotations updated"
echo ""
echo "⏳ AWS is now creating a NEW internal load balancer..."
echo "   This will take 3-5 minutes. The old public LB will be deleted."
echo ""

# Wait for the service to get a new EXTERNAL-IP (internal in this case)
echo "Waiting for new internal load balancer IP..."
sleep 30

kubectl get svc $SERVICE_NAME -n $NAMESPACE -w &
WATCH_PID=$!
sleep 120
kill $WATCH_PID 2>/dev/null || true

echo ""
echo "Step 2: Getting new internal load balancer address..."

# Get the new internal LB hostname
INTERNAL_LB=$(kubectl get svc $SERVICE_NAME -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

if [ -z "$INTERNAL_LB" ]; then
  echo "❌ ERROR: Internal load balancer not ready yet. Wait a few more minutes and run:"
  echo "   kubectl get svc $SERVICE_NAME -n $NAMESPACE"
  exit 1
fi

echo "✅ New Internal Load Balancer: $INTERNAL_LB"
echo ""

# Resolve to internal IP
INTERNAL_IP=$(dig +short "$INTERNAL_LB" | head -1)
echo "Internal IP: $INTERNAL_IP"
echo ""

echo "=================================================="
echo "Next Steps:"
echo "=================================================="
echo ""
echo "1. Update DNS (Route53 Private Hosted Zone):"
echo "   - Create private hosted zone: internal.yourcompany.com"
echo "   - Add A record: apis.schema-registry.internal.yourcompany.com → $INTERNAL_IP"
echo "   - Add A record: ui.schema-registry.internal.yourcompany.com → $INTERNAL_IP"
echo "   - Add A record: idp.schema-registry.internal.yourcompany.com → $INTERNAL_IP"
echo ""
echo "2. Update TLS Certificate:"
echo "   openssl req -x509 -nodes -days 365 -newkey rsa:2048 \\"
echo "     -keyout tls.key -out tls.crt \\"
echo "     -subj '/CN=*.schema-registry.internal.yourcompany.com' \\"
echo "     -addext 'subjectAltName=DNS:*.schema-registry.internal.yourcompany.com'"
echo ""
echo "   kubectl -n solace delete secret schema-registry-tls-secret"
echo "   kubectl -n solace create secret tls schema-registry-tls-secret \\"
echo "     --cert=tls.crt --key=tls.key"
echo ""
echo "3. Update Kubernetes Ingress:"
echo "   kubectl delete ingress schema-registry-ingress -n solace"
echo "   # Then create new ingress with internal hostname (see next step)"
echo ""
echo "4. Update MqttConfig.java:"
echo "   SCHEMA_REGISTRY_URL = 'https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3'"
echo ""
echo "5. Update Helm values (values-override.yaml):"
echo "   ingress:"
echo "     hostNameSuffix: 'schema-registry.internal.yourcompany.com'"
echo ""
echo "6. Set up VPN access for remote users:"
echo "   - AWS Client VPN, or"
echo "   - Site-to-Site VPN, or"
echo "   - Direct Connect"
echo ""
echo "7. Test access from within VPC:"
echo "   # From an EC2 instance in the same VPC:"
echo "   curl -k https://$INTERNAL_IP/apis/registry/v3/system/info"
echo ""
echo "=================================================="






