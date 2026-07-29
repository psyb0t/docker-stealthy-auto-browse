# docker-stealthy-auto-browse

[![CI](https://github.com/psyb0t/docker-stealthy-auto-browse/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-stealthy-auto-browse/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/badges/version.svg)](https://github.com/psyb0t/docker-stealthy-auto-browse/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/stealthy-auto-browse?style=flat-square)](https://hub.docker.com/r/psyb0t/stealthy-auto-browse)

Stealth browser automation that actually works. Runs Camoufox (custom Firefox) in Docker with zero Chrome DevTools Protocol exposure, real OS-level mouse and keyboard input via PyAutoGUI, and a JSON HTTP API + MCP server to control it all remotely. Watch it live via noVNC. Run a single instance or spin up a cluster behind HAProxy with Redis cookie sync, request queuing, and sticky sessions. Drive it with curl, pipe YAML scripts through stdin, send multi-step scripts via the API, use page loaders to auto-handle popups and paywalls, or connect AI agents directly via MCP. Optional Bearer token auth via `AUTH_TOKEN`.

Passes Cloudflare, CreepJS, BrowserScan, Pixelscan, and every other bot detector we've thrown at it. While Chromium-based tools are getting caught by the first line of defense, this thing walks through the front door unnoticed.

## Table of Contents

- [What's Inside](#whats-inside)
- [Quick Start](#quick-start)
- [Two Input Modes](#two-input-modes)
- [Virtual Camera & Microphone](#virtual-camera--microphone)
- [MCP Server](#mcp-server)
- [Agent integrations](#agent-integrations)
- [Script Mode](#script-mode)
- [Page Loaders](#page-loaders)
- [Screen Recording](#screen-recording)
- [Cluster Mode](#cluster-mode)
- [Authentication](#authentication)
- [Configuration](#configuration)
- [Bot Detection Results](#bot-detection-results)
- [License](#license)

## What's Inside

| Component      | What It Does                                                                                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Camoufox**   | A custom build of Firefox with zero Chrome DevTools Protocol exposure. Bot detectors look for CDP signals — this browser simply doesn't have any.                                           |
| **Xvfb**       | Virtual framebuffer that lets the browser run with a full graphical display inside a container, no physical monitor needed. This matters because headless mode is another detection signal. |
| **PyAutoGUI**  | Generates real OS-level mouse movements and keystrokes. The browser receives these as genuine user input — it has no idea it's being automated.                                             |
| **noVNC**      | Web-based VNC client so you can watch the browser in real time from your own browser. Great for debugging and seeing exactly what's happening.                                              |
| **Openbox**    | Lightweight window manager — adds title bars and resize handles to popup windows (OAuth dialogs, etc.) that would otherwise be too small to interact with. Zero stealth impact.             |
| **HTTP API**   | A JSON API on port 8080 that lets you control everything — navigate pages, click elements, type text, take screenshots, manage tabs, handle cookies, and more.                              |
| **MCP Server** | [Model Context Protocol](https://modelcontextprotocol.io/) server at `/mcp` on the same port. AI agents (Claude, etc.) can drive the browser directly over MCP using Streamable HTTP.       |
| **ffmpeg**     | `x11grab` against Xvfb for screen recording. Captures actual rendered pixels including the OS-level mouse cursor — see [Screen Recording](#screen-recording).                               |

Pre-installed extensions: **uBlock Origin** (ads/trackers), **LocalCDN** (prevents CDN tracking), **ClearURLs** (strips tracking params), **Consent-O-Matic** (auto-handles cookie popups).

## Quick Start

```bash
docker run -d --name browser \
  -p 8080:8080 \
  -p 5900:5900 \
  psyb0t/stealthy-auto-browse
```

Port **8080** is the HTTP API, port **5900** is the VNC viewer (`http://localhost:5900/`).

```bash
# Navigate
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "goto", "url": "https://example.com"}'

# Get page text
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "get_text"}'

# Click by CSS selector (preferred — fast and reliable)
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "click", "selector": "button#submit"}'

# Screenshot (last resort — prefer get_text; always resize to save tokens)
curl "http://localhost:8080/screenshot/browser?whLargest=512" -o screenshot.png
```

**Run multi-step scripts in one request:**

```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{
    "action": "run_script",
    "steps": [
      {"action": "goto", "url": "https://example.com", "wait_until": "domcontentloaded"},
      {"action": "sleep", "duration": 2},
      {"action": "get_text", "output_id": "text"},
      {"action": "eval", "expression": "document.title", "output_id": "title"}
    ]
  }'
```

Also accepts `"yaml": "..."` with the same YAML format used in script mode. In single-instance mode, requests are serialized automatically — send multiple scripts in parallel and they queue up.

See [docs/api.md](docs/api.md) for all actions and the full API reference.

## Two Input Modes

There are two ways to interact with pages. **System input** uses PyAutoGUI to generate real OS-level mouse and keyboard events — the browser cannot tell these apart from a real human. **Playwright input** uses CSS selectors and DOM event injection — easier, but theoretically detectable by behavioral analysis. Use system input on any site with bot protection.

Full breakdown and usage guide: [docs/stealth.md](docs/stealth.md)

## Virtual Camera & Microphone

Mount test media read-only at `/media` and set `VIRTUAL_CAMERA_FILE` and/or `VIRTUAL_MICROPHONE_FILE`. Pages that call `navigator.mediaDevices.getUserMedia()` receive tracks captured from those files, so camera and microphone checks can run without host hardware.

```bash
docker run -d -p 8080:8080 \
  -v ./media:/media:ro \
  -e VIRTUAL_CAMERA_FILE=camera.webm \
  -e VIRTUAL_MICROPHONE_FILE=microphone.wav \
  psyb0t/stealthy-auto-browse
```

Sources must remain inside `/media`; restart the browser after changing them. A request for a kind without a configured virtual source fails with `NotFoundError` rather than falling back to hardware. Virtual tracks use the source file's native format, so pages must not require incompatible exact media constraints. This virtualizes `getUserMedia()` only, not `enumerateDevices()`. See [docs/configuration.md](docs/configuration.md) for details.

## MCP Server

AI agents can control the browser over the [Model Context Protocol](https://modelcontextprotocol.io/) via Streamable HTTP at `/mcp` on the same port 8080. All browser actions are exposed as MCP tools — navigation, screenshots, clicking, typing, JavaScript evaluation, cookies, and more.

Connect any MCP-compatible client (Claude Desktop, Claude Code, custom agents) to `http://localhost:8080/mcp/` and start browsing.

Works in both standalone and [cluster mode](#cluster-mode).

## Agent integrations

The [skill](.agents/skills/stealthy-auto-browse) works in any agent that reads `.agents/skills/`, and installs natively in the clients below.

### Claude Code

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install stealthy-auto-browse@psyb0t
```

Claude Code prompts for the stealthy-auto-browse URL and, if auth is enabled, the token — the token is stored in your OS keychain.

### Codex

```bash
codex plugin marketplace add psyb0t/agents
codex plugin add stealthy-auto-browse@psyb0t
```

Installed via the marketplace, the skill invokes as `$stealthy-auto-browse:stealthy-auto-browse`. Codex also picks the skill up automatically with no install in any repo containing `.agents/skills/`, where it invokes as plain `$stealthy-auto-browse`.

### OpenClaw

The skill is published to ClawHub on every release:

```bash
openclaw skills install @psyb0t/stealthy-auto-browse
```

For MCP clients that speak local stdio, the [`@psyb0t/stealthy-auto-browse`](.agents/plugins/stealthy-auto-browse) plugin bridges to the service's `/mcp` endpoint:

```bash
openclaw plugins install clawhub:@psyb0t/stealthy-auto-browse
```

Then set `STEALTHY_AUTO_BROWSE_URL` (and `AUTH_TOKEN` if the server requires auth).

## Script Mode

Pipe a YAML script into the container, get JSON results on stdout, container exits. No HTTP server. Good for CI, cron jobs, one-shot scraping.

```bash
cat my-script.yaml | docker run --rm -i \
  -e TARGET_URL=https://example.com \
  psyb0t/stealthy-auto-browse --script > results.json
```

Full docs: [docs/script-mode.md](docs/script-mode.md)

## Page Loaders

Define URL patterns + action sequences in YAML files. Mount them at `/loaders`. Whenever `goto` matches a pattern, the loader runs automatically — removes popups, waits for content, cleans up the page. Greasemonkey for the HTTP API.

Full docs: [docs/page-loaders.md](docs/page-loaders.md)

## Screen Recording

Record the browser as MP4 with mouse cursor visible. ffmpeg `x11grab` against Xvfb writes to a mounted `/recordings` volume. Three modes: `window` (full Camoufox window), `viewport` (chrome cropped using calibrated `mozInnerScreenX/Y`), `desktop` (entire Xvfb screen). Slug provided at stop time so you name the file after the run completes. Path-traversal-safe, collision-safe, crash-safe.

```bash
mkdir -p ./recordings
docker run -d -p 8080:8080 -v ./recordings:/recordings psyb0t/stealthy-auto-browse
```

```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "start_recording", "mode": "viewport", "fps": 20}'

# … do stuff …

curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action": "stop_recording", "slug": "my-flow"}'
# → ./recordings/my-flow.mp4
```

Also works inside `run_script` (cluster-mode safe: start and stop must live in the same `run_script` so both hit the same instance). Full action table + script-mode example + notes in [docs/api.md#screen-recording](docs/api.md#screen-recording).

## Cluster Mode

Run multiple browser instances behind HAProxy with a request queue, sticky sessions, and Redis cookie sync (default 5, configurable via `NUM_REPLICAS`). Download the compose file and HAProxy config, then start:

```bash
curl -LO https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/main/docker-compose.cluster.yml
docker compose -f docker-compose.cluster.yml up -d
```

Cookies set on any instance propagate to all others instantly via Redis PubSub. Log in once, the whole fleet is authenticated.

Each browser defaults to a 5GB memory limit. Set `BROWSER_MEMORY_LIMIT` and `BROWSER_MEMORY_RESERVATION` when your fleet or display resolution needs a different budget; see [cluster mode](docs/cluster-mode.md#environment-variables).

**Script-only enforcement (v1.0.0+):** When `NUM_REPLICAS > 1`, both the HTTP API and MCP server restrict to `run_script` only (plus `ping` and `sleep`). Individual actions are rejected to prevent stale content bugs from cross-instance routing. All actions remain available as steps inside `run_script`.

Full docs: [docs/cluster-mode.md](docs/cluster-mode.md)

## Authentication

Set `AUTH_TOKEN` to require a Bearer token on all requests (except `/health`):

```bash
docker run -d -p 8080:8080 -e AUTH_TOKEN=your-token-here psyb0t/stealthy-auto-browse
```

Pass the token in the `Authorization` header:

```bash
# Header
curl -H "Authorization: Bearer your-token-here" http://localhost:8080 ...
```

## Examples

See [`.agents/skills/stealthy-auto-browse/scripts/`](.agents/skills/stealthy-auto-browse/scripts/) for ready-to-use scripts:

- **[`websearch.py`](.agents/skills/stealthy-auto-browse/scripts/websearch.py)** — Multi-engine parallel web search (Brave, Google, Bing) with structured results and AI overview extraction. Outputs JSON with title, URL, and snippet for each result.

## Configuration

Full environment variables table, proxy setup, persistent profiles, browser extensions, and VNC access: [docs/configuration.md](docs/configuration.md)

## Bot Detection Results

| Service                                                                | Result   | What They Check                                                         |
| ---------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------- |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/)                    | **Pass** | Canvas/WebGL fingerprint consistency, lies detection, worker comparison |
| [BrowserScan](https://www.browserscan.net/bot-detection)               | **Pass** | WebDriver flag, CDP signals, navigator properties                       |
| [Pixelscan](https://pixelscan.net/)                                    | **Pass** | Fingerprint coherence, timezone/IP match, WebRTC leaks                  |
| [Cloudflare](https://cloudflare.com)                                   | **Pass** | Challenge pages, Turnstile, bot management                              |
| [SannySoft](https://bot.sannysoft.com/)                                | **Pass** | Intoli + fingerprint scanner tests                                      |
| [Incolumitas](https://bot.incolumitas.com/)                            | **Pass** | Modern detection techniques                                             |
| [Rebrowser](https://bot-detector.rebrowser.net/)                       | **Pass** | CDP leak detection, webdriver, viewport analysis                        |
| [BrowserLeaks WebRTC](https://browserleaks.com/webrtc)                 | **Pass** | WebRTC IP leak detection                                                |
| [DeviceAndBrowserInfo](https://deviceandbrowserinfo.com/are_you_a_bot) | **Pass** | 19 checks, all green, "You are human!"                                  |
| [IpHey](https://iphey.com/)                                            | **Pass** | "Trustworthy" rating                                                    |
| [Fingerprint.com](https://fingerprint.com/demo/)                       | **Pass** | Identified as normal Firefox, no bot flags                              |

Why it works: [docs/stealth.md](docs/stealth.md)

## Known Issues / TODO

- **`system_click` reliability** — OS-level mouse clicks can land in the wrong place if the window offset is stale. Needs a more robust coordinate mapping solution so it works reliably without manual `calibrate` calls.

## License

**WTFPL** — Do What The Fuck You Want To Public License
