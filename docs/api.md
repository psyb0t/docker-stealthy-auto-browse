# HTTP API reference

## Endpoints

| Endpoint              | Method | What It Does                                             |
| --------------------- | ------ | -------------------------------------------------------- |
| `/`                   | POST   | Execute any browser action (see Actions Reference below) |
| `/screenshot/browser` | GET    | Browser viewport as PNG (what the page looks like)       |
| `/screenshot/desktop` | GET    | Full virtual desktop as PNG (including browser chrome)   |
| `/state`              | GET    | Current URL, page title, and window offset as JSON       |
| `/health`             | GET    | Returns `ok` when the browser is ready                   |
| `/mcp/`               | POST   | MCP (Model Context Protocol) Streamable HTTP endpoint    |

## Authentication

If `AUTH_TOKEN` is set, all requests (except `/health`) require authentication:

```
Authorization: Bearer <token>
```

## Request correlation headers

Every request gets a `trace_id` (logged on every line of the server's JSON log for that request) and a `request_id`. Both come back in the response so you can correlate your side with server logs.

- **`X-Request-Id`**: pass an incoming value (shape `[A-Za-z0-9._-]{1,64}`) and the server logs that exact ID and echoes it back. If missing or invalid, the server mints a fresh UUID4-hex and uses that. Use it for end-to-end tracing from your client (n8n, MCP, curl) through to the browser action.
- **`X-Trace-Id`**: the server always sets it on the response (UUID4 hex). Find any log line for this request by grepping the JSON log for the trace ID.

## Auto-recovery from browser crashes

If Camoufox/Firefox dies mid-session (OOM, segfault, external SIGKILL), the next request triggers an automatic relaunch. The server tears down the dead context and runs `launch_persistent_context` again. The persistent profile (`/userdata` mount) survives, so cookies / fingerprint / sessions all stay. The recovery request itself takes ~4-5s extra (Camoufox cold start); subsequent requests run at full speed.

Crash diagnostics land in the JSON log at `WARNING` whenever recovery fires: `dmesg` grep for OOM / Camoufox / Firefox kills, `/proc/meminfo` snapshot, `/proc/loadavg`, and the surviving process inventory. If your container hits this often, `dmesg` will usually point at the docker OOM-killer. Bump the container memory limit.

## Cluster mode restriction

When running in cluster mode (`NUM_REPLICAS > 1`), only `run_script`, `ping`, and `sleep` actions are allowed. All other actions return an error directing you to use `run_script`. This prevents stale content bugs from calls hitting different browser instances. See [cluster-mode.md](cluster-mode.md#script-only-mode-v100) for details.

## Request serialization

In single-instance mode, only one request runs at a time. Additional requests queue up and execute sequentially. `/health` and `/state` are never blocked.

## Request / response format

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

## Example: full login flow (undetectable)

```bash
API=http://localhost:8080

# 1. Navigate to login page
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "goto", "url": "https://example.com/login"}'

# 2. Find all interactive elements (buttons, inputs, links)
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "get_interactive_elements"}'
# Returns elements with x, y (centre), w, h, text, and CSS selectors

# 3. Click the email field (use x, y, w, h from step 2)
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "system_click", "x": 400, "y": 200, "w": 280, "h": 36}'

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

Every interaction (clicks, typing, key presses) uses OS-level input. The site sees human mouse paths, key holds, and typing rhythm. No CDP signals. No automation fingerprints.

## Actions reference

All actions are sent as `POST /` with JSON body `{"action": "name", ...params}`.

### Navigation

| Action    | Parameters                                                  | What It Does |
| --------- | ----------------------------------------------------------- | ------------ |
| `goto`    | `url`, `wait_until`, `referer`, `timeout`, `retry_count`, `retry_delay` | Navigate to a URL. `wait_until`: `"domcontentloaded"` (default), `"load"`, `"networkidle"`. `referer`: set the HTTP Referer header. |
| `refresh` | `wait_until`, `timeout`, `retry_count`, `retry_delay`      | Reload the current page. Returns URL and title. |

Navigation never relies on a Playwright default. Every attempt gets the app's explicit `timeout` of 30 seconds unless you set another value. `retry_count` defaults to 1, so a timed-out navigation gets one retry. Set it to 0 to disable retries. `retry_delay` defaults to 1 second and doubles before each further retry. `timeout` must be 0.1-120 seconds, `retry_count` 0-2, `retry_delay` 0-10 seconds, and the whole worst-case attempt sequence must fit within 120 seconds. Only Playwright navigation timeouts retry. Other navigation failures return immediately.

### System input (OS-level, undetectable, last resort)

These actions drive the real X11 mouse and keyboard with timing modeled on how people actually move, click, type, and scroll. See [Humanized Input](#humanized-input) for the model, the defaults, and every tuning option.

| Action         | Parameters                                                                  | What It Does |
| -------------- | --------------------------------------------------------------------------- | ------------ |
| `system_click` | `x`, `y`, `w`, `h`, `duration`, plus [pointer options](#pointer-options)    | Moves the mouse to the target centred on viewport `x`, `y` along a human path, then clicks with a human pause and button hold. Pass the element's `w` and `h` (from `get_interactive_elements`) so the click lands at a human offset inside it, never dead centre. **Last resort.** Prefer `click` with a CSS selector. The screen offset is re-measured before every move, so no `calibrate` call is needed. Returns `landed_at` with the viewport point that was clicked. |
| `mouse_move`   | `x`, `y`, `w`, `h`, `duration`, `speed`, `curvature`, `tremor`              | Moves the mouse like `system_click` but does **not** click. Use to hover over elements (dropdown menus, tooltips). Returns `landed_at`. |
| `mouse_click`  | `x`, `y` (optional), `w`, `h`, plus [pointer options](#pointer-options)     | With `x`, `y`: moves there like `system_click`, then clicks. Without them: clicks where the pointer is, with the same human press and hold. Give both `x` and `y` or neither. Returns `clicked_at` (the requested point, or `"current"`) and `landed_at`. |
| `system_type`  | `text`, `interval`, `typos`, `typo_rate`, `key_hold`, `rollover`            | Types text with **real OS keystrokes**: each key is held and released, fast typists overlap keys, Shift goes down before capitals, and the rhythm follows the key pair. Characters outside the US layout (é, ñ, €) are typed too. `typos: true` makes and corrects occasional typos on plain text fields only (see [Typos](#typos)). You must focus an input field first. Returns `typed_len`, `typos` (true when typos were enabled for the field), and `typos_disabled_reason` when typos were requested but refused. |
| `send_key`     | `key`, `key_hold`, `instant`                                                | Sends a keyboard key or combo with a human hold. `instant: true` presses and releases at once with no human timing, as before 3.0.0. It is faster but easy for a page to spot. Examples: `"enter"`, `"tab"`, `"escape"`, `"backspace"`, `"ctrl+a"`, `"ctrl+shift+t"`. Combo keys go down in order and come up in reverse. Uses PyAutoGUI key names. |
| `scroll`       | `amount`, `x`, `y`, `notch_gap`                                             | Scrolls `amount` mouse wheel notches (a non-zero integer, default -3), one at a time with a human rhythm. Amounts above 4 are split into several gestures with short pauses. **Negative = scroll down**, positive = scroll up. If `x`, `y` are provided (both or neither), moves the mouse there first (useful for scrolling inside a specific element). |

### Playwright input (DOM events, use these first)

| Action  | Parameters                  | What It Does                                                                                                                                                              |
| ------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `click` | `selector`                  | **Preferred click method.** Clicks an element by CSS selector or XPath (`xpath=//button[@id='submit']`). Fast and reliable. Only fall back to `system_click` if the site actively detects and blocks DOM event injection. |
| `fill`  | `selector`, `value`         | Sets an input field's value instantly. Clears existing content first. Fast but detectable, because it doesn't generate individual keystroke events.                       |
| `type`  | `selector`, `text`, `delay` | Types into an element character-by-character via Playwright. Middle ground between `fill` (instant) and `system_type` (OS-level). `delay` defaults to 0.05s between keys. |

### Page inspection

| Action                     | Parameters     | What It Does                                                                                                                                                                                                                                               |
| -------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_interactive_elements` | `visible_only` | Scans the page and returns **every** interactive element (buttons, links, inputs, selects, textareas) with their viewport coordinates (`x`, `y`), dimensions (`w`, `h`), `text`, CSS `selector`, and `visible` status. This is how you find what to click. |
| `get_text`                 | none           | Returns all visible text from the page body (truncated to 10,000 chars). Usually the first thing to call after navigating. It tells you what's on the page without a screenshot.                                                                           |
| `get_html`                 | none           | Returns the full HTML source of the page. Use when `get_text` doesn't give enough structure.                                                                                                                                                               |
| `get_page_info`            | none           | Returns the current URL, title, ready state, viewport/document dimensions, and scroll position.                                                                                                                                                           |
| `detect_challenge`         | `scroll_into_view` | Read-only best-effort detection of documented challenge integrations and conservative visible generic cues. Optional viewport reveal never clicks, solves, focuses, or enters a frame. |
| `get_element`              | `selector`     | Returns one matching element's tag, text (truncated to 10,000 chars), attributes, bounding box, and visibility.                                                                                                                                          |
| `get_elements`             | `selector`, `limit` | Returns summaries for up to `limit` matching elements (1-100; default 20). Each summary includes tag, text, attributes, bounding box, and visibility.                                                                                              |
| `get_computed_style`       | `selector`, `properties` | Returns computed CSS property values. `properties` accepts up to 50 CSS property names; defaults to `display`, `visibility`, `color`, and `background-color`.                                                                                   |
| `eval`                     | `expression`   | Executes JavaScript in the page context and returns the result. Example: `"document.title"`, `"document.querySelectorAll('a').length"`.                                                                                                                    |

### Challenge detection

`detect_challenge` is for notifying a human during an authorised QA or compatibility flow, not for bypassing a verification system. It inspects only the top-level document's bounded iframe/script origins, fixed widget selectors, and published integration globals. It does not traverse cross-origin frames, click any widget, submit a response, or make a network request.

```json
{"action": "detect_challenge"}
```

Pass `"scroll_into_view": true` when a person will take over in VNC and the first visible detected frame or widget should be brought into the viewport:

```json
{"action": "detect_challenge", "scroll_into_view": true}
```

`scroll_into_view` defaults to `false`, preserving read-only detection behavior. When requested, the action scrolls only; it never clicks, focuses, solves, submits, or enters a challenge frame. The opt-in response adds `data.scrolled_into_view`, which is `true` when a detected target is in the viewport after the action. It is `false` when no visible detected target with usable geometry is available or the page cannot scroll it into view.

The action returns `data.status` as `"absent"`, `"present"`, or `"unknown"`. `unknown` means that the page snapshot could not be evaluated; it is deliberately not reported as absence. When present, `matches` contain `vendor`, `confidence` (`"high"`, `"medium"`, or `"low"`), `locations`, fixed `evidence` labels, and bounded frame/element bounding boxes where available. Frame evidence includes only host plus path. The action excludes query strings, fragments, site keys, response values, element text, attributes, and HTML.

Known high-confidence markers cover Cloudflare Turnstile, Google reCAPTCHA, hCaptcha, Friendly Captcha, ALTCHA, Arkose, AWS WAF, and GeeTest where their documented container, iframe/script origin, or global is present. A visible generic captcha/challenge cue is returned as vendor `"unknown"` at low confidence. Detection is intentionally best-effort. An `absent` result does not guarantee that a custom, delayed, or server-side challenge will not appear later.

Use it inside a script with the existing output condition:

```yaml
steps:
  - action: detect_challenge
    scroll_into_view: true
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

### Wait conditions

Use these instead of `sleep`. They wait for **actual page state**, not arbitrary time.

| Action                  | Parameters                     | What It Does                                                                                                                                                    |
| ----------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wait_for_element`      | `selector`, `state`, `timeout` | Waits for an element to reach a state. `state`: `"visible"` (default), `"hidden"`, `"attached"`, `"detached"`. `timeout` in seconds (default 30). CSS or XPath. |
| `wait_for_text`         | `text`, `timeout`              | Waits for specific text to appear anywhere on the page (substring match).                                                                                       |
| `wait_for_url`          | `url`, `timeout`               | Waits for the URL to match a glob pattern. `*` matches any chars except `/`, `**` matches everything. Example: `"**/dashboard"`.                                |
| `wait_for_network_idle` | `timeout`                      | Waits until no network requests have been made for 500ms. Useful for pages that load content dynamically.                                                       |

### Tab management

| Action       | Parameters          | What It Does                                                                                            |
| ------------ | ------------------- | ------------------------------------------------------------------------------------------------------- |
| `list_tabs`  | none                | Returns all open tabs with their index, URL, and which one is active.                                   |
| `new_tab`    | `url`, `wait_until`, `timeout`, `retry_count`, `retry_delay` | Opens a new tab (becomes the active tab). Optionally navigates to a URL with the same explicit navigation controls as `goto`. |
| `switch_tab` | `index`             | Switches the active tab by index (0-based). All subsequent actions operate on the active tab.           |
| `close_tab`  | `index` (optional)  | Closes a tab. If no index, closes the active tab. After closing, the last remaining tab becomes active. |

Firefox opens each tab as its own OS window, so `new_tab` / `switch_tab` / `close_tab` also **foreground** the target tab's window (the display, screenshots, recordings, and VNC follow the active tab) and transfer OS-level keyboard focus into its content so `send_key` / `system_type` reach the switched tab. Foregrounding uses a brief off-screen mouse gesture that renders no context menu (clean in recordings).

Closing the last tab leaves Firefox with no window, and Firefox cannot open a tab from that state. `list_tabs` then reports `count: 0`. The next action that needs a page, including `new_tab` and `GET /screenshot/browser`, relaunches the browser on the same profile and runs on the fresh tab. The relaunch takes a few seconds. Cookies and storage survive it, but the closed page's state does not.

### Dialog handling

Browsers have modal dialogs (alert, confirm, prompt). By default, the browser **auto-accepts** dialogs (clicks OK). Use `handle_dialog` to dismiss or provide prompt text.

| Action            | Parameters       | What It Does                                                                                                                                                                                                                                                                                                                                  |
| ----------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handle_dialog`   | `accept`, `text` | Pre-configures how the browser handles the **next** dialog. `accept`: `true` = click OK, `false` = click Cancel. `text`: response for prompt dialogs. **Call this before the action that triggers the dialog.** If you don't, the dialog is auto-accepted (clicks OK). You only need this if you want to dismiss (Cancel) or provide prompt text. |
| `get_last_dialog` | none             | Returns info about the last dialog: `type` (alert/confirm/prompt/beforeunload), `message`, `default_value`, `buttons`.                                                                                                                                                                                                                        |

### Cookies

| Action           | Parameters                           | What It Does                                                                                                                                                    |
| ---------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_cookies`    | `urls` (optional)                    | Returns all browser cookies. Optionally filter by URL list. Each cookie includes name, value, domain, path, httpOnly, secure, etc.                              |
| `set_cookie`     | `name`, `value`, `url`/`domain`, ... | Sets a cookie. Needs at minimum: `name`, `value`, and either `url` or `domain`. Accepts all standard cookie fields (path, httpOnly, secure, sameSite, expires). |
| `delete_cookies` | none                                 | Clears all cookies from the browser context.                                                                                                                    |

### Storage

Access the page's localStorage and sessionStorage. Storage is per-origin, so you must be on the right page.

| Action          | Parameters             | What It Does                                                                      |
| --------------- | ---------------------- | --------------------------------------------------------------------------------- |
| `get_storage`   | `type`                 | Returns all items as key-value pairs. `type`: `"local"` (default) or `"session"`. |
| `set_storage`   | `type`, `key`, `value` | Sets a single key-value pair.                                                     |
| `clear_storage` | `type`                 | Clears all items.                                                                 |

### Downloads & uploads

| Action              | Parameters              | What It Does                                                                                                                                                                                                           |
| ------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_last_download` | none                    | Returns info about the most recent file download: `url`, `filename`, and local `path` inside the container. Returns `null` if nothing downloaded yet.                                                                  |
| `upload_file`       | `selector`, `file_path` | Programmatically sets a file on an `<input type="file">` element without opening the OS file picker. File must exist inside the container (use `docker cp` to copy files in). You still need to submit the form after. |

### Network logging

Record all HTTP requests and responses the page makes. Useful for finding API endpoints, debugging, or verifying resources loaded.

| Action                 | Parameters | What It Does                                                                                                                     |
| ---------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `enable_network_log`   | none       | Starts recording. Each entry captures: URL, method, resource type (fetch/document/script/image/etc), status code, and timestamp. |
| `disable_network_log`  | none       | Stops recording. Already-captured entries remain.                                                                                |
| `get_network_log`      | none       | Returns all captured entries with their count.                                                                                   |
| `clear_network_log`    | none       | Deletes captured entries. Keeps logging on if it was on.                                                                         |
| `getclear_network_log` | none       | Returns all captured entries and clears the log in one call.                                                                     |

### Console logging

Capture `console.log`, `console.error`, `console.warn`, and other console output from the page. Useful for debugging page behavior or extracting data logged by scripts.

| Action                 | Parameters | What It Does                                                                                                                                                              |
| ---------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enable_console_log`   | none       | Starts capturing console messages. Each entry has: `type` (log/error/warning/info/debug/trace/table/etc), `text`, `location`, `timestamp`. |
| `disable_console_log`  | none       | Stops capturing. Already-captured entries remain.                                                                                                                         |
| `get_console_log`      | none       | Returns all captured entries with their count.                                                                                                                            |
| `clear_console_log`    | none       | Deletes captured entries. Keeps capturing on if it was on.                                                                                                                |
| `getclear_console_log` | none       | Returns all captured entries and clears the log in one call.                                                                                                              |

### Display & calibration

| Action             | Parameters | What It Does                                                                                                                                                                                                                                                                                                                                               |
| ------------------ | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `calibrate`        | none       | Measures the mapping between viewport coordinates (from `get_interactive_elements`) and screen coordinates (what PyAutoGUI uses) and returns it as `window_offset`. You do not need to call it: every OS-level pointer action and every `viewport` recording re-measures the offset first, so fullscreen, new tab windows, and browser relaunches are handled. |
| `get_resolution`   | none       | Returns the virtual display resolution (width, height).                                                                                                                                                                                                                                                                                                    |
| `set_color_scheme` | `scheme`   | Makes every open tab, and every tab opened later, report `dark`, `light`, or `no-preference` for `prefers-color-scheme`, so sites that follow the system theme switch to it. Firefox has no `no-preference` media value, so pages see `light` for it. `default` turns this off. Takes effect at once and survives a browser relaunch. It changes the page content only, not the browser's own toolbar. Returns `scheme` and `pages`, the number of open tabs it was applied to. |
| `enter_fullscreen` | none       | Puts the browser in fullscreen mode (hides address bar and window chrome).                                                                                                                                                                                                                                                                                 |
| `exit_fullscreen`  | none       | Exits fullscreen mode.                                                                                                                                                                                                                                                                                                                                     |

### Scrolling

| Action                       | Parameters                          | What It Does                                                                                                                                                                                                                                         |
| ---------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scroll_to_bottom`           | `delay`                             | Scrolls the entire page top-to-bottom using **JavaScript** (`window.scrollBy`), then back to top. Useful for triggering lazy-loaded content. `delay` (default 0.4s) is the pause between scroll steps. This is fast but uses JS, not OS-level input. |
| `scroll_to_bottom_humanized` | `min_clicks`, `max_clicks`, `delay`, `max_gestures`, `return_to_top`, `notch_gap`, `scroll_back`, `drift` | Same goal as above but scrolls like a reader with the **real mouse wheel**: gestures of `min_clicks`-`max_clicks` notches (integers 1-50, default 2-6, `min_clicks` not above `max_clicks`), a reading pause around `delay` seconds (default 0.5) after each, idle pointer drift during some pauses, and an occasional short scroll back up. Stops when two gestures in a row move nothing (lazy-loaded pages get one retry) or after `max_gestures` (1-10000, default 500). With `return_to_top` (default `true`) it goes back up with the Home key, or with upward wheel flicks when a text field has focus, instead of a script jump. Returns `gestures`, `scroll_backs`, `reached_bottom`, and `returned_to_top` (false if the top was not reached within 3 seconds). |

### Utility

| Action            | Parameters                                                  | What It Does                                                                                                                                                                                                                                 |
| ----------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_script`      | `steps` or `yaml`, `name`, `on_error`                       | Execute multiple actions as a single atomic request. `steps` may contain action dicts plus `if`, bounded `repeat`, and bounded `while` control nodes. `yaml` uses the same format as `--script` mode. `on_error`: `"stop"` (default) or `"continue"`. Steps with `output_id` collect results. See [script-mode.md#control-flow](script-mode.md#control-flow).     |
| `ping`            | none                                                        | Health check that returns `"pong"` and the current page URL.                                                                                                                                                                                 |
| `sleep`           | `duration`                                                  | Pauses for N seconds. Prefer `wait_for_element` or `wait_for_text` when waiting for page content.                                                                                                                                            |
| `close`           | none                                                        | Shuts down the browser. The container stops after this.                                                                                                                                                                                      |
| `save_screenshot` | `output_id`, `path`, `type`, `width`, `height`, `whLargest` | Captures a screenshot. `type`: `"browser"` (default) or `"desktop"`. Optional `path` to also write PNG to disk. In script mode, `output_id` collects the base64 PNG into the outputs dict. Supports resize via `width`/`height`/`whLargest`. |
| `get_virtual_media_state` | none | Reports virtual-media configuration and, in dynamic mode, each active source basename and source revision. |

### Virtual camera and microphone

Set `VIRTUAL_CAMERA_FILE` and/or `VIRTUAL_MICROPHONE_FILE` to make the configured video or audio file available as the corresponding track returned by page `navigator.mediaDevices.getUserMedia()`. Both files must resolve inside `VIRTUAL_MEDIA_DIR` (default `/media`), normally a read-only bind mount. The browser must restart after changing this static source configuration.

With `VIRTUAL_MEDIA_DYNAMIC=true`, these actions switch a file-backed source while preserving the identities of tracks already returned to a page. Dynamic mode is disabled by default.

| Action | Parameters | What It Does |
| ------ | ---------- | ------------ |
| `set_virtual_media_source` | `kind`, `source` | Selects an existing source for `kind` (`"camera"` or `"microphone"`). `source` is a relative name that resolves to a regular file inside `VIRTUAL_MEDIA_DIR`. |
| `upload_virtual_media` | `kind`, `filename`, `content_base64`, `activate` | Stores strict base64 file content under `VIRTUAL_MEDIA_DIR` for `kind` (`"camera"` or `"microphone"`). `filename` must be a safe basename whose declared media type matches `kind`; it supplies only the extension. The response returns a generated collision-safe stored basename, and no existing named source is overwritten. Before storage or activation, `ffprobe` must confirm the decoded content contains the requested video or audio stream. `activate` optionally switches to the uploaded source immediately. Uploads are limited by `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` (50 MiB by default). |

Both actions require dynamic mode. Uploads also require a writable `VIRTUAL_MEDIA_DIR`. They are ordinary authenticated actions: when `AUTH_TOKEN` is set, include `Authorization: Bearer <token>` as for every other action. The controller accepts only approved files under the configured media directory. It never accepts arbitrary host paths, remote URLs, WebSocket streams, or other live ingress. `get_virtual_media_state` reports the active source basename and revision without exposing source paths or upload bytes.

This virtualizes `getUserMedia()` streams only. It does not add devices to `enumerateDevices()`. If a request asks for a kind without a configured virtual source, it fails with `NotFoundError` instead of using a native device. Virtual tracks retain the configured file's format and do not emulate incompatible exact media constraints. Use `get_virtual_media_state` to confirm the configured source types and dynamic source state.

### Screen recording

ffmpeg `x11grab` against the Xvfb display. Captures actual rendered pixels including the OS-level mouse cursor (PyAutoGUI moves are visible). One active recording per container; a second `start_recording` while active returns an error. Requires `/recordings` mounted as a host volume. If it is not mounted (or not writable), `start_recording` fails fast.

| Action              | Parameters     | What It Does                                                                                                                                                                                                                                                                              |
| ------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `start_recording`   | `mode`, `fps`, `show_cursor` | Begin recording to `/recordings/.tmp-<id>.mp4`. The action returns only after ffmpeg creates the temporary MP4, with a 10-second startup limit. `mode`: `"window"` (default, full Camoufox window including chrome), `"viewport"` (crops chrome at the page position from `mozInnerScreenX/Y`, measured when the recording starts), `"desktop"` (entire Xvfb screen). `fps`: 1 to 60, default 15. `show_cursor`: bool, default `true`. Set it to `false` to record without the OS-level mouse cursor (`ffmpeg -draw_mouse 0`). Returns `recording_id`, `tmp_path`, `show_cursor`, and `capture_size`. |
| `stop_recording`    | `slug`         | Stop the active recording, finalize, and rename tmp file to `/recordings/<slug>.mp4`. `slug` must match `[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}` (no path traversal). If `<slug>.mp4` already exists, the file is saved as `<slug>-2.mp4`, `<slug>-3.mp4`, etc. Returns `path`, `duration_s`, `size_bytes`. |
| `recording_status`  | none           | `{"active": true, "recording_id", "mode", "started_at", "elapsed_s", "tmp_path"}` when recording, `{"active": false}` otherwise.                                                                                                                                                          |

Mount and run:

```bash
mkdir -p ./recordings
docker run -d -p 8080:8080 \
  -v ./recordings:/recordings \
  psyb0t/stealthy-auto-browse
```

Script-mode pattern with multiple start/stop pairs in one run. All files land in `./recordings/`:

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
- `viewport` mode measures the page's current screen position (`mozInnerScreenX/Y` from Firefox) when the recording starts and uses it as the crop top-left, so it crops correctly after fullscreen or tab changes. If the position cannot be read, it uses the last measured offset.
- In cluster mode (`NUM_REPLICAS > 1`), recording actions are only usable from inside a `run_script` call. Outside it, the cluster restriction rejects them. Start and stop must live in the same `run_script` so they hit the same browser instance.
- Crashes and forced shutdown abort any active recording and remove its tmp file. At next startup, the app sweeps `/recordings/.tmp-*.mp4` files older than 1h.

## Humanized input

The OS-level actions above plan every movement and keystroke from published human motor data, then replay it through X11 with each event on its own schedule. Defaults are tuned to look like one consistent person per container; every option below is per request and optional.

**Mouse.** Each move is a sum of overlapping sigma-lognormal strokes. A curved main stroke lands a little off, a mid-course re-aim sometimes follows, and one or two corrections finish exactly on the target. Movement time follows Fitts's law (longer moves and smaller targets take longer), speed peaks about a third of the way in, roughly a third of moves overshoot and correct, and a sub-pixel hand tremor rides on top. Events go out at a 125 Hz USB-mouse rate. The defaults were fitted to the SapiMouse dataset of 120 people.

**Clicks.** After arriving, the press waits a short human pause (usually ~40 ms, sometimes a few hundred while "checking" the target), holds the button ~85-125 ms, and occasionally slips 1-2 px while pressed (Balabit dataset). The aim point is scattered inside the target, never its exact centre.

**Typing.** Gaps between key presses are right-skewed (lognormal), shorter for common letter pairs and alternating hands, longer for the same finger and at word starts, with a slow drift in pace. Every key has its own hold, so fast typists press the next key before releasing the last; the same key never overlaps itself. Shift is pressed with the opposite hand well before the capital and held across runs of capitals. Each container picks a typist speed between slow and very fast (Aalto 136M keystrokes study).

**Wheel.** Scrolling happens in gestures of slow controlled notches or fast flicks that speed up and slow down. Two notches are never less than 15 ms apart.

### Pointer options

Accepted by `system_click`, `mouse_click`, and `mouse_move` (the click options apply to clicks only).

| Option        | Range       | Default            | Effect |
| ------------- | ----------- | ------------------ | ------ |
| `w`, `h`      | > 0         | small control      | Target size in px. The landing point is scattered inside it and movement time follows its size. |
| `duration`    | > 0         | Fitts's law        | Rescale the whole move to this many seconds. Wins over `speed`. |
| `speed`       | 0.1-10      | 1 (session pace)   | Speed multiplier; 2 moves twice as fast. |
| `curvature`   | 0-5         | 1                  | How much the main stroke bends; 0 makes it straight. Corrections keep their own small bend. |
| `tremor`      | 0-5 px      | 0.35               | Hand tremor amplitude. |
| `click_hold`  | 0.01-5 s    | session ~85-125 ms | Median time the button stays down. |
| `click_delay` | 0-10 s      | human mix          | Median pause between arriving and pressing. |

### Typing options

Accepted by `system_type`. `send_key` accepts `key_hold` (default median 100 ms, held 50-250 ms), or `instant: true` to skip human timing entirely; the two cannot be combined.

| Option      | Range      | Default              | Effect |
| ----------- | ---------- | -------------------- | ------ |
| `interval`  | > 0 s      | session typist speed | Median gap between key presses; also picks the matching typist's spread and key overlap. |
| `key_hold`  | 0.01-2 s   | session ~75-145 ms   | Median time each key is held. |
| `rollover`  | 0-1        | by typist speed      | When the next key comes before the last one is released, the chance of keeping that overlap instead of releasing first. |
| `typos`     | true/false | false                | Make and correct occasional typos. |
| `typo_rate` | 0-0.2      | 0.02 when typos      | Typos per letter. Setting it turns typos on; `typos: false` with a non-zero `typo_rate` is an error. |

#### Typos

Typos are always corrected. A neighbouring key, a swapped pair, or a doubled or missed letter is noticed right away about half the time, otherwise one to three keys later or at the end of the word, then erased with Backspace and retyped, so the final text is exactly what you asked for. They are only made in a focused plain `text`/`search` input or a `textarea`. They are switched off, and the response says why in `typos_disabled_reason` (`no_focused_text_field`, `field_type`, `contenteditable`, `maxlength`, `pattern`, `inputmode`, `autocomplete`, or `control_characters`), for password, email, number, and other typed inputs, fields with `maxlength` or `pattern`, numeric/tel/email/url `inputmode`, one-time-code, payment, password, email, phone, and URL `autocomplete`, contenteditable editors, text containing a tab, and newlines in a single-line input. After typing with typos, the field's value is checked; if a page script changed it so it no longer matches, the action fails with `typed value does not match the requested text after typo corrections`.

### Wheel options

| Option        | Actions                                   | Range       | Default          | Effect |
| ------------- | ----------------------------------------- | ----------- | ---------------- | ------ |
| `notch_gap`   | `scroll`, `scroll_to_bottom_humanized`    | 0.015-5 s   | flick/controlled | Median seconds between wheel notches. |
| `scroll_back` | `scroll_to_bottom_humanized`              | 0-1         | 0.06             | Chance per gesture of scrolling back up a little (at most twice per page). |
| `drift`       | `scroll_to_bottom_humanized`              | 0-1         | 0.4              | Chance per reading pause of drifting the pointer. |

Out-of-range values are rejected with an error naming the option before any input is sent. Numeric strings such as `"250"` are accepted where numbers are expected. `typos`, `instant`, and `return_to_top` must be JSON booleans; `"true"` is rejected.

OS-level input runs in a worker thread, so `/health`, screenshots, and MCP stay responsive while a long `system_type` or scroll plays. Actions themselves still run one at a time.

## Screenshots

Both screenshot endpoints support resize parameters. The default resolution is 1920x1080, which is a big image. You almost always want to resize.

```bash
# Resize to 512px on the longest side (best default: keeps aspect ratio and size small)
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
