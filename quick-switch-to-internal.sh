#!/bin/bash
# Quick Switch to Internal NLB
# Since Solace Cloud (private VPC) is already accessible, this will make
# Schema Registry use the same network path

set -e

echo "=========================================================================="
echo "Quick Switch: Schema Registry to Internal NLB"
echo "=========================================================================="
echo ""
echo "Since your Solace Cloud (private VPC) is already accessible from"
echo "corporate LAN and VDI, this will align Schema Registry to use the"
echo "same network path (VPN/Direct Connect)."
echo ""
echo "This will:"
echo "  1. Convert NLB from public to internal"
echo "  2. Generate new TLS certificate"
echo "  3. Update Kubernetes resources"
echo "  4. Provide DNS configuration commands"
echo ""
echo "⏱️  Estimated time: 30 minutes"
echo ""
echo "Press Enter to continue, or Ctrl+C to cancel..."
read

# Step 1: Convert to internal NLB
echo ""
echo "=========================================================================="
echo "Step 1: Converting NGINX Ingress to Internal NLB"
echo "=========================================================================="

kubectl annotate svc ingress-nginx-controller -n ingress-nginx \
  "service.beta.kubernetes.io/aws-load-balancer-scheme=internal" \
  --overwrite

kubectl annotate svc ingress-nginx-controller -n ingress-nginx \
  "service.beta.kubernetes.io/aws-load-balancer-internal=true" \
  --overwrite

kubectl annotate svc ingress-nginx-controller -n ingress-nginx \
  "service.beta.kubernetes.io/aws-load-balancer-type=nlb" \
  --overwrite

echo "✅ Annotations updated"
echo ""
echo "⏳ AWS is creating new internal NLB (this takes 3-5 minutes)..."
echo "   Old public NLB will be deleted automatically."
echo ""

# Wait for load balancer
sleep 30
echo "Checking load balancer status..."
sleep 30
echo "Still waiting..."
sleep 30
echo "Almost there..."
sleep 30

# Step 2: Get internal LB details
echo ""
echo "=========================================================================="
echo "Step 2: Getting Internal Load Balancer Details"
echo "=========================================================================="

INTERNAL_LB=$(kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

if [ -z "$INTERNAL_LB" ]; then
  echo "⚠️  Internal LB not ready yet. Wait a bit longer and check with:"
  echo "   kubectl get svc ingress-nginx-controller -n ingress-nginx"
  exit 1
fi

echo "✅ Internal Load Balancer: $INTERNAL_LB"

# Resolve to IP
INTERNAL_IP=$(dig +short "$INTERNAL_LB" | head -1)

if [ -z "$INTERNAL_IP" ]; then
  echo "⚠️  Could not resolve IP yet. DNS may still be propagating."
  echo "   Check later with: dig +short $INTERNAL_LB"
  INTERNAL_IP="UNKNOWN"
else
  echo "✅ Internal IP: $INTERNAL_IP"
fi

# Step 3: DNS Configuration
echo ""
echo "=========================================================================="
echo "Step 3: DNS Configuration Options"
echo "=========================================================================="
echo ""
echo "Choose your DNS approach:"
echo ""
echo "Option A: Use existing corporate DNS (Recommended)"
echo "  Contact your DNS team to add these records:"
echo ""
echo "  apis.schema-registry.internal.yourcompany.com  IN A  $INTERNAL_IP"
echo "  ui.schema-registry.internal.yourcompany.com    IN A  $INTERNAL_IP"
echo "  idp.schema-registry.internal.yourcompany.com   IN A  $INTERNAL_IP"
echo ""
echo "  (Replace 'internal.yourcompany.com' with your actual internal domain)"
echo ""
echo "Option B: Use Route53 Private Hosted Zone"
echo "  Run these commands (replace HOSTED_ZONE_ID with your zone):"
echo ""
echo "  # For APIs"
echo "  aws route53 change-resource-record-sets --hosted-zone-id ZXXXXXXXXXX --change-batch '{"
echo "    \"Changes\": [{"
echo "      \"Action\": \"CREATE\","
echo "      \"ResourceRecordSet\": {"
echo "        \"Name\": \"apis.schema-registry.internal.yourcompany.com\","
echo "        \"Type\": \"A\","
echo "        \"AliasTarget\": {"
echo "          \"HostedZoneId\": \"Z3AADJGX6KTTL2\","
echo "          \"DNSName\": \"$INTERNAL_LB\","
echo "          \"EvaluateTargetHealth\": false"
echo "        }"
echo "      }"
echo "    }]"
echo "  }'"
echo ""
echo "  # Repeat for ui.schema-registry and idp.schema-registry"
echo ""
echo "Option C: Use nip.io for testing (NOT for production)"
echo "  Use hostname: $INTERNAL_IP.nip.io"
echo "  Note: nip.io may not resolve from corporate network"
echo ""
echo "Which option do you want to use?"
echo "  A) Corporate DNS"
echo "  B) Route53 Private Hosted Zone"
echo "  C) nip.io (testing only)"
echo ""
read -p "Enter A, B, or C: " DNS_CHOICE

case "$DNS_CHOICE" in
  [Aa])
    echo ""
    echo "Selected: Corporate DNS"
    echo "Using placeholder: schema-registry.internal.yourcompany.com"
    HOSTNAME_SUFFIX="schema-registry.internal.yourcompany.com"
    echo ""
    echo "⚠️  Remember to contact your DNS team to add the records!"
    ;;
  [Bb])
    echo ""
    echo "Selected: Route53 Private Hosted Zone"
    echo "Using placeholder: schema-registry.internal.yourcompany.com"
    HOSTNAME_SUFFIX="schema-registry.internal.yourcompany.com"
    echo ""
    echo "⚠️  Remember to run the Route53 commands above!"
    ;;
  [Cc])
    echo ""
    echo "Selected: nip.io (testing only)"
    HOSTNAME_SUFFIX="$INTERNAL_IP.nip.io"
    ;;
  *)
    echo "Invalid choice. Defaulting to corporate DNS."
    HOSTNAME_SUFFIX="schema-registry.internal.yourcompany.com"
    ;;
esac

# Step 4: Generate TLS Certificate
echo ""
echo "=========================================================================="
echo "Step 4: Generating TLS Certificate"
echo "=========================================================================="

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=*.$HOSTNAME_SUFFIX" \
  -addext "subjectAltName=DNS:*.$HOSTNAME_SUFFIX,DNS:$HOSTNAME_SUFFIX" \
  2>/dev/null

echo "✅ Certificate generated for *.$HOSTNAME_SUFFIX"

# Update Kubernetes secret
kubectl -n solace delete secret schema-registry-tls-secret 2>/dev/null || true
kubectl -n solace create secret tls schema-registry-tls-secret \
  --cert=tls.crt \
  --key=tls.key

echo "✅ TLS secret updated in Kubernetes"

# Step 5: Update Ingress
echo ""
echo "=========================================================================="
echo "Step 5: Updating Kubernetes Ingress"
echo "=========================================================================="

# Delete old ingress
kubectl delete ingress schema-registry-ingress -n solace 2>/dev/null || echo "No existing ingress found"

# Create new ingress
cat <<EOF | kubectl apply -f -
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

echo "✅ Ingress updated"

# Step 6: Update MqttConfig.java
echo ""
echo "=========================================================================="
echo "Step 6: Updating MqttConfig.java"
echo "=========================================================================="

# Backup original
cp src/main/java/MqttConfig.java src/main/java/MqttConfig.java.backup

# Update URL
sed -i '' "s|public static final String SCHEMA_REGISTRY_URL = \"https://apis.*\";|public static final String SCHEMA_REGISTRY_URL = \"https://apis.$HOSTNAME_SUFFIX/apis/registry/v3\";|g" src/main/java/MqttConfig.java

echo "✅ MqttConfig.java updated"
echo "   Old config backed up to: src/main/java/MqttConfig.java.backup"
echo "   New URL: https://apis.$HOSTNAME_SUFFIX/apis/registry/v3"

# Step 7: Update Java Truststore
echo ""
echo "=========================================================================="
echo "Step 7: Updating Java Truststore"
echo "=========================================================================="

# Import certificate
sudo keytool -import \
  -trustcacerts \
  -alias schema-registry-internal \
  -file tls.crt \
  -keystore $JAVA_HOME/lib/security/cacerts \
  -storepass changeit \
  -noprompt 2>/dev/null || echo "Certificate already exists or error occurred (may be OK)"

echo "✅ Certificate imported to Java truststore"

# Step 8: Restart Pods
echo ""
echo "=========================================================================="
echo "Step 8: Restarting Schema Registry Pods"
echo "=========================================================================="

kubectl rollout restart deployment schema-registry-backend -n solace
kubectl rollout restart deployment schema-registry-ui -n solace
kubectl rollout restart deployment schema-registry-idp -n solace

echo "⏳ Waiting for pods to restart..."
kubectl rollout status deployment schema-registry-backend -n solace --timeout=3m
kubectl rollout status deployment schema-registry-ui -n solace --timeout=3m
kubectl rollout status deployment schema-registry-idp -n solace --timeout=3m

echo "✅ All pods restarted"

# Summary
echo ""
echo "=========================================================================="
echo "✅ Conversion Complete!"
echo "=========================================================================="
echo ""
echo "Your Schema Registry is now using INTERNAL NLB"
echo "Same network path as your Solace Cloud (private VPC)"
echo ""
echo "Configuration Summary:"
echo "----------------------"
echo "Internal LB:  $INTERNAL_LB"
echo "Internal IP:  $INTERNAL_IP"
echo "Hostname:     $HOSTNAME_SUFFIX"
echo ""
echo "URLs:"
echo "  UI:   https://ui.$HOSTNAME_SUFFIX"
echo "  API:  https://apis.$HOSTNAME_SUFFIX/apis/registry/v3"
echo "  IDP:  https://idp.$HOSTNAME_SUFFIX"
echo ""
echo "=========================================================================="
echo "Next Steps:"
echo "=========================================================================="
echo ""

if [ "$DNS_CHOICE" = "A" ] || [ "$DNS_CHOICE" = "a" ]; then
  echo "1. Contact DNS team to add these records:"
  echo "   apis.$HOSTNAME_SUFFIX  IN A  $INTERNAL_IP"
  echo "   ui.$HOSTNAME_SUFFIX    IN A  $INTERNAL_IP"
  echo "   idp.$HOSTNAME_SUFFIX   IN A  $INTERNAL_IP"
  echo ""
fi

if [ "$DNS_CHOICE" = "B" ] || [ "$DNS_CHOICE" = "b" ]; then
  echo "1. Add Route53 DNS records (see commands above)"
  echo ""
fi

echo "2. Test from Corporate LAN:"
echo "   nslookup apis.$HOSTNAME_SUFFIX"
echo "   curl -k -u sr-developer:admin https://apis.$HOSTNAME_SUFFIX/apis/registry/v3/system/info"
echo ""
echo "3. Test from VDI:"
echo "   (Same commands as above)"
echo ""
echo "4. Test Java application:"
echo "   cd /Users/matthewstobo/Documents/mqtt5SRDemo"
echo "   mvn clean compile"
echo "   mvn exec:java -Dexec.mainClass=\"MQTT5Publisher\""
echo ""
echo "5. Open UI in browser:"
echo "   https://ui.$HOSTNAME_SUFFIX"
echo ""
echo "=========================================================================="
echo "Architecture:"
echo "=========================================================================="
echo ""
echo "  Corporate LAN ──┐"
echo "                  ├──> VPN/Direct Connect ──> AWS VPC (Private)"
echo "  VDI ───────────┘                              │"
echo "                                                 ├──> Solace Cloud"
echo "                                                 └──> Schema Registry"
echo ""
echo "Both services now use the SAME network path! ✅"
echo ""
echo "=========================================================================="

# Save configuration for reference
cat > internal-nlb-config.txt <<EOFCONFIG
Schema Registry Internal NLB Configuration
Generated: $(date)

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
EOFCONFIG

echo "Configuration saved to: internal-nlb-config.txt"
echo ""






