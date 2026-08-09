# SAMDUO Local TCP Control

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Tests](https://github.com/StekkerDeal/samduo-local/actions/workflows/tests.yml/badge.svg)](https://github.com/StekkerDeal/samduo-local/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/release/StekkerDeal/samduo-local.svg)](https://github.com/StekkerDeal/samduo-local/releases)
![Maintained](https://img.shields.io/badge/maintained-yes-brightgreen.svg)

Local, cloud-free Home Assistant integration for SAMDUO plug-in home batteries, using the
SAMDUO Open API (JSON over TCP, port 3335). No account, no cloud, no polling of foreign
servers - your battery data stays on your LAN.

> **Status: monitoring + power control (v0.2.x).** All telemetry as sensors, including
> Energy-Dashboard-ready charge/discharge counters, plus a signed power setpoint backed
> by the device's built-in watchdog, a backup output switch, and a release-control button.

## Supported devices

| Device | Status |
|---|---|
| SAMDUO Nex E6000 | ✅ Tested (review unit, fw 0.0.0.236) |
| SAMDUO Nex E6000H | Untested - same protocol per spec |
| SAMDUO Nex P2800 Pro | Untested - same protocol per spec |
| SAMDUO Nex BP2800 | Untested - same protocol per spec |

The SAMDUO P1 Meter advertises the same mDNS service but speaks a different command set;
it is deliberately not offered by discovery.

## Requirements

- Home Assistant 2024.11 or newer.
- The battery on the same LAN as Home Assistant (TCP port 3335 reachable).
- **The "HEMS Managed" toggle in the SAMDUO app must be switched OFF** - with it on, the
  device ignores external commands.

⚠️ **The device answers only one TCP client at a time.** A second connection is accepted
but never answered. If the sensors stop updating while the SAMDUO app is open, close the
app; the integration reconnects automatically.

## Installation

### HACS (recommended)

[![Add to Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=StekkerDeal&repository=samduo-local&category=integration)

Or manually: HACS → Integrations → ⋮ → Custom repositories → add
`https://github.com/StekkerDeal/samduo-local` as category *Integration*, then install
**SAMDUO Local TCP Control** and restart Home Assistant.

> Beta versions are published as GitHub pre-releases. Enable *Show beta versions* on the
> HACS entry to receive them.

### Manual

Copy `custom_components/samduo_battery` into your `config/custom_components/` directory
and restart Home Assistant.

## Setup

Batteries on the LAN are discovered automatically (mDNS) and appear under
**Settings → Devices & Services** - confirm the discovered device and pick a name.
Manual setup is available as fallback: add the integration and enter the battery's IP
address. The serial number is read from the device automatically.

## Entities

| Entity | Unit | Notes |
|---|---|---|
| Battery SOC | % | Dynamic battery icon |
| Battery Power | W | **Positive = discharging, negative = charging** (see below) |
| Battery Status | - | Charging / Discharging / Idle (±10 W deadband) |
| Backup Power | W | Off-grid (EPS) output |
| Energy Charged | kWh | Native device counter, `total_increasing` |
| Energy Discharged | kWh | Native device counter, `total_increasing` |
| Grid Voltage / Grid Frequency / Off-grid Voltage | V / Hz / V | Diagnostic |
| Battery Voltage | V | Diagnostic |
| Battery SOH | % | Diagnostic |
| Inverter Temperature 1 / 2 | °C | Diagnostic |
| Inverter Status | - | `Normal` or the raw undocumented state value |
| Error Code | - | Raw `inv_err1`; bit meanings undocumented by SAMDUO |
| Control Time Remaining | s | Watchdog countdown of an active power setpoint |
| **Power Setpoint** | W | number - the one control surface, see below |
| **Backup Output** | - | switch - off-grid (EPS) output on/off |
| **Release Control** | - | button - hand the battery back to its own logic |

### Sign convention

SAMDUO devices report and accept power as **positive = discharging, negative =
charging**. This integration passes that through unchanged, which matches EMHASS's
`p_batt_forecast` natively. Other battery integrations may use the opposite convention -
mind the sign when you combine them in automations or templates.

## Battery control

One control surface: the **Power Setpoint** number. Positive = discharge, negative =
charge, 0 = hold idle - the value passes to the device unmodified. There is
deliberately no separate direction selector or power slider: one entity, one truth.

**The watchdog does the safety work.** Every setpoint is written with a timeout
(default 90 s) and refreshed automatically (default every 30 s). If Home Assistant
crashes, loses the network, or is shut down, the battery resumes its own self-management
within the timeout - no stuck setpoints, by hardware design. Both intervals are
configurable in the integration options.

**Release Control** writes a short 0 W hold and stops the refreshing: the battery idles
within seconds and then returns to self-management. Until you press it (or restart HA),
an active setpoint - including 0 - is held indefinitely.

### Power limits

Setpoints are clamped to the per-direction limits in the integration options
(**Settings → Devices & Services → SAMDUO → Configure**). The defaults are **800 W in
both directions**: the grid-compliant output level. They can be raised to at most
2600 W, the E6000 inverter rating.

Measured behaviour on a Nex E6000 (fw 0.0.0.236): delivered power tracks the
commanded setpoint within about 0.5 % up to the charge/discharge limits configured in
the **SAMDUO app**, and the firmware silently clamps at those app limits above them -
a command's echo and the stored control config will still show the higher number
while the inverter delivers only the app limit. The protocol itself accepts values
up to ±5000 W without error, so the integration limits and the app limits are the
layers that actually decide what flows. Keep the integration limits at or below the
app limits so what you command is what you get.

**Raising the limits above 800 W is entirely at your own risk.** Check the grid-feed
regulations that apply at your location, the wiring and fusing of the circuit the
battery is on, and your model's inverter rating. Only the Nex E6000 has been
measured; other models are unverified.

### Driving it from EMHASS

EMHASS's `p_batt_forecast` uses the same sign convention, so the automation needs no
sign flip:

```yaml
automation:
  - alias: "EMHASS: apply battery setpoint"
    triggers:
      - trigger: state
        entity_id: sensor.p_batt_forecast
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.state not in ('unknown', 'unavailable') }}"
    actions:
      - action: number.set_value
        target:
          entity_id: number.samduo_nex_e6000_power_setpoint
        data:
          value: "{{ states('sensor.p_batt_forecast') | float(0) | round(0) }}"

  - alias: "EMHASS: stale plan -> release the battery"
    triggers:
      - trigger: state
        entity_id: sensor.p_batt_forecast
        to: ["unknown", "unavailable"]
        for: "00:30:00"
    actions:
      - action: button.press
        target:
          entity_id: button.samduo_nex_e6000_release_control
```

The second automation still matters: the device watchdog protects against Home
Assistant dying, not against an optimizer that stops producing fresh plans while HA
keeps refreshing the last setpoint.

## Energy Dashboard

The battery reports lifetime charge/discharge counters, so no Riemann-sum helpers are
needed. In **Settings → Dashboards → Energy → Battery storage**, set:

- *Energy going into the battery*: **Energy Charged**
- *Energy coming out of the battery*: **Energy Discharged**

## Security note

The SAMDUO local API has **no authentication**: any host on your LAN can read telemetry
from (and later: control) the battery. Keep the battery on a trusted network segment.

## Issues

Please use the issue templates and include the Home Assistant log lines (filter on
"samduo") at
[github.com/StekkerDeal/samduo-local/issues](https://github.com/StekkerDeal/samduo-local/issues).

## Credits

Maintained by [StekkerDeal](https://stekkerdeal.nl/).

## License

MIT, see [LICENSE](LICENSE)
