# Changelog

All notable changes to this project are documented in this file.

## [1.4.0] — 2026-07-25

### Added

- **`@psyb0t/stealthy-auto-browse` code plugin** (`.agents/plugins/stealthy-auto-browse/`) — a stdio↔HTTP MCP bridge (`mcp-remote`) to the container's `/mcp` endpoint, so an OpenClaw/MCP agent can drive the stealth browser as a tool. MIT-licensed. CI now publishes the plugin alongside the skill via the reusable `clawhub-publish.yml`.

### Changed

- Skill: minor accuracy fixes to `SKILL.md` / `references/setup.md`.

## [1.3.8] — 2026-07-24

### Changed

- **Trimmed the published ClawHub skill to clear the security review.** ClawHub's scanner rated the skill "suspicious", the decisive concern being the bundled `scripts/websearch.py` Google/Bing/Brave scraper — flagged as expanding the skill "beyond owned or authorized QA targets" past its defensive-testing purpose. Fix: excluded `scripts/` from the published skill via a new `.agents/skills/stealthy-auto-browse/.clawhubignore` (the script stays in the repo for local use, just isn't shipped as part of the ClawHub artifact), removed its section from `SKILL.md`, and made the auto-dialog-accept and URL-triggered-loader warnings prominent (both were flagged as under-emphasized user-control risks). The stealth-browser core was already accepted by the reviewer as expected for authorized/defensive testing, so no functionality changed.

## [1.3.7] — 2026-07-24

### Changed

- **Pinned the base image `python:3.12-slim-bookworm` by digest** (`@sha256:d50fb7…`). Tags are mutable; a digest is content-addressed, so the build can't silently shift under a re-tagged base — same supply-chain hygiene as the pinned `camoufox==0.4.11` / `playwright==1.53.0`. Re-resolve on a conscious base bump with `docker buildx imagetools inspect python:3.12-slim-bookworm --format '{{.Manifest.Digest}}'`.

## [1.3.6] — 2026-07-24

### Fixed

- **Docker build no longer breaks on camoufox drift.** `camoufox[geoip]` was unpinned, so a fresh build pulled whatever was newest — camoufox 0.5.x reshaped the browser's on-disk `distribution/` layout (no default `policies.json`), and `install_extensions.py` crashed mid-build with `FileNotFoundError: .../distribution/policies.json`. Two-part fix: (1) pinned `camoufox[geoip]==0.4.11` in the `Dockerfile` — the Firefox 135 build that the already-pinned `playwright==1.53.0` is matched to (bump both in lockstep); (2) `install_extensions.py` now creates `policies.json` (starting from `{"policies": {}}`) when the browser build didn't ship a default one, instead of assuming it exists.

## [1.3.5] — 2026-07-24

### Added

- **CI publishes the skill to ClawHub on tag pushes.** `pipeline.yml` gained a `publish-skills-to-clawhub` job that calls the reusable `clawhub-skills-publish-workflow.yml` to publish every skill under `.agents/skills/` (currently `stealthy-auto-browse`) to ClawHub. It runs on tag pushes only and `needs:` the Docker image workflow, so it fires only after the image build/push + GitHub Release succeed. Requires the `CLAWHUB_TOKEN` repo secret.
- **Screen recording is now documented in the skill.** `SKILL.md` gained a "Screen Recording" section (`start_recording` / `stop_recording` / `recording_status`, modes, cluster-mode `run_script` requirement) and `references/setup.md` documents the `/recordings` mount. The recording feature itself shipped earlier; this fills the documentation gap.

### Changed

- **Renamed the skill directory `.agents/.skills/` → `.agents/skills/`** (drops the leading dot on the `skills` dir). `.dockerignore` now excludes `.agents` (instead of the old `.skills`) so the skill tree stays out of the image.
- **`pipeline.yml`: the Grype image scan no longer fails the run** (`scan_fail_build: false`) — camoufox/Go carry known-unfixable upstream criticals, so findings are reported to the repo **Security → Code scanning** tab (via the reusable workflow's SARIF upload; the job grants `security-events: write`) instead of blocking. This is also what lets the ClawHub publish job depend on the build succeeding.

## [1.3.4] — 2026-07-04

### Fixed

- **Switching tabs no longer navigates the page.** The tab focus gesture in `Browser.focus_tab_window()` (`app/browser.py`) clicks the content at screen (5, 200) to transfer OS keyboard focus into the page. After the browser chrome offset, that point lands on the top-left of page content — exactly where site logos and nav links live — so on some pages the click activated a link and navigated the tab (e.g. a top-left `<a href="/">` logo reset a single-page app back to its home view). The previous mitigation (a small left "drag" — moved mouseup) did not help: Firefox dispatches the DOM `click` to the nearest common ancestor of mousedown/mouseup regardless of a few pixels of movement. Fix: before the focus click, the tab handlers in `app/main.py` inject a full-viewport transparent `position:fixed` overlay at maximum `z-index` (`2147483647`) so the click lands on that inert div instead of any link/button, then remove it immediately — keyboard focus still transfers, nothing on the page is activated. The gesture is now a plain `xdotool click 1` and the old drag + `getSelection().removeAllRanges()` selection-cleanup path is gone.
- **New-tab windows are resized to fill the screen so screen recording follows the active tab pixel-for-pixel.** Playwright's Firefox backend opens each tab as its own OS window; windows created after startup came up slightly smaller than the display (e.g. 1918×1055 on a 1920×1080 screen), so raising a new tab left a thin strip of the previous window visible at the bottom edge — which the fixed-region `ffmpeg` x11grab recorder captured. `focus_tab_window()` now moves the raised window to 0,0 and sizes it to `xdotool getdisplaygeometry` before focusing, matching the launch window's full-screen geometry.

### Added

- Regression test `test_switch_tab_no_link_activation` in `tests/test_tabs.sh` (and registered in `test.sh`): switches to a tab whose page is a full-viewport link with an `onclick` marker and asserts the focus click does not fire it (the overlay absorbs the click) while keyboard input still reaches the content (`send_key pagedown` scrolls the page).

## [1.3.3] — 2026-07-04

### Fixed

- **Screen recording now works at any `XVFB_RESOLUTION`, not just 1920×1080.** `entrypoint.sh` started Xvfb with a fixed `-screen 0 1920x1080` framebuffer and then used `xrandr` to switch to the requested resolution. But `xrandr` can only select modes that fit inside the initial framebuffer allocation — it cannot grow it — so at a larger/taller size (e.g. `1920x1920`) the reported mode changed while the real root framebuffer stayed 1920×1080. Viewport recording then computed its capture area from `XVFB_RESOLUTION` and `ffmpeg` x11grab tried to capture outside the actual screen, failing with `Capture area … outside the screen size 1920x1080` and producing no file. Fix: allocate the framebuffer at the requested size up front (`Xvfb :99 -screen 0 "${XVFB_RESOLUTION}x${XVFB_DEPTH}"`) and drop the xrandr resize step. Recording now succeeds at square/tall resolutions.

### Changed

- `XVFB_RESOLUTION` no longer has a 1920×1080 cap — the framebuffer is allocated to match, so any width/height works (larger = more memory, slower software rendering, but no hard limit). Docs updated accordingly.

## [1.3.2] — 2026-07-04

### Fixed

- **Pinned Playwright to 1.53.0 to stop driver crash-loops on many sites (reddit.com, anything throwing uncaught page errors).** `camoufox` 0.4.11 declares an unpinned `playwright` dependency, so a fresh image build pulled the newest Playwright (1.61.0). Playwright ≥ 1.60 is incompatible with Camoufox's custom Firefox 135 build: its driver throws on certain protocol events (an uncaught page error with no source location deref's `pageError.location.url`; WebSocket-open events hit similar asserts), which kills the driver's Node process — the Python side then sees "Connection closed while reading from the driver" and relaunches into a crash-loop. Reproduces reliably on reddit.com and any page with CSP/cross-origin script errors. Pinning `playwright==1.53.0` in the `Dockerfile` (a pre-1.60 release matching the Camoufox 0.4.11 / Firefox 135 protocol) fixes the entire class of crashes at the source. Refs: daijro/camoufox#617, microsoft/playwright#39767. Bump the pin in lockstep with any future `camoufox` upgrade.

## [1.3.1] — 2026-07-03

### Fixed

- **`switch_tab` / `new_tab` / `close_tab` now foreground the target tab and focus its content.** Playwright's Firefox backend opens each tab as a separate OS window and `page.bring_to_front()` is a no-op there, so previously these actions only moved an internal pointer for where subsequent commands landed — the display (screenshots, recordings, VNC) kept showing whatever tab was last on top, and OS-level keyboard input (`send_key` / `system_type`) never reached the switched tab. New `Browser.focus_tab_window()` (in `app/browser.py`) raises the correct window with `xdotool windowactivate` (matching page-creation order to X window-ID order) and transfers keyboard focus into the page content. Wired into all three tab actions in `app/main.py`; `get_active_page()` is now async and heals a dead browser before use.
- **Content focus uses a menu-free mouse gesture.** The focus transfer is a left-button press → 6px move → release rather than a click: the moved release fires no `click` event (so no link/button under the cursor is activated) and the left button renders no context menu (previously a right-click was used, which flashed the native menu on every tab switch — ugly in recordings). Any stray drag-selection is cleared via `getSelection().removeAllRanges()` in `app/main.py`.

### Added

- Regression tests `test_switch_tab_foreground` (asserts the desktop pixels follow the switched tab via screenshot sampling) and `test_switch_tab_keyboard` (asserts `send_key` reaches switched-tab content) in `tests/test_tabs.sh`.

## [1.3.0] — 2026-06-21

### Fixed

- **Auto-recovery from Camoufox crashes.** Reported by @shadowjig: after 3 executions of an n8n workflow hitting Facebook, every subsequent `goto` returned `Page.goto: Connection closed while reading from the driver` until the container was manually restarted. Root cause: `app/browser.py:_get_page` cached `self._page` forever — when Camoufox died mid-session (OOM was the usual culprit), the cached Page reference pointed at a dead Playwright Page object and the python app had no recovery path. Fix: `Browser.is_healthy()` round-trips to the driver (`context.cookies()`) on every page acquisition; if the probe fails, `Browser.ensure_healthy()` tears down + relaunches `launch_persistent_context` (persistent profile survives intact). Both `_get_page()` and the top-level `main.get_active_page()` now route through `ensure_healthy()`. Cold-restart cost: ~4–5s for the request that triggered recovery; subsequent requests run at full speed. New regression test (`tests/test_recovery.sh::test_recovery_camoufox_crash`) simulates the crash via `pkill -9 -f camoufox-bin` and asserts the next request succeeds.

### Added

- **Crash postmortem logging.** When the health probe trips and `ensure_healthy` decides to relaunch, the new `_log_browser_postmortem()` writes structured diagnostics at WARNING so the operator doesn't have to guess what killed Camoufox: `pgrep` inventory of remaining `camoufox-bin` processes, last 200 lines of `dmesg` grepped for OOM / camoufox / firefox kills, `/proc/meminfo` snapshot (MemTotal / MemAvailable / SwapFree), `/proc/loadavg`. For the typical Facebook + persistent-profile OOM case, the dmesg block surfaces `Killed process N (camoufox-bin) total-vm:…` so the answer is in the log rather than the operator's head.
- **Playwright lifecycle handlers** registered at launch — `context.on("close")` and `page.on("crash")` — so the death event lands in the log as soon as Playwright sees it, not when the next request fails.
- **Structured JSON logging overhaul.** `app/logger.py` rewritten:
  - ISO 8601 UTC timestamps with microsecond precision (`"time": "2026-06-21T22:41:13.499770Z"`)
  - Nested `source.{function, file, line}` instead of the previous flat `module:func:line` string
  - `trace_id` on every log line (auto-generated per HTTP request via ContextVar)
  - `request_id` on HTTP request lines, seeded from the incoming `X-Request-Id` header (shape-validated against `^[A-Za-z0-9._-]{1,64}$`) or generated as a fresh UUID4-hex if missing/invalid
  - Logger-level redactor masks `password|token|secret|api_key|authorization|cookie|set-cookie|auth_token|access_token|refresh_token|client_secret|private_key|session` keys to `[REDACTED]` at format time, so structured logging of headers / cookies / request bodies / DSN structs is safe by default
  - Default sink switched from `stdout` to `stderr` (Docker captures stderr to `docker logs`; stdout is reserved for the `--script` mode JSON result)
  - Optional rotating file via `LOG_FILE` env var (10MB × 5 backups)
  - `LOG_LEVEL` env var honored (DEBUG / INFO / WARN / ERROR)
  - Exception stack trace included as a structured `exception` field when `exc_info` is set
- **HTTP trace middleware** in `main.py` wraps the auth middleware so even 401s carry a `trace_id` in logs. Response headers gain `X-Request-Id` (echoed) and `X-Trace-Id` (always set) so callers (n8n, MCP clients, operator curl) can correlate their side with the server logs.

### Changed

- `main.get_active_page()` is now `async` and calls `await browser.ensure_healthy()` before reading the page list. All call sites in `main.py` updated to `await` it.
- A handful of f-string log calls converted to structured form (`extra={...}`) so the redactor can do its job: `dialog received`, `download`, `invalid request JSON`, `log_request`/`log_response`. The rest of the codebase still has f-string log calls — flagged as a follow-up pass; not a behavior change today.

## [1.2.0] — 2026-06-20

### Added

- **`show_cursor` parameter on `start_recording`** (default `true`). Controls ffmpeg's `-draw_mouse` flag. Set `false` to record without the OS-level mouse cursor sprite — useful for visual-regression captures or any case where cursor pixels add noise. Default `true` preserves v1.1.x behavior. The flag round-trips: `start_recording` echoes `show_cursor` in its descriptor so callers can confirm what ffmpeg actually got.
- `test_recording_hide_cursor` in `tests/test_recording.sh` — exercises `show_cursor: false` end-to-end (start → stop → MP4 validity) and asserts the descriptor echoes the requested value; also asserts the default-omitted case reports `True`.
- `test_recording_viewport_uses_calibration` registered in `tests/test_recording.sh`'s `ALL_TESTS+=` block (was already in `test.sh` but missing from the file's own list — extra-container runs would have skipped it).

### Changed

- `docs/api.md` `start_recording` row gained the `show_cursor` parameter.
- `app/mcp_server.py` `run_script` docstring's RECORDING section documents `show_cursor` so MCP clients see it in the tool schema.

## [1.1.1] — 2026-06-20

### Fixed

- **Doc sync gaps from v1.1.0.** `README.md` had no mention of screen recording — added TOC entry, ffmpeg row in the "What's Inside" table, and a "Screen Recording" section with quick-start + curl example. `docs/script-mode.md` gained an "Example: Record a Flow" showing `start_recording` / `stop_recording` as YAML steps with the `/recordings` mount. `docs/cluster-mode.md` got a paragraph in the script-only-mode section spelling out that `start_recording` and `stop_recording` must live in the same `run_script` call so both hit the same sticky-routed instance. `docs/api.md` viewport-mode wording corrected — it previously said "crops the ~81px chrome strip" (the v1.0.x hardcoded behavior) but now uses the calibrated `mozInnerScreenX/Y` offset.

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
