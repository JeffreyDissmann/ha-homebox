# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-03-28

### Added

- Added integration subentry support so the Integrations page "Add" button can create and link a new HomeBox item from a Home Assistant device.
- Added `missing_config_entry` abort translations for English and German.

### Changed

- Updated subentry action labels to "Create new HomeBox item" (EN) / "Neues HomeBox Element erstellen" (DE).
- Improved HomeBox API error detail handling to include server-provided messages where available.

### Fixed

- Fixed create/link flow to always apply the HomeAssistant tag after item creation, including create fallback paths.
- Fixed `InvalidAuth` initialization to call the base `HomeAssistantError` constructor.
- Fixed options flow edge cases by guarding missing `runtime_data` instead of crashing.
- Refactored duplicated create/link logic into shared helpers and restored stage-based error logging.

## [0.4.0] - 2026-03-22

### Added

- Added linked battery forecast support with a per-device diagnostic date sensor for estimated battery depletion.
- Added HomeBox maintenance synchronization: linked battery forecasts now create and update maintenance entries directly in HomeBox.
- Added Battery Notes enrichment for maintenance data (battery type, quantity, and last replacement date).
- Added translated diagnostic sensor names for English and German.

### Changed

- Polling interval is now daily.
- Battery detection now supports more real-world entity registry variants (`device_class` and `original_device_class`).
- HomeBox maintenance descriptions were simplified to battery-focused lines only.
- Maintenance cost is now auto-derived from battery quantity (default `1` when unknown).

## [0.3.1] - 2026-03-22

### Changed

- Streamlined linking options by removing legacy manual-link wizard steps and related dead code.
- Adjusted coordinator polling interval to 1 hour.
- Improved discovered-link card title handling to consistently display the HomeBox item name.

### Added

- Added German (`de`) translations for the HomeBox integration UI.

## [0.3.0] - 2026-03-22

### Added

- Added Integrations-page discovery prompts for tagged HomeBox items that still need linking, replacing the previous notification-only workflow.
- Added a guided discovery-linking flow with top suggested Home Assistant devices and manual device selection fallback.

### Changed

- Discovery card title now shows the HomeBox item name directly.
- Discovery linking step now includes richer context (item metadata and improved suggested device labels with manufacturer/model).
- Matching suggestions now consistently show the top 3 candidates.

## [0.2.0] - 2026-03-15

### Added

- New options workflow to create and link a HomeBox item directly from an unlinked Home Assistant device.
- Prefilled item details from Home Assistant (name, manufacturer, model, serial number, and area-based location).
- Optional image URL import during item creation with upload support to HomeBox.

### Changed

- Linking and unlinking options now use clearer labels and improved selection behavior.
- Unlink flow now selects from linked Home Assistant devices only.
- User-facing config/options wording was polished for consistency and clarity.

### Fixed

- Added rollback cleanup when create-and-link fails after item creation.
- Image upload failures no longer abort item creation/linking and now surface as warnings.

## [0.1.3] - 2026-03-15

### Fixed

- Fixed HomeBox linking notification URL to open the integration page path (`/config/integrations/integration/homebox`) instead of an incorrect target.

## [0.1.2] - 2026-03-15

### Changed

- Added a clickable link in the HomeBox linking notification to open the integration page directly.
- Link wizard now hides Home Assistant devices that are already linked to a HomeBox item.

## [0.1.1] - 2026-03-15

### Changed

- Config flow now clearly labels HomeBox login as email-based.
- Authentication error text now references email address and password.

## [0.1.0] - 2026-03-15

### Added

- Initial public release of the HomeBox custom integration for Home Assistant (HACS).
