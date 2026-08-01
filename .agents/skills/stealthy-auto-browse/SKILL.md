---
name: stealthy-auto-browse
description: Headless-detection-resistant browser automation in Docker for authorized QA, compatibility testing, and defensive security research. Camoufox + OS-level input + persistent fingerprints. Use only with sites you own or have written authorization to test.
homepage: https://github.com/psyb0t/docker-stealthy-auto-browse
user-invocable: true
metadata:
  { "openclaw": { "emoji": "🕵️", "primaryEnv": "STEALTHY_AUTO_BROWSE_URL", "requires": { "bins": ["docker", "curl"] } } }
---

# stealthy-auto-browse

Browser automation in Docker built for QA against anti-bot stacks, compatibility testing of detection libraries (CreepJS, BrowserScan, Pixelscan, Cloudflare), and defensive security research where standard headless browsers produce false-positive blocks. Uses Camoufox (custom Firefox, no CDP signals) + PyAutoGUI for OS-level input.

For installation, configuration, and container setup, see [references/setup.md](references/setup.md).

## Security & safety

- **Authorized targets only.** This tool is built for QA/testing against sites and systems you own or have written authorization to test — not for scraping or automating third-party sites without permission. See "Authorized Use Only" below before pointing it at anything.
- **Data capture is powerful — scope it.** `get_text`, `get_html`, `get_interactive_elements`, `eval`, and the screenshot/`save_screenshot` actions can extract full page content, DOM structure, and rendered pixels in one call. Only capture what the authorized test actually needs; don't sweep pages outside scope just because the API makes it easy.
- **Dialogs auto-accept by default — this can approve destructive actions.** `confirm()`, `beforeunload`, and permission prompts are accepted automatically unless you called `handle_dialog` with `accept: false` first. An agent must disable or scope auto-accept (call `handle_dialog` with `accept: false` before any step that might raise a dialog) whenever it is acting on a stateful site (one with real data, real accounts, or irreversible actions behind a confirm prompt), and must never drive this tool against a site where an accidental confirm would be harmful. See "Dialogs" below.
- **No auth when `AUTH_TOKEN` is unset.** With it empty the HTTP API and MCP surface are UNAUTHENTICATED — anyone who can reach the port gets full browser control (navigation, input, cookies, screenshots, script execution). NEVER expose such an instance beyond localhost; set `AUTH_TOKEN` and bind to `127.0.0.1` or put it behind an authenticating proxy. See [references/setup.md](references/setup.md).
- **Loaders execute automatically on matching URLs.** Only mount loader YAML you wrote or audited — see "Page Loaders" below.

## Authorized Use Only

This tool is intentionally hard to fingerprint as automation. That makes it dangerous if misused. Only use it for:

- Sites you own or operate
- Sites you have **written authorization** to test (security engagements, bug bounty in-scope targets)
- Your own anti-bot / fraud detection stack for QA and regression testing
- Detection-library research in a controlled environment
- Compatibility testing where legitimate automation is being misclassified as malicious

**Do not** use this to evade access controls, scrape sites against their ToS, automate logged-in activity on accounts you don't own, abuse rate limits, or bypass CAPTCHAs you weren't authorized to bypass. Many jurisdictions criminalize unauthorized access regardless of technical means. The maintainers are not responsible for misuse.

If you're unsure whether your use case is authorized, it isn't. Stop and get written permission first.

## When To Use

- Validating that your own anti-bot rules behave correctly under realistic automation
- Compatibility / regression testing where another headless browser is wrongly flagged
- Authorized pentests / bug bounty on in-scope targets that require human-like interaction
- Maintaining a stable test session against your own site or a sanctioned staging environment

## When NOT To Use

- Any site you don't own or aren't explicitly authorized to test
- Scraping content protected by ToS, paywalls, or rate limits
- Driving real (non-test) user accounts on third-party services
- Static HTML — use `curl` or `WebFetch`
- Sites with no detection layer — use a normal browser skill

## Setup

The API should already be running. Set the base URL:

```bash
export STEALTHY_AUTO_BROWSE_URL=http://127.0.0.1:8080
```

**Verify:** `curl $STEALTHY_AUTO_BROWSE_URL/health` returns `ok`.

Recommended defaults: bind to `127.0.0.1`, set `AUTH_TOKEN` to a strong random value, do not expose port 5900 to anything beyond localhost, and pin the container image by digest. See [references/setup.md](references/setup.md).

## HTTP API

All commands: `POST $STEALTHY_AUTO_BROWSE_URL/` with JSON body `{"action": "name", ...params}`.

**`AUTH_TOKEN` is required for any non-localhost deployment.** When set, include it on every request (except `/health`):

```
Authorization: Bearer <key>
```

Query-string authentication is not supported because URLs leak into logs.

In single-instance mode, requests are serialized automatically — only one runs at a time, the rest queue up.

Every response:

```json
{
  "success": true,
  "timestamp": 1234567890.123,
  "data": { ... },
  "error": "only when success is false"
}
```

## Two Input Modes

### System Input — OS-Level Events

Uses PyAutoGUI for real OS-level mouse/keyboard events. The browser doesn't see synthetic DOM events. Use only for legitimate detection-stack testing where DOM-event automation is incorrectly blocked.

- `system_click` — move mouse with human-like curve, then click (viewport x,y coords)
- `mouse_move` — move mouse without clicking (hover menus, tooltips)
- `mouse_click` — click at position or current location (no smooth movement)
- `system_type` — type text character-by-character with randomized delays
- `send_key` — press a key or combo (`enter`, `tab`, `ctrl+a`)
- `scroll` — mouse wheel scroll (negative = down)

Get viewport coordinates from `get_interactive_elements`.

### Playwright Input — DOM Events

Uses Playwright's DOM events. Faster, uses CSS selectors/XPath, distinguishable as automation.

- `click` — click by selector
- `fill` — set input value instantly
- `type` — type into element character-by-character

### Which To Use

- **Clicking:** always try `click` with a CSS selector first — fast and reliable.
  Only fall back to `system_click` if your authorized test target requires OS-level input.
  `system_click` requires `calibrate` first or coordinates will be wrong.
- **Typing:** `fill` for inputs (fast). `system_type` only when OS-level input is genuinely required by the test target.
- **No detection layer in scope?** Playwright input (`click`, `fill`) is fine.
- **Testing OS-input behavior of your own detection stack?** System input + `calibrate` first.

## Typical Workflow

1. `goto` → load the page
2. `get_text` → read what's on the page
3. `get_interactive_elements` → find buttons/inputs with selectors and x,y coords
4. `click` (CSS selector) → interact; fall back to `system_click` only when test scope requires
5. `wait_for_element` / `wait_for_text` → wait for results
6. `get_text` → verify

## Actions Reference

### Navigation

```json
{"action": "goto", "url": "https://example.com"}
{"action": "goto", "url": "https://example.com", "wait_until": "networkidle"}
{"action": "goto", "url": "https://example.com", "referer": "https://google.com/search?q=stuff"}
{"action": "refresh"}
{"action": "refresh", "wait_until": "networkidle"}
```

`wait_until`: `"domcontentloaded"` (default), `"load"`, `"networkidle"`.
`referer`: set HTTP Referer header (for sites that check referrer).

Response: `{"url": "...", "title": "..."}`

### System Input (OS-Level)

```json
{"action": "system_click", "x": 500, "y": 300}
{"action": "system_click", "x": 500, "y": 300, "duration": 0.5}
{"action": "mouse_move", "x": 500, "y": 300}
{"action": "mouse_click", "x": 500, "y": 300}
{"action": "mouse_click"}
{"action": "system_type", "text": "hello world", "interval": 0.08}
{"action": "send_key", "key": "enter"}
{"action": "send_key", "key": "ctrl+a"}
{"action": "scroll", "amount": -3}
{"action": "scroll", "amount": -3, "x": 500, "y": 300}
```

### Playwright Input (DOM Events)

```json
{"action": "click", "selector": "#submit-btn"}
{"action": "click", "selector": "xpath=//button[@id='submit']"}
{"action": "fill", "selector": "input[name='email']", "value": "user@example.com"}
{"action": "type", "selector": "#search", "text": "query", "delay": 0.05}
```

### Page Inspection

> Combined with screenshots (below) and script/run_script mode, these actions let one call extract full page text, DOM structure, and rendered pixels — powerful data capture. Use only against authorized targets and only pull what the test actually needs; this is not a general-purpose scraping tool for sites you don't have permission to collect from.

```json
{"action": "get_interactive_elements"}
{"action": "get_interactive_elements", "visible_only": true}
{"action": "get_text"}
{"action": "get_html"}
{"action": "get_page_info"}
{"action": "detect_challenge"}
{"action": "get_element", "selector": "main article"}
{"action": "get_elements", "selector": "main article a", "limit": 25}
{"action": "get_computed_style", "selector": "main article", "properties": ["display", "font-size"]}
{"action": "eval", "expression": "document.title"}
{"action": "eval", "expression": "document.querySelectorAll('a').length"}
```

`get_interactive_elements` returns all buttons, links, inputs with `x`, `y`, `w`, `h`, `text`, `selector`, `visible`. Pass `x`, `y` directly to `system_click`.

`get_text` returns visible page text (truncated to 10,000 chars). Call this first after navigating.

For structured extraction, use `get_element` for one matching node or `get_elements` for a bounded list. `get_page_info` returns document/viewport state, and `get_computed_style` exposes selected CSS values without a custom JavaScript expression.

### Challenge Detection

`detect_challenge` is read-only, best-effort detection for authorised QA flows that need to request human review. It returns `absent`, `present`, or `unknown`, plus bounded known-vendor or generic low-confidence evidence. It never clicks, solves, submits, or enters a challenge frame, and excludes query strings, fragments, site keys, response values, page text, and HTML from the result. Set `scroll_into_view: true` to bring the first visible detected frame or widget into the viewport for VNC handoff; it still never clicks or focuses it. It recognises documented Turnstile, reCAPTCHA, hCaptcha, Friendly Captcha, ALTCHA, Arkose, AWS WAF, and GeeTest signals when exposed in the top-level page.

Use an `output_id` plus a script `if` output condition to decide whether your orchestration should notify a person. In cluster mode, place `detect_challenge` inside `run_script`.

### Virtual Camera and Microphone

For authorized camera/microphone compatibility tests, mount test media at `/media` and configure `VIRTUAL_CAMERA_FILE` and/or `VIRTUAL_MICROPHONE_FILE` before the browser starts. A page's `navigator.mediaDevices.getUserMedia()` call then receives tracks captured from those files. A request for an unconfigured kind fails with `NotFoundError` rather than falling back to hardware. This virtualizes streams only; it does not add native devices to `enumerateDevices()`. Source files must remain within `VIRTUAL_MEDIA_DIR` (default `/media`) and the browser must restart after static-source changes.

Set `VIRTUAL_MEDIA_DYNAMIC=true` to switch file-backed sources at runtime. `set_virtual_media_source` takes `kind` (`"camera"` or `"microphone"`) plus an existing relative `source` name; `upload_virtual_media` takes `kind`, a safe `filename` whose declared media type matches that kind, strict base64 `content_base64`, and optional `activate`. The filename supplies only the extension: the service generates and returns a collision-safe stored basename and never overwrites a named source. It checks decoded uploads with `ffprobe` for the requested video or audio stream before storage or activation. Uploads are limited to `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` (50 MiB by default) and need a writable `VIRTUAL_MEDIA_DIR`. Already acquired page streams retain their track identities after a source switch. Only files inside the configured media directory are accepted—never arbitrary paths, remote URLs, WebSocket streams, or other live ingress. These actions require the same Bearer authorization as every other action when `AUTH_TOKEN` is set. See [references/setup.md](references/setup.md).

### Screenshots

```bash
# Browser viewport
curl -s "$STEALTHY_AUTO_BROWSE_URL/screenshot/browser?whLargest=512" -o screenshot.png

# Full desktop
curl -s "$STEALTHY_AUTO_BROWSE_URL/screenshot/desktop?whLargest=512" -o desktop.png
```

Resize params: `whLargest=512` (recommended), `width=800`, `height=300`, `width=400&height=400`.

Via action (for script mode — returns base64 with `output_id`):

```json
{"action": "save_screenshot"}
{"action": "save_screenshot", "type": "desktop"}
{"action": "save_screenshot", "output_id": "my_screenshot", "whLargest": 512}
{"action": "save_screenshot", "path": "/output/page.png"}
```

### Screen Recording

Captures actual rendered pixels (ffmpeg `x11grab` against the Xvfb display) including the OS-level mouse cursor — PyAutoGUI moves are visible. One active recording per container; a second `start_recording` while one is active returns an error. **Requires `/recordings` mounted as a host volume** — see [references/setup.md](references/setup.md). If not mounted (or not writable), `start_recording` fails fast.

```json
{"action": "start_recording"}
{"action": "start_recording", "mode": "viewport", "fps": 30}
{"action": "start_recording", "mode": "desktop", "show_cursor": false}
{"action": "recording_status"}
{"action": "stop_recording", "slug": "my-flow"}
```

`mode`: `"window"` (default, full Camoufox window incl. chrome), `"viewport"` (crops chrome using the calibrated `window_offset` — lazy-recalibrates if unset), `"desktop"` (entire Xvfb screen). `fps`: 1–60, default 15. `show_cursor`: bool, default `true` — set `false` to record without the OS-level cursor sprite.

`start_recording` returns `recording_id`, `tmp_path`, `show_cursor`, `capture_size`. `stop_recording` finalizes and renames the tmp file to `/recordings/<slug>.mp4` — `slug` must match `[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}` (no path traversal); a colliding slug is saved as `<slug>-2.mp4`, etc. Returns `path`, `duration_s`, `size_bytes`. `recording_status` returns `{"active": true, "recording_id", "mode", "started_at", "elapsed_s", "tmp_path"}` when recording, `{"active": false}` otherwise.

Encoder: H.264 (`libx264`), `-preset ultrafast`, `-crf 28`, `yuv420p` — tuned for low CPU over file size on browser footage.

**Cluster mode:** recording actions are only usable from inside `run_script` — outside, the script-only restriction rejects them. `start_recording` and `stop_recording` must live in the SAME `run_script` call so both hit the same browser instance:

```json
{"action": "run_script", "steps": [
    {"action": "start_recording", "mode": "viewport", "fps": 20},
    {"action": "goto", "url": "https://example.com", "wait_until": "networkidle"},
    {"action": "stop_recording", "slug": "example-page"}
]}
```

`calibrate` after `enter_fullscreen`/`exit_fullscreen` or any chrome-state change so a following `viewport` recording crops at the right line.

### Wait Conditions

Use these instead of `sleep`.

```json
{"action": "wait_for_element", "selector": "#results", "state": "visible", "timeout": 10}
{"action": "wait_for_text", "text": "Search results", "timeout": 10}
{"action": "wait_for_url", "url": "**/dashboard", "timeout": 10}
{"action": "wait_for_network_idle", "timeout": 30}
```

`state`: `"visible"` (default), `"hidden"`, `"attached"`, `"detached"`.

### Tabs

```json
{"action": "list_tabs"}
{"action": "new_tab", "url": "https://example.com"}
{"action": "switch_tab", "index": 0}
{"action": "close_tab", "index": 1}
```

### Dialogs

> **⚠️ Dialogs are auto-accepted by default.** If a scripted step raises an unexpected `confirm()` / `beforeunload` / permission prompt, it WILL be accepted automatically — which can confirm a destructive or irreversible action. When a run might hit a dialog you don't want accepted, call `handle_dialog` with `accept: false` BEFORE the triggering action, and review your scripts for steps that could raise one.
>
> **Agent guardrail:** when driving a stateful site (real accounts, real data, or any confirm/permission prompt that could trigger an irreversible action), disable or scope auto-accept first — call `handle_dialog` with `accept: false` before the step that might raise the dialog, and only re-enable acceptance for a specific, expected prompt you intend to approve. Never run this tool against a site where an accidental confirm would be harmful.

Call `handle_dialog` BEFORE the action that triggers the dialog.

```json
{"action": "handle_dialog", "accept": true}
{"action": "handle_dialog", "accept": false}
{"action": "handle_dialog", "accept": true, "text": "prompt response"}
{"action": "get_last_dialog"}
```

### Cookies

```json
{"action": "get_cookies"}
{"action": "get_cookies", "urls": ["https://example.com"]}
{"action": "set_cookie", "name": "session", "value": "abc", "url": "https://example.com"}
{"action": "delete_cookies"}
```

### Storage

```json
{"action": "get_storage", "type": "local"}
{"action": "set_storage", "type": "local", "key": "theme", "value": "dark"}
{"action": "clear_storage", "type": "local"}
```

`type`: `"local"` (default) or `"session"`.

### Downloads & Uploads

```json
{"action": "get_last_download"}
{"action": "upload_file", "selector": "#file-input", "file_path": "/tmp/doc.pdf"}
```

### Network Logging

```json
{"action": "enable_network_log"}
{"action": "get_network_log"}
{"action": "clear_network_log"}
{"action": "getclear_network_log"}
{"action": "disable_network_log"}
```

### Console Logging

Capture `console.log`, `console.error`, `console.warn`, etc. Each entry has `type`, `text`, `location`, `timestamp`.

```json
{"action": "enable_console_log"}
{"action": "get_console_log"}
{"action": "clear_console_log"}
{"action": "getclear_console_log"}
{"action": "disable_console_log"}
```

### Scrolling

```json
{"action": "scroll_to_bottom", "delay": 0.4}
{"action": "scroll_to_bottom_humanized", "min_clicks": 2, "max_clicks": 6, "delay": 0.5}
```

`scroll_to_bottom` uses JS (fast). `scroll_to_bottom_humanized` uses OS-level mouse wheel.

### Display

```json
{"action": "calibrate"}
{"action": "get_resolution"}
{"action": "enter_fullscreen"}
{"action": "exit_fullscreen"}
```

Call `calibrate` after fullscreen changes.

### Multi-Step Scripts

Run multiple actions as one atomic request. Steps with `output_id` collect results.

```json
{"action": "run_script", "steps": [
    {"action": "goto", "url": "https://example.com", "wait_until": "domcontentloaded"},
    {"action": "sleep", "duration": 2},
    {"action": "get_text", "output_id": "text"},
    {"action": "eval", "expression": "document.title", "output_id": "title"}
]}
```

Also accepts `"yaml": "..."` with the same YAML format used in script mode.

`on_error`: `"stop"` (default) or `"continue"`.

### Utility

```json
{"action": "ping"}
{"action": "sleep", "duration": 2}
{"action": "close"}
```

### State Endpoints (GET)

```bash
curl $STEALTHY_AUTO_BROWSE_URL/health     # "ok" when ready
curl $STEALTHY_AUTO_BROWSE_URL/state      # {"status", "url", "title", "window_offset"}
```

## MCP Server

The browser exposes all actions as MCP tools via Streamable HTTP at `/mcp/` on the same port as the HTTP API.

```
http://127.0.0.1:8080/mcp/
```

Connect any MCP-compatible client to that URL. All actions from the HTTP API are available as tools — dedicated tools include `goto`, `screenshot`, `system_click`, `system_type`, `eval_js`, `get_text`, `detect_challenge`, `click`, `fill`, `run_script` (multi-step), and `browser_action` (generic fallback for everything else — cookies, tabs, storage, dialogs, downloads, logging, recording, and more).

If `AUTH_TOKEN` is set, configure the MCP client to send `Authorization: Bearer <key>` when connecting to `http://127.0.0.1:8080/mcp/`.

Works in both standalone and cluster mode. In cluster mode, only `run_script` is available (same restriction as HTTP API).

## Cluster Mode

Run multiple browser instances behind HAProxy with a request queue, sticky sessions, and Redis cookie sync. For setup see [references/setup.md](references/setup.md).

Entry point is `http://127.0.0.1:8080` — same API. HAProxy queues requests when all instances are busy instead of returning errors.

**Script-only enforcement (v1.0.0+):** When `NUM_REPLICAS > 1`, both the HTTP API and MCP server only allow `run_script`, `ping`, and `sleep`. All other individual actions are rejected. Use `run_script` to bundle multiple actions into a single atomic request — one request = one routing decision = one browser instance handles the entire sequence. All actions remain available as steps inside `run_script`.

**Sticky sessions:** HAProxy sets an `INSTANCEID` cookie. Send it back on subsequent requests to keep routing to the same browser instance. All browser state (tabs, DOM, JS, local storage) lives on that specific container — only cookies sync via Redis.

**Redis cookie sync:** Cookies set on any instance propagate to all others instantly via PubSub. Authenticate once against your own test target, the whole fleet shares the session.

## Script Mode

Pipe a YAML script via stdin, get JSON results on stdout, container exits. No HTTP server.

```bash
cat my-script.yaml | docker run --rm -i \
  -e TARGET_URL=https://example.com \
  psyb0t/stealthy-auto-browse@sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c \
  --script > results.json
```

Replace the digest with the one you've reviewed for the version you're running (`docker pull psyb0t/stealthy-auto-browse:v1.0.0 && docker inspect --format='{{index .RepoDigests 0}}' psyb0t/stealthy-auto-browse:v1.0.0`).

### Script Format

```yaml
name: Scrape Example
on_error: stop    # "stop" (default) or "continue"
steps:
  - action: goto
    url: ${env.TARGET_URL}
    wait_until: networkidle
  - action: sleep
    duration: 2
  - action: save_screenshot
    output_id: screenshot
  - action: get_text
    output_id: page_text
  - action: eval
    expression: "document.title"
    output_id: title
```

### Output

```json
{
  "name": "Scrape Example",
  "success": true,
  "steps_executed": 5,
  "steps_total": 5,
  "duration": 3.42,
  "step_results": [ ... ],
  "outputs": {
    "screenshot": "data:image/png;base64,iVBOR...",
    "page_text": { "text": "...", "length": 1234 },
    "title": { "result": "Example Domain" }
  }
}
```

- **`output_id`** on any step collects its result into `outputs`. Screenshots become base64 data URIs.
- **`${env.VAR_NAME}`** substitutes environment variables.
- **`on_error: continue`** keeps going past failures. `stop` (default) halts.
- **Control flow:** `if` conditions can inspect elements, text, URL globs, JavaScript booleans, and prior outputs. `repeat` and `while` require explicit bounds (1–100 iterations); a `while` that remains true at its bound fails visibly. See the [control-flow reference](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/docs/script-mode.md#control-flow) for the complete schema and limits.
- **All HTTP API actions** work as script steps.
- **Logs go to stderr**, stdout is clean JSON.
- **Exit code** 0 on success, 1 on failure.

### Example: Screenshot + Extract (against your own site)

```bash
cat <<'EOF' | docker run --rm -i -e URL=https://staging.your-site.example \
  psyb0t/stealthy-auto-browse@sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c \
  --script > results.json
name: Quick Scrape
steps:
  - action: goto
    url: ${env.URL}
    wait_until: networkidle
  - action: save_screenshot
    output_id: screenshot
    whLargest: 1024
  - action: get_text
    output_id: text
  - action: eval
    expression: "document.title"
    output_id: title
EOF
```

## Page Loaders (URL-Triggered Automation)

Mount YAML files to `/loaders`. When `goto` hits a matching URL, the loader's steps execute instead of normal navigation. Works in both API and script mode.

> **⚠️ Loaders run automatically the moment a matching URL is visited** — with no fresh confirmation at that point — and their steps can modify page state (`eval`, clicks, form fills). Only mount loaders you wrote or audited, and **review every loader YAML before mounting it.** Don't mount loader files from an untrusted source.

```bash
docker run -d -p 127.0.0.1:8080:8080 -v ./my-loaders:/loaders \
  psyb0t/stealthy-auto-browse@sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c
```

In script mode:

```bash
cat script.yaml | docker run --rm -i \
  -v ./my-loaders:/loaders \
  psyb0t/stealthy-auto-browse@sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c \
  --script
```

### Loader Format

```yaml
name: News Site Cleanup
match:
  domain: news-site.com       # exact hostname (www. stripped)
  path_prefix: /articles      # path starts with
  regex: "article/\\d+"       # full URL regex
steps:
  - action: goto
    url: "${url}"              # ${url} = original URL
    wait_until: networkidle
  - action: eval
    expression: "document.querySelector('.cookie-banner')?.remove()"
  - action: wait_for_element
    selector: "article"
    timeout: 10
```

Match fields are optional but at least one is required. All specified fields must match.

## Account & Session Hygiene

Persistent profiles let cookies, sessions, and fingerprints survive restarts. Use them responsibly:

- **Test accounts only.** Provision isolated accounts dedicated to QA on your own systems. Never persist sessions for real (production / personal / customer) accounts you don't own.
- **Treat the profile volume as a secret.** It contains live session cookies — back it up encrypted or not at all, and shred it (`rm -rf ./profile`) when the test run is done.
- **Don't share profile volumes across environments.** A profile built against staging shouldn't be reused against prod or vice versa.
- **Rotate credentials after authorized testing concludes** if the same accounts are used by humans too.

## Tips

1. **Read text, not pixels** — always try `get_text` or `get_html` first; screenshots are last resort
2. **Screenshots: use `whLargest=512`** — full resolution wastes tokens; fine detail is rarely needed
3. **Prefer `click` with CSS selector** — reliable and fast; use `system_click` only when scope requires OS-level input
4. **`calibrate` before `system_click`** — without it, coordinates are wrong and clicks miss
5. **Always `get_interactive_elements` before clicking** — gets both selectors and coordinates
6. **Match TZ to IP location** — timezone mismatch is a fingerprint inconsistency that breaks realistic test scenarios
7. **Wait conditions over sleep** — `wait_for_element`, `wait_for_text`, `wait_for_url`
8. **`handle_dialog` BEFORE the trigger** — dialogs are auto-accepted otherwise
9. **`calibrate` after fullscreen** — coordinate mapping shifts
