# Data Center MQTT Topic and Schema Versioning

Technical reference for data center HVAC event streams (temperature, humidity, pressure) using BACnet/Modbus/MQTT with event-driven architecture.

---

## Goals

- Keep high-volume telemetry deterministic and low-cost
- Keep AI in the query/reasoning path only
- Enable safe schema evolution without breaking consumers
- Support parallel migration from old to new contracts

---

## Canonical Topic Taxonomy

Use versioned namespaces for all major streams:

- `dc/v1/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}`
- `dc/v1/event/{site}/{severity}/{eventType}`
- `dc/v1/sketch/{site}/{room}/{incidentId}`
- `dc/v1/cmd/{site}/{system}/{action}` (optional control namespace)

Examples:

- `dc/v1/raw/dc1/hall-a/row-a3/rack-12/crac-07/supply_temp_c`
- `dc/v1/event/dc1/critical/CoolingDriftDetected`
- `dc/v1/sketch/dc1/hall-a/INC-DC1-2026-04-25-0142`

---

## Versioning Policy

Use both topic versioning and payload schema versioning:

1. **Topic version (`/v1/`)** controls routing compatibility.
2. **Payload schema (`schema`)** controls parser/contract validation.

Recommended payload metadata:

```json
{
  "schema": "dc.event.v1",
  "schemaRevision": "1.0.0"
}
```

### When to bump versions

- **Non-breaking**: add optional fields -> keep topic `v1`, update `schemaRevision` (for example `1.1.0`)
- **Breaking**: remove/rename fields or change semantics -> publish new topic namespace `v2` and schema family `dc.*.v2`

---

## Event Types (v1)

- `TelemetryAccepted`
- `CoolingDriftDetected`
- `HumidityRiskDetected`
- `PressureContainmentRiskDetected`
- `MultiSignalHotspotDetected`
- `IncidentOpened`
- `IncidentUpdated`
- `IncidentClosed`
- `OperatorQueryRequested`

---

## Sample Contracts

### Raw telemetry (`dc.raw.v1`)

Topic:
`dc/v1/raw/dc1/hall-a/row-a3/rack-12/crac-07/supply_temp_c`

```json
{
  "schema": "dc.raw.v1",
  "schemaRevision": "1.0.0",
  "ts": "2026-04-25T16:07:10Z",
  "value": 29.4,
  "unit": "C",
  "quality": "GOOD",
  "sourceProtocol": "BACnet",
  "site": "dc1",
  "room": "hall-a",
  "row": "row-a3",
  "rack": "rack-12",
  "asset": "crac-07",
  "metric": "supply_temp_c"
}
```

### Routed event (`dc.event.v1`)

Topic:
`dc/v1/event/dc1/critical/CoolingDriftDetected`

```json
{
  "schema": "dc.event.v1",
  "schemaRevision": "1.0.0",
  "eventId": "evt-9f7a2",
  "ts": "2026-04-25T16:07:12Z",
  "eventType": "CoolingDriftDetected",
  "severity": "critical",
  "stateTransition": {
    "from": "WARNING",
    "to": "CRITICAL"
  },
  "incidentId": "INC-DC1-2026-04-25-0142"
}
```

### Sketch summary (`dc.sketch.v1`)

Topic:
`dc/v1/sketch/dc1/hall-a/INC-DC1-2026-04-25-0142`

```json
{
  "schema": "dc.sketch.v1",
  "schemaRevision": "1.0.0",
  "incidentId": "INC-DC1-2026-04-25-0142",
  "site": "dc1",
  "room": "hall-a",
  "summary": "Row A3 shows coordinated cooling stress with elevated temperature, rising humidity, and pressure drift.",
  "deterministicFindings": [
    "CoolingDriftDetected at CRITICAL severity",
    "State transition WARNING->CRITICAL",
    "Cross-signal correlation score 0.89"
  ]
}
```

---

## Broker-Level Processing Expectations

Before publishing `dc/v1/event/...`, deterministic services should apply:

- deadband filtering
- rate-of-change and sustained drift checks
- state transition detection (`NORMAL -> WARNING -> CRITICAL`)
- dedupe/cooldown for repeated incident emissions
- multi-signal correlation at room/row/rack scope

---

## Migration Pattern (v1 -> v2)

For breaking changes:

1. Publish both `dc/v1/...` and `dc/v2/...` in parallel.
2. Move consumers one group at a time.
3. Freeze `v1` updates except critical fixes.
4. Decommission `v1` after agreed cutover window.

This avoids flag-day migrations and keeps operations stable.
