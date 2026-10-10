# Stealth & detection evasion

## Why this exists

Every browser automation tool based on Chromium has the same fundamental problem: Chrome DevTools Protocol. CDP is how Playwright, Puppeteer, and Selenium talk to the browser, and it's also how bot detection services know you're automated. You can install stealth plugins, patch `navigator.webdriver`, and spoof fingerprints all day. CDP is still there, and services like Cloudflare, DataDome, PerimeterX, and Akamai will find it.

This container takes a different approach:

- **Camoufox** (custom Firefox fork) instead of Chromium. There is no CDP to detect because Firefox doesn't use it
- **PyAutoGUI** for mouse and keyboard. Input happens at the OS level, not through the browser's automation API. The browser doesn't know it's being automated. No JavaScript in the world can tell the difference between PyAutoGUI and a real human
- **Native fingerprint injection** generated through browserforge and applied
  by Camoufox. The generated properties persist with the profile, and Linux
  font aliases plus WebGL renderer capabilities stay in one coherent cohort.
- Everything packaged in a single Docker container, run with one command

## Two input modes

Understanding the difference between these is key to not getting detected.

### System input (undetectable)

Actions: `system_click`, `mouse_move`, `mouse_click`, `system_type`, `send_key`, `scroll`, `scroll_to_bottom_humanized`

These use PyAutoGUI to generate **real OS-level events**, timed from human motor data rather than random jitter:

- **Mouse:** curved sigma-lognormal paths with Fitts's-law timing, a speed peak early in the move, overshoot and correction on about a third of moves, and hand tremor, posted at a 125 Hz mouse rate.
- **Clicks:** a human pause before the press, a ~100 ms button hold, and a landing point scattered inside the target instead of its exact centre.
- **Typing:** right-skewed key rhythm that depends on the key pair, real key holds, overlapping keys for fast typists, and Shift pressed before capitals. Optional typos are always corrected.
- **Wheel:** multi-notch gestures sent one notch at a time with a flick or controlled rhythm, reading pauses, and occasional scroll-backs.

Each container behaves like one consistent person. The main timings (speed, path bend, tremor, click and key holds, typing rhythm, typos, wheel spacing) can be tuned per request; see [Humanized Input](api.md#humanized-input). The test suite scores these sessions with open-source bot detectors (motion-attestation, Gaitcha) and checks that a robotic control session is caught by the same checks.

System input uses **viewport coordinates** (x, y pixel positions). Get these from `get_interactive_elements`.

### Playwright input (detectable but convenient)

Actions: `click`, `fill`, `type`

These use Playwright's DOM automation to find elements by **CSS selector or XPath** and dispatch events through the browser's API. Faster and easier (no coordinate math), but the event injection patterns are theoretically detectable by sophisticated behavioral analysis.

### Which to use

- **Default:** use `click` with a CSS selector. It's fast, reliable, and works for most sites.
- **Site explicitly detects and blocks DOM event injection?** Fall back to system input.
- **Using `system_click`?** Call `calibrate` first. Without it the coordinates are offset and the click lands in the wrong place.
- **Pass `w` and `h` with `system_click`.** `get_interactive_elements` returns them; with them the click lands at a human offset inside the element instead of near its centre.
- **Filling forms on a protected site?** `fill` first; if blocked, `system_click` to focus then `system_type`.
- **Just scraping?** Playwright input (`click`, `fill`) is fine.

## Bot detection test results

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

## Why it actually works

Most stealth tools try to **hide** automation signals. This container **doesn't have them in the first place**:

- **No CDP**. Firefox doesn't have Chrome DevTools Protocol. There's nothing to hide because it doesn't exist.
- **Native fingerprint injection**. Camoufox applies the generated fingerprint
  below the page JavaScript layer. The persisted configuration keeps the main
  context, workers, bundled Linux font aliases, and WebGL cohort aligned across
  restarts.
- **`navigator.webdriver` is `false`**. Nothing patches it to return false. It is false because Camoufox doesn't set it.
- **Real input events**. PyAutoGUI generates OS-level mouse and keyboard events. No DOM event injection for the browser to detect.

## AI agent integration

This project ships as a skill in `.skills/`. AI agents that support skills can pick it up and use the browser on demand. Start the container, set the env var, and the agent handles the rest.

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
