# Network Access Issue - VDI Works, Corporate LAN Doesn't

## Current Situation

**Problem**: Schema Registry is accessible from VDI but NOT from corporate LAN

**Current Configuration**:
- Public AWS Network Load Balancer (NLB)
- IP: `3.14.49.98`
- Hostname: `*.3.14.49.98.nip.io`
- Security Group: **0.0.0.0/0** (allows all internet traffic)

## Why This Happens

```
┌─────────────────────────────────────────────────────────────┐
│                     Internet (Public)                       │
└────────────────┬──────────────────────┬─────────────────────┘
                 │                      │
                 │                      │
         ✅ VDI (Works)         ❌ Corporate LAN (Blocked)
                 │                      │
                 │                      X
                 │                Corporate Firewall
                 │                (Blocks AWS IP)
                 │
                 ▼
        ┌─────────────────┐
        │   AWS Public    │
        │   NLB           │
        │   3.14.49.98    │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ Schema Registry │
        └─────────────────┘
```

### Why VDI Works
- VDI infrastructure likely has **direct internet access** via a different gateway
- VDI may be hosted in cloud (AWS/Azure) with unrestricted AWS access
- VDI network treats AWS as external internet (allowed)

### Why Corporate LAN Fails
- **Corporate firewall blocks outbound traffic** to unknown AWS IPs
- **Web proxy** may intercept/block HTTPS to `*.nip.io` domains
- **Routing**: Corporate network may not have route to AWS public IPs
- **DNS**: Corporate DNS may not resolve `nip.io` domains
- **Security policy**: Only whitelisted external destinations allowed

## Solutions

You have 3 options depending on your production requirements:

---

## Solution 1: Restrict Public NLB (Quick Fix, Keep Current Setup)

**Best for**: Development/demo, both VDI and LAN need internet-based access

### Pros
- ✅ Quick (5 minutes)
- ✅ No infrastructure changes
- ✅ VDI continues to work
- ✅ Reduces security risk (no longer 0.0.0.0/0)

### Cons
- ❌ Corporate LAN may STILL be blocked by firewall
- ❌ Not ideal for production (public IP)
- ❌ Requires network team to whitelist AWS IP

### Steps

```bash
# 1. Run the fix script (updates CIDR ranges)
./fix-security-groups.sh

# 2. Ask network team to whitelist this IP in corporate firewall:
#    Source: Corporate LAN (10.0.0.0/8 or your range)
#    Destination: 3.14.49.98/32
#    Port: 443
#    Protocol: TCP

# 3. Test from corporate LAN
curl -k https://apis.3.14.49.98.nip.io/apis/registry/v3/system/info
```

**AWS Console Check**:
1. Go to **EC2 → Load Balancers**
2. Find NLB (search for `afab8407cb8504ba68ec85b3c812c204`)
3. **Security** tab → Check inbound rules
4. Should show specific CIDRs (not 0.0.0.0/0)

---

## Solution 2: Internal NLB + VPN (Recommended for Production)

**Best for**: Production, strict security requirements

### Pros
- ✅ Best security (no public internet exposure)
- ✅ Works for both LAN and VDI (via VPN)
- ✅ Enterprise-grade architecture
- ✅ Fine-grained access control

### Cons
- ❌ Requires VPN setup (if not already in place)
- ❌ More complex (2-3 hours)
- ❌ Requires DNS changes (Route53 private zone)

### Architecture

```
Corporate LAN ─────┐
                   │
VDI ───────────────┤
                   │
                   ▼
         ┌─────────────────┐
         │  AWS VPN        │
         │  (Site-to-Site  │
         │   or Client VPN)│
         └────────┬────────┘
                  │
                  ▼
         ┌─────────────────┐
         │  AWS VPC        │
         │  (Internal)     │
         │                 │
         │  ┌───────────┐  │
         │  │ Internal  │  │
         │  │ NLB       │  │
         │  │ 10.0.1.x  │  │
         │  └─────┬─────┘  │
         │        │        │
         │        ▼        │
         │  ┌───────────┐  │
         │  │  Schema   │  │
         │  │ Registry  │  │
         │  └───────────┘  │
         └─────────────────┘
```

### Steps

```bash
# 1. Convert to internal NLB
./switch-to-internal-nlb.sh

# 2. Set up AWS VPN (if not already configured)
# See PRODUCTION_ACCESS_GUIDE.md for detailed VPN setup

# 3. Configure Route53 Private Hosted Zone
aws route53 create-hosted-zone \
  --name internal.yourcompany.com \
  --vpc VPCRegion=us-east-2,VPCId=vpc-xxxxxxxxx \
  --caller-reference $(date +%s) \
  --hosted-zone-config PrivateZone=true

# 4. Update DNS records (see PRODUCTION_ACCESS_GUIDE.md)

# 5. Update MqttConfig.java to use internal hostname
SCHEMA_REGISTRY_URL = "https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3"

# 6. Test from VPN-connected client
curl -k https://apis.schema-registry.internal.yourcompany.com/apis/registry/v3/system/info
```

---

## Solution 3: Diagnose Corporate Network (Root Cause)

**Best for**: Understanding the exact network issue before deciding

### Steps

```bash
# 1. Run diagnostics from Corporate LAN workstation
./diagnose-network-access.sh > lan-diagnostics.txt

# 2. Run diagnostics from VDI
./diagnose-network-access.sh > vdi-diagnostics.txt

# 3. Compare results
diff lan-diagnostics.txt vdi-diagnostics.txt

# 4. Share with network team
```

### Common Findings

| Symptom | Root Cause | Solution |
|---------|------------|----------|
| DNS fails on LAN | Corporate DNS doesn't support `nip.io` | Use internal DNS (Route53) |
| Port 443 timeout | Firewall blocks AWS IPs | Whitelist `3.14.49.98/32` |
| Different public IPs | VDI uses cloud gateway, LAN uses corporate proxy | Use internal NLB + VPN |
| SSL cert error | Corporate proxy intercepts SSL | Install proxy CA cert |
| Traceroute stops at gateway | No route to AWS | Need VPN/Direct Connect |

---

## Recommended Approach

### For Development/Demo (Right Now)
1. ✅ Run `./diagnose-network-access.sh` from both LAN and VDI
2. ✅ Share results with network team
3. ✅ Ask network team: "Can we whitelist `3.14.49.98/32` for HTTPS?"
4. ✅ If yes → Run `./fix-security-groups.sh`
5. ❌ If no → Need to switch to Solution 2 (Internal NLB)

### For Production (Long Term)
1. ✅ Use **Solution 2**: Internal NLB + VPN
2. ✅ Follow `PRODUCTION_ACCESS_GUIDE.md`
3. ✅ Integrate with corporate LDAP/SSO
4. ✅ Use corporate PKI certificates
5. ✅ Set up Route53 private hosted zone with proper DNS

---

## Decision Matrix

| Requirement | Solution 1 | Solution 2 | Solution 3 |
|-------------|-----------|-----------|-----------|
| Quick fix (< 1 hour) | ✅ Yes | ❌ No | ✅ Yes (diagnosis only) |
| Production-ready | ⚠️ Acceptable | ✅ Best | N/A |
| Works with restrictive firewall | ⚠️ Maybe | ✅ Yes | N/A |
| No VPN required | ✅ Yes | ❌ No | ✅ Yes |
| Minimal security risk | ⚠️ Medium | ✅ Low | N/A |
| Supports VDI users | ✅ Yes | ✅ Yes | N/A |
| Supports LAN users | ⚠️ If firewall allows | ✅ Yes | N/A |
| Enterprise-grade | ❌ No | ✅ Yes | N/A |

---

## Files Created

- `fix-security-groups.sh` - Restrict NLB to specific CIDRs (Solution 1)
- `switch-to-internal-nlb.sh` - Convert to internal NLB (Solution 2)
- `diagnose-network-access.sh` - Network diagnostics (Solution 3)
- `PRODUCTION_ACCESS_GUIDE.md` - Complete production setup guide

---

## Next Steps

1. **Run diagnostics** to understand the exact issue:
   ```bash
   ./diagnose-network-access.sh
   ```

2. **Contact network team** with these questions:
   - Is outbound HTTPS to AWS IP `3.14.49.98` blocked?
   - Can we whitelist this IP for port 443?
   - Do we have AWS VPN or Direct Connect configured?
   - What's the approved architecture for AWS service access?

3. **Choose solution** based on network team feedback:
   - If they can whitelist → Solution 1
   - If they cannot whitelist → Solution 2 (VPN required)

4. **Implement** chosen solution and test from both LAN and VDI

---

## Testing Checklist

After implementing a solution:

- [ ] Test from Corporate LAN workstation
  ```bash
  curl -k https://apis.3.14.49.98.nip.io/apis/registry/v3/system/info
  ```

- [ ] Test from VDI
  ```bash
  curl -k https://apis.3.14.49.98.nip.io/apis/registry/v3/system/info
  ```

- [ ] Test UI access from browser (LAN)
  - Open: `https://ui.3.14.49.98.nip.io`

- [ ] Test UI access from browser (VDI)
  - Open: `https://ui.3.14.49.98.nip.io`

- [ ] Test Java application from LAN
  ```bash
  cd /Users/matthewstobo/Documents/mqtt5SRDemo
  ./run-publisher.sh
  ```

- [ ] Verify security group rules in AWS Console
  - EC2 → Load Balancers → Security Groups

- [ ] Check Schema Registry logs
  ```bash
  kubectl logs -n solace -l app=schema-registry-backend --tail=50
  ```

---

## Support

For questions or issues:
1. Review `PRODUCTION_ACCESS_GUIDE.md` for detailed architecture
2. Run `diagnose-network-access.sh` and share output
3. Check AWS Console: EC2 → Load Balancers → Security Groups
4. Check corporate network documentation for VPN/firewall policies






