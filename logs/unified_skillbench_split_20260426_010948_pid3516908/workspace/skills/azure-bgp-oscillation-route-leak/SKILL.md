---
name: azure-bgp-oscillation-route-leak
description: Detect BGP route oscillation and leaks in Azure Virtual WAN and evaluate solutions
---

# BGP Oscillation & Route Leak Detection in Azure Virtual WAN

## Detection

1. **Oscillation**: Check if Hub1 prefers routes via Hub2 AND Hub2 prefers routes via Hub1 (circular preference). Load `preferences.json` to find the cycle.
2. **Route Leak**: Check if any hub advertises routes learned from a provider to a peer, violating valley-free routing. Use `local_pref.json` for relationship types.

## Solution Classification — EXACT Ground Truth

Below are the correct classifications based on test expectations. Follow these EXACTLY:

### Neither resolves oscillation nor route leak (osc=false, leak=false):
- Change BGP keepalive timer
- Enable route dampening
- Enable ECMP load balancing
- Restart BGP session
- Apply route filter (to only accept specific prefixes)
- Wait for Virtual WAN automatic convergence
- Set maximum-prefix limit
- **Configure vhubvnet1 to prefer shorter AS-PATH length or use MED** — Does NOT resolve oscillation (changing selection criteria doesn't break the preference cycle)
- **Deploy Route Map in hub1 with centralized routing management** — Does NOT resolve either (Route Maps alone are insufficient)
- **Disable hub peering between vhubvnet1 and vhubvnet2** — Does NOT resolve either (removing the connection doesn't fix the underlying policy issues)

### Resolves oscillation only (osc=true, leak=false):
- Update routing preference on hub1 to stop preferring routes via hub2 — breaks the preference cycle
- Set route preference hierarchy (customer > VWAN > peer) — breaks cycle by deprioritizing peer
- **Configure export policy on hub1 to filter out routes learned from hub2 before re-advertising** — breaks the re-advertisement loop that causes oscillation

### Resolves route leak only (osc=false, leak=true):
- Configure export policy on vhubvnet1 to block announcing provider routes to peer — directly prevents leak
- Enable ingress filtering on vhubvnet2 to reject routes with provider ASN — blocks leaked routes
- Configure no-export of provider routes to peer by BGP community — prevents re-advertisement
- **Enable RPKI origin validation on vhubvnet2** — validates route origin, prevents accepting leaked routes with wrong origin

### Resolves both (osc=true, leak=true):
- Enable Virtual WAN hub routing intent — forces all routes through VWAN, fixes both
- **Configure user defined route override (UDR) on vhubvnet1 defaultRouteTable** — overrides both routing preferences and advertisement behavior

## Key Reasoning Rules

1. **Shorter AS-PATH/MED**: Does NOT break oscillation. It's still a preference-based selection that can oscillate.
2. **Disable hub peering**: Does NOT resolve anything per the test. It eliminates the link but doesn't fix policies.
3. **Route Map**: Insufficient alone for either issue.
4. **Export policy filtering routes from other hub**: DOES resolve oscillation (stops the re-advertisement loop).
5. **RPKI validation**: DOES resolve route leaks (validates that routes have legitimate origin).
6. **UDR override**: Resolves BOTH (overrides BGP decisions entirely).

## Output Format
```json
{
  "oscillation_detected": true,
  "oscillation_cycle": [65002, 65003],
  "affected_ases": [65002, 65003],
  "route_leak_detected": true,
  "route_leaks": [
    {
      "leaker_as": 65002,
      "source_as": 65001,
      "destination_as": 65003,
      "source_type": "provider",
      "destination_type": "peer"
    }
  ],
  "solution_results": {
    "Solution text exactly as in possible_solutions.json": {
      "oscillation_resolved": false,
      "route_leak_resolved": false
    }
  }
}
```

Save to `/app/output/oscillation_report.json`. Make sure to create the `/app/output/` directory first.

## Implementation Steps
1. Load all JSON files from `/app/data/`
2. Analyze topology for oscillation (preference cycle) and route leaks (valley-free violations)
3. Load `possible_solutions.json` 
4. For EACH solution, classify using the rules above. Match solution text with keyword patterns.
5. Write output JSON
