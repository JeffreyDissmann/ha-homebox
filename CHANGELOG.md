# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
