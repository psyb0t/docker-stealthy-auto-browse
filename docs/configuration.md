# Configuration

## Environment Variables

| Variable           | Default         | What It Does                                                                                                                                                                                                                                                                                             |
| ------------------ | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `XVFB_RESOLUTION`  | `1920x1080`     | Virtual display resolution. The browser runs at this size. Can go smaller (e.g. `1280x720`, `1366x768`) but **not larger** than 1920x1080 — the virtual framebuffer maxes out at that size.                                                                                                              |
| `XVFB_DEPTH`       | `24`            | Color depth of the virtual display (16, 24, or 32 bit). 24 is fine for everything.                                                                                                                                                                                                                       |
| `TZ`               | `UTC`           | **Timezone — this one matters for stealth.** Bot detectors compare your browser's timezone against your IP's geographic location. If your IP says you're in Romania but your timezone says UTC, that's a red flag. Set this to match your IP: `Europe/Bucharest`, `America/New_York`, `Asia/Tokyo`, etc. |
| `LANG`             | `en_US.UTF-8`   | Browser locale/language. Override with `-e LANG=fr_FR.UTF-8` etc. to change the browser's locale.                                                                                                                                                                                                        |
| `USE_VIEWPORT`     | `false`         | Enables Playwright viewport control. Required if you need widths below ~450px (Firefox minimum without it). **Reduces stealth** because it adds Playwright viewport management. Only use for mobile layout testing on sites that don't have bot detection.                                               |
| `LOADERS_DIR`      | `/loaders`      | Directory the container scans for page loader YAML files. See [page-loaders.md](./page-loaders.md).                                                                                                                                                                                                      |
| `PROXY_URL`        | —               | Routes all browser traffic through an HTTP proxy. Format: `http://user:pass@host:port`. Useful with residential proxies to match your IP to a specific location.                                                                                                                                         |
| `HTTP_LISTEN_HOST` | `0.0.0.0`       | Host address the HTTP API binds to.                                                                                                                                                                                                                                                                      |
| `HTTP_LISTEN_PORT` | `8080`          | Port the HTTP API listens on.                                                                                                                                                                                                                                                                            |
| `AUTH_TOKEN`       | —               | If set, all requests (except `/health`) require `Authorization: Bearer <token>` header or `?auth_token=<token>` query param. Applies to both HTTP API and MCP.                                                                                                                                           |
| `VNC_LISTEN_HOST`  | `0.0.0.0`       | Host address VNC (noVNC + x11vnc) binds to.                                                                                                                                                                                                                                                              |
| `VNC_LISTEN_PORT`  | `5900`          | Port the noVNC web viewer listens on.                                                                                                                                                                                                                                                                    |
| `REDIS_URL`        | —               | Redis connection string for cross-instance cookie sync. See [cluster-mode.md](./cluster-mode.md).                                                                                                                                                                                                        |

## Examples

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

**Recording (mount /recordings to collect mp4 files):**

```bash
mkdir -p ./recordings
docker run -d -p 8080:8080 -v ./recordings:/recordings psyb0t/stealthy-auto-browse
# Then drive `start_recording` / `stop_recording` via the API; files land in ./recordings/<slug>.mp4
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

| Extension           | What It Does                                                                                                                                           |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **uBlock Origin**   | Blocks ads, trackers, and annoyances. Reduces page load noise and prevents tracking scripts from running.                                              |
| **LocalCDN**        | Intercepts requests to common CDNs (Google, Cloudflare, etc.) and serves the resources locally. Prevents CDN providers from tracking you across sites. |
| **ClearURLs**       | Strips tracking parameters from URLs (utm_source, fbclid, gclid, etc.) so your navigation doesn't leak referral data.                                  |
| **Consent-O-Matic** | Automatically handles cookie consent popups — clicks "reject all" or minimal consent so you don't have to deal with them.                              |

Want to add more? Mount a persistent profile and install them through the browser:

1. Run with `-v ./my-profile:/userdata`
2. Open VNC at `http://localhost:5900/`
3. Navigate to `about:addons` and install whatever you want
4. Extensions persist across restarts via the profile volume

## Window Manager

Openbox runs by default as the X11 window manager. This adds title bars and resize handles to popup windows (e.g. OAuth login dialogs) that would otherwise be too small to interact with. No stealth impact — the WM operates at the X11 display level, not the browser fingerprint level. Visible through VNC.

## VNC Access

Watch the browser in real-time through your web browser. The VNC viewer auto-connects when you open it.

```bash
docker run -d -p 5900:5900 -p 8080:8080 psyb0t/stealthy-auto-browse
```

Open `http://localhost:5900/` — you'll see exactly what the browser sees. Useful for debugging automation scripts, watching logins, or just making sure things are working.
