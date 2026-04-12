# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Home Assistant custom integration that connects to [HomeBox](https://homebox.software/) — an open-source inventory management system. It is installed via HACS and has no build system or automated test suite.

## Development Workflow

### Devcontainer (recommended)

Open the repo in VS Code and select **Reopen in Container**. The container:
- Installs `homeassistant` via `scripts/setup` on first create
- Forwards port 8123

Start Home Assistant via the **Run Home Assistant** VS Code task (or run `scripts/develop` directly). This sets `PYTHONPATH` so HA discovers `custom_components/homebox/` from the repo root without any symlinking.

Home Assistant runs with debug logging for `custom_components.homebox` (see `config/configuration.yaml`). The `config/` directory persists your HA setup (entities, config entries) between runs; only `configuration.yaml` is committed — runtime data is gitignored.

After changing Python files, restart HA (`Ctrl+C` then re-run `scripts/develop`). The UI reloads automatically if you only change strings/translations.

### Without devcontainer

Install dependencies and run the same script locally:

```bash
pip install -r requirements.txt
scripts/develop
```

## Architecture

### Entry Points & Lifecycle

- **`__init__.py`** — Integration setup/teardown. Registers device registry event listeners for link cleanup and area-to-location sync when devices are removed or renamed.
- **`config_flow.py`** — Large (1300+ lines) multi-step UI flow covering: initial setup, device linking, bulk area import wizard, and options management.
- **`coordinator.py`** — `HomeBoxDataUpdateCoordinator` polls HomeBox daily. Each refresh fetches statistics, scans tagged items, calculates battery forecasts, and syncs maintenance counts.

### Core Subsystems

| File | Role |
|---|---|
| `api.py` | HomeBox REST API client — authentication, CRUD for items/locations/tags/maintenance, image upload |
| `sensor.py` | Three entity classes: statistics sensors, linked item ID sensor, battery depletion date sensor |
| `linking.py` | Bidirectional link map between HA devices and HomeBox items; tag scanning; area→location sync |
| `battery_forecast.py` | Queries HA's recorder for 1d/7d/30d battery snapshots and computes a depletion date forecast |
| `maintenance.py` | Syncs battery depletion forecasts into HomeBox maintenance entries; integrates with Battery Notes |
| `item_fields.py` | Reads/writes custom fields on HomeBox items (backlink field, HA device ID) |
| `const.py` | All shared constants: `DOMAIN`, config keys, tag names, backlink field name, poll interval |
| `models.py` | Dataclasses: `HomeBoxGroupStatistics`, `HomeBoxItemSummary` |

### Data Flow

1. Config entry is created with host/username/password via the config flow.
2. `HomeBoxDataUpdateCoordinator` polls daily and updates sensors.
3. Sensors read coordinator data; no direct API calls from sensors.
4. Device registry listeners in `__init__.py` fire synchronously on device events (removal, area change) to keep HomeBox locations and backlinks in sync.
5. Unlinked HomeBox items tagged "HomeAssistant" trigger HA integration discovery flows so the user can link them to HA devices.

### Key Design Decisions

- **Coordinator-based polling** at a 1-day interval (inventory data doesn't need real-time updates).
- **Tag-driven discovery**: The `"HomeAssistant"` tag on a HomeBox item signals it should be linked; discovery flows handle the pairing UI.
- **Backlink field**: A custom field on the HomeBox item stores the HA device ID, enabling reverse lookup.
- **Battery Notes integration**: Optional enrichment — if the Battery Notes integration is present, its replacement cost data is used when creating HomeBox maintenance entries.
