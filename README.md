# docker-stealthy-auto-browse

Stealth browser automation that actually works. A Docker container running Camoufox (custom Firefox) with zero Chrome DevTools Protocol exposure, real OS-level mouse and keyboard input, and a dead-simple HTTP API to control it all.

Passes Cloudflare, CreepJS, BrowserScan, Pixelscan, and every other bot detector we've thrown at it. While Chromium-based tools are getting caught by the first line of defense, this thing walks through the front door unnoticed.

## Table of Contents

- [Why This Exists](#why-this-exists)
- [What's Inside](#whats-inside)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Two Input Modes (This Is Important)](#two-input-modes-this-is-important)
- [HTTP API](#http-api)
- [Actions Reference](#actions-reference)
- [Screenshots](#screenshots)
- [Page Loaders (URL-Triggered Automation)](#page-loaders-url-triggered-automation)
- [Environment Variables](#environment-variables)
- [Persistent Profiles](#persistent-profiles)
- [Browser Extensions](#browser-extensions)
- [VNC Access](#vnc-access)
- [OpenClaw / ClawHub Skill](#openclaw--clawhub-skill)
- [Claude Code Integration](#claude-code-integration)
- [Bot Detection Test Results](#bot-detection-test-results)
- [License](#license)

## Why This Exists

Every browser automation tool based on Chromium has the same fundamental problem: Chrome DevTools Protocol. CDP is how Playwright, Puppeteer, and Selenium talk to the browser, and it's also how bot detection services know you're automated. You can install stealth plugins, patch `navigator.webdriver`, and spoof fingerprints all day — CDP is still there, and services like Cloudflare, DataDome, PerimeterX, and Akamai will find it.

This container takes a completely different approach:

- **Camoufox** (custom Firefox fork) instead of Chromium — there is no CDP to detect because Firefox doesn't use it
- **PyAutoGUI** for mouse and keyboard — input happens at the OS level, not through the browser's automation API. The browser genuinely doesn't know it's being automated. No JavaScript in the world can tell the difference between PyAutoGUI and a real human
- **Real fingerprints** via browserforge — no spoofing means no inconsistencies between the main context and web workers (a common detection vector)
- Everything packaged in a single Docker container — one command to run

## What's Inside

| Component | What It Does |
|-----------|-------------|
| **Camoufox** | A custom build of Firefox with zero Chrome DevTools Protocol exposure. Bot detectors look for CDP signals — this browser simply doesn't have any. |
| **Xvfb** | Virtual framebuffer that lets the browser run with a full graphical display inside a container, no physical monitor needed. This matters because headless mode is another detection signal. |
| **PyAutoGUI** | Generates real OS-level mouse movements and keystrokes. The browser receives these as genuine user input — it has no idea it's being automated. |
| **noVNC** | Web-based VNC client so you can watch the browser in real time from your own browser. Great for debugging and seeing exactly what's happening. |
| **HTTP API** | A JSON API on port 8080 that lets you control everything — navigate pages, click elements, type text, take screenshots, manage tabs, handle cookies, and more. |

Pre-installed extensions: **uBlock Origin** (ads/trackers), **LocalCDN** (prevents CDN tracking), **ClearURLs** (strips tracking params), **Consent-O-Matic** (auto-handles cookie popups).

## Quick Start

```bash
docker run -d --name browser \
  -p 8080:8080 \
  -p 5900:5900 \
  psyb0t/stealthy-auto-browse
```

That's it. The browser is now running. Port **8080** is the HTTP API, port **5900** is the VNC viewer.

**Navigate to a page:**
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "goto", "url": "https://example.com"}'
```

**Read the page content:**
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "get_text"}'
```

**Find every button, link, and input on the page:**
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "get_interactive_elements"}'
```

This returns each element's coordinates, text, and CSS selector — everything you need to interact with it.

**Click something with a real mouse movement (undetectable):**
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "system_click", "x": 500, "y": 300}'
```

**Type with real keystrokes (undetectable):**
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "system_type", "text": "hello world"}'
```

**Take a screenshot:**
```bash
curl http://localhost:8080/screenshot/browser?whLargest=512 -o screenshot.png
```

**Open a URL on startup:**
```bash
docker run -d -p 8080:8080 psyb0t/stealthy-auto-browse https://example.com
```

**Watch the browser live** at `http://localhost:5900/` (noVNC auto-connects).

## How It Works

Everything goes through a single HTTP API on port 8080. You send JSON commands and get JSON responses.

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

## Two Input Modes (This Is Important)

There are two ways to interact with pages, and understanding the difference is key to not getting detected.

### System Input — Undetectable

Actions: `system_click`, `mouse_move`, `mouse_click`, `system_type`, `send_key`, `scroll`

These use PyAutoGUI to generate **real OS-level events**. The mouse physically moves across the virtual screen with human-like curves and jitter. The keyboard generates real keystroke events. The browser has absolutely no way to know these aren't from a real human sitting at a computer.

System input uses **viewport coordinates** (x, y pixel positions). Get these from `get_interactive_elements`.

### Playwright Input — Detectable But Convenient

Actions: `click`, `fill`, `type`

These use Playwright's DOM automation to find elements by **CSS selector or XPath** and dispatch events through the browser's API. Faster and easier (no coordinate math), but the event injection patterns are theoretically detectable by sophisticated behavioral analysis.

### Which Should You Use?

- **Site has bot detection?** Use system input. Always.
- **Just scraping something that doesn't fight back?** Playwright input is fine and easier.
- **Filling forms on a protected site?** `system_click` to focus the field, then `system_type` to enter text.
- **Have a CSS selector but no coordinates?** Use `click`. Have coordinates from `get_interactive_elements`? Use `system_click`.

## HTTP API

### Endpoints

| Endpoint | Method | What It Does |
|----------|--------|-------------|
| `/` | POST | Execute any browser action (see Actions Reference below) |
| `/screenshot/browser` | GET | Browser viewport as PNG — what the page looks like |
| `/screenshot/desktop` | GET | Full virtual desktop as PNG — including browser chrome |
| `/state` | GET | Current URL, page title, and window offset as JSON |
| `/health` | GET | Returns `ok` when the browser is ready |

### Example: Full Login Flow (Undetectable)

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

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `goto` | `url`, `wait_until` | Navigate to a URL. `wait_until` controls when the page is considered loaded: `"domcontentloaded"` (default, fast), `"load"` (all resources), `"networkidle"` (no network activity for 500ms). |
| `refresh` | `wait_until` (optional) | Reload the current page. Returns URL and title. |

### System Input (OS-Level, Undetectable)

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `system_click` | `x`, `y`, `duration` | Moves the mouse to viewport coordinates with a **human-like curved path** (random jitter, eased acceleration), then clicks. The primary way to click things when stealth matters. `duration` controls movement time (random 0.2-0.6s if omitted). |
| `mouse_move` | `x`, `y`, `duration` | Moves the mouse with human-like movement but does **not** click. Use to hover over elements (trigger dropdown menus, tooltips) or simulate natural mouse behavior between actions. |
| `mouse_click` | `x`, `y` (optional) | Clicks at a position or wherever the mouse currently is. Unlike `system_click`, this does **not** do the smooth mouse movement first — it's a direct click. Use after `mouse_move` when you want to separate movement and click. |
| `system_type` | `text`, `interval` | Types text character-by-character via **real OS keystrokes**. Each key has a randomized delay (jittered around `interval`, default 0.08s) to mimic human typing speed. You must focus an input field first. |
| `send_key` | `key` | Sends a keyboard key or combo. Examples: `"enter"`, `"tab"`, `"escape"`, `"backspace"`, `"ctrl+a"`, `"ctrl+shift+t"`. Uses PyAutoGUI key names. |
| `scroll` | `amount`, `x`, `y` | Scrolls using the mouse wheel. **Negative = scroll down**, positive = scroll up. If `x`, `y` are provided, moves the mouse there first (useful for scrolling inside a specific element). |

### Playwright Input (DOM Events, Detectable)

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `click` | `selector` | Clicks an element by CSS selector or XPath (`xpath=//button[@id='submit']`). Faster than `system_click` but uses Playwright's DOM event injection. |
| `fill` | `selector`, `value` | Sets an input field's value instantly. Clears existing content first. Fast but doesn't generate individual keystroke events — detectable. |
| `type` | `selector`, `text`, `delay` | Types into an element character-by-character via Playwright. Middle ground between `fill` (instant) and `system_type` (OS-level). `delay` defaults to 0.05s between keys. |

### Page Inspection

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `get_interactive_elements` | `visible_only` | Scans the page and returns **every** interactive element (buttons, links, inputs, selects, textareas) with their viewport coordinates (`x`, `y`), dimensions (`w`, `h`), `text`, CSS `selector`, and `visible` status. This is how you find what to click. |
| `get_text` | — | Returns all visible text from the page body (truncated to 10,000 chars). Usually the first thing to call after navigating — tells you what's on the page without a screenshot. |
| `get_html` | — | Returns the full HTML source of the page. Use when `get_text` doesn't give enough structure. |
| `eval` | `expression` | Executes JavaScript in the page context and returns the result. Example: `"document.title"`, `"document.querySelectorAll('a').length"`. |

### Wait Conditions

Use these instead of `sleep` — they wait for **actual page state**, not arbitrary time.

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `wait_for_element` | `selector`, `state`, `timeout` | Waits for an element to reach a state. `state`: `"visible"` (default), `"hidden"`, `"attached"`, `"detached"`. `timeout` in seconds (default 30). CSS or XPath. |
| `wait_for_text` | `text`, `timeout` | Waits for specific text to appear anywhere on the page (substring match). |
| `wait_for_url` | `url`, `timeout` | Waits for the URL to match a glob pattern. `*` matches any chars except `/`, `**` matches everything. Example: `"**/dashboard"`. |
| `wait_for_network_idle` | `timeout` | Waits until no network requests have been made for 500ms. Useful for pages that load content dynamically. |

### Tab Management

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `list_tabs` | — | Returns all open tabs with their index, URL, and which one is active. |
| `new_tab` | `url`, `wait_until` | Opens a new tab (becomes the active tab). Optionally navigates to a URL. |
| `switch_tab` | `index` | Switches the active tab by index (0-based). All subsequent actions operate on the active tab. |
| `close_tab` | `index` (optional) | Closes a tab. If no index, closes the active tab. After closing, the last remaining tab becomes active. |

### Dialog Handling

Browsers have modal dialogs (alert, confirm, prompt). By default, dialogs are **auto-accepted** (clicks OK). Use `handle_dialog` to dismiss or provide prompt text.

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `handle_dialog` | `accept`, `text` | Pre-configures how the **next** dialog will be handled. `accept`: `true` = click OK, `false` = click Cancel. `text`: response for prompt dialogs. **Call this BEFORE the action that triggers the dialog.** If you don't, the dialog is auto-accepted (clicks OK). You only need this if you want to dismiss (Cancel) or provide prompt text. |
| `get_last_dialog` | — | Returns info about the last dialog: `type` (alert/confirm/prompt/beforeunload), `message`, `default_value`, `buttons`. |

### Cookies

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `get_cookies` | `urls` (optional) | Returns all browser cookies. Optionally filter by URL list. Each cookie includes name, value, domain, path, httpOnly, secure, etc. |
| `set_cookie` | `name`, `value`, `url`/`domain`, ... | Sets a cookie. Needs at minimum: `name`, `value`, and either `url` or `domain`. Accepts all standard cookie fields (path, httpOnly, secure, sameSite, expires). |
| `delete_cookies` | — | Clears all cookies from the browser context. |

### Storage

Access the page's localStorage and sessionStorage. Storage is per-origin — you must be on the right page.

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `get_storage` | `type` | Returns all items as key-value pairs. `type`: `"local"` (default) or `"session"`. |
| `set_storage` | `type`, `key`, `value` | Sets a single key-value pair. |
| `clear_storage` | `type` | Clears all items. |

### Downloads & Uploads

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `get_last_download` | — | Returns info about the most recent file download: `url`, `filename`, and local `path` inside the container. Returns `null` if nothing downloaded yet. |
| `upload_file` | `selector`, `file_path` | Programmatically sets a file on an `<input type="file">` element without opening the OS file picker. File must exist inside the container (use `docker cp` to copy files in). You still need to submit the form after. |

### Network Logging

Record all HTTP requests and responses the page makes. Useful for finding API endpoints, debugging, or verifying resources loaded.

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `enable_network_log` | — | Starts recording. Each entry captures: URL, method, resource type (fetch/document/script/image/etc), status code, and timestamp. |
| `disable_network_log` | — | Stops recording. Already-captured entries remain. |
| `get_network_log` | — | Returns all captured entries with their count. |
| `clear_network_log` | — | Deletes captured entries. Keeps logging on if it was on. |

### Display & Calibration

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `calibrate` | — | Recalculates the mapping between viewport coordinates (from `get_interactive_elements`) and screen coordinates (what PyAutoGUI uses). The browser window has chrome (title bar, etc.) that offsets the content area. **Call this after entering/exiting fullscreen**, or if `system_click` seems to be hitting the wrong spot. Auto-calculated at startup. |
| `get_resolution` | — | Returns the virtual display resolution (width, height). |
| `enter_fullscreen` | — | Puts the browser in fullscreen mode (hides address bar and window chrome). Call `calibrate` after. |
| `exit_fullscreen` | — | Exits fullscreen mode. Call `calibrate` after. |

### Scrolling

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `scroll_to_bottom` | `delay` | Scrolls the entire page top-to-bottom using **JavaScript** (`window.scrollBy`), then back to top. Useful for triggering lazy-loaded content. `delay` (default 0.4s) is the pause between scroll steps. This is fast but uses JS, not OS-level input. |
| `scroll_to_bottom_humanized` | `min_clicks`, `max_clicks`, `delay` | Same goal as above but uses **real OS-level mouse wheel scrolling** (PyAutoGUI) with randomized scroll amounts and jittered delays. Undetectable by behavioral analysis. Slower but stealthy. |

### Utility

| Action | Parameters | What It Does |
|--------|------------|-------------|
| `ping` | — | Health check that returns `"pong"` and the current page URL. |
| `sleep` | `duration` | Pauses for N seconds. Prefer `wait_for_element` or `wait_for_text` when waiting for page content. |
| `close` | — | Shuts down the browser. The container stops after this. |

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

| Parameter | What It Does |
|-----------|-------------|
| `whLargest=512` | Scales so the largest dimension is 512px, keeps aspect ratio. Use this by default. |
| `width=800` | Scales to 800px wide, keeps aspect ratio. |
| `height=300` | Scales to 300px tall, keeps aspect ratio. |
| `width=400&height=400` | Forces exact dimensions (may stretch). |

## Page Loaders (URL-Triggered Automation)

Page loaders are like **Greasemonkey/Tampermonkey userscripts** but for the HTTP API. You define a set of actions that automatically run whenever the browser navigates to a matching URL. Instead of manually sending a sequence of commands every time you visit a site, you write it once as a YAML file and the container handles it.

This is useful for things like: removing cookie popups, dismissing overlays, waiting for dynamic content, cleaning up pages before scraping, or any repetitive setup you'd otherwise do manually every time.

### How They Work

1. You create YAML files that define URL patterns and a list of steps
2. Mount those files into the container at `/loaders`
3. Whenever `goto` navigates to a URL that matches a loader's pattern, the loader's steps run automatically instead of the default navigation

**The steps are the exact same actions as the HTTP API.** Every action you can send via `POST /` (goto, eval, click, system_click, sleep, scroll, wait_for_element, etc.) works as a loader step. Same names, same parameters.

### Setup

```bash
docker run -d -p 8080:8080 -p 5900:5900 \
  -v ./my-loaders:/loaders \
  psyb0t/stealthy-auto-browse
```

### Loader Format

```yaml
name: Human-readable name for this loader
match:
  domain: example.com         # Exact hostname match (www. is stripped automatically)
  path_prefix: /articles      # URL path must start with this
  regex: "article/\\d+"       # Full URL must match this regex
steps:
  - action: goto              # Same actions as the HTTP API
    url: "${url}"             # ${url} is replaced with the original URL
    wait_until: networkidle
  - action: eval
    expression: "document.querySelector('.cookie-banner')?.remove()"
  - action: wait_for_element
    selector: "#main-content"
    timeout: 10
```

### Match Rules

All match fields are **optional**, but at least one is required. If you specify multiple fields, **all** of them must match for the loader to trigger:

- **`domain`**: Exact hostname. `www.` is stripped from both sides before comparing, so `domain: example.com` matches `www.example.com` too.
- **`path_prefix`**: The URL path must start with this string. `path_prefix: /blog` matches `/blog`, `/blog/post-1`, `/blog/archive`, etc.
- **`regex`**: The full URL is tested against this regular expression.

### The `${url}` Placeholder

In any string value within a step, `${url}` is replaced with the original URL that was passed to `goto`. This lets you do things like navigate to the URL with custom wait settings, or pass it to JavaScript:

```yaml
steps:
  - action: goto
    url: "${url}"
    wait_until: networkidle
  - action: eval
    expression: "console.log('Loaded:', '${url}')"
```

### Practical Example: Clean Scraping

Say you're scraping a news site that has cookie popups, newsletter modals, and lazy-loaded content. Without a loader, you'd send 5+ commands after every `goto`. With a loader:

```yaml
# loaders/news_site.yaml
name: News Site Cleanup
match:
  domain: news-site.com
steps:
  # Navigate with full network wait so everything loads
  - action: goto
    url: "${url}"
    wait_until: networkidle

  # Wait for the main content to be there
  - action: wait_for_element
    selector: "article"
    timeout: 10

  # Kill the cookie popup
  - action: eval
    expression: "document.querySelector('.cookie-consent')?.remove()"

  # Kill the newsletter modal
  - action: eval
    expression: "document.querySelector('.newsletter-overlay')?.remove()"

  # Scroll to trigger lazy-loaded images
  - action: scroll_to_bottom
    delay: 0.3

  # Small pause for everything to settle
  - action: sleep
    duration: 1
```

Now when you `goto` any URL on `news-site.com`, all of this happens automatically. Your response includes `"loader": "News Site Cleanup"` so you know it triggered.

### Response When a Loader Triggers

```json
{
  "success": true,
  "data": {
    "loader": "News Site Cleanup",
    "steps_executed": 6,
    "last_result": { "success": true, "timestamp": 1234567890.456, "data": { "slept": 1 } }
  }
}
```

## Environment Variables

| Variable | Default | What It Does |
|----------|---------|-------------|
| `LISTEN_HOST` | `localhost` | Host address for VNC and API to bind to. Set to `0.0.0.0` to allow external connections. The API always listens on `0.0.0.0`; VNC respects this setting. |
| `XVFB_RESOLUTION` | `1920x1080` | Virtual display resolution. The browser runs at this size. Can go smaller (e.g. `1280x720`, `1366x768`) but **not larger** than 1920x1080 — the virtual framebuffer maxes out at that size. |
| `XVFB_DEPTH` | `24` | Color depth of the virtual display (16, 24, or 32 bit). 24 is fine for everything. |
| `TZ` | `UTC` | **Timezone — this one matters for stealth.** Bot detectors compare your browser's timezone against your IP's geographic location. If your IP says you're in Romania but your timezone says UTC, that's a red flag. Set this to match your IP: `Europe/Bucharest`, `America/New_York`, `Asia/Tokyo`, etc. |
| `LANG` | `en_US.UTF-8` | Browser locale/language. Not set in the Dockerfile — the code defaults to `en_US.UTF-8` internally. Override with `-e LANG=fr_FR.UTF-8` etc. to change the browser's locale. |
| `USE_VIEWPORT` | `false` | Enables Playwright viewport control. Required if you need widths below ~450px (Firefox minimum without it). **Reduces stealth** because it adds Playwright viewport management. Only use for mobile layout testing on sites that don't have bot detection. |
| `LOADERS_DIR` | `/loaders` | Directory the container scans for page loader YAML files. |
| `PROXY_URL` | — | Routes all browser traffic through an HTTP proxy. Format: `http://user:pass@host:port`. Useful with residential proxies to match your IP to a specific location. |

### Examples

**Allow external connections (from other hosts):**
```bash
docker run -d -e LISTEN_HOST=0.0.0.0 -p 8080:8080 -p 5900:5900 psyb0t/stealthy-auto-browse
```

**Match timezone to IP location (important for stealth):**
```bash
docker run -d -e TZ=Europe/Bucharest -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Use a proxy:**
```bash
docker run -d -e PROXY_URL=http://user:pass@proxy:8888 -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Custom resolution:**
```bash
docker run -d -e XVFB_RESOLUTION=1280x720 -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Mobile viewport (for layout testing, reduces stealth):**
```bash
docker run -d -e USE_VIEWPORT=true -e XVFB_RESOLUTION=375x812 -p 8080:8080 psyb0t/stealthy-auto-browse
```

## Persistent Profiles

Mount a directory to `/userdata` to keep cookies, localStorage, browser sessions, and the generated fingerprint across container restarts. Without this, every restart is a fresh browser with a new identity.

```bash
docker run -d \
  -p 8080:8080 \
  -p 5900:5900 \
  -v ./my-profile:/userdata \
  psyb0t/stealthy-auto-browse
```

This is how you maintain a logged-in session without re-authenticating every time the container restarts.

## Browser Extensions

Pre-installed in every container:

| Extension | What It Does |
|-----------|-------------|
| **uBlock Origin** | Blocks ads, trackers, and annoyances. Reduces page load noise and prevents tracking scripts from running. |
| **LocalCDN** | Intercepts requests to common CDNs (Google, Cloudflare, etc.) and serves the resources locally. Prevents CDN providers from tracking you across sites. |
| **ClearURLs** | Strips tracking parameters from URLs (utm_source, fbclid, gclid, etc.) so your navigation doesn't leak referral data. |
| **Consent-O-Matic** | Automatically handles cookie consent popups — clicks "reject all" or minimal consent so you don't have to deal with them. |

Want to add more? Mount a persistent profile and install them through the browser:

1. Run with `-v ./my-profile:/userdata`
2. Open VNC at `http://localhost:5900/`
3. Navigate to `about:addons` and install whatever you want
4. Extensions persist across restarts via the profile volume

## VNC Access

Watch the browser in real-time through your web browser. The VNC viewer auto-connects when you open it.

```bash
docker run -d -p 5900:5900 -p 8080:8080 psyb0t/stealthy-auto-browse
```

Open `http://localhost:5900/` — you'll see exactly what the browser sees. Useful for debugging automation scripts, watching logins, or just making sure things are working.

## OpenClaw / ClawHub Skill

This project is available as an [OpenClaw](https://docs.openclaw.ai/) skill on [ClawHub](https://clawhub.ai/psyb0t/stealthy-auto-browse). Install it and any OpenClaw-compatible AI agent can use the browser on demand — it loads automatically when the agent needs to browse something that regular HTTP requests can't handle.

**Install:**

```bash
clawhub install psyb0t/stealthy-auto-browse
```

**Configure** (`~/.openclaw/openclaw.json`):

```json
{
  "skills": {
    "entries": {
      "stealthy-auto-browse": {
        "env": {
          "STEALTHY_AUTO_BROWSE_URL": "http://localhost:8080"
        }
      }
    }
  }
}
```

Start the container, and the agent handles the rest. The skill only loads when browser automation is actually needed — it won't consume tokens until then.

## Claude Code Integration

This container works great with [Claude Code](https://claude.ai/code). Claude can launch the browser, navigate pages, read content, click elements, fill forms, and handle complex multi-step workflows — all through the HTTP API.

For a ready-to-use Claude Code setup, check out [docker-claude-code](https://github.com/psyb0t/docker-claude-code).

See [`.claude/INSTRUCTIONS.md`](.claude/INSTRUCTIONS.md) for the full guide Claude uses to control the browser.

## Bot Detection Test Results

Tested against major bot detection services:

| Service | Result | What They Check |
|---------|--------|----------------|
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **Pass** | Canvas/WebGL fingerprint consistency, lies detection, worker comparison |
| [BrowserScan](https://www.browserscan.net/bot-detection) | **Pass** | WebDriver flag, CDP signals, navigator properties |
| [Pixelscan](https://pixelscan.net/) | **Pass** | Fingerprint coherence, timezone/IP match, WebRTC leaks |
| [Cloudflare](https://cloudflare.com) | **Pass** | Challenge pages, Turnstile, bot management |
| [SannySoft](https://bot.sannysoft.com/) | **Pass** | Intoli + fingerprint scanner tests |
| [Incolumitas](https://bot.incolumitas.com/) | **Pass** | Modern detection techniques |
| [Rebrowser](https://bot-detector.rebrowser.net/) | **Pass** | CDP leak detection, webdriver, viewport analysis |
| [BrowserLeaks WebRTC](https://browserleaks.com/webrtc) | **Pass** | WebRTC IP leak detection |
| [DeviceAndBrowserInfo](https://deviceandbrowserinfo.com/are_you_a_bot) | **Pass** | 19 checks, all green, "You are human!" |
| [IpHey](https://iphey.com/) | **Pass** | "Trustworthy" rating |
| [Fingerprint.com](https://fingerprint.com/demo/) | **Pass** | Identified as normal Firefox, no bot flags |

### Why It Actually Works

Most stealth tools try to **hide** automation signals. This container **doesn't have them in the first place**:

- **No CDP** — Firefox doesn't have Chrome DevTools Protocol. There's nothing to hide because it doesn't exist.
- **No fingerprint spoofing** — The fingerprint is generated once and applied consistently. Main context matches web workers (a common detection vector for spoofers).
- **`navigator.webdriver` is `false`** — Not patched to return false, it's genuinely false because Camoufox doesn't set it.
- **Real input events** — PyAutoGUI generates OS-level mouse and keyboard events. No DOM event injection for the browser to detect.

## License

**WTFPL** — Do What The Fuck You Want To Public License
