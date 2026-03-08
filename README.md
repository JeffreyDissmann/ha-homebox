# HomeBox for Home Assistant (HACS)

Custom integration for connecting Home Assistant to HomeBox.

## Features

- Config flow (UI setup)
- Sensors:
  - Total items
  - Total locations
  - Total value
- Device entry with configuration URL back to HomeBox

## Installation (HACS)

1. In HACS, open `Integrations`.
2. Click menu -> `Custom repositories`.
3. Add your GitHub repo URL.
4. Category: `Integration`.
5. Install `HomeBox`.
6. Restart Home Assistant.
7. Add integration via `Settings` -> `Devices & Services`.

## Configuration

Provide:
- Host URL of HomeBox (common ports: `7745` default, `3100` common Docker mapping)
- Username
- Password

