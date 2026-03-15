# HomeBox for Home Assistant (HACS)

A custom Home Assistant integration that connects to [HomeBox](https://github.com/sysadminsmedia/homebox), exposes inventory statistics, and supports linking Home Assistant devices to HomeBox items.

[![Open your Home Assistant instance and add this repository inside HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JeffreyDissmann&repository=ha-homebox&category=integration)
[![Open your Home Assistant instance and start setting up HomeBox](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=homebox)

## Features

- UI config flow (`host`, `username`, `password`, integration name, optional area)
- Polling-based HomeBox statistics
- Sensors:
  - `homebox_total_items`
  - `homebox_total_locations`
  - `homebox_total_value`
- Device linking workflow (Home Assistant device <-> HomeBox item)
- HomeBox backlink field support on linked items
- Home Assistant area -> HomeBox location synchronization
- Manual resync and stale backlink cleanup from integration options

## Requirements

- Home Assistant version `2024.1.0` or newer
- A reachable HomeBox instance
- HomeBox user credentials

Common HomeBox endpoints:

- default port: `7745`
- common Docker host mapping: `3100`

Examples:

- `http://192.168.1.20:7745`
- `http://homebox.local:3100`

## Installation (HACS)

Quick action:

- Click: `Add repository to HACS` button above.

1. Open HACS -> `Integrations`.
2. Open the menu -> `Custom repositories`.
3. Add `https://github.com/JeffreyDissmann/ha-homebox`.
4. Select category `Integration`.
5. Install `HomeBox`.
6. Restart Home Assistant.
7. Go to `Settings` -> `Devices & Services` -> `Add Integration` -> `HomeBox`.

## Configuration

During setup, provide:

- HomeBox host URL
- HomeBox username
- HomeBox password
- Integration display name (default: `HomeBox`)
- Optional Home Assistant area

## Entity Model

Primary HomeBox device:

- `homebox_total_items`
- `homebox_total_locations`
- `homebox_total_value`

Linked Home Assistant devices:

- one diagnostic sensor per linked HA device exposing `homebox_id`

## Linking Workflow

From HomeBox integration options:

1. `Link HA device`: choose an unlinked tagged HomeBox item, then choose a HA device.
2. `Unlink HA device`: remove an existing mapping.
3. `Refresh HomeBox items`: re-scan tagged items and clean stale backlinks.

Tag-driven behavior:

- The integration manages/uses the HomeBox tag `HomeAssistant`.
- Tagged items that are not linked are surfaced for linking.

## Troubleshooting

- `Invalid authentication`:
  - verify username/password
  - verify URL and port
  - verify reverse proxy/auth setup if used
- `Cannot connect`:
  - verify network reachability from Home Assistant to HomeBox
  - verify protocol (`http` vs `https`)
- Link removed but still visible:
  - run `Refresh HomeBox items` in integration options
  - reload the integration

## Issues

Please report bugs and feature requests here:

- https://github.com/JeffreyDissmann/ha-homebox/issues

## Project Status

Active custom integration focused on HomeBox inventory stats + HA device linking.
