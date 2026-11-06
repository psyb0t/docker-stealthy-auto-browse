# docker-stealthy-auto-browse

[![CI](https://github.com/psyb0t/docker-stealthy-auto-browse/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-stealthy-auto-browse/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/badges/version.svg)](https://github.com/psyb0t/docker-stealthy-auto-browse/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/stealthy-auto-browse?style=flat-square)](https://hub.docker.com/r/psyb0t/stealthy-auto-browse)

Stealth browser automation that actually works. One Docker container runs Camoufox on a graphical display, accepts JSON HTTP and MCP commands, sends real OS-level mouse and keyboard input, and exposes a live noVNC viewer. It supports single-browser sessions, atomic scripts, page loaders, recording, virtual media, CAPTCHA detection, optional CAPTCHA solver providers, and clustered browsers with Redis cookie sync.

The image avoids Chromium CDP signals and keeps its generated Linux font and WebGL fingerprints internally consistent. Detection results still depend on the site, network exit, timezone, browser build, and test date.

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [HTTP API](#http-api)
- [MCP and agent integrations](#mcp-and-agent-integrations)
- [Optional features](#optional-features)
- [Cluster mode](#cluster-mode)
- [Configuration and security](#configuration-and-security)
- [Development](#development)
- [Detection testing](#detection-testing)
- [Documentation](#documentation)
- [License](#license)

## Quick start

```bash
docker run -d --name browser \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:5900:5900 \
  psyb0t/stealthy-auto-browse
```

Open `http://localhost:5900/` to watch the browser. Port 8080 serves the HTTP API and MCP server.

```bash
# Navigate
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action":"goto","url":"https://example.com"}'

# Read the page
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action":"get_text"}'

# Click with OS-level input
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"action":"system_click","selector":"button#submit"}'

# Capture a token-sized screenshot
curl "http://localhost:8080/screenshot/browser?whLargest=512" -o screenshot.png
```

Run several actions atomically with `run_script`:

```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{
    "action": "run_script",
    "steps": [
      {"action": "goto", "url": "https://example.com"},
      {"action": "get_text", "output_id": "text"},
      {"action": "eval", "expression": "document.title", "output_id": "title"}
    ]
  }'
```

The server serializes requests in single-instance mode. Navigation has bounded retries and timeouts. See the [HTTP API reference](docs/api.md) for every action and parameter.

## How it works

| Component | Purpose |
| --- | --- |
| Camoufox | Patched Firefox with no Chrome DevTools Protocol and an isolated automation world. |
| Xvfb and Openbox | Full graphical display and window management without a physical monitor. |
| PyAutoGUI and XTest | Trusted OS-level mouse, keyboard, and wheel events with humanized timing. |
| HTTP API and MCP | Remote browser control on port 8080. |
| noVNC | Live browser view on port 5900, with the automated pointer visible by default. |
| ffmpeg | MP4 recording from the X11 display. |

The image includes uBlock Origin, LocalCDN, ClearURLs, and Consent-O-Matic. Optional CAPTCHA solver integrations are disabled unless configured.

Two input modes are available. Playwright actions such as `click` and `fill` are fast DOM operations. System actions such as `system_click`, `system_type`, `mouse_move`, and `scroll` send OS-level input with paths and timing fitted to published human input data. `human_pause` represents visible reading, review, or composition time without continuously jiggling the pointer. Use the system actions when trusted input or behavior matters. See [Stealth and detection evasion](docs/stealth.md) and [Humanized input](docs/api.md#humanized-input).

## HTTP API

Send `POST /` with an `action` and its parameters. The API covers navigation, DOM and system input, tabs, dialogs, cookies, storage, downloads, uploads, clipboard access, screenshots, recording, virtual media, challenge detection, waits, network logs, console logs, and JavaScript evaluation.

Useful read endpoints:

- `GET /health`
- `GET /state`
- `GET /screenshot/browser`
- `GET /screenshot/desktop`

`detect_challenge` reports bounded evidence for known challenge widgets without clicking or solving them. Set `scroll_into_view: true` to bring the first visible match into the viewport for a noVNC handoff. See [Challenge detection](docs/api.md#challenge-detection).

## MCP and agent integrations

The Streamable HTTP MCP server lives at `http://localhost:8080/mcp/`. It exposes dedicated tools for common actions plus `browser_action` and `run_script` for the complete API.

Install the published integrations:

```bash
# Claude Code
claude plugin marketplace add psyb0t/agents
claude plugin install stealthy-auto-browse@psyb0t

# Codex
codex plugin marketplace add psyb0t/agents
codex plugin add stealthy-auto-browse@psyb0t

# OpenClaw skill
openclaw skills install @psyb0t/stealthy-auto-browse

# OpenClaw MCP bridge
openclaw plugins install clawhub:@psyb0t/stealthy-auto-browse
```

The source skill is under [`.agents/skills/stealthy-auto-browse`](.agents/skills/stealthy-auto-browse). Set `STEALTHY_AUTO_BROWSE_URL` and `AUTH_TOKEN` where the client integration requires them.

## Optional features

- **Script mode:** Pipe YAML to `docker run --rm -i ... --script` and receive JSON on stdout. Conditions, branches, bounded loops, and output references are supported. See [Script mode](docs/script-mode.md).
- **Page loaders:** Run URL-matched YAML actions after navigation to handle repeated page cleanup. See [Page loaders](docs/page-loaders.md).
- **Clipboard:** `set_clipboard` and `get_clipboard` use the real X11 text clipboard shared by Camoufox and noVNC. For substantial text, set the clipboard, focus the field, then paste with `send_key` and `ctrl+v`. See [Clipboard](docs/api.md#clipboard).
- **Virtual camera and microphone:** Mount media under `/media` and configure static or dynamic sources for `getUserMedia()` tests. See [Virtual camera and microphone](docs/api.md#virtual-camera-and-microphone) and [Configuration](docs/configuration.md).
- **Recording:** `start_recording` and `stop_recording` write collision-safe MP4 files to a mounted `/recordings` directory. See [Screen recording](docs/api.md#screen-recording).
- **CAPTCHA solver providers:** Configure one supported provider through environment variables. Official Firefox extensions are checksum-verified before use. Anti-Captcha uses its official HTTP API. No provider is installed or contacted by default. See [CAPTCHA solver providers](docs/configuration.md#captcha-solver-providers).

## Cluster mode

`docker-compose.cluster.yml` runs several browsers behind HAProxy with request queuing, sticky sessions, and Redis cookie sync. The default is five browser replicas with a 5 GB memory limit each. When `NUM_REPLICAS > 1`, HTTP and MCP accept atomic `run_script` requests plus `ping` and `sleep`, which keeps every sequence on one browser.

See [Cluster mode](docs/cluster-mode.md) for setup, scaling, health checks, and resource controls.

## Configuration and security

Set `AUTH_TOKEN` to require a Bearer token on every endpoint except `/health`:

```bash
docker run -d \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:5900:5900 \
  -e AUTH_TOKEN=your-token-here \
  psyb0t/stealthy-auto-browse
```

Keep the API and noVNC ports on loopback unless a trusted network or authenticated reverse proxy protects them. Match `TZ` to the proxy exit location. Persist `/userdata` when browser identity and session state must survive restarts.

The [configuration reference](docs/configuration.md) covers every environment variable, proxies, persistent profiles, extensions, virtual media, VNC, and solver providers.

## Development

Docker is the only development dependency. The Makefile runs tooling in the project dev image.

```bash
make build
make lint
make test-unit
make test
make test-real
make sec
```

`make test-real` is opt-in because it calls public detector sites. Use `make help` for all targets.

## Detection testing

Observed results are not a promise for every site or future detector version. Test the exact image, proxy, timezone, and target flow you plan to use.

The live suite checks fingerprint consistency on [liarjs.dev](https://liarjs.dev/) and compares humanized system input against Playwright and raw PyAutoGUI controls on DeviceAndBrowserInfo and FCaptcha. The deterministic suite also runs open-source behavioral detectors with robotic controls that must be caught. Current methods and observed results are in [Stealth and detection evasion](docs/stealth.md).

## Documentation

- [HTTP API reference](docs/api.md)
- [Configuration](docs/configuration.md)
- [Script mode](docs/script-mode.md)
- [Page loaders](docs/page-loaders.md)
- [Cluster mode](docs/cluster-mode.md)
- [Stealth and detection evasion](docs/stealth.md)
- [Changelog](CHANGELOG.md)

## License

**WTFPL** (Do What The Fuck You Want To Public License)
