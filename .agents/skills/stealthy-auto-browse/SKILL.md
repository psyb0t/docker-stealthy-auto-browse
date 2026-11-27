---
name: stealthy-auto-browse
description: Headless-detection-resistant browser automation through an already-running stealthy-auto-browse instance (Camoufox, OS-level input, persistent fingerprints), driven over its HTTP API or MCP endpoint for authorized QA, compatibility testing and defensive security research. Use when the user asks to drive that browser, and only with sites you own or have written authorization to test.
homepage: https://github.com/psyb0t/docker-stealthy-auto-browse
user-invocable: true
permissions:
  shell: runs `curl` against the browser's HTTP API
  network: calls the HTTP API and MCP endpoint at STEALTHY_AUTO_BROWSE_URL (default http://127.0.0.1:8080); the browser itself sends traffic to the sites it is told to visit, through the proxy the operator configured, and to the CAPTCHA provider when one is configured
  filesystem: screenshots and script results are written where the user directs
metadata:
  { "openclaw": { "emoji": "🕵️", "primaryEnv": "STEALTHY_AUTO_BROWSE_URL", "requires": { "bins": ["curl"] } } }
---

# stealthy-auto-browse

Browser automation in Docker built for QA against anti-bot stacks, compatibility testing of detection libraries (CreepJS, BrowserScan, Pixelscan, Cloudflare), and defensive security research where standard headless browsers produce false-positive blocks. Uses Camoufox (custom Firefox, no CDP signals) + PyAutoGUI for OS-level input.

If the user's instance has a CAPTCHA provider configured, extension-based providers work on their own and Anti-Captcha uses the `solve_captcha` action. Use solving only on authorized sites.

## Security & safety

- **Authorized targets only.** This tool is built for QA/testing against sites and systems you own or have written authorization to test. It is not for scraping or automating third-party sites without permission. See "Authorized use only" below before pointing it at anything.
- **Data capture is powerful, so scope it.** `get_text`, `get_html`, `get_interactive_elements`, `eval`, and the screenshot/`save_screenshot` actions can extract full page content, DOM structure, and rendered pixels in one call. Only capture what the authorized test actually needs; don't sweep pages outside scope just because the API makes it easy.
- **Dialogs auto-accept by default, which can approve destructive actions.** The browser accepts `alert()`, `confirm()`, `prompt()` and `beforeunload` dialogs automatically unless you called `handle_dialog` with `accept: false` first. That setting covers only the next dialog, so an agent must call `handle_dialog` with `accept: false` before each step that might raise a dialog whenever it is acting on a stateful site (one with real data, real accounts, or irreversible actions behind a confirm prompt), and must never drive this tool against a site where an accidental confirm would be harmful. See "Dialogs" below.
- **No auth when `AUTH_TOKEN` is unset.** With it empty the HTTP API and MCP server are **unauthenticated**. Anyone who can reach the port gets full browser control (navigation, input, cookies, screenshots, script execution). If the user's instance is open, tell them rather than relying on it, and never point this tool at an instance exposed beyond localhost without a token.
- **Where data goes.** Page content, screenshots and cookies stay on your machine unless you send them elsewhere. The browser sends requests to the sites you point it at, through the proxy the operator configured. With a CAPTCHA provider configured, the challenge goes to that provider's service.
- **Destructive operations.** `close` shuts the browser and ends the container, so send it only when the user asked. `run_command` and `launch_app` run commands inside the container; use them only if the user's instance has them enabled and the user asked for them.
- **Loaders execute automatically on matching URLs.** Only rely on loader YAML the user wrote or audited. See "Page loaders" below.

## Authorized use only

This tool is intentionally hard to fingerprint as automation. That makes it dangerous if misused. Only use it for:

- Sites you own or operate
- Sites you have **written authorization** to test (security engagements, bug bounty in-scope targets)
- Your own anti-bot / fraud detection stack for QA and regression testing
- Detection-library research in a controlled environment
- Compatibility testing where a detection stack misclassifies legitimate automation as malicious

**Do not** use this to evade access controls, scrape sites against their ToS, automate logged-in activity on accounts you don't own, abuse rate limits, or bypass CAPTCHAs you weren't authorized to bypass. Many jurisdictions criminalize unauthorized access regardless of technical means. The maintainer is not responsible for misuse.

If you're unsure whether your use case is authorized, it isn't. Stop and get written permission first.

## When to use

- Validating that your own anti-bot rules behave correctly under realistic automation
- Compatibility / regression testing where another headless browser is wrongly flagged
- Authorized pentests / bug bounty on in-scope targets that require human-like interaction
- Maintaining a stable test session against your own site or a sanctioned staging environment

## When not to use

- Any site you don't own or aren't explicitly authorized to test
- Scraping content protected by ToS, paywalls, or rate limits
- Driving real (non-test) user accounts on third-party services
- Static HTML: use `curl` or `WebFetch`
- Sites with no detection layer: use a normal browser skill

## Setup

The browser must already be installed and running; this skill only covers using it. Installation and configuration are in the docs at https://psyb0t.github.io/docker-stealthy-auto-browse/ and the README at https://github.com/psyb0t/docker-stealthy-auto-browse. Read `STEALTHY_AUTO_BROWSE_URL` (for example `http://127.0.0.1:8080`) and, if the instance has one, `AUTH_TOKEN` from the environment the user set. **Verify:** `curl $STEALTHY_AUTO_BROWSE_URL/health` returns `ok`.

## HTTP API

All commands: `POST $STEALTHY_AUTO_BROWSE_URL/` with JSON body `{"action": "name", ...params}`.

When the instance has an `AUTH_TOKEN`, include it on every request (except `/health`):

```
Authorization: Bearer <key>
```

Query-string authentication is not supported because URLs leak into logs.

In single-instance mode, the server serializes requests automatically. Only one runs at a time and the rest queue up.

Every response:

```json
{
  "success": true,
  "timestamp": 1234567890.123,
  "data": { ... },
  "error": "only when success is false"
}
```

## Two input modes

### System input: OS-level events

Uses PyAutoGUI for real OS-level mouse/keyboard events. The browser doesn't see synthetic DOM events. Use only for legitimate detection-stack testing where the stack incorrectly blocks DOM-event automation.

- `system_click`: move along a human path to the target, then press with a human hold (viewport x,y of the target centre, plus its w,h)
- `system_hover`, `system_double_click`, `system_right_click`: target an element by selector, role, or reusable `element_id`
- `system_mouse_down`, `system_mouse_up`, `system_drag`: trusted held-button and drag gestures
- `mouse_move`: move without clicking (hover menus, tooltips)
- `mouse_click`: click where the pointer is, or move to x,y first
- `system_type`: type with real key holds, human rhythm, Shift before capitals; optional self-correcting typos
- `send_key`: press a key or combo (`enter`, `tab`, `ctrl+a`)
- `scroll`: mouse wheel scroll, one notch at a time (negative = down)

Get viewport coordinates and sizes (`x`, `y`, `w`, `h`) from `get_interactive_elements` and pass all four to `system_click`.

Timing is modeled on human motor data and is consistent per container. Override it per request when needed: `speed`, `curvature`, `tremor`, `click_hold`, `click_delay` on pointer actions; `interval`, `key_hold`, `rollover`, `typos`, `typo_rate` on `system_type`; `key_hold` or `instant: true` (no human timing) on `send_key`; `notch_gap` on wheel actions; `max_gestures`, `return_to_top`, `scroll_back`, `drift` on `scroll_to_bottom_humanized`; and `duration`, `drift_interval`, `drift_distance` on `human_pause`. Pointer actions return `landed_at`. Ranges and defaults: [Humanized input](https://psyb0t.github.io/docker-auto-linux-desk/humanized-input/).

### Playwright input: DOM events

Uses Playwright's DOM events. Faster, uses CSS selectors/XPath, distinguishable as automation.

- `click`: click by selector
- `fill`: set input value instantly
- `type`: type into element character-by-character
- `hover`, `double_click`, `right_click`, `drag_and_drop`: DOM pointer gestures

Element actions accept one of `selector`, `role` plus optional accessible `name`, or a reusable `element_id`. Add `frames` as an ordered iframe selector chain. CSS selectors pierce open shadow roots. Closed shadow roots cannot be entered. Prefer role and accessible name when the page has good accessibility metadata. Create a stable reference before repeated operations on a dynamic page, then reacquire it after navigation.

### Which to use

- **Input:** default to the OS-level actions (`system_click`, `paste_text`, `system_type`, `scroll`). They send trusted events with human timing and leave no DOM injection trail. Use `click`, `fill` and other DOM input only when the user asked for it or speed matters more than stealth.
- **Clicking:** use `system_click` with a selector, `element_id`, or the `x`, `y`, `w`, `h` from `get_interactive_elements`. It re-measures the window position itself, so no `calibrate` call is needed.
- **Typing:** `paste_text` (see Clipboard below); keep `system_type` for a few characters or a field that blocks pasting.
- **Speed over stealth, or the user asked for DOM input?** Playwright input (`click`, `fill`) is fine.

## Typical workflow

1. `goto` → load the page
2. `get_text` → read what's on the page
3. `get_interactive_elements` → find buttons/inputs with selectors and x,y coords
4. `system_click` / `paste_text` → interact; `click` / `fill` only when the user asked for DOM input or speed matters more than stealth
5. `wait_for_element` / `wait_for_text` → wait for results
6. `get_text` → verify

## Actions reference

This section covers the common actions. The [HTTP API reference](https://psyb0t.github.io/docker-stealthy-auto-browse/api/) also documents network interception and HAR export, accessibility lookup (`get_by_role`, `get_accessibility_tree`), element references, `wait_for_dom_change`, window control and popups, permissions and geolocation, and the desktop actions.

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

### System input (OS-level)

```json
{"action": "system_click", "x": 500, "y": 300, "w": 120, "h": 40}
{"action": "system_click", "x": 500, "y": 300, "w": 120, "h": 40, "speed": 1.5, "click_hold": 0.15}
{"action": "mouse_move", "x": 500, "y": 300}
{"action": "mouse_click", "x": 500, "y": 300}
{"action": "mouse_click"}
{"action": "system_type", "text": "hello world"}
{"action": "system_type", "text": "a comment", "typos": true}
{"action": "system_type", "text": "hello world", "interval": 0.12, "key_hold": 0.09}
{"action": "send_key", "key": "enter"}
{"action": "send_key", "key": "ctrl+a"}
{"action": "send_key", "key": "enter", "instant": true}
{"action": "scroll", "amount": -3}
{"action": "scroll", "amount": -3, "x": 500, "y": 300, "notch_gap": 0.1}
```

`system_type` only makes typos on plain text fields and always corrects them; on password, email, maxlength and similar fields the response has `"typos": false` and a `typos_disabled_reason`.

### Playwright input (DOM events)

```json
{"action": "click", "selector": "#submit-btn"}
{"action": "click", "selector": "xpath=//button[@id='submit']"}
{"action": "fill", "selector": "input[name='email']", "value": "user@example.com"}
{"action": "type", "selector": "#search", "text": "query", "delay": 0.05}
```

### Page inspection

> Combined with screenshots (below) and script/run_script mode, these actions let one call extract full page text, DOM structure, and rendered pixels. That is powerful data capture. Use only against authorized targets and only pull what the test actually needs; this is not a general-purpose scraping tool for sites you don't have permission to collect from.

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
{"action": "eval", "expression": "document.title", "world": "isolated"}
```

`eval` runs in the page's own JavaScript world by default, so it can read globals a site defines (`window.__NEXT_DATA__`). With `"world": "isolated"` it sees only the DOM, and page scripts cannot observe or hook it. Prefer `isolated` for DOM reads on sites that watch for automation.

`get_interactive_elements` returns all buttons, links, inputs with `x`, `y`, `w`, `h`, `text`, `selector`, `visible`, and an `element_id` where one could be created. Pass `x`, `y`, `w`, `h` to `system_click` so the click lands at a human offset inside the element. The `selector` is a best-effort guess and can be an absolute path like `/html[1]/body[1]/button[1]` that only works with an `xpath=` prefix. It is not always unique (radio buttons in a group share one), and `element_id` is its first match, so it can belong to another element; for those rows use the coordinates.

`get_text` returns visible page text (truncated to 10,000 chars). Call this first after navigating.

For structured extraction, use `get_element` for one matching node or `get_elements` for a bounded list. `get_page_info` returns document/viewport state, and `get_computed_style` exposes selected CSS values without a custom JavaScript expression.

### Challenge detection

`detect_challenge` is read-only, best-effort detection for authorised QA flows that need to request human review. It returns `detected` (`true` only when something was found) and `status` (`absent`, `present`, or `unknown`), plus bounded known-vendor or generic low-confidence evidence. It never clicks, solves, submits, or enters a challenge frame, and excludes query strings, fragments, site keys, response values, page text, and HTML from the result. Set `scroll_into_view: true` to bring the first visible detected frame or widget into the viewport for VNC handoff; it still never clicks or focuses it. It recognises documented Turnstile, reCAPTCHA, hCaptcha, Friendly Captcha, ALTCHA, Arkose, AWS WAF, and GeeTest signals when exposed in the top-level page.

Use an `output_id` plus a script `if` output condition to decide whether your orchestration should notify a person. In cluster mode, place `detect_challenge` inside `run_script`.

### Virtual camera and microphone

For authorized camera/microphone compatibility tests, an instance whose operator configured virtual media returns tracks captured from files when a page calls `navigator.mediaDevices.getUserMedia()`. A request for an unconfigured kind fails with `NotFoundError` rather than falling back to hardware. This virtualizes streams only; it does not add native devices to `enumerateDevices()`.

If the instance allows dynamic sources, `set_virtual_media_source` takes `kind` (`"camera"` or `"microphone"`) plus an existing relative `source` name; `upload_virtual_media` takes `kind`, a safe `filename` whose declared media type matches that kind, strict base64 `content_base64`, and optional `activate`. The filename supplies only the extension: the service generates and returns a collision-safe stored basename and never overwrites a named source. Decoded uploads are checked with `ffprobe` for the requested video or audio stream, and are size-limited (50 MiB by default). Already acquired page streams retain their track identities after a source switch. The service accepts only files inside its media directory, never arbitrary paths, remote URLs, WebSocket streams, or other live ingress. These actions need the same Bearer authorization as every other action.

### Screenshots

```bash
# Browser viewport
curl -s "$STEALTHY_AUTO_BROWSE_URL/screenshot/browser?whLargest=512" -o screenshot.png

# Full desktop
curl -s "$STEALTHY_AUTO_BROWSE_URL/screenshot/desktop?whLargest=512" -o desktop.png
```

Resize params: `whLargest=512` (recommended), `width=800`, `height=300`, `width=400&height=400`.

Via action (for script mode, returns base64 with `output_id`):

```json
{"action": "save_screenshot"}
{"action": "save_screenshot", "type": "desktop"}
{"action": "save_screenshot", "output_id": "my_screenshot", "whLargest": 512}
{"action": "save_screenshot", "path": "/output/page.png"}
```

`path` writes inside the container, over HTTP too, and the browser user must be able to write that directory.

### Screen recording

Captures actual rendered pixels (ffmpeg `x11grab` against the Xvfb display) including the OS-level mouse cursor, so PyAutoGUI moves are visible. One active recording per container; a second `start_recording` while one is active returns an error. **Requires the operator to have mounted `/recordings`.** If it is not mounted (or not writable), `start_recording` fails fast.

```json
{"action": "start_recording"}
{"action": "start_recording", "mode": "viewport", "fps": 30}
{"action": "start_recording", "mode": "desktop", "show_cursor": false}
{"action": "recording_status"}
{"action": "stop_recording", "slug": "my-flow"}
```

`mode`: `"window"` (default) and `"desktop"` capture the whole Xvfb screen (the active tab's window fills it); `"viewport"` starts at the page's top-left corner, measured at start, and runs to the screen edges. `fps`: 1-60, default 15. `show_cursor`: bool, default `true`. Set `false` to record without the OS-level cursor sprite.

`start_recording` returns `recording_id`, `tmp_path`, `show_cursor`, `capture_size`. `stop_recording` finalizes and renames the tmp file to `/recordings/<slug>.mp4`. `slug` must match `[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}` (no path traversal); `stop_recording` saves a colliding slug as `<slug>-2.mp4`, etc. Returns `path`, `duration_s`, `size_bytes`. `recording_status` returns `{"active": true, "recording_id", "mode", "started_at", "elapsed_s", "tmp_path"}` when recording, `{"active": false}` otherwise.

Encoder: H.264 (`libx264`), `-preset ultrafast`, `-crf 28`, `yuv420p`, tuned for low CPU over file size on browser footage.

**Cluster mode:** recording actions are only usable from inside `run_script`. Outside it, the script-only restriction rejects them. `start_recording` and `stop_recording` must live in the **same** `run_script` call so both hit the same browser instance:

```json
{"action": "run_script", "steps": [
    {"action": "start_recording", "mode": "viewport", "fps": 20},
    {"action": "goto", "url": "https://example.com", "wait_until": "networkidle"},
    {"action": "stop_recording", "slug": "example-page"}
]}
```

### Wait conditions

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

> **Dialogs are auto-accepted by default.** If a scripted step raises an unexpected `confirm()` or `beforeunload` dialog, the browser **will** accept it automatically, which can confirm a destructive or irreversible action. When a run might hit a dialog you don't want accepted, call `handle_dialog` with `accept: false` **before** the triggering action, and review your scripts for steps that could raise one.
>
> **Agent guardrail:** when driving a stateful site (real accounts, real data, or any confirm prompt that could trigger an irreversible action), call `handle_dialog` with `accept: false` before every step that might raise a dialog. It applies to the next dialog only and then auto-accept returns, so repeat it before each risky step. Call it with `accept: true` only for a specific, expected prompt you intend to approve. Never run this tool against a site where an accidental confirm would be harmful.

Call `handle_dialog` **before** the action that triggers the dialog.

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

### Downloads & uploads

```json
{"action": "get_last_download"}
{"action": "upload_file", "selector": "#file-input", "file_path": "/tmp/doc.pdf"}
```

### Clipboard

These actions use the real X11 clipboard shared by Camoufox and noVNC. Formats are `text`, `html`, `image/png`, and `files`. Clipboard payloads are bounded and are not written to request logs.

People paste far more than they type. Enter text with `paste_text`; use `fill` only when the caller asked for DOM input or speed matters more than stealth. `paste_text` puts the text on the clipboard, clicks the field when given a target or coordinates, and presses `ctrl+a`, `backspace` and `ctrl+v` with human timing. Use `system_type` only for a few characters: a word, a short search, a small edit, or a field that blocks pasting.

```json
{"action": "set_clipboard", "text": "paste me"}
{"action": "get_clipboard"}
```

Both are dedicated MCP tools. In cluster mode, put them inside one `run_script` call.

### Events and human handoff

Use `get_events` or `wait_for_event` instead of repeatedly polling page state. The HTTP SSE stream is `GET /events`. Event history is bounded and process-local.

Call `begin_handoff` before asking a person to use noVNC. While handoff is active every action is refused except a short read-only list (`get_text`, `get_html`, `get_page_info`, `list_tabs`, `save_screenshot`, the event reads and `handoff_status`, among others; see [Events and human handoff](https://psyb0t.github.io/docker-stealthy-auto-browse/api/#events-and-human-handoff)). `detect_challenge`, `eval` and the waits are refused too. `handoff_status` reports trusted pointer, keyboard, and wheel activity. Call `end_handoff` before resuming automated actions.

### Network logging

```json
{"action": "enable_network_log"}
{"action": "get_network_log"}
{"action": "clear_network_log"}
{"action": "getclear_network_log"}
{"action": "disable_network_log"}
```

### Console logging

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
{"action": "scroll_to_bottom_humanized", "delay": 1.5, "scroll_back": 0.1, "return_to_top": false}
```

`scroll_to_bottom` uses JS (fast). `scroll_to_bottom_humanized` scrolls like a reader with the OS-level wheel: gestures, reading pauses around `delay`, pointer drift, occasional scroll-backs, and Home (wheel flicks when a text field has focus) to return to the top.

### Human pause

Use `human_pause` only when the visible task naturally includes reading, reviewing, or composing. It keeps the pointer still most of the time and optionally makes sparse bounded drifts. Do not insert it between every action and do not use it as a replacement for `sleep`.

```json
{"action": "human_pause", "duration": 4, "drift": true, "drift_interval": 12, "drift_distance": 60}
```

Optional `x`, `y`, `w`, and `h` define a safe viewport rectangle. The action blocks until the pause ends and returns `elapsed`, `drifts`, `parked`, and `landed_at`; it never leaves background pointer movement running.

### Display

```json
{"action": "calibrate"}
{"action": "get_resolution"}
{"action": "set_color_scheme", "scheme": "dark"}
{"action": "enter_fullscreen"}
{"action": "exit_fullscreen"}
```

`calibrate` returns the current `window_offset`. OS-level pointer actions and `viewport` recordings measure it themselves, so you never have to call it.

### Multi-step scripts

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

The response's top-level `success` is `true` whenever the script was valid and ran, even if a step failed. Check `data.success` and each `data.step_results[].error`.

### Utility

```json
{"action": "ping"}
{"action": "sleep", "duration": 2}
{"action": "close"}
```

`close` shuts down the browser and the container stops. Do not send it unless you mean to end the container.

### State endpoints (GET)

```bash
curl $STEALTHY_AUTO_BROWSE_URL/health     # "ok" when ready
curl $STEALTHY_AUTO_BROWSE_URL/state      # {"status", "url", "title", "window_offset"}
```

## MCP server

The browser exposes all actions as MCP tools via Streamable HTTP at `/mcp/` on the same port as the HTTP API.

```
$STEALTHY_AUTO_BROWSE_URL/mcp/
```

Connect any MCP-compatible client to that URL. All actions from the HTTP API are available through dedicated tools or `browser_action`. Dedicated tools include `goto`, `screenshot`, `system_click`, `paste_text`, `system_type`, `human_pause`, `eval_js`, `get_text`, `set_clipboard`, `set_clipboard_data`, `get_clipboard`, `get_events`, `wait_for_event`, `begin_handoff`, `handoff_status`, `end_handoff`, `detect_challenge`, `click`, `fill`, and `run_script`.

If `AUTH_TOKEN` is set, configure the MCP client to send `Authorization: Bearer <key>` when connecting to `$STEALTHY_AUTO_BROWSE_URL/mcp/`.

Works in both standalone and cluster mode. In cluster mode, MCP exposes only `run_script`; the HTTP API also accepts `ping` and `sleep`.

## Cluster mode

An instance may run as several browsers behind HAProxy with a request queue, sticky sessions, and Redis cookie sync.

The entry point is the same `STEALTHY_AUTO_BROWSE_URL`, with the same API. HAProxy queues requests when all instances are busy instead of returning errors.

**Script-only enforcement:** When `NUM_REPLICAS > 1`, MCP exposes only `run_script`, and the HTTP API only accepts `run_script`, `ping`, and `sleep`. Both reject all other individual actions. Use `run_script` to bundle multiple actions into a single atomic request. One request = one routing decision = one browser instance handles the entire sequence. All actions remain available as steps inside `run_script`.

**Sticky sessions:** HAProxy sets an `INSTANCEID` cookie. Send it back on subsequent requests to keep routing to the same browser instance. All browser state (tabs, DOM, JS, local storage) lives on that specific container. Only cookies sync via Redis.

**Redis cookie sync:** With `REDIS_URL` configured, cookies created, changed, or deleted by websites and API calls reconcile through Redis on a one-second observation cycle. Propagation is eventual. Only cookies are shared, not localStorage, tabs, or device-bound session state, so a usable cross-replica login is not guaranteed. `delete_cookies` on one replica clears the shared jar and every replica's cookies. Upgrade all replicas together.

## Script mode

A script is a YAML list of actions. Send it to a running instance with `run_script` (`"yaml": "..."`), or the operator can run a one-shot container that reads it from stdin with `--script` and prints JSON results; see the [script mode docs](https://psyb0t.github.io/docker-stealthy-auto-browse/script-mode/).

### Script format

```yaml
name: Scrape Example
on_error: stop    # "stop" (default) or "continue"
steps:
  - action: goto
    url: https://staging.your-site.example
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
- **`${env.VAR_NAME}`** substitutes environment variables of the instance.
- **`on_error: continue`** keeps going past failures. `stop` (default) halts.
- **Control flow:** `if` conditions can inspect elements, text, URL globs, JavaScript booleans, and prior outputs. `repeat` and `while` require explicit bounds (1-100 iterations); a `while` that remains true at its bound fails visibly. See the [control-flow reference](https://psyb0t.github.io/docker-stealthy-auto-browse/script-mode/#control-flow) for the complete schema and limits.
- **All HTTP API actions** work as script steps.

## Page loaders (URL-triggered automation)

An instance can have loader YAML files from its operator. When `goto` hits a matching URL, the loader's steps execute instead of normal navigation. Works in both API and script mode.

> **Loaders run automatically the moment the browser visits a matching URL**, with no fresh confirmation at that point, and their steps can modify page state (`eval`, clicks, form fills). Only rely on loaders the user wrote or audited, and tell the user if a loader you did not expect changes what a page does.

### Loader format

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

Set at least one match field; all specified fields must match. A misspelled field name leaves the loader matching every URL. A loader without a `goto` to `"${url}"` never loads the page.

## Account & session hygiene

A persistent profile lets cookies, sessions, and fingerprints survive restarts. Use it responsibly:

- **Test accounts only.** Use isolated accounts dedicated to QA on your own systems. Never persist sessions for real (production / personal / customer) accounts you don't own.
- **Treat the profile as a secret.** It contains live session cookies.
- **Don't share a profile across environments.** A profile built against staging shouldn't be reused against prod or vice versa.
- **Rotate credentials after authorized testing concludes** if humans also use the same accounts.

## Tips

1. **Read text, not pixels.** Always try `get_text` or `get_html` first; screenshots are last resort
2. **Screenshots: use `whLargest=512`.** Full resolution wastes tokens; fine detail is rarely needed
3. **Prefer `click` with CSS selector.** It is reliable and fast; use `system_click` only when scope requires OS-level input
4. **Always `get_interactive_elements` before clicking.** It gets both selectors and coordinates
5. **Wait conditions over sleep:** `wait_for_element`, `wait_for_text`, `wait_for_url`
6. **`handle_dialog` before the trigger.** Otherwise the browser auto-accepts dialogs
