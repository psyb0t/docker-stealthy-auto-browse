# Changelog

All notable changes to this project are documented in this file.

## [1.1.0] — 2026-06-20

### Added

- **Screen recording subsystem** (`app/recorder.py`). ffmpeg `x11grab` against Xvfb (DISPLAY=:99) writes to `/recordings/<slug>.mp4`. Mouse cursor included (`-draw_mouse 1`, XFixes). Three modes:
  - `window` (default) — full Camoufox window incl. chrome
  - `viewport` — crops chrome using the calibrated `window_offset` (mozInnerScreenX/Y), so this mode tracks browser layout instead of a hardcoded chrome height
  - `desktop` — entire Xvfb screen
- New API actions: `start_recording {mode, fps}`, `stop_recording {slug}`, `recording_status`. One active recording at a time per container. `slug` is provided at stop time so the caller names the file after the run, not before. Slug allowlist `[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}` (no path traversal). Filename collision auto-renames to `<slug>-2.mp4`, `<slug>-3.mp4`, etc.
- `/recordings` mount required and validated at `start_recording` time — fails fast if missing or not writable. `Dockerfile` creates the dir; `entrypoint.sh` chowns it alongside `/userdata` and `/loaders`.
- Crash safety: a SIGINT-then-wait shutdown finalizes the MP4 cleanly. Orphan tmp files (`/recordings/.tmp-*.mp4`) older than 1h are swept at app startup.
- 6 regression tests in `tests/test_recording.sh`: basic record + ftyp magic + size check, stop-without-start error, double-start error, bad-slug rejection (path traversal), viewport mode uses calibrated offset (verified via `capture_size`), slug-collision rename to `-2`.
- MCP tool docstrings updated: `run_script` now documents the RECORDING action group; `browser_action`'s "useful for" list mentions recording.
- New entry `.demo-recordings/` added to `.gitignore`.

### Fixed

- **`get_window_offset_js` silent swallow** (`app/main.py:249`). Previously `except Exception: return {"x": 0, "y": 0}` — failure was indistinguishable from a valid `(0, 0)` result on a fullscreen window, so a broken calibrate silently produced wrong coordinates for every subsequent `system_click`. Now logs a warning on exception AND validates the JS result shape (must be a dict with numeric x/y), logs a warning and falls back to `(0, 0)` if Firefox returns anything unexpected (e.g. a future Camoufox version that spoofs `mozInnerScreen*`). Per `rules/05-error-handling.md` "never silently swallow".

## [1.0.1] — 2026-06-10

### Fixed

- **`send_key` regression introduced in v0.22.5 / v1.0.0**: PageDown (and every other key sent via `send_key`) silently did nothing because the Openbox WM added in v0.22.5 changed how Firefox got initial X keyboard focus — a freshly mapped Camoufox window gave focus to the chrome (URL bar) instead of the content widget, so PyAutoGUI keystrokes never reached the page. Fix: at browser launch, after the xdotool window resize, issue a one-time `xdotool mousemove 5 200 && xdotool click 1` to transfer Firefox's internal focus to the content widget. The focus persists across `goto`, `new_tab`, and `switch_tab`, so `send_key` works for the lifetime of the container as before. Reported by @shadowjig.

### Added

- `test_send_key_pagedown` regression test in `tests/test_input.sh` — sends `pagedown` via `send_key` and verifies both that the document-level `keydown` listener captured `PageDown` and that `window.scrollY` advanced.

## [1.0.0] — 2026-04-20

### BREAKING

- **Cluster mode restricts API to `run_script` only.** When `NUM_REPLICAS > 1`, both the HTTP API and MCP server only allow `run_script` (plus `ping` and `sleep`). Individual actions like `goto`, `get_text`, `click` are rejected with an error directing users to `run_script`. This prevents stale-content bugs caused by sequential calls hitting different browser instances when MCP gateways or HTTP clients fail to maintain session stickiness.

### Changed

- **MCP server**: Only `run_script` tool exposed in cluster mode, with a comprehensive description documenting every available action and its parameters so LLMs know what steps to use.
- **HTTP API**: Actions not in `{run_script, ping, sleep}` return an error with guidance in cluster mode. `run_script` internally dispatches all actions with no restriction inside scripts.
- **docker-compose.cluster.yml**: Passes `NUM_REPLICAS` env var to browser containers.

### Added

- `test_mcp_cluster_mode`: 10 assertions covering MCP tool restriction, action docs, `run_script` execution, and HTTP API enforcement.
- MCP test for nonexistent tool error handling.
- Exact MCP tool count assertion (17 tools in single-instance mode).

## [0.22.5] — 2026-04-20

### Added

- **Openbox window manager** — lightweight WM that adds title bars and resize handles to popup windows (OAuth dialogs, etc.) that would otherwise be too small to interact with. Zero stealth impact.
- Parallel test runner for faster CI.

### Fixed

- Cluster test stability improvements.

## [0.22.4] — 2026-04-19

### Fixed

- Pin Debian Bookworm base image for reproducible builds.
- Re-enable BrowserScan test (last in suite).

## [0.22.3] — 2026-04-19

### Fixed

- Various bug fixes and stability improvements.

## [0.22.2] — 2026-04-19

### Fixed

- Various bug fixes and stability improvements.

## [0.22.1] — 2026-04-19

### Fixed

- Various bug fixes and stability improvements.

## [0.22.0] — 2026-04-18

### Added

- **PUID/PGID support** — run the container as a custom user via `PUID` and `PGID` environment variables.

## [0.21.1] — 2026-04-17

### Fixed

- Restrict `/__queue/status` endpoint to private networks only.

## [0.21.0] — 2026-04-17

### Changed

- Improved LLM-facing documentation and action descriptions.

## [0.20.0] — 2026-04-17

### Changed

- **Rename `MAX_CONCURRENT` to `NUM_REPLICAS`** for clarity.
- Inline HAProxy config directly in docker-compose instead of separate file.

## [0.19.0] — 2026-04-16

### Added

- **Centralized JSON logger** with source file, function name, and line number in every log entry.

## [0.18.1] — 2026-04-16

### Fixed

- MCP backend: remove `maxconn 1` from HAProxy to allow concurrent SSE + POST connections.

## [0.18.0] — 2026-04-16

### Changed

- Move skills directory to `.agents/`.
- Add MCP server information to skill docs.
- HAProxy MCP routing support.
- Cluster MCP integration test.

## [0.17.2] — 2026-04-15

### Fixed

- Documentation: add `AUTH_TOKEN`, `run_script`, request serialization details. Fix hardcoded counts.

## [0.17.1] — 2026-04-15

### Fixed

- Skill docs: `run_script`, `auth_token` query param, request serialization.

## [0.17.0] — 2026-04-15

### Added

- **`run_script` API** — execute multi-step scripts in a single request. Steps run atomically on one browser instance.
- **Request serialization** — concurrent requests are automatically queued in single-instance mode.
- **`AUTH_TOKEN` authentication** — Bearer token auth on all endpoints (except `/health`). Supports header and query param.

### Changed

- Test suite refactored for `run_script` and auth coverage.

## [0.16.0] — 2026-04-14

### Added

- **MCP server** — Model Context Protocol server at `/mcp` using Streamable HTTP transport. AI agents can drive the browser directly over MCP.
- **Memory limits** — container resource constraints.
- **Redis persistence** — Redis data survives container restarts.
- 500-request stress test.

## [0.15.0] — 2026-04-13

### Added

- **Cluster mode** — run multiple browser instances behind HAProxy with request queuing, sticky sessions, and Redis cookie sync. Configurable via `NUM_REPLICAS` (originally `MAX_CONCURRENT`).
- Documentation for cluster mode.

## [0.14.0] — 2026-04-12

### Added

- **Console log capture** — `enable_console_log`, `disable_console_log`, `get_console_log`, `clear_console_log`.
- **`getclear` actions** — atomic get-and-clear for both console and network logs.

## [0.13.0] — 2026-04-11

### Added

- **Configurable listen host/port** — `HTTP_LISTEN_HOST`, `HTTP_LISTEN_PORT`, `VNC_LISTEN_HOST`, `VNC_LISTEN_PORT` environment variables.

### Changed

- Restructure skill documentation.
- Remove `INSTRUCTIONS.md` (consolidated into skill docs).

## [0.12.0] — 2026-04-10

### Added

- **`referer` param on `goto`** — set a custom Referer header when navigating.

## [0.11.0] — 2026-04-09

### Added

- **Script execution mode** — pipe YAML scripts via stdin, get JSON results on stdout. No HTTP server. For CI, cron jobs, one-shot scraping.

### Changed

- Move skills to `.skills/` directory.
- Remove URL argument (use `goto` action instead).

## [0.10.0] — 2026-04-08

### Added

- **`refresh` action** with optional `wait_until` parameter.

### Removed

- `back`/`forward` actions — Camoufox persistent context doesn't support browser history (`page.goto()` doesn't create history entries).

### Fixed

- Documentation accuracy: dialog handling, XVFB_RESOLUTION limits, loader `last_result` format, `get_interactive_elements` fields, scroll action categorization, login flow example wording, `handle_dialog` tips.

## [0.9.1] — 2026-04-07

### Changed

- Page loaders now live-reload when YAML files change (no container restart needed).

## [0.9.0] — 2026-04-06

### Added

- **Tabs**: `list_tabs`, `new_tab`, `switch_tab`, `close_tab`.
- **Dialogs**: `handle_dialog`, `get_last_dialog`.
- **Cookies**: `get_cookies`, `set_cookie`, `delete_cookies`.
- **Storage**: `get_storage`, `set_storage`, `clear_storage` (local + session).
- **Downloads**: `get_last_download`.
- **Uploads**: `upload_file` (Playwright `set_input_files`).
- **Network logging**: `enable_network_log`, `disable_network_log`, `get_network_log`, `clear_network_log`.
- **Wait conditions**: `wait_for_element`, `wait_for_text`, `wait_for_url`, `wait_for_network_idle`.
- **Proxy support**: `PROXY_URL` environment variable.
- **XPath selectors**: `xpath=` prefix on all element actions.
- Modular test suite (50 tests across 13 files).

## [0.8.0] — 2026-04-05

### Added

- Screenshot resize query params: `?width=`, `?height=`, `?whLargest=`.

## [0.7.1] — 2026-04-04

### Fixed

- Calibration reliability improvements.
- Better test coverage.

## [0.7.0] — 2026-04-04

### Changed

- Remove runtime resolution setter (use `XVFB_RESOLUTION` env var instead).

## [0.6.0] — 2026-04-03

### Fixed

- Fingerprint injection now uses Camoufox C++ level spoofing instead of JS injection.

## [0.5.0] — 2026-04-02

### Added

- **Dynamic resolution control** with mobile viewport support.

## [0.4.0] — 2026-04-01

### Added

- Page loaders (Greasemonkey-style URL-triggered action sequences).

## [0.3.0] — 2026-03-31

### Added

- **`send_key` action** — send keyboard shortcuts and special keys via PyAutoGUI.

## [0.2.1] — 2026-03-30

### Fixed

- Various bug fixes.

## [0.2.0] — 2026-03-29

### Changed

- Full stealth overhaul — passes all major bot detectors.

## [0.0.2] — 2026-03-28

### Fixed

- Early bug fixes and improvements.

## [0.0.1] — 2026-03-27

### Added

- Initial release. Camoufox + Xvfb + PyAutoGUI + HTTP API in Docker.
