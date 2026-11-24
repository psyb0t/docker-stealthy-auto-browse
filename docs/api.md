---
title: HTTP API reference
description: Browser actions, element targets, authentication, events, clipboard formats, recording, media sources, and request limits.
sidebar_position: 4
---

# HTTP API reference

## Endpoints

| Endpoint              | Method | What It Does                                             |
| --------------------- | ------ | -------------------------------------------------------- |
| `/`                   | POST   | Execute any browser action (see Actions reference below) |
| `/screenshot/browser` | GET    | Browser viewport as PNG (what the page looks like)       |
| `/screenshot/desktop` | GET    | Full virtual desktop as PNG (including browser chrome)   |
| `/state`              | GET    | Current URL, page title, and window offset as JSON, plus the resolution, handoff state and input surface |
| `/events`             | GET    | Authenticated browser event stream as SSE                |
| `/health`             | GET    | Returns `ok` while the HTTP server is serving; not a browser health probe. |
| `/mcp/`               | POST   | MCP (Model Context Protocol) Streamable HTTP endpoint    |

## Authentication

If `AUTH_TOKEN` is set, all requests (except `/health`) require authentication. Query-string `auth_token` values are rejected. This authentication does not cover noVNC; see [installation and security](installation.md).

```
Authorization: Bearer <token>
```

## Request correlation headers

Every request gets a `trace_id` (logged on every line of the server's JSON log for that request) and a `request_id`. Both come back in the response so you can correlate your side with server logs.

- **`X-Request-Id`**: pass an incoming value (shape `[A-Za-z0-9._-]{1,64}`) and the server logs that exact ID and echoes it back. If missing or invalid, the server mints a fresh UUID4-hex and uses that. Use it for end-to-end tracing from your client (n8n, MCP, curl) through to the browser action.
- **`X-Trace-Id`**: the server always sets it on the response (UUID4 hex). Find any log line for this request by grepping the JSON log for the trace ID.

## Auto-recovery from browser crashes

If Camoufox/Firefox dies mid-session (OOM, segfault, external SIGKILL), the next browser-dependent request triggers an automatic relaunch. The server tears down the dead context and runs `launch_persistent_context` again using its profile. Persisted cookies and fingerprint data can survive; unsaved state and a usable login are not guaranteed after a crash. The recovery request includes browser startup time.

Crash diagnostics land in the JSON log at `WARNING` whenever recovery fires: available kernel OOM messages, `/proc/meminfo`, `/proc/loadavg`, and the surviving process inventory. Kernel logs may be unavailable inside the container. Check memory limits and actual container state rather than assuming every crash is an OOM kill. Recovery handles a dead browser while the application remains alive; it does not restart a terminated container.

## Cluster mode restriction

When running in cluster mode (`NUM_REPLICAS > 1`), `POST /` accepts only the `run_script`, `ping`, and `sleep` actions. All other actions return an error directing you to use `run_script`. This prevents stale content bugs from calls hitting different browser instances. The GET endpoints (`/screenshot/*`, `/state`, `/events`) still work and land on whichever replica the proxy picks. Do not leave `begin_handoff` active at the end of a script: the next `run_script` is refused by the handoff, and `end_handoff` is refused by the cluster restriction, so only a browser relaunch clears it; in practice, restart that replica's container. See [cluster-mode.md](cluster-mode.md#script-only-mode-v100) for details.

## Request serialization

HTTP action requests and MCP actions share one execution lock per browser. Additional actions wait for it. Health, state, event streams, and screenshot routes are outside that lock; browser-dependent reads can still wait on browser RPCs or recovery.

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

### Failure responses

`POST /` expects a JSON object with an `action`. Action failures, invalid JSON and a body that is not a JSON object all use the same envelope with `success: false` and an `error` string while retaining HTTP 200. Authentication failures return HTTP 401. Invalid event-stream cursors return HTTP 400. `GET /screenshot/browser` returns HTTP 400 when no page is active or when `selector` and `full_page` are combined, HTTP 500 when the selector matches no element or several elements or capture fails, and HTTP 404 for an unknown screenshot name. Screenshot failures are plain text rather than the action envelope. Do not assume curl's HTTP success proves an action succeeded; read `success`.

## Example: login flow with OS-level input

```bash
API=http://localhost:8080

# 1. Navigate to login page
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "goto", "url": "https://example.com/login"}'

# 2. Find all interactive elements (buttons, inputs, links)
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "get_interactive_elements"}'
# Returns elements with x, y (centre), w, h, text, a selector and element_id

# 3. Click the email field and paste the address (x, y, w, h from step 2)
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "paste_text", "x": 400, "y": 200, "w": 280, "h": 36, "text": "test.user@example.com"}'

# 4. Click the password field and paste the password
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "paste_text", "x": 400, "y": 260, "w": 280, "h": 36, "text": "not-a-real-password!"}'

# 5. Press Enter to submit
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "send_key", "key": "enter"}'

# 6. Wait for redirect to dashboard
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "wait_for_url", "url": "**/dashboard", "timeout": 15}'

# 7. Verify we're logged in
curl -X POST $API -H 'Content-Type: application/json' \
  -d '{"action": "get_text"}'
```

The clicks, pastes, and key presses in this example use OS-level input with modeled paths, holds, and rhythm. The coordinates are illustrative; resolve the real target positions before using them, or pass an [element target](#element-targets) instead. This does not guarantee that the target site accepts the session or classifies it as human.

## Actions reference

All actions are sent as `POST /` with JSON body `{"action": "name", ...params}`.

### Element targets

Actions that operate on one element accept exactly one of `selector`, `element_id`, or `role`. A role target also accepts the accessible `name`; `role` must be a supported ARIA role. Selector and role targets accept `frames`, an ordered list of iframe selectors from the top page to the target frame. Frame chains support up to eight levels. CSS selectors pierce open shadow roots. Closed shadow roots are inaccessible. Selectors use Playwright selector syntax: CSS by default, XPath with `xpath=` or a leading `//` or `..`, and other Playwright engines such as `text=` also work.

A target must match exactly one element, or the action fails with `target matched N elements; expected exactly 1`. Two exceptions act on the first match without complaining: `click`, `fill` and `type` with a plain `selector` and no `frames`, and `wait_for_element` with a selector or role target.

`create_element_reference` returns an opaque `element_id` that remains tied to the same DOM node while it is attached. References expire after five minutes, are bounded to 512 per process, and become stale after top-level navigation, page close, browser relaunch, or explicit release. `get_element`, DOM input, waits, uploads, element screenshots, and OS-level target actions accept these references. `get_accessibility_tree` accepts selector or role targets, but not `element_id`.

### Navigation

| Action    | Parameters                                                  | What It Does |
| --------- | ----------------------------------------------------------- | ------------ |
| `goto`    | `url`, `wait_until`, `referer`, `timeout`, `retry_count`, `retry_delay` | Navigate to a URL. `wait_until`: `"domcontentloaded"` (default), `"load"`, `"networkidle"`. `referer`: set the HTTP Referer header. |
| `refresh` | `wait_until`, `timeout`, `retry_count`, `retry_delay`      | Reload the current page. Returns URL and title. |

Navigation never relies on a Playwright default. Every attempt gets the app's explicit `timeout` of 30 seconds unless you set another value. `retry_count` defaults to 1, so a timed-out navigation gets one retry. Set it to 0 to disable retries. `retry_delay` defaults to 1 second and doubles before each further retry. `timeout` must be 0.1-120 seconds, `retry_count` 0-2, `retry_delay` 0-10 seconds, and the whole worst-case attempt sequence must fit within 120 seconds. Only Playwright navigation timeouts retry. Other navigation failures return immediately.

### System input (OS-level)

These actions drive the real X11 mouse and keyboard with timing modeled on how people actually move, click, type, and scroll. See [Humanized input](#humanized-input) for the model, the defaults, and every tuning option. Every action here that takes `x` and `y` also accepts an [element target](#element-targets) instead; the element's current box supplies `x`, `y`, `w` and `h`.

| Action         | Parameters                                                                  | What It Does |
| -------------- | --------------------------------------------------------------------------- | ------------ |
| `system_click` | `x`, `y`, `w`, `h` or element target, `duration`, plus [pointer options](#pointer-options) | Moves the mouse to the target centred on viewport `x`, `y` along a human path, then clicks with a human pause and button hold. Pass the element's `w` and `h` (from `get_interactive_elements`) so the click lands at a human offset inside it, never dead centre. **Last resort.** Prefer `click` with a CSS selector. The screen offset is re-measured before every move, so no `calibrate` call is needed. Returns `landed_at` with the viewport point that was clicked. |
| `mouse_move`   | `x`, `y`, `w`, `h`, `duration`, `speed`, `curvature`, `tremor`              | Moves the mouse like `system_click` but does **not** click. Use to hover over elements (dropdown menus, tooltips). Returns `landed_at`. |
| `mouse_click`  | `x`, `y` (optional), `w`, `h`, plus [pointer options](#pointer-options)     | With `x`, `y`: moves there like `system_click`, then clicks. Without them: clicks where the pointer is, with the same human press and hold. Give both `x` and `y` or neither. Returns `clicked_at` (the requested point, or `"current"`) and `landed_at`. |
| `system_type`  | `text`, `interval`, `typos`, `typo_rate`, `key_hold`, `rollover`            | Types text with **real OS keystrokes**: each key is held and released, fast typists overlap keys, Shift goes down before capitals, and the rhythm follows the key pair. Characters outside the US layout (é, ñ, €) are typed too. `typos: true` makes and corrects occasional typos on plain text fields only (see [Typos](#typos)). You must focus an input field first: a `selector` or other target here does not focus the field, so click it or use `paste_text` first. Returns `typed_len`, `typos` (true when typos were enabled for the field), and `typos_disabled_reason` when typos were requested but refused. |
| `send_key`     | `key`, `key_hold`, `instant`                                                | Sends a keyboard key or combo with a human hold. `instant: true` presses and releases at once with no human timing, as before 3.0.0. It is faster but easy for a page to spot. Examples: `"enter"`, `"tab"`, `"escape"`, `"backspace"`, `"ctrl+a"`, `"ctrl+shift+t"`. Combo keys go down in order and come up in reverse. Uses PyAutoGUI key names. Keys go to the focused element; a target passed here focuses nothing. |
| `scroll`       | `amount`, `x`, `y`, `notch_gap`                                             | Scrolls `amount` mouse wheel notches (a non-zero integer, default -3), one at a time with a human rhythm. Amounts above 4 are split into several gestures with short pauses. **Negative = scroll down**, positive = scroll up. If `x`, `y` are provided (both or neither), moves the mouse there first (useful for scrolling inside a specific element). |
| `human_pause`  | `duration`, `x`, `y`, `w`, `h`, `drift`, `drift_interval`, `drift_distance`, `speed`, `curvature`, `tremor` | Represents visible reading, review, or composition time. It optionally parks the pointer inside a viewport rectangle, remains mostly still, and makes sparse bounded human-shaped drifts. It blocks for the requested time and never starts background movement. Use `sleep` for ordinary waiting. Returns `duration`, `elapsed`, `drifts`, `parked`, and `landed_at`. |
| `system_hover` | element target, plus [pointer options](#pointer-options) | Resolves the element's current viewport rectangle and moves the real pointer into it. |
| `system_double_click` | element target, plus [pointer options](#pointer-options) | Resolves the target, moves there, and sends two real left-button clicks with a valid double-click interval. |
| `system_right_click` | element target, plus [pointer options](#pointer-options) | Resolves the target and sends a real right-button click. |
| `system_mouse_down` | optional `x`, `y` or element target, `button` | Moves to the point or target along a human path when supplied, then holds the button. `button` is `left` (default), `middle` or `right`. |
| `system_mouse_up` | optional `x`, `y` or element target, `button` | Moves to the point along a human path at the session's default pace when supplied (`w`, `h`, `duration` and per-request pointer options are not applied), then releases the button. `button` is `left` (default), `middle` or `right`. |
| `system_drag` | `source`, `target`, `speed`, `curvature`, `tremor` | Resolves source and target objects, then performs a real held-button drag with humanized movement between their centres. Only `speed`, `curvature` and `tremor` apply; `w`, `h`, `duration` and the click options are ignored. |

### Playwright input (DOM events, use these first)

| Action  | Parameters                  | What It Does                                                                                                                                                              |
| ------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `click` | `selector` or element target | **Preferred click method.** Clicks an element by CSS selector or XPath (`xpath=//button[@id='submit']`). Fast and reliable. Only fall back to `system_click` if the site actively detects and blocks DOM event injection. |
| `fill`  | `selector` or element target, `value` | Sets an input field's value instantly. Clears existing content first. Fast but detectable, because it doesn't generate individual keystroke events.                       |
| `type`  | `selector` or element target, `text`, `delay` | Types into an element character-by-character via Playwright. Middle ground between `fill` (instant) and `system_type` (OS-level). `delay` defaults to 0.05s between keys. |
| `hover` | element target | Hovers a target through Playwright. |
| `double_click` | element target | Double-clicks a target through Playwright. |
| `right_click` | element target | Right-clicks a target through Playwright. |
| `drag_and_drop` | `source`, `target` | Drags one target object to another through Playwright. |

### Page inspection

| Action                     | Parameters     | What It Does                                                                                                                                                                                                                                               |
| -------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_interactive_elements` | `visible_only` | Scans the page and returns interactive elements with viewport coordinates, dimensions, text, selector, visibility, and reusable `element_id` where a stable handle can be created. `visible_only` defaults to `true`. The `selector` is a best-effort guess: `#id`, a unique class, `name`, a test or ARIA attribute, or, failing those, an absolute path such as `/html[1]/body[1]/div[2]/button[1]`. That path has no `xpath=` prefix and an `#id` is not CSS-escaped, so either can fail in `click` or `get_element`; add `xpath=` to the path. Only the class guess is checked for uniqueness, so an `#id`, `name` or attribute selector can match several elements (every radio button in a group shares `input[name="..."]`). The `element_id` is the first match of `selector`, so for such a row it can belong to another element, and an element whose selector does not parse gets none. For those rows use `x`, `y`, `w`, `h` with OS-level input. |
| `get_text`                 | none           | Returns all visible text from the page body (truncated to 10,000 chars). Usually the first thing to call after navigating. It tells you what's on the page without a screenshot.                                                                           |
| `get_html`                 | none           | Returns the full HTML source of the page. Use when `get_text` doesn't give enough structure.                                                                                                                                                               |
| `get_page_info`            | none           | Returns the current URL, title, ready state, viewport/document dimensions, and scroll position.                                                                                                                                                           |
| `detect_challenge`         | `scroll_into_view` | Read-only best-effort detection of documented challenge integrations and conservative visible generic cues. Optional viewport reveal never clicks, solves, focuses, or enters a frame. |
| `solve_captcha`            | `task`, `timeout`, `poll_interval` | Submit an Anti-Captcha task using the server-side configured key and wait for the provider solution. Requires `CAPTCHA_SOLVER_PROVIDER=anticaptcha`. See [Anti-Captcha API tasks](#anti-captcha-api-tasks). |
| `get_element`              | element target | Returns one matching element's reusable ID, tag, text, attributes, bounding box, and visibility. |
| `get_elements`             | `selector`, `frames`, `limit` | Returns summaries and reusable IDs for up to `limit` matches. `limit` is 1 through 100 and defaults to 20. |
| `get_computed_style`       | `selector`, `properties` | Returns computed CSS property values. `properties` accepts up to 50 CSS property names; defaults to `display`, `visibility`, `color`, and `background-color`.                                                                                   |
| `eval`                     | `expression`, `world` | Executes JavaScript in the page and returns the result. Example: `"document.title"`, `"document.querySelectorAll('a').length"`. `world` is `page` (default) or `isolated`. `page` runs in the page's own JavaScript world and can read globals the site defines, such as `window.__NEXT_DATA__`. `isolated` sees the DOM but not the page's variables, and page scripts cannot observe or hook it. Use `isolated` for DOM reads on sites that watch for automation. |
| `create_element_reference` | element target | Returns metadata and a reusable opaque `element_id`. |
| `release_element_reference` | `element_id` | Disposes one stored reference. |
| `get_accessibility_tree` | optional selector or role target | Returns Playwright's ARIA snapshot for the target or page body. |
| `get_by_role` | `role`, `name`, `frames`, `limit` | Returns matching accessible elements and reusable IDs. |

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

The action returns `data.detected`, a boolean that is `true` only when a match was found, and `data.status` as `"absent"`, `"present"`, or `"unknown"`. `unknown` means that the page snapshot could not be evaluated; it is deliberately not reported as absence, and `detected` is `false` then. When present, `matches` contain `vendor`, `confidence` (`"high"`, `"medium"`, or `"low"`), `locations`, fixed `evidence` labels, and bounded frame/element bounding boxes where available. Frame evidence includes only host plus path. The action excludes query strings, fragments, site keys, response values, element text, attributes, and HTML.

`vendor` is one of `turnstile`, `recaptcha`, `hcaptcha`, `friendlycaptcha`, `altcha`, `arkose`, `aws_waf`, `geetest`, or `unknown`. Cloudflare Turnstile, Google reCAPTCHA, hCaptcha, Friendly Captcha, ALTCHA, Arkose and AWS WAF are high confidence where their documented container, iframe/script origin, or global is present. GeeTest (by its global) and Friendly Captcha found only by its script path are medium confidence. A visible generic captcha/challenge cue is returned as vendor `"unknown"` at low confidence. Detection is intentionally best-effort. An `absent` result does not guarantee that a custom, delayed, or server-side challenge will not appear later.

### Anti-Captcha API tasks

`solve_captcha` is available through HTTP, the dedicated MCP tool, `run_script`, and YAML script mode. It accepts an [Anti-Captcha task object](https://anti-captcha.com/apidoc/task-types/RecaptchaV2TaskProxyless) without `clientKey`. The configured key and fixed provider endpoint cannot be overridden by a request.

```json
{"action":"solve_captcha","task":{"type":"RecaptchaV2TaskProxyless","websiteURL":"https://example.com","websiteKey":"EXAMPLE-DO-NOT-USE"},"timeout":120,"poll_interval":5}
```

`timeout` accepts 5 through 600 seconds and defaults to 120. `poll_interval` accepts 1 through 30 seconds and defaults to 5. The action holds the request lock while it waits, so every other action on that browser queues for up to `timeout` seconds. Provider errors and invalid result shapes fail the action without returning the configured key. The action creates a paid provider task, so callers should invoke it only after detecting a challenge and confirming authorization.

On success, `data` is Anti-Captcha's task result with `provider` (`anticaptcha`) and `taskId` added. The token is under `data.solution`, in the field the task type defines, such as `gRecaptchaResponse` for reCAPTCHA. Putting the token into the page is up to the caller.

Use it inside a script after `detect_challenge`, with an output condition:

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
        - action: solve_captcha
          task:
            type: RecaptchaV2TaskProxyless
            websiteURL: https://example.com
            websiteKey: EXAMPLE-DO-NOT-USE
          timeout: 120
          output_id: solution
```

### Wait conditions

Use these instead of `sleep`. They wait for **actual page state**, not arbitrary time.

| Action                  | Parameters                     | What It Does                                                                                                                                                    |
| ----------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wait_for_element`      | `selector` or element target, `state`, `timeout` | Waits for an element to reach a state. `state`: `"visible"` (default), `"hidden"`, `"attached"`, `"detached"`. `timeout` in seconds (default 30). CSS or XPath. |
| `wait_for_text`         | `text`, `timeout`              | Waits for specific text to appear anywhere on the page (substring match). `timeout` defaults to 30 seconds.                                                     |
| `wait_for_url`          | `url`, `timeout`               | Waits for the URL to match a glob pattern. `*` matches any chars except `/`, `**` matches everything. Example: `"**/dashboard"`. `timeout` defaults to 30 seconds. |
| `wait_for_network_idle` | `timeout`                      | Waits until no network requests have been made for 500ms. Useful for pages that load content dynamically. `timeout` defaults to 30 seconds.                    |
| `wait_for_dom_change` | element target, `condition`, `attribute`, `timeout` | Waits for `added`, `removed`, `text` (default), `attribute`, `visible`, `hidden`, or `moved`. `attribute` is required for attribute changes. `timeout` defaults to 30 seconds, at most 300. A timeout is not an error: it returns `success: true` with `changed: false`. `added` returns `changed: true` at once when the element already exists. |

`wait_for_element`, `wait_for_text`, `wait_for_url` and `wait_for_network_idle` fail with the Playwright timeout message when the timeout passes.

### Tab management

| Action       | Parameters          | What It Does                                                                                            |
| ------------ | ------------------- | ------------------------------------------------------------------------------------------------------- |
| `list_tabs`  | none                | Returns all open tabs with their index, URL, and which one is active.                                   |
| `new_tab`    | `url`, `wait_until`, `timeout`, `retry_count`, `retry_delay` | Opens a new tab (becomes the active tab). Optionally navigates to a URL with the same explicit navigation controls as `goto`. |
| `switch_tab` | `index`             | Switches the active tab by index (0-based). All subsequent actions operate on the active tab.           |
| `close_tab`  | `index` (optional)  | Closes a tab. If no index, closes the active tab. After closing, the last remaining tab becomes active. |
| `list_windows` | none | Returns visible Camoufox X windows, geometry, page URL, and active state. |
| `focus_window` | `index` | Makes the page at the index active and foregrounds its window as described below, which also moves it back to fill the screen. |
| `move_resize_window` | `index`, `x`, `y`, `width`, `height` | Moves and resizes one visible Camoufox X window. Minimum size is 100 by 100. The next `focus_window`, `new_tab`, `switch_tab` or `close_tab` undoes it. |
| `wait_for_popup` | `after_id`, `timeout` | Waits for a popup event after the event ID `after_id` (default 0) and returns it as `popup`, or `null` after `timeout` seconds (default 30). Pass the latest event ID you have seen: with the default 0 it returns the oldest retained popup at once. When the cursor is older than the retained history, it returns the `gap` record as `popup` instead. |
| `close_window` | `index` | Closes the page at the window index. |

Firefox opens each tab as its own OS window, so `new_tab`, `switch_tab`, `close_tab` and `focus_window` also **foreground** the target tab's window (the display, screenshots, recordings, and VNC follow the active tab) and transfer OS-level keyboard focus into its content so `send_key` and `system_type` reach the switched tab. To do that, the browser raises the window, moves it to the screen's top-left corner and resizes it to fill the screen, then jumps the pointer to screen point (5, 200) with no human path and sends one left click there. Before the click it adds a transparent full-viewport `div#__sab_focus_overlay` to the page, so the click lands on it and activates nothing, and removes it afterwards. A page can observe all of this: the inserted and removed element, and a trusted click after a pointer jump. Prefer staying in one tab on pages that watch input or DOM changes.

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
| `delete_cookies` | none                                 | Clears all cookies from the browser context. With `REDIS_URL` set, it also deletes the shared cookie jar and tells every replica to clear its cookies, which logs out the whole fleet. |

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
| `enable_network_log`   | `capture_request_bodies`, `capture_response_bodies`, `max_body_bytes` | Starts recording. Body capture is off by default. The per-body limit defaults to 256 KiB and may not exceed 1 MiB. |
| `disable_network_log`  | none       | Stops recording. Already-captured entries remain.                                                                                |
| `get_network_log`      | none       | Returns all captured entries with their count.                                                                                   |
| `clear_network_log`    | none       | Deletes captured entries. Keeps logging on if it was on.                                                                         |
| `getclear_network_log` | none       | Returns all captured entries and clears the log in one call.                                                                     |
| `set_network_rules` | `rules` | Replaces the whole rule set with 1 to 100 ordered rules for future requests. Each rule has a `url` pattern and an `action`: `continue`, `block`, `abort`, `redirect`, or `fulfill`. Always set `action`: validation accepts a rule without it, but the interception handler then fails on every matching request. Optional `method` and `resource_type` narrow the match. `continue` accepts `headers` (merged over the request's) and `post_data`. `block` and `abort` accept `error_code`. Redirects require an HTTP(S) `redirect_url` without credentials. Fulfill rules accept bounded status, headers, content type, and text body. The first matching rule wins. |
| `clear_network_rules` | none | Removes all interception rules. |
| `export_har` | optional `path` | Returns a bounded HAR 1.2 object assembled from the current network log. With `path`, it also writes the file inside the container, over HTTP as well as in script mode. |

Rule `url` patterns use shell-style matching against the full URL, where `*` also matches `/`, unlike the `wait_for_url` glob. Anchor the scheme and host, as in `https://www.example/*`: a `*` before the host can also match other hosts whose path contains your text. Rules affect only requests started after installation. The network log keeps the newest 5,000 entries. Sensitive request and response headers are replaced with `[REDACTED]`. URL query values whose names look like credentials are also redacted. Response bodies are base64 in network log and HAR results.

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

### Clipboard

The clipboard actions and `paste_text` come from auto-linux-desk ([clipboard reference](https://psyb0t.github.io/docker-auto-linux-desk/api/#clipboard), [entering text](https://psyb0t.github.io/docker-auto-linux-desk/api/#entering-text)); here `paste_text` also accepts an element target.

The clipboard actions use the real X11 `CLIPBOARD` selection shared by Camoufox and noVNC. Browser copy operations are visible to `get_clipboard`, and text written by `set_clipboard` can be pasted into the page with `Ctrl+V`. Clipboard contents are not written to request logs.

People paste far more than they type. Enter text with `paste_text`, or `fill` where DOM input is acceptable. `paste_text` puts the text on the clipboard, clicks the field when given a target or coordinates, and presses `ctrl+a`, `backspace` and `ctrl+v` with human timing. Use `system_type` only for a few characters: a word, a short search, a small edit, a field that blocks pasting, or a test that needs per-keystroke behavior.

| Action | Parameters | What It Does |
| ------ | ---------- | ------------ |
| `set_clipboard` | `format`, `text`, `content_base64`, `files` | Replaces one clipboard target. `format` defaults to `text`. Use `text` for `text` and `html`, strict base64 PNG in `content_base64` for `image/png`, or up to 32 absolute existing regular file paths in `files`. Text and clipboard payloads are limited to 1 MiB. File lists reject symlinks and more than 1 GiB of aggregate file size. |
| `get_clipboard` | `format` | Reads `text`, `html`, `image/png`, or `files`. PNG content returns as base64. File content is not returned. |
| `paste_text` | `text`, element target or `x`, `y`, `w`, `h`, `replace`, plus [pointer options](#pointer-options) | Enters text the way people do. Puts `text` (up to 1 MiB) on the clipboard, clicks the field with the real mouse when a target or coordinates are given, then presses `ctrl+a`, `backspace` and `ctrl+v` with human timing and irregular pauses between them. `replace: false` skips select-all and clear and pastes at the caret. Returns `pasted_len`, `replaced`, and `clicked_at` and `landed_at` when it clicked. For page text fields; a terminal binds those keys differently. The text stays on the shared clipboard; after pasting a secret, overwrite it with `set_clipboard`. |

All three work as HTTP actions, dedicated MCP tools, YAML script steps, and `run_script` steps. Cluster mode exposes only `run_script`, so place clipboard operations in the same script when they must run on one browser instance.

### Scrolling

| Action                       | Parameters                          | What It Does                                                                                                                                                                                                                                         |
| ---------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scroll_to_bottom`           | `delay`                             | Scrolls the entire page top-to-bottom using **JavaScript** (`window.scrollBy`), then back to top. Useful for triggering lazy-loaded content. `delay` (default 0.4s) is the pause between scroll steps. This is fast but uses JS, not OS-level input. |
| `scroll_to_bottom_humanized` | `min_clicks`, `max_clicks`, `delay`, `max_gestures`, `return_to_top`, `notch_gap`, `scroll_back`, `drift` | Same goal as above but scrolls like a reader with the **real mouse wheel**: gestures of `min_clicks`-`max_clicks` notches (integers 1-50, default 2-6, `min_clicks` not above `max_clicks`), a reading pause around `delay` seconds (default 0.5) after each, idle pointer drift during some pauses, and an occasional short scroll back up. Stops when two gestures in a row move nothing (lazy-loaded pages get one retry) or after `max_gestures` (1-10000, default 500). With `return_to_top` (default `true`) it goes back up with the Home key, or with upward wheel flicks when a text field has focus, instead of a script jump. Returns `gestures`, `scroll_backs`, `reached_bottom`, and `returned_to_top` (false if the top was not reached within 3 seconds). |

### Utility

| Action            | Parameters                                                  | What It Does                                                                                                                                                                                                                                 |
| ----------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_script`      | `steps` or `yaml`, `name`, `on_error`                       | Execute multiple actions as a single atomic request. `steps` may contain action dicts plus `if`, bounded `repeat`, and bounded `while` control nodes. `yaml` uses the same format as `--script` mode. `on_error`: `"stop"` (default) or `"continue"`. Steps with `output_id` collect results. The top-level `success` is `false` when the request is refused: an invalid or missing script, YAML that does not parse, or an active handoff. It is `true` even when a step failed. Check `data.success` and each `data.step_results[].error`. `data` has the same shape as the [script mode output](script-mode.md#output-json). See [script-mode.md#control-flow](script-mode.md#control-flow). |
| `ping`            | none                                                        | Health check that returns `"pong"` and the current page URL.                                                                                                                                                                                 |
| `sleep`           | `duration`                                                  | Pauses for `duration` seconds, 0 to 3600, default 1. Prefer `wait_for_element` or `wait_for_text` when waiting for page content.                                                                                                             |
| `close`           | none                                                        | Shuts down the browser. The container stops after this.                                                                                                                                                                                      |
| `save_screenshot` | `output_id`, `path`, `type`, element target, `full_page`, `width`, `height`, `whLargest` | Captures PNG only. `type` is `browser` or `desktop`. Browser capture can select one element or the full page. Element and full-page capture cannot be combined. Optional `path` writes the PNG inside the container, over HTTP as well as in scripts; the browser user must be able to write there. In a script, `output_id` collects a `data:image/png;base64,` URI. |

### Desktop actions

The desktop, the HTTP and MCP server and the humanized input come from [auto-linux-desk](https://psyb0t.github.io/docker-auto-linux-desk/), so its desktop actions also work here over HTTP and in scripts. They use screen pixels, not viewport coordinates. They have no dedicated MCP tools in this image.

| Action | What It Does |
| ------ | ------------ |
| `desktop_list_windows` | Lists top-level X11 windows with ID, title, WM_CLASS, PID, geometry and focus. |
| `desktop_focus_window`, `desktop_move_resize_window`, `desktop_set_window_state`, `desktop_close_window` | Focus, move, resize, maximize, minimize, fullscreen or close one window, picked by `id`, `title`, `wm_class` or `pid`. |
| `desktop_keybindings` | Lists the Openbox keyboard shortcuts, each with the `send_key` value that presses it. |
| `desktop_screenshot` | The whole screen as PNG, the same as `GET /screenshot/desktop`. |
| `desktop_accessibility_tree`, `desktop_accessibility_find`, `desktop_accessibility_action` | Read the controls desktop apps expose over AT-SPI2 (role, name, states, bounds, actions, text), filter them, and run a control's action or set its text without moving the mouse. See [Accessibility tree](https://psyb0t.github.io/docker-auto-linux-desk/accessibility/). |
| `list_actions` | Every registered action name. |
| `run_command`, `launch_app`, `list_launched`, `kill_launched` | Run a shell command, or start, list and stop a program. `run_command` and `launch_app` are refused unless `DESK_EXEC_ENABLED=true` (see [Configuration](configuration.md#environment-variables)), because they run anything in the container. |

See the [auto-linux-desk actions reference](https://psyb0t.github.io/docker-auto-linux-desk/api/) for every parameter and return field.

### Events and human handoff

These come from auto-linux-desk; see its [events and handoff reference](https://psyb0t.github.io/docker-auto-linux-desk/api/#events-and-handoff) for the event shape and the `/events` stream. This image adds browser events and counts page input during a handoff.

| Action | Parameters | Result and limits |
| --- | --- | --- |
| `get_events` | `after_id`, `types`, `limit` | Reads retained events. Defaults: cursor 0, all types, limit 100. Limit is 1 through 1,000. |
| `wait_for_event` | `after_id`, `types`, `timeout` | Returns matching events or an empty list on timeout. Timeout defaults to 30 seconds and must be greater than 0 and at most 300. |
| `begin_handoff` | none | Pauses automation and starts page activity listeners. Fails if already active or no page exists. |
| `handoff_status` | none | Returns active state, start time, and observed activity counts when active. |
| `end_handoff` | none | Returns duration and activity, then resumes automation. Fails if no handoff is active. |

`get_events` reads the process-local event ring, which keeps the newest 1,000 records. `wait_for_event` waits without polling. A cursor older than retained history receives a `gap` record first, whatever the `types` filter. A future cursor is rejected.

The event types are `navigation`, `popup`, `page_closed`, `dialog`, `download`, `console`, `request`, `response`, `challenge_result` (from `detect_challenge`), `handoff_started`, `handoff_ended`, `action_failure` and `gap`. `request` and `response` are published for every network request even when network logging is off, so on a busy page they push other events out of the ring within seconds. Filter with `types` and keep your cursor current.

`GET /events` streams the same records as server-sent events. Use repeated `type` query parameters to filter and `after_id` to resume. The stream uses the same Bearer authentication as the rest of the API. Event storage is process-local and is not shared across cluster replicas.

`begin_handoff` refuses every API and MCP action with `human handoff is active`, except this list, while leaving noVNC input available: `ping`, `sleep`, `list_actions`, `get_events`, `wait_for_event`, `handoff_status`, `end_handoff`, `get_text`, `get_html`, `get_page_info`, `list_tabs`, `list_windows`, `save_screenshot`, `recording_status`, `get_resolution`, `desktop_screenshot`, `desktop_list_windows`, `desktop_keybindings`, `desktop_accessibility_tree`, `desktop_accessibility_find` and `list_launched`. Other reads such as `detect_challenge`, `get_interactive_elements`, `get_element`, `eval`, `get_cookies`, the logs, the `wait_for_*` actions and `run_script` are refused too. The GET endpoints (`/screenshot/*`, `/state`, `/events`, `/health`) keep working.

`handoff_status` reports trusted pointer, keyboard, and wheel activity observed by listeners on the document where handoff began. Navigation can replace those listeners. `end_handoff` removes them, returns the final counts, and resumes automation. A browser relaunch also ends the handoff and publishes `handoff_ended` with `reason: browser_relaunch`. Handoff does not configure viewer access or authentication.

### Permissions and geolocation

| Action | Parameters | Result and limits |
| --- | --- | --- |
| `set_permissions` | `permissions`, optional `origin` | Grants a non-empty list from the supported values below. Origin must be bare HTTP(S), without credentials, query or fragment. |
| `clear_permissions` | none | Clears browser permission grants and remembered grants. |
| `set_geolocation` | `latitude`, `longitude`, `accuracy` | Latitude is -90 through 90, longitude -180 through 180, accuracy 0 through 100,000 meters. Accuracy defaults to 0. All values must be finite. Grant geolocation permission separately. |

Allowed permission values are `accessibility-events`, `accelerometer`, `ambient-light-sensor`, `background-sync`, `camera`, `clipboard-read`, `clipboard-write`, `geolocation`, `gyroscope`, `magnetometer`, `microphone`, `midi`, `midi-sysex`, `notifications`, and `payment-handler`. Configured permission and geolocation state is reapplied after an automatic browser relaunch.

### Virtual camera and microphone

Set `VIRTUAL_CAMERA_FILE` and/or `VIRTUAL_MICROPHONE_FILE` to make the configured video or audio file available as the corresponding track returned by page `navigator.mediaDevices.getUserMedia()`. Both files must resolve inside `VIRTUAL_MEDIA_DIR` (default `/media`), normally a read-only bind mount. The browser must restart after changing this static source configuration.

With `VIRTUAL_MEDIA_DYNAMIC=true`, these actions switch a file-backed source while preserving the identities of tracks already returned to a page. Dynamic mode is disabled by default.

| Action | Parameters | What It Does |
| ------ | ---------- | ------------ |
| `get_virtual_media_state` | none | Reports virtual-media configuration and, in dynamic mode, each active source basename and source revision. Works in static mode too. |
| `set_virtual_media_source` | `kind`, `source` | Selects an existing source for `kind` (`"camera"` or `"microphone"`). `source` is a relative name that resolves to a regular file inside `VIRTUAL_MEDIA_DIR`. |
| `upload_virtual_media` | `kind`, `filename`, `content_base64`, `activate` | Stores strict base64 file content under `VIRTUAL_MEDIA_DIR` for `kind` (`"camera"` or `"microphone"`). `filename` must be a safe basename whose declared media type matches `kind`; it supplies only the extension. The response returns a generated collision-safe stored basename, and no existing named source is overwritten. Before storage or activation, `ffprobe` must confirm the decoded content contains the requested video or audio stream. `activate` optionally switches to the uploaded source immediately. Uploads are limited by `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` (52428800 bytes, 50 MiB, by default). |

`set_virtual_media_source` and `upload_virtual_media` require dynamic mode. Uploads also require a `VIRTUAL_MEDIA_DIR` the browser user (`PUID`/`PGID`) can write; the entrypoint does not repair its ownership. They are ordinary authenticated actions: when `AUTH_TOKEN` is set, include `Authorization: Bearer <token>` as for every other action. The controller accepts only approved files under the configured media directory. It never accepts arbitrary host paths, remote URLs, WebSocket streams, or other live ingress. `get_virtual_media_state` reports the active source basename and revision without exposing source paths or upload bytes.

This virtualizes `getUserMedia()` streams only. It does not add devices to `enumerateDevices()`. If a request asks for a kind without a configured virtual source, it fails with `NotFoundError` instead of using a native device. Virtual tracks retain the configured file's format and do not emulate incompatible exact media constraints. Use `get_virtual_media_state` to confirm the configured source types and dynamic source state.

### Screen recording

Recording comes from auto-linux-desk; its [screen reference](https://psyb0t.github.io/docker-auto-linux-desk/api/#screen) has every parameter and return field. `window` and `desktop` both capture the whole screen. The active tab's window fills the screen, so `window` shows it with its title bar and toolbar. `viewport` starts the capture at the page's top-left corner.

ffmpeg `x11grab` against the Xvfb display captures actual rendered pixels, including the OS-level mouse cursor by default. One recording can be active per container; a second `start_recording` returns an error. `/recordings` must exist and be writable. Mount it to retain files outside the container; the recorder checks directory existence and permissions, not whether it is a mount point.

| Action              | Parameters     | What It Does                                                                                                                                                                                                                                                                              |
| ------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `start_recording`   | `mode`, `fps`, `show_cursor` | Begin recording to `/recordings/.tmp-<id>.mp4`. The action returns only after ffmpeg creates the temporary MP4, with a 20-second startup limit. `mode`: `"window"` (default) and `"desktop"` capture the entire Xvfb screen from (0, 0); `"viewport"` starts at the page position from `mozInnerScreenX/Y`, measured when the recording starts, and runs to the screen's right and bottom edges, so it cuts off the title bar and toolbar but is not cropped to the viewport size. `fps`: 1 to 60, default 15. `show_cursor`: bool, default `true`. Set it to `false` to record without the OS-level mouse cursor (`ffmpeg -draw_mouse 0`). Returns `recording_id`, `tmp_path`, `show_cursor`, and `capture_size`. |
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
- `viewport` mode measures the page's current screen position (`mozInnerScreenX/Y` from Firefox) when the recording starts and uses it as the capture's top-left corner, so it starts at the page after fullscreen or tab changes. If the position cannot be read, it uses the last measured offset.
- In cluster mode (`NUM_REPLICAS > 1`), recording actions are only usable from inside a `run_script` call. Outside it, the cluster restriction rejects them. Start and stop must live in the same `run_script` so they hit the same browser instance.
- Crashes and forced shutdown abort any active recording and remove its tmp file. At next startup, the app sweeps `/recordings/.tmp-*.mp4` files older than 1h.

## Humanized input

The OS-level actions plan every movement and keystroke from published human motor data, then replay it through X11 with each event on its own schedule. Each container behaves like one consistent person, and every timing option is per request and optional. The input comes from auto-linux-desk: [Humanized input](https://psyb0t.github.io/docker-auto-linux-desk/humanized-input/) there explains the mouse, click, typing, wheel and pause model and lists every option with its range and default. What this image adds:

- **Viewport coordinates.** `x` and `y` are page viewport pixels. The browser window's position on screen is re-measured (`mozInnerScreenX/Y`) before every pointer move, so coordinates stay right after fullscreen, new tab windows and browser relaunches. `calibrate` reports the current offset.
- **Element targets.** Pointer actions accept an [element target](#element-targets) instead of coordinates; the element's current box becomes the target, with its width and height.
- **Typo safety** for browser fields, below.
- **Page scrolling.** `scroll_to_bottom_humanized` reads the page's scroll position, so it is available here.

### Pointer options

`w`, `h`, `duration`, `speed`, `curvature`, `tremor`, `click_hold` and `click_delay`. See [pointer options](https://psyb0t.github.io/docker-auto-linux-desk/humanized-input/#pointer-options) for ranges and defaults.

### Pause options

`duration`, `x`, `y`, `w`, `h`, `drift`, `drift_interval`, `drift_distance`, `speed`, `curvature` and `tremor`, with `x` and `y` in viewport coordinates. See [pause options](https://psyb0t.github.io/docker-auto-linux-desk/humanized-input/#pause-options).

### Typing options

`interval`, `key_hold`, `rollover`, `typos` and `typo_rate` for `system_type`; `key_hold` or `instant` for `send_key`. See [typing options](https://psyb0t.github.io/docker-auto-linux-desk/humanized-input/#typing-options).

#### Typos

Typos are always corrected. A neighbouring key, a swapped pair, or a doubled or missed letter is noticed right away about half the time, otherwise one to three keys later or at the end of the word, then erased with Backspace and retyped, so the final text is exactly what you asked for. They are only made in a focused plain `text`/`search` input or a `textarea`. They are switched off, and the response says why in `typos_disabled_reason` (`no_focused_text_field`, `field_type`, `contenteditable`, `maxlength`, `pattern`, `inputmode`, `autocomplete`, or `control_characters`), for password, email, number, and other typed inputs, fields with `maxlength` or `pattern`, numeric/tel/email/url `inputmode`, one-time-code, payment, password, email, phone, and URL `autocomplete`, contenteditable editors, text containing a tab, and newlines in a single-line input. After typing with typos, the field's value is checked; if a page script changed it so it no longer matches, the action fails with `typed value does not match the requested text after typo corrections`.

### Wheel options

`notch_gap` for `scroll` and `scroll_to_bottom_humanized`; `scroll_back` and `drift` for `scroll_to_bottom_humanized`. See [wheel options](https://psyb0t.github.io/docker-auto-linux-desk/humanized-input/#wheel-options).

## Screenshots

Both screenshot endpoints return PNG and support resize parameters. The browser endpoint also accepts `selector`, repeated `frame` parameters for an ordered iframe chain, and `full_page=true`. Element and full-page capture cannot be combined. The default resolution is 1920x1080, which is a big image. You almost always want to resize.

```bash
# Resize to 512px on the longest side (best default: keeps aspect ratio and size small)
curl http://localhost:8080/screenshot/browser?whLargest=512 -o screenshot.png

# Resize to 800px wide
curl http://localhost:8080/screenshot/browser?width=800 -o screenshot.png

# Exact 400x400 dimensions
curl 'http://localhost:8080/screenshot/browser?width=400&height=400' -o screenshot.png

# Full desktop (includes browser chrome, taskbar, etc.)
curl http://localhost:8080/screenshot/desktop?whLargest=512 -o desktop.png
```

| Parameter              | What It Does                                                                       |
| ---------------------- | ---------------------------------------------------------------------------------- |
| `whLargest=512`        | Scales so the largest dimension is 512px, keeps aspect ratio. Use this by default. |
| `width=800`            | Scales to 800px wide, keeps aspect ratio.                                          |
| `height=300`           | Scales to 300px tall, keeps aspect ratio.                                          |
| `width=400&height=400` | Forces exact dimensions (may stretch).                                             |
