# AGENTS.md

Guidance for coding agents working in this repository. Read it before changing anything.

## What this project is

Stealth browser automation in Docker. One container runs Camoufox (a patched Firefox) on Xvfb with Openbox, exposes a JSON HTTP API and an MCP server on port 8080, and a noVNC viewer on port 5900. The two things that set it apart:

- **No Chrome DevTools Protocol.** Firefox has no CDP, so there is no CDP to leak.
- **Real OS-level input.** `system_click`, `system_type`, `scroll` and friends send X11 events through PyAutoGUI/XTest, timed from published human motor data. Pages receive trusted events with no injection trail and human timing.

Users are mostly AI agents driving the browser through MCP or the HTTP API (the skill in `.agents/skills/` is widely installed), plus people running scripts and clusters. Treat every public behavior as something an agent in the wild depends on.

## Ground rules

- **Stay in scope.** Do the change you were asked for. Propose anything extra; do not do it.
- **Humanized input only touches the OS-level actions.** `system_click`, `mouse_click`, `mouse_move`, `system_type`, `send_key`, `scroll`, `scroll_to_bottom_humanized`. Playwright `click`, `type`, `fill` and the JS `scroll_to_bottom` stay exactly as they are.
- **Every humanized default is overridable per request.** New timing knobs get a validated request parameter, an MCP parameter, a row with range and default in the base's Humanized input page, and their name in the matching option list in `docs/api.md` "Humanized input".
- **Never vendor third-party code or data.** Detectors and research datasets are fetched at pinned commits in test-only images, or not at all. Models fitted to human data ship as small parameter sets with the source cited, never as copied tables.
- **No real data in prompts, fixtures or docs.** Use placeholders. Test credentials must be obviously fake (`test.user@example.com`, `not-a-real-password!`).
- **Releases are cut by the maintainer.** Do not commit, push, tag, or edit a released CHANGELOG section unless told to in that turn. Released versions are immutable; changes go in a new section.
- **Docker is the only dependency.** All tooling runs in the dev container through `make`. Do not install tools on the host.
- **Clean up what you start.** Remove only containers and images you created, and say so.

## Build, run, develop

```bash
make build                     # psyb0t/stealthy-auto-browse:latest
docker run -d -p 8080:8080 -p 5900:5900 psyb0t/stealthy-auto-browse

make lint                      # ruff + shellcheck
make lint-fix / make format    # Python fixes and formatting
make test-unit                 # in-process Python unit tests
make test                      # complete Docker-backed suite
make test TEST_ARGS="test_calibrate test_eval_world"
make test-real                 # opt-in live checks against public sites
make sec                       # writes sec.sarif
make help
```

Only `make test` and `make test-real` get the Docker socket, because the suites start their own browser, fixture and detector containers.

Before calling a change done: `make lint`, `make test-unit`, and the integration tests that cover it. Anything touching the browser launch, fingerprint, input, or Dockerfile needs the full `make test`. Humanized-input timing tests are heavy and run alone on purpose; do not parallelize them.

## Architecture

The image is built `FROM psyb0t/auto-linux-desk` ([repository](https://github.com/psyb0t/docker-auto-linux-desk)). The base owns the entrypoint (PUID/PGID, Xvfb, Openbox, x11vnc, noVNC), the HTTP API, MCP, script mode, the request lock, auth, events, handoff, recording, the clipboard and the humanized input planners and executor. This repository adds the browser as an auto-linux-desk plugin: `AUTO_LINUX_DESK_PLUGINS=stealthy_plugin` makes the base import `app/stealthy_plugin.py` and call `register(registry)` before the server starts.

```
Dockerfile               # FROM the pinned base; renames desk to browser (UID 1000, /home/browser); Camoufox, Playwright, extensions
docker/entrypoint.d/     # Root hooks the base entrypoint sources before dropping privileges (Camoufox GeoIP chown)
app/
  stealthy_plugin.py     # register(): browser actions, page input wrappers, surface, MCP tools, state, /screenshot/browser, startup/shutdown, cluster guard
  browser_actions.py     # Camoufox session, page event handlers and every browser action (_dispatch_action_inner)
  browser_input.py       # BrowserSurface (viewport offset, size, scroll, focused field) and the typo guard
  mcp_server.py          # Browser MCP tools and instructions; replaces the base tools
  browser.py             # Camoufox launch, identity persistence and upgrade, window fitting, relaunch/recovery, color scheme, virtual media
  challenge_detector.py  # Read-only CAPTCHA/challenge detection
  captcha_solver.py      # Optional CAPTCHA solver config, pinned extensions, Anti-Captcha API
  dom_targets.py         # Element targets, frame chains, element references
  loaders.py             # URL-triggered page loaders
  redis_sync.py          # Cluster cookie sync
  browser_events.py      # Old names for the base event bus
  main.py                # Shim: `python main.py` starts the base server with the plugin
  get_elements.js        # Interactive element scan
scripts/install_extensions.py  # Pinned, checksum-verified uBlock Origin, LocalCDN, ClearURLs, Consent-O-Matic
tests/contract_snapshot.py     # Records the public contract of an image, to diff two builds
.agents/                 # Published skill and Claude/Codex/OpenClaw plugin manifests
docker-compose.cluster.yml
```

Humanized input timing, the planners and their unit tests live in the base. A change there is a base release, then a base pin bump here.

### The base pin

`ARG BASE_IMAGE` in the Dockerfile pins an auto-linux-desk release by tag and digest. Bump both together and re-resolve the digest with `docker buildx imagetools inspect psyb0t/auto-linux-desk:vX.Y.Z --format '{{.Manifest.Digest}}'`. To build against a local base, run `docker build -t psyb0t/auto-linux-desk:local .` in the base checkout, then `make build BASE_IMAGE=psyb0t/auto-linux-desk:local`. Before releasing a bump, take `tests/contract_snapshot.py` snapshots of the old and new images and diff them: every difference must be intended and in the CHANGELOG.

`run_command` and `launch_app` from the base ship disabled (`DESK_EXEC_ENABLED=false`). Keep it that way unless the maintainer decides otherwise.

## The browser stack and how to upgrade it

The `camoufox` Python package, Playwright, and the Camoufox browser build are **pinned together** in the `Dockerfile`. Each camoufox release pairs with one browser build and caps Playwright, because Playwright drives the browser over Camoufox's Juggler protocol and any Playwright minor can change it. Current set: camoufox 0.5.8, browser 156.0.1-beta.36, Playwright 1.62.0.

To bump:

1. Read the new camoufox release: its paired browser (`browser-pin.json` in the wheel), its Playwright ceiling, and `PLAYWRIGHT_BROWSER_FLOORS` in `__version__.py`.
2. Update all three pins (`CAMOUFOX_VERSION`, `CAMOUFOX_BUILD` and the pip pins) and both architecture sha256 values (take them from the GitHub release asset digests). The build fails if the downloaded archive's digest does not match. The build installs the archive itself instead of running `camoufox fetch`, because that lists releases through the unauthenticated GitHub API and CI runners hit its rate limit. If camoufox changes its install layout (`browsers/official/<version>-<build>-<sha8>`, `version.json`, `config.json`, `.0.5_FLAG`), update that step.
3. The fpgen fingerprint model installs as root in its own build step so site-packages stays read-only for the `browser` user. Keep it that way.
4. Check every camoufox internal `app/browser.py` imports (`launch_options`, `get_env_vars`, `firefox_gpus`, `installed_verstr`, `Screen`, `DefaultAddons`). Camoufox renames and removes internals between minors.
5. Check `scripts/install_extensions.py` against the new `distribution/` layout.
6. Run the full `make test` and `make test-real`. Do not trust a passing build.
7. Build arm64 too (`docker buildx build --platform linux/arm64 .` with QEMU registered). Some dependencies have no arm64 wheel and compile there; an amd64 build will not show it. Start the arm64 image and check the browser launches.

## Things that will bite you

**Isolated world.** Camoufox runs Playwright's `evaluate`, selectors and init scripts in an isolated JavaScript world. It sees the DOM but not variables the page defines, and page scripts cannot see or hook it. Consequences:

- `MAIN_WORLD_PREFIX` (`mw:`) in `browser.py` runs a script in the page's own world. It needs `main_world_eval=True` at launch (set), returns by value only, and takes no element handles.
- The `eval` action defaults to the page world (`world: "page"`) so users can read site globals; `world: "isolated"` opts out. The virtual media init script, its change event, and fullscreen also use the page world.
- Anything else that only reads the DOM, layout or native window properties stays isolated. Prefer that.
- To check whether the page defined a global without running page code, use `Object.prototype.hasOwnProperty.call(window.wrappedJSObject || window, name)` from the isolated world (see `challenge_detector.py`).

**No synthetic user activation.** Camoufox's `evaluate` grants no user gesture, which is correct and stealthier. Gesture-gated APIs (`requestFullscreen`, popups after an await, autoplay) fail from scripts. Fullscreen is allowed through the single pref `full-screen-api.allow-trusted-requests-only=false`. Do not add a general activation hack; real input (OS-level or Playwright clicks) grants activation like a person.

**Identity persistence.** The identity lives in `/userdata/stealthy-auto-browse-props.json`.

- A new identity is generated by camoufox's `launch_options` (fpgen), then the screen is set to the Xvfb size, the window geometry keys are removed, the Linux font families are added, and the result is saved.
- `launch_options` fills **every missing key** from a fresh random fingerprint. Anything you want gone (window geometry, stock-default values) must be removed again **after** it runs, or random values come back.
- Saved identities from older images are upgraded in place: the UA's Firefox version follows the installed browser, values stock Firefox decides for itself are dropped (`_STOCK_DEFAULT_KEYS`), and a GPU camoufox has no WebGL data for is dropped so a fitting one is drawn. `tests/fixtures/userdata-3.0/` is a real 3.0.x identity; `test_persisted_identity_upgrade` must keep passing.
- Changing what gets persisted or migrated is a user-visible change. Note it in the CHANGELOG and `docs/configuration.md`.

**Window geometry.** Openbox draws a title bar and border. `_fit_window_to_screen` sizes the browser window so the frame fits the display; the page sees its real `innerHeight`, `outerHeight` and position. Do not force window sizes into the fingerprint. `test_calibrate` asserts the page and window fit on the screen.

**Window offset.** OS-level pointer actions and viewport recordings re-measure the page's screen position (`mozInnerScreenX/Y`) before every action, so fullscreen, new tab windows and relaunches never need `calibrate`.

**Firefox prefs.** `launch_options` returns identity prefs in `firefox_user_prefs`. Add to that dict; never replace it.

## Humanized input

The planners and the executor live in auto-linux-desk (`autolinuxdesk/human_*.py` and `autolinuxdesk/system.py`); change them there. Planners are pure and seedable; `system.py` only replays timed events. Defaults are fitted to published data (SapiMouse for mouse paths, Balabit for clicks, the Aalto 136M keystrokes study for typing) and drawn once per process as one consistent "person". Keep that split: no timing logic in the executor, no I/O in the planners.

- Mouse: overlapping sigma-lognormal strokes, Fitts's-law duration, overshoot and correction on about a third of moves, sub-pixel tremor, 125 Hz events.
- Clicks: aim scattered inside the target (pass `w`/`h`), never dead centre; pre-press dwell; lognormal hold.
- Typing: lognormal gaps by key pair, a hold on every key, rollover for fast typists, never a key overlapping itself, Shift pressed with the opposite hand before capitals, Unicode through xdotool. Typos are opt-in, always corrected, refused on fields where a correction could submit bad data, and the final value is verified.
- Wheel: one notch at a time with a flick or controlled rhythm, at least 15 ms apart.
- The executor shifts the remaining schedule when X stalls instead of bursting late events.
- Input runs in a worker thread; actions stay serialized by the request lock.

## Tests

- `test.sh` sources every `tests/test_*.sh`. Register new tests in the file's `ALL_TESTS` and, if they use the shared container, in `MAIN_TESTS` in `test.sh`. Heavy timing tests go in `HEAVY_TESTS`.
- Prefer table-driven cases and assert exact values.
- `tests/test_humanized_input.sh` scores real browser sessions with open-source detectors (motion-attestation, Gaitcha) built in `tests/apps/input_detectors/` at pinned commits. **Every detector check plays a robotic control session that must be caught.** A detector that passes everything proves nothing.
- `make test-real` (`tests/test_live_behavior.sh`, `tests/test_fingerprint.sh`) runs opt-in checks against public sites: liarjs.dev fingerprint consistency, the DeviceAndBrowserInfo login test, and the FCaptcha demo (checkbox and invisible). Each behavior check runs humanized input, Playwright DOM input and raw PyAutoGUI from the same container and IP. The humanized run must pass and both robot runs must fail or score clearly worse. The robots are controls, not features. Public sites change and go down; when one fails, look at the site before the code. These tests are not in the default suite.
- Regressions get a test that fails before the fix.

## Docs

User docs live in `README.md` and `docs/` (`index.md`, `installation.md`, `configuration.md`, `api.md`, `agents.md`, `script-mode.md`, `page-loaders.md`, `cluster-mode.md`, `stealth.md`, `troubleshooting.md`, `development.md`), plus the published skill in `.agents/skills/stealthy-auto-browse/`. Any behavior, parameter, default, or env var change updates all affected files and the MCP tool descriptions in the same change.

The site at https://psyb0t.github.io/docker-stealthy-auto-browse/ is built from `docs/*.md` by Docusaurus in `website/` (exact versions in `website/package.json` and the pnpm lockfile, a digest-pinned Node image, no network during the build). `.github/workflows/docs.yml` deploys `website/build`. `website/src/css/theme.css` is shared with the other psyb0t project sites.

- Every page has front matter with `title`, a 50 to 170 character `description` and `sidebar_position` (the home page also has `slug: /`). The sidebar follows `sidebar_position`; page URLs are `/<file name>/`. Pages are plain Markdown; `website/prepare.py` refuses Liquid and writes `llms.txt`.
- Link other pages by their relative `.md` path. Links from `docs/` to repository files use full `https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/<path>` URLs (`tree/` for directories). README and skill links to the site use full `https://psyb0t.github.io/docker-stealthy-auto-browse/...` URLs.
- After a docs change, run `make docs` (it fails on a broken link or anchor), then `make docs-preview` and check desktop and phone widths and search. After changing a package version, run `make docs-lock` and `make docs-audit`.
- One prose paragraph per source line; let Markdown wrap.
- Plain, exact, active voice. No em dashes, no AI vocabulary, no filler.
- Observed detection results are labeled as observed, never promised.
- No local paths, scratch directories or agent-process details in public text.
- CHANGELOG: add to an `[Unreleased]` section in the existing style; describe user-visible effects and migration steps.

## HTTP API

POST `/` with `{"action": "<name>", ...params}`. Also `GET /screenshot/browser`, `GET /screenshot/desktop` (`?width=`, `?height=`, `?whLargest=`), `GET /state`, `GET /health`. Full reference: `docs/api.md`.

- Navigation: `goto` (`referer`, `wait_until`, timeout and retry controls), `refresh`
- Playwright input: `click`, `fill`, `type` (CSS or `xpath=`)
- OS-level input: `mouse_move`, `mouse_click`, `system_click`, `scroll`, `system_type`, `send_key` (`instant: true` skips human timing), `scroll_to_bottom_humanized`
- Tabs, dialogs, cookies, storage, downloads, `upload_file`, network and console logs, wait conditions
- Inspection: `get_interactive_elements`, `get_text`, `get_html`, `get_page_info`, `get_element`, `get_elements`, `get_computed_style`, `detect_challenge`, `eval` (`world`: `page` default or `isolated`)
- Display: `get_resolution`, `calibrate`, `enter_fullscreen`, `exit_fullscreen`, `set_color_scheme`
- Virtual media, screenshots (`save_screenshot`), recording (`start_recording`, `stop_recording`, `recording_status`), `run_script`, `sleep`, `ping`, `close`

## MCP server

Streamable HTTP at `/mcp` (fastmcp). `run_script` is always registered; single-instance mode adds `goto`, `get_text`, `get_html`, `set_clipboard`, `get_clipboard`, `set_clipboard_data`, `get_events`, `wait_for_event`, `begin_handoff`, `handoff_status`, `end_handoff`, `detect_challenge`, `solve_captcha`, `get_interactive_elements`, `screenshot`, `system_click`, `system_type`, `paste_text`, `send_key`, `mouse_move`, `scroll`, `human_pause`, `click`, `fill`, `eval_js`, `wait_for_element`, `wait_for_text`, and the generic `browser_action`. `mcp_server.register(registry)` takes `registry.call` as its dispatcher, replaces the base MCP tools and instructions, and sets script-only mode in cluster mode; the base serves `/mcp`. Keep tool parameters and descriptions in sync with the HTTP actions.

## Cluster mode

`docker-compose.cluster.yml` runs `NUM_REPLICAS` browsers behind HAProxy with sticky sessions and Redis cookie sync. With more than one replica, MCP exposes only `run_script` and `POST /` accepts only `run_script`, `ping` and `sleep`, so a step sequence runs on one instance. See `docs/cluster-mode.md`.

## Environment variables

Full table: `docs/configuration.md`. The important ones: `TZ` (must match the IP's location), `LANG`, `XVFB_RESOLUTION`, `PROXY_URL` (use HTTP for authenticated proxies), `AUTH_TOKEN`, `USE_VIEWPORT` (reduces stealth), `DESK_DARK_MODE` (dark desktop and browser; pages see `prefers-color-scheme: dark`), `PUID`/`PGID`, `LOADERS_DIR`, virtual media variables, `NUM_REPLICAS`/`REDIS_URL`, `LOG_LEVEL`/`LOG_FILE`.

## Page loaders and script mode

Loaders are YAML files in `/loaders` that replace navigation for matching URLs (`docs/page-loaders.md`, examples in `loaders/`). Script mode reads YAML from stdin with `--script`, runs every step, prints results JSON, and exits (`docs/script-mode.md`, `scripts/example_script.yaml`). Both run the same actions as the API, and `eval` steps use the page world by default.
