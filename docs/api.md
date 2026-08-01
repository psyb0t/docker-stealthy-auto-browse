# HTTP API Reference

## Endpoints

| Endpoint              | Method | What It Does                                             |
| --------------------- | ------ | -------------------------------------------------------- |
| `/`                   | POST   | Execute any browser action (see Actions Reference below) |
| `/screenshot/browser` | GET    | Browser viewport as PNG — what the page looks like       |
| `/screenshot/desktop` | GET    | Full virtual desktop as PNG — including browser chrome   |
| `/state`              | GET    | Current URL, page title, and window offset as JSON       |
| `/health`             | GET    | Returns `ok` when the browser is ready                   |
| `/mcp/`               | POST   | MCP (Model Context Protocol) Streamable HTTP endpoint    |

## Authentication

If `AUTH_TOKEN` is set, all requests (except `/health`) require authentication:

```
Authorization: Bearer <token>
```

## Request Correlation Headers

Every request gets a `trace_id` (logged on every line of the server's JSON log for that request) and a `request_id`. Both come back in the response so you can correlate your side with server logs.

- **`X-Request-Id`** — pass an incoming value (shape `[A-Za-z0-9._-]{1,64}`) and the server logs that exact ID and echoes it back. If missing or invalid, the server mints a fresh UUID4-hex and uses that. Useful for end-to-end tracing from your client (n8n, MCP, curl) through to the browser action.
- **`X-Trace-Id`** — always set by the server on the response (UUID4 hex). Find any log line for this request by grepping the JSON log for the trace ID.

## Auto-Recovery from Browser Crashes

If Camoufox/Firefox dies mid-session (OOM, segfault, external SIGKILL), the next request triggers an automatic relaunch: the dead context is torn down and `launch_persistent_context` runs again. Persistent profile (`/userdata` mount) survives, so cookies / fingerprint / sessions all stay. The recovery request itself takes ~4–5s extra (Camoufox cold start); subsequent requests run at full speed.

Crash diagnostics land in the JSON log at `WARNING` whenever recovery fires: `dmesg` grep for OOM / Camoufox / Firefox kills, `/proc/meminfo` snapshot, `/proc/loadavg`, and the surviving process inventory. If your container hits this often, `dmesg` will usually point at the docker OOM-killer — bump the container memory limit.

## Cluster Mode Restriction

When running in cluster mode (`NUM_REPLICAS > 1`), only `run_script`, `ping`, and `sleep` actions are allowed. All other actions return an error directing you to use `run_script`. This prevents stale content bugs from calls hitting different browser instances. See [cluster-mode.md](cluster-mode.md#script-only-mode-v100) for details.

## Request Serialization

In single-instance mode, only one request runs at a time. Additional requests queue up and execute sequentially. `/health` and `/state` are never blocked.

## Request / Response Format

**Command format:**

```
POST http://localhost:8080/
Content-Type: application/json

{"action": "action_name", "param1": "value1", "param2": "value2"}
```

**Response format:**

```json
{
  "success": true,
  "timestamp": 1234567890.123,
  "data": { ... },
  "error": "only present when success is false"
}
```

The `data` field contains action-specific results (page text, element coordinates, cookie values, etc.).

## Example: Full Login Flow (Undetectable)

```bash
API=http://localhost:8080

# 1. Navigate to login page
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "goto", "url": "https://example.com/login"}'

# 2. Find all interactive elements (buttons, inputs, links)
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "get_interactive_elements"}'
# Returns elements with x, y coordinates, text, and CSS selectors

# 3. Click the email field (use coordinates from step 2)
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "system_click", "x": 400, "y": 200}'

# 4. Type email with human-like keystrokes
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "system_type", "text": "user@example.com"}'

# 5. Tab to password field
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "send_key", "key": "tab"}'

# 6. Type password
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "system_type", "text": "secretpassword"}'

# 7. Press Enter to submit
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "send_key", "key": "enter"}'

# 8. Wait for redirect to dashboard
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "wait_for_url", "url": "**/dashboard", "timeout": 15}'

# 9. Verify we're logged in
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "get_text"}'
```

Every interaction (clicks, typing, key presses) uses OS-level input. The site sees a real human typing at a natural speed with randomized delays. No CDP signals. No automation fingerprints.

## Actions Reference

All actions are sent as `POST /` with JSON body `{"action": "name", ...params}`.

### Navigation

| Action    | Parameters                     | What It Does                                                                                                                                                               |
| --------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `goto`    | `url`, `wait_until`, `referer` | Navigate to a URL. `wait_until`: `"domcontentloaded"` (default), `"load"`, `"networkidle"`. `referer`: set the HTTP Referer header (useful for sites that check referrer). |
| `refresh` | `wait_until` (optional)        | Reload the current page. Returns URL and title.                                                                                                                            |

### System Input (OS-Level, Undetectable — Last Resort)

| Action         | Parameters           | What It Does                                                                                                                                                                                                                                      |
| -------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `system_click` | `x`, `y`, `duration` | Moves the mouse to viewport coordinates with a **human-like curved path** (random jitter, eased acceleration), then clicks. **Last resort** — prefer `click` with a CSS selector. Only use this when the site detects DOM event injection. Requires `calibrate` to have been called first or coordinates will be wrong. `duration` controls movement time (random 0.2-0.6s if omitted). |
| `mouse_move`   | `x`, `y`, `duration` | Moves the mouse with human-like movement but does **not** click. Use to hover over elements (trigger dropdown menus, tooltips) or simulate natural mouse behavior between actions.                                                                |
| `mouse_click`  | `x`, `y` (optional)  | Clicks at a position or wherever the mouse currently is. Unlike `system_click`, this does **not** do the smooth mouse movement first — it's a direct click. Use after `mouse_move` when you want to separate movement and click.                  |
| `system_type`  | `text`, `interval`   | Types text character-by-character via **real OS keystrokes**. Each key has a randomized delay (jittered around `interval`, default 0.08s) to mimic human typing speed. You must focus an input field first.                                       |
| `send_key`     | `key`                | Sends a keyboard key or combo. Examples: `"enter"`, `"tab"`, `"escape"`, `"backspace"`, `"ctrl+a"`, `"ctrl+shift+t"`. Uses PyAutoGUI key names.                                                                                                   |
| `scroll`       | `amount`, `x`, `y`   | Scrolls using the mouse wheel. **Negative = scroll down**, positive = scroll up. If `x`, `y` are provided, moves the mouse there first (useful for scrolling inside a specific element).                                                          |

### Playwright Input (DOM Events — Use These First)

| Action  | Parameters                  | What It Does                                                                                                                                                              |
| ------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `click` | `selector`                  | **Preferred click method.** Clicks an element by CSS selector or XPath (`xpath=//button[@id='submit']`). Fast and reliable. Only fall back to `system_click` if the site actively detects and blocks DOM event injection. |
| `fill`  | `selector`, `value`         | Sets an input field's value instantly. Clears existing content first. Fast but doesn't generate individual keystroke events — detectable.                                 |
| `type`  | `selector`, `text`, `delay` | Types into an element character-by-character via Playwright. Middle ground between `fill` (instant) and `system_type` (OS-level). `delay` defaults to 0.05s between keys. |

### Page Inspection

| Action                     | Parameters     | What It Does                                                                                                                                                                                                                                               |
| -------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_interactive_elements` | `visible_only` | Scans the page and returns **every** interactive element (buttons, links, inputs, selects, textareas) with their viewport coordinates (`x`, `y`), dimensions (`w`, `h`), `text`, CSS `selector`, and `visible` status. This is how you find what to click. |
| `get_text`                 | —              | Returns all visible text from the page body (truncated to 10,000 chars). Usually the first thing to call after navigating — tells you what's on the page without a screenshot.                                                                             |
| `get_html`                 | —              | Returns the full HTML source of the page. Use when `get_text` doesn't give enough structure.                                                                                                                                                               |
| `get_page_info`            | —              | Returns the current URL, title, ready state, viewport/document dimensions, and scroll position.                                                                                                                                                           |
| `detect_challenge`         | —              | Read-only best-effort detection of documented challenge integrations and conservative visible generic cues. Returns `absent`, `present`, or `unknown`; never clicks, solves, enters a frame, returns site keys/tokens, or returns URL queries/fragments. |
| `get_element`              | `selector`     | Returns one matching element's tag, text (truncated to 10,000 chars), attributes, bounding box, and visibility.                                                                                                                                          |
| `get_elements`             | `selector`, `limit` | Returns summaries for up to `limit` matching elements (1–100; default 20). Each summary includes tag, text, attributes, bounding box, and visibility.                                                                                              |
| `get_computed_style`       | `selector`, `properties` | Returns computed CSS property values. `properties` accepts up to 50 CSS property names; defaults to `display`, `visibility`, `color`, and `background-color`.                                                                                   |
| `eval`                     | `expression`   | Executes JavaScript in the page context and returns the result. Example: `"document.title"`, `"document.querySelectorAll('a').length"`.                                                                                                                    |

### Challenge Detection

`detect_challenge` is for notifying a human during an authorised QA or compatibility flow, not for bypassing a verification system. It inspects only the top-level document's bounded iframe/script origins, fixed widget selectors, and published integration globals. It does not traverse cross-origin frames, click any widget, submit a response, or make a network request.

```json
{"action": "detect_challenge"}
```

The action returns `data.status` as `"absent"`, `"present"`, or `"unknown"`. `unknown` means that the page snapshot could not be evaluated; it is deliberately not reported as absence. When present, `matches` contain `vendor`, `confidence` (`"high"`, `"medium"`, or `"low"`), `locations`, fixed `evidence` labels, and bounded frame/element bounding boxes where available. Frame evidence includes only host plus path: query strings, fragments, site keys, response values, element text, attributes, and HTML are excluded.

Known high-confidence markers cover Cloudflare Turnstile, Google reCAPTCHA, hCaptcha, Friendly Captcha, ALTCHA, Arkose, AWS WAF, and GeeTest where their documented container, iframe/script origin, or global is present. A visible generic captcha/challenge cue is returned as vendor `"unknown"` at low confidence. This is intentionally best-effort: an `absent` result does not guarantee that a custom, delayed, or server-side challenge will not appear later.

Use it inside a script with the existing output condition:

```yaml
steps:
  - action: detect_challenge
    output_id: challenge
  - if:
      condition:
        type: output
        output_id: challenge
        path: [detected]
        equals: true
      then:
        - action: eval
          expression: "'human-review-needed'"
          output_id: next_step
```

### Wait Conditions

Use these instead of `sleep` — they wait for **actual page state**, not arbitrary time.

| Action                  | Parameters                     | What It Does                                                                                                                                                    |
| ----------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wait_for_element`      | `selector`, `state`, `timeout` | Waits for an element to reach a state. `state`: `"visible"` (default), `"hidden"`, `"attached"`, `"detached"`. `timeout` in seconds (default 30). CSS or XPath. |
| `wait_for_text`         | `text`, `timeout`              | Waits for specific text to appear anywhere on the page (substring match).                                                                                       |
| `wait_for_url`          | `url`, `timeout`               | Waits for the URL to match a glob pattern. `*` matches any chars except `/`, `**` matches everything. Example: `"**/dashboard"`.                                |
| `wait_for_network_idle` | `timeout`                      | Waits until no network requests have been made for 500ms. Useful for pages that load content dynamically.                                                       |

### Tab Management

| Action       | Parameters          | What It Does                                                                                            |
| ------------ | ------------------- | ------------------------------------------------------------------------------------------------------- |
| `list_tabs`  | —                   | Returns all open tabs with their index, URL, and which one is active.                                   |
| `new_tab`    | `url`, `wait_until` | Opens a new tab (becomes the active tab). Optionally navigates to a URL.                                |
| `switch_tab` | `index`             | Switches the active tab by index (0-based). All subsequent actions operate on the active tab.           |
| `close_tab`  | `index` (optional)  | Closes a tab. If no index, closes the active tab. After closing, the last remaining tab becomes active. |

Firefox opens each tab as its own OS window, so `new_tab` / `switch_tab` / `close_tab` also **foreground** the target tab's window — the display, screenshots, recordings, and VNC follow the active tab — and transfer OS-level keyboard focus into its content so `send_key` / `system_type` reach the switched tab. Foregrounding uses a brief off-screen mouse gesture that renders no context menu (clean in recordings).

### Dialog Handling

Browsers have modal dialogs (alert, confirm, prompt). By default, dialogs are **auto-accepted** (clicks OK). Use `handle_dialog` to dismiss or provide prompt text.

| Action            | Parameters       | What It Does                                                                                                                                                                                                                                                                                                                                  |
| ----------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handle_dialog`   | `accept`, `text` | Pre-configures how the **next** dialog will be handled. `accept`: `true` = click OK, `false` = click Cancel. `text`: response for prompt dialogs. **Call this BEFORE the action that triggers the dialog.** If you don't, the dialog is auto-accepted (clicks OK). You only need this if you want to dismiss (Cancel) or provide prompt text. |
| `get_last_dialog` | —                | Returns info about the last dialog: `type` (alert/confirm/prompt/beforeunload), `message`, `default_value`, `buttons`.                                                                                                                                                                                                                        |

### Cookies

| Action           | Parameters                           | What It Does                                                                                                                                                    |
| ---------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_cookies`    | `urls` (optional)                    | Returns all browser cookies. Optionally filter by URL list. Each cookie includes name, value, domain, path, httpOnly, secure, etc.                              |
| `set_cookie`     | `name`, `value`, `url`/`domain`, ... | Sets a cookie. Needs at minimum: `name`, `value`, and either `url` or `domain`. Accepts all standard cookie fields (path, httpOnly, secure, sameSite, expires). |
| `delete_cookies` | —                                    | Clears all cookies from the browser context.                                                                                                                    |

### Storage

Access the page's localStorage and sessionStorage. Storage is per-origin — you must be on the right page.

| Action          | Parameters             | What It Does                                                                      |
| --------------- | ---------------------- | --------------------------------------------------------------------------------- |
| `get_storage`   | `type`                 | Returns all items as key-value pairs. `type`: `"local"` (default) or `"session"`. |
| `set_storage`   | `type`, `key`, `value` | Sets a single key-value pair.                                                     |
| `clear_storage` | `type`                 | Clears all items.                                                                 |

### Downloads & Uploads

| Action              | Parameters              | What It Does                                                                                                                                                                                                           |
| ------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_last_download` | —                       | Returns info about the most recent file download: `url`, `filename`, and local `path` inside the container. Returns `null` if nothing downloaded yet.                                                                  |
| `upload_file`       | `selector`, `file_path` | Programmatically sets a file on an `<input type="file">` element without opening the OS file picker. File must exist inside the container (use `docker cp` to copy files in). You still need to submit the form after. |

### Network Logging

Record all HTTP requests and responses the page makes. Useful for finding API endpoints, debugging, or verifying resources loaded.

| Action                 | Parameters | What It Does                                                                                                                     |
| ---------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `enable_network_log`   | —          | Starts recording. Each entry captures: URL, method, resource type (fetch/document/script/image/etc), status code, and timestamp. |
| `disable_network_log`  | —          | Stops recording. Already-captured entries remain.                                                                                |
| `get_network_log`      | —          | Returns all captured entries with their count.                                                                                   |
| `clear_network_log`    | —          | Deletes captured entries. Keeps logging on if it was on.                                                                         |
| `getclear_network_log` | —          | Returns all captured entries and clears the log in one call.                                                                     |

### Console Logging

Capture `console.log`, `console.error`, `console.warn`, and other console output from the page. Useful for debugging page behavior or extracting data logged by scripts.

| Action                 | Parameters | What It Does                                                                                                                                                              |
| ---------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enable_console_log`   | —          | Starts capturing console messages. Each entry has: `type` (log/error/warning/info/debug/trace/table/etc), `text`, `location`, `timestamp`. |
| `disable_console_log`  | —          | Stops capturing. Already-captured entries remain.                                                                                                                         |
| `get_console_log`      | —          | Returns all captured entries with their count.                                                                                                                            |
| `clear_console_log`    | —          | Deletes captured entries. Keeps capturing on if it was on.                                                                                                                |
| `getclear_console_log` | —          | Returns all captured entries and clears the log in one call.                                                                                                              |

### Display & Calibration

| Action             | Parameters | What It Does                                                                                                                                                                                                                                                                                                                                               |
| ------------------ | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `calibrate`        | —          | Recalculates the mapping between viewport coordinates (from `get_interactive_elements`) and screen coordinates (what PyAutoGUI uses). The browser window has chrome (title bar, etc.) that offsets the content area. **Call this after entering/exiting fullscreen**, or if `system_click` seems to be hitting the wrong spot. Auto-calculated at startup. |
| `get_resolution`   | —          | Returns the virtual display resolution (width, height).                                                                                                                                                                                                                                                                                                    |
| `enter_fullscreen` | —          | Puts the browser in fullscreen mode (hides address bar and window chrome). Call `calibrate` after.                                                                                                                                                                                                                                                         |
| `exit_fullscreen`  | —          | Exits fullscreen mode. Call `calibrate` after.                                                                                                                                                                                                                                                                                                             |

### Scrolling

| Action                       | Parameters                          | What It Does                                                                                                                                                                                                                                         |
| ---------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scroll_to_bottom`           | `delay`                             | Scrolls the entire page top-to-bottom using **JavaScript** (`window.scrollBy`), then back to top. Useful for triggering lazy-loaded content. `delay` (default 0.4s) is the pause between scroll steps. This is fast but uses JS, not OS-level input. |
| `scroll_to_bottom_humanized` | `min_clicks`, `max_clicks`, `delay` | Same goal as above but uses **real OS-level mouse wheel scrolling** (PyAutoGUI) with randomized scroll amounts and jittered delays. Undetectable by behavioral analysis. Slower but stealthy.                                                        |

### Utility

| Action            | Parameters                                                  | What It Does                                                                                                                                                                                                                                 |
| ----------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_script`      | `steps` or `yaml`, `name`, `on_error`                       | Execute multiple actions as a single atomic request. `steps` may contain action dicts plus `if`, bounded `repeat`, and bounded `while` control nodes. `yaml` uses the same format as `--script` mode. `on_error`: `"stop"` (default) or `"continue"`. Steps with `output_id` collect results. See [script-mode.md#control-flow](script-mode.md#control-flow).     |
| `ping`            | —                                                           | Health check that returns `"pong"` and the current page URL.                                                                                                                                                                                 |
| `sleep`           | `duration`                                                  | Pauses for N seconds. Prefer `wait_for_element` or `wait_for_text` when waiting for page content.                                                                                                                                            |
| `close`           | —                                                           | Shuts down the browser. The container stops after this.                                                                                                                                                                                      |
| `save_screenshot` | `output_id`, `path`, `type`, `width`, `height`, `whLargest` | Captures a screenshot. `type`: `"browser"` (default) or `"desktop"`. Optional `path` to also write PNG to disk. In script mode, `output_id` collects the base64 PNG into the outputs dict. Supports resize via `width`/`height`/`whLargest`. |
| `get_virtual_media_state` | — | Reports virtual-media configuration and, in dynamic mode, each active source basename and source revision. |

### Virtual Camera and Microphone

Set `VIRTUAL_CAMERA_FILE` and/or `VIRTUAL_MICROPHONE_FILE` to make the configured video or audio file available as the corresponding track returned by page `navigator.mediaDevices.getUserMedia()`. Both files must resolve inside `VIRTUAL_MEDIA_DIR` (default `/media`), normally a read-only bind mount. The browser must restart after changing this static source configuration.

With `VIRTUAL_MEDIA_DYNAMIC=true`, these actions switch a file-backed source while preserving the identities of tracks already returned to a page. Dynamic mode is disabled by default.

| Action | Parameters | What It Does |
| ------ | ---------- | ------------ |
| `set_virtual_media_source` | `kind`, `source` | Selects an existing source for `kind` (`"camera"` or `"microphone"`). `source` is a relative name that resolves to a regular file inside `VIRTUAL_MEDIA_DIR`. |
| `upload_virtual_media` | `kind`, `filename`, `content_base64`, `activate` | Stores strict base64 file content under `VIRTUAL_MEDIA_DIR` for `kind` (`"camera"` or `"microphone"`). `filename` must be a safe basename whose declared media type matches `kind`; it supplies only the extension. The response returns a generated collision-safe stored basename, and no existing named source is overwritten. Before storage or activation, `ffprobe` must confirm the decoded content contains the requested video or audio stream. `activate` optionally switches to the uploaded source immediately. Uploads are limited by `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` (50 MiB by default). |

Both actions require dynamic mode. Uploads also require a writable `VIRTUAL_MEDIA_DIR`. They are ordinary authenticated actions: when `AUTH_TOKEN` is set, include `Authorization: Bearer <token>` as for every other action. The controller accepts only approved files under the configured media directory—never arbitrary host paths, remote URLs, WebSocket streams, or other live ingress. `get_virtual_media_state` reports the active source basename and revision without exposing source paths or upload bytes.

This virtualizes `getUserMedia()` streams only: it does not add devices to `enumerateDevices()`. If a request asks for a kind without a configured virtual source, it fails with `NotFoundError` instead of using a native device. Virtual tracks retain the configured file's format and do not emulate incompatible exact media constraints. Use `get_virtual_media_state` to confirm the configured source types and dynamic source state.

### Screen Recording

ffmpeg `x11grab` against the Xvfb display. Captures actual rendered pixels including the OS-level mouse cursor (PyAutoGUI moves are visible). One active recording per container; second `start_recording` while active returns an error. Requires `/recordings` mounted as a host volume — if not mounted (or not writable), `start_recording` fails fast.

| Action              | Parameters     | What It Does                                                                                                                                                                                                                                                                              |
| ------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `start_recording`   | `mode`, `fps`, `show_cursor` | Begin recording to `/recordings/.tmp-<id>.mp4`. `mode`: `"window"` (default, full Camoufox window incl. chrome), `"viewport"` (crops chrome using the calibrated `window_offset` from `mozInnerScreenX/Y` — lazy-recalibrates if unset), `"desktop"` (entire Xvfb screen). `fps`: 1–60, default 15. `show_cursor`: bool, default `true` — set to `false` to record without the OS-level mouse cursor (`ffmpeg -draw_mouse 0`). Returns `recording_id`, `tmp_path`, `show_cursor`, and `capture_size`. |
| `stop_recording`    | `slug`         | Stop the active recording, finalize, and rename tmp file to `/recordings/<slug>.mp4`. `slug` must match `[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}` (no path traversal). If `<slug>.mp4` already exists, the file is saved as `<slug>-2.mp4`, `<slug>-3.mp4`, etc. Returns `path`, `duration_s`, `size_bytes`. |
| `recording_status`  | —              | `{"active": true, "recording_id", "mode", "started_at", "elapsed_s", "tmp_path"}` when recording, `{"active": false}` otherwise.                                                                                                                                                          |

Mount and run:

```bash
mkdir -p ./recordings
docker run -d -p 8080:8080 \
  -v ./recordings:/recordings \
  psyb0t/stealthy-auto-browse
```

Script-mode pattern — multiple start/stop pairs in one run, all files land in `./recordings/`:

```yaml
steps:
  - action: start_recording
    mode: window
  - action: goto
    url: https://example.com
    wait_until: networkidle
  - action: stop_recording
    slug: example-page
  - action: start_recording
    mode: viewport
    fps: 30
  - action: goto
    url: https://another.example
  - action: stop_recording
    slug: another-page
```

Notes:

- Encoder defaults: H.264 (`libx264`), `-preset ultrafast`, `-crf 28`, `yuv420p`. Picked for low CPU + reasonable file size on browser footage. To re-encode for distribution, run a second pass externally.
- `viewport` mode uses the calibrated `window_offset` (`mozInnerScreenX/Y` from Firefox) as the crop top-left. If `window_offset` is still `(0, 0)` at start time (calibrate never ran or it returned zero), `start_recording` lazily re-runs calibrate before launching ffmpeg. Call `calibrate` after `enter_fullscreen`/`exit_fullscreen` or any chrome-state change so the next recording cuts at the right line.
- In cluster mode (`NUM_REPLICAS > 1`), recording actions are only usable from inside a `run_script` call — outside, the cluster restriction rejects them. Start and stop must live in the same `run_script` so they hit the same browser instance.
- Crashes and forced shutdown abort any active recording and remove its tmp file. At next startup, the app sweeps `/recordings/.tmp-*.mp4` files older than 1h.

## Screenshots

Both screenshot endpoints support resize parameters. The default resolution is 1920x1080 — that's a big image. You almost always want to resize.

```bash
# Resize to 512px on longest side (best default — keeps aspect ratio, manageable size)
curl http://localhost:8080/screenshot/browser?whLargest=512 -o screenshot.png

# Resize to 800px wide
curl http://localhost:8080/screenshot/browser?width=800 -o screenshot.png

# Exact 400x400 dimensions
curl http://localhost:8080/screenshot/browser?width=400&height=400 -o screenshot.png

# Full desktop (includes browser chrome, taskbar, etc.)
curl http://localhost:8080/screenshot/desktop?whLargest=512 -o desktop.png
```

| Parameter              | What It Does                                                                       |
| ---------------------- | ---------------------------------------------------------------------------------- |
| `whLargest=512`        | Scales so the largest dimension is 512px, keeps aspect ratio. Use this by default. |
| `width=800`            | Scales to 800px wide, keeps aspect ratio.                                          |
| `height=300`           | Scales to 300px tall, keeps aspect ratio.                                          |
| `width=400&height=400` | Forces exact dimensions (may stretch).                                             |
