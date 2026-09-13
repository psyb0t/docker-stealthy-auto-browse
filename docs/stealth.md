# Stealth & Detection Evasion

## Why This Exists

Every browser automation tool based on Chromium has the same fundamental problem: Chrome DevTools Protocol. CDP is how Playwright, Puppeteer, and Selenium talk to the browser, and it's also how bot detection services know you're automated. You can install stealth plugins, patch `navigator.webdriver`, and spoof fingerprints all day — CDP is still there, and services like Cloudflare, DataDome, PerimeterX, and Akamai will find it.

This container takes a completely different approach:

- **Camoufox** (custom Firefox fork) instead of Chromium — there is no CDP to detect because Firefox doesn't use it
- **PyAutoGUI** for mouse and keyboard — input happens at the OS level, not through the browser's automation API. The browser genuinely doesn't know it's being automated. No JavaScript in the world can tell the difference between PyAutoGUI and a real human
- **Native fingerprint injection** generated through browserforge and applied
  by Camoufox. The generated properties persist with the profile, and Linux
  font aliases plus WebGL renderer capabilities stay in one coherent cohort.
- Everything packaged in a single Docker container — one command to run

## Two Input Modes

Understanding the difference between these is key to not getting detected.

### System Input — Undetectable

Actions: `system_click`, `mouse_move`, `mouse_click`, `system_type`, `send_key`, `scroll`

These use PyAutoGUI to generate **real OS-level events**. The mouse physically moves across the virtual screen with human-like curves and jitter. The keyboard generates real keystroke events. The browser has absolutely no way to know these aren't from a real human sitting at a computer.

System input uses **viewport coordinates** (x, y pixel positions). Get these from `get_interactive_elements`.

### Playwright Input — Detectable But Convenient

Actions: `click`, `fill`, `type`

These use Playwright's DOM automation to find elements by **CSS selector or XPath** and dispatch events through the browser's API. Faster and easier (no coordinate math), but the event injection patterns are theoretically detectable by sophisticated behavioral analysis.

### Which to Use

- **Default:** use `click` with a CSS selector. It's fast, reliable, and works for most sites.
- **Site explicitly detects and blocks DOM event injection?** Fall back to system input.
- **Using `system_click`?** Call `calibrate` first — without it the coordinates are offset and the click lands in the wrong place.
- **Filling forms on a protected site?** `fill` first; if blocked, `system_click` to focus then `system_type`.
- **Just scraping?** Playwright input (`click`, `fill`) is fine.

## Bot Detection Test Results

Observed against major bot detection services. Results can change with the
service version, network exit, timezone, browser build, and tested flow. Run
your own check against the exact setup you plan to use.

| Service                                                                | Result                           | What They Check                                                         |
| ---------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------- |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/)                    | **Pass**                         | Canvas/WebGL fingerprint consistency, lies detection, worker comparison |
| [liarjs.dev](https://liarjs.dev/)                                      | **82/100, no critical findings** | Font, canvas, worker, GPU, network, and behavioral consistency          |
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

The liarjs.dev result was observed with `TZ` matched to the network exit. Its
remaining warnings were empty native media-device enumeration and the human
challenge not being taken. Run `make test-real` for an opt-in live regression
of the font, canvas, worker, and WebGL checks. The default suite uses local
fixtures and does not depend on a third-party service.

## Why It Actually Works

Most stealth tools try to **hide** automation signals. This container **doesn't have them in the first place**:

- **No CDP** — Firefox doesn't have Chrome DevTools Protocol. There's nothing to hide because it doesn't exist.
- **Native fingerprint injection**. Camoufox applies the generated fingerprint
  below the page JavaScript layer. The persisted configuration keeps the main
  context, workers, bundled Linux font aliases, and WebGL cohort aligned across
  restarts.
- **`navigator.webdriver` is `false`** — Not patched to return false, it's genuinely false because Camoufox doesn't set it.
- **Real input events** — PyAutoGUI generates OS-level mouse and keyboard events. No DOM event injection for the browser to detect.

## AI Agent Integration

This project ships as a skill in `.skills/` — AI agents that support skills can pick it up and use the browser on demand. Start the container, set the env var, and the agent handles the rest.

```bash
export STEALTHY_AUTO_BROWSE_URL=http://localhost:8080
```

### Claude Code

The `.skills/` directory in this repo is all Claude Code needs. Clone/copy this repo (or just the `.skills/` dir) into your project and Claude Code will automatically discover the skill.

For a ready-to-use Claude Code setup, check out [docker-claude-code](https://github.com/psyb0t/docker-claude-code).

### OpenClaw / ClawHub

```bash
clawhub install psyb0t/stealthy-auto-browse
```

Configure in `~/.openclaw/openclaw.json`:

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
