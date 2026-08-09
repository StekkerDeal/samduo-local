# SAMDUO Local TCP Control

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Tests](https://github.com/StekkerDeal/samduo-local/actions/workflows/tests.yml/badge.svg)](https://github.com/StekkerDeal/samduo-local/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/release/StekkerDeal/samduo-local.svg)](https://github.com/StekkerDeal/samduo-local/releases)
![Maintained](https://img.shields.io/badge/maintained-yes-brightgreen.svg)

Local, cloud-free Home Assistant integration for SAMDUO plug-in home batteries, using the
SAMDUO Open API (JSON over TCP, port 3335). No account, no cloud, no polling of foreign
servers — your battery data stays on your LAN.

> **Status: monitoring-only (v0.1.x).** All telemetry is available as sensors, including
> Energy-Dashboard-ready charge/discharge counters. Power control (setpoint with the
> device's built-in watchdog) is in development.

## Supported devices

| Device | Status |
|---|---|
| SAMDUO Nex E6000 | ✅ Tested (review unit, fw 0.0.0.236) |
| SAMDUO Nex E6000H | Untested — same protocol per spec |
| SAMDUO Nex P2800 Pro | Untested — same protocol per spec |
| SAMDUO Nex BP2800 | Untested — same protocol per spec |

The SAMDUO P1 Meter advertises the same mDNS service but speaks a different command set;
it is deliberately not offered by discovery.

## Requirements

- Home Assistant 2024.11 or newer.
- The battery on the same LAN as Home Assistant (TCP port 3335 reachable).
- **The "HEMS Managed" toggle in the SAMDUO app must be switched OFF** — with it on, the
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
**Settings → Devices & Services** — confirm the discovered device and pick a name.
Manual setup is available as fallback: add the integration and enter the battery's IP
address. The serial number is read from the device automatically.

## Entities

| Entity | Unit | Notes |
|---|---|---|
| Battery SOC | % | Dynamic battery icon |
| Battery Power | W | **Positive = discharging, negative = charging** (see below) |
| Battery Status | — | Charging / Discharging / Idle (±10 W deadband) |
| Backup Power | W | Off-grid (EPS) output |
| Energy Charged | kWh | Native device counter, `total_increasing` |
| Energy Discharged | kWh | Native device counter, `total_increasing` |
| Grid Voltage / Grid Frequency / Off-grid Voltage | V / Hz / V | Diagnostic |
| Battery Voltage | V | Diagnostic |
| Battery SOH | % | Diagnostic |
| Inverter Temperature 1 / 2 | °C | Diagnostic |
| Inverter Status | — | `Normal` or the raw undocumented state value |
| Error Code | — | Raw `inv_err1`; bit meanings undocumented by SAMDUO |
| Control Time Remaining | s | Watchdog countdown of an active power setpoint |

### Sign convention

SAMDUO devices report and accept power as **positive = discharging, negative =
charging**. This integration passes that through unchanged, which matches EMHASS's
`p_batt_forecast` natively. Other battery integrations may use the opposite convention —
mind the sign when you combine them in automations or templates.

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
