---
title: Configuration
description: Configure browser identity, proxies, display size, authentication, solver providers, virtual media, persistent profiles, and logging.
sidebar_position: 3
---

# Configuration

## Environment variables

| Variable           | Default         | What it does                                                                                                                                                                                                                                                                                             |
| ------------------ | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `XVFB_RESOLUTION`  | `1920x1080`     | Virtual display resolution. The browser runs at this size and the Xvfb framebuffer is allocated to match, so any width/height works (e.g. `1280x720`, `1920x1920`, `2560x1440`). Larger framebuffers use more memory and are slower to software-render, but there is no hard cap. Screen recording captures this exact size. Set a square/tall resolution here if that's what you need to record. |
| `XVFB_DEPTH`       | `24`            | Color depth of the virtual display: 16 or 24. Xvfb has no 32-bit screen depth, so `32` stops startup. 24 is fine for everything.                                                                                                                                                                                                                       |
| `TZ`               | `UTC`           | **Timezone. This one matters for stealth.** Bot detectors compare your browser's timezone against your IP's geographic location. If your IP says you're in Romania but your timezone says UTC, that's a red flag. Set this to match your IP: `Europe/Bucharest`, `America/New_York`, `Asia/Tokyo`, etc. |
| `LANG`             | `en_US.UTF-8`   | Browser locale/language. Override with `-e LANG=fr_FR.UTF-8` etc. to change the browser's locale.                                                                                                                                                                                                        |
| `USE_VIEWPORT`     | `false`         | Enables Playwright viewport control. Required if you need widths below ~450px (Firefox minimum without it). **Reduces stealth** because it adds Playwright viewport management. Only use for mobile layout testing on sites that don't have bot detection.                                               |
| `LOADERS_DIR`      | `/loaders`      | Directory the container scans for page loader YAML files. See [page-loaders.md](./page-loaders.md).                                                                                                                                                                                                      |
| `PROXY_URL`        | unset           | Routes all browser traffic through a proxy. Both `http://user:pass@host:port` and `socks5://host:port` work, but **Camoufox (Firefox) does not reliably support _authenticated_ SOCKS5** (username/password). For any exit that needs credentials, use an HTTP proxy. Unauthenticated SOCKS5 is fine. Include the port: a URL without one does not work. Write credentials unencoded: percent-escapes are passed through literally. The password may contain `@` and `:`; the username may not contain `:`; neither may contain `/`, `?` or `#`. Use an authorized exit whose location matches the browser fingerprint you are testing. |
| `HTTP_LISTEN_HOST` | `0.0.0.0`       | Host address the HTTP API binds to.                                                                                                                                                                                                                                                                      |
| `HTTP_LISTEN_PORT` | `8080`          | Port the HTTP API listens on.                                                                                                                                                                                                                                                                            |
| `AUTH_TOKEN`       | unset           | If set, all requests (except `/health`) require an `Authorization: Bearer <token>` header. Applies to both HTTP API and MCP.                                                                                                                                                                             |
| `DESK_EXEC_ENABLED` | `false` | `true` turns on `run_command` and `launch_app`, which run any command or program in the container as the browser user. Off by default so the API never gains shell access by itself. Set `AUTH_TOKEN` before turning it on. See [Desktop actions](api.md#desktop-actions). |
| `DESK_DARK_MODE` | `false` | `true` turns the desktop and the browser dark: the browser's toolbar, xterm, window title bars and the background (the base image's [dark mode](https://psyb0t.github.io/docker-auto-linux-desk/installation/#dark-mode)), and every tab, including tabs opened later, starts in the dark color scheme, as `set_color_scheme` with `dark` does. `set_color_scheme` still changes the tabs at runtime. Accepts `true`, `1`, `yes`, `on` and `false`, `0`, `no`, `off`; any other value stops startup. Pages can read `prefers-color-scheme`, so leave it off when the browser should look like a default install to sites. |
| `DESK_SUDO_ENABLED` | `false` | `true` gives the browser user passwordless sudo inside the container. See the base image's [passwordless sudo](https://psyb0t.github.io/docker-auto-linux-desk/installation/#passwordless-sudo). |
| `KEYBINDINGS_FILE` | unset | Path to a key map that replaces the window manager's whole keyboard map at startup. See the base image's [keyboard shortcuts](https://psyb0t.github.io/docker-auto-linux-desk/keyboard/#replacing-the-key-map). |
| `CAPTCHA_SOLVER_PROVIDER` | unset | Optional provider: `nopecha`, `capsolver`, `capmonster`, `nocaptchaai`, `yescaptcha`, `2captcha`, or `anticaptcha`. Selecting a provider enables it. |
| `CAPTCHA_SOLVER_ENABLED` | unset | Optional `true` or `false` switch (case-insensitive). A selected provider already enables support, and `true` still needs a provider. `false` conflicts with a provider or key. |
| `CAPTCHA_SOLVER_API_KEY` | unset | Provider API key. Mutually exclusive with `CAPTCHA_SOLVER_API_KEY_FILE`. Environment values are visible through `docker inspect`. NopeCHA alone permits no key. |
| `CAPTCHA_SOLVER_API_KEY_FILE` | unset | Path to a UTF-8 file containing the provider API key, typically a mounted Docker secret. |
| `VIRTUAL_MEDIA_DIR` | `/media` | Directory containing virtual media files. Configured source paths and dynamic source names must resolve inside this directory. Mount it read-only for static media; dynamic uploads require it to be writable by `PUID`/`PGID`, and the entrypoint does not repair its ownership. |
| `VIRTUAL_CAMERA_FILE` | unset | Video file to return as the video track from page `getUserMedia()`. Absolute or relative to `VIRTUAL_MEDIA_DIR`; validated at startup. |
| `VIRTUAL_MICROPHONE_FILE` | unset | Audio file to return as the audio track from page `getUserMedia()`. Absolute or relative to `VIRTUAL_MEDIA_DIR`; validated at startup. |
| `VIRTUAL_MEDIA_DYNAMIC` | `false` | Enables runtime selection and bounded upload of file-backed virtual camera and microphone sources. Accepts `true`, `1`, `yes` and `false`, `0`, `no` (not `on` or `off`); any other value stops startup. Existing static-media behavior is unchanged when false. Uploads require a safe filename with a declared video/audio type matching the requested kind. The service stores them under generated collision-safe basenames and checks them with `ffprobe` for the requested stream before storage or activation. |
| `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` | `52428800` (50 MiB) | Maximum decoded upload size accepted by `upload_virtual_media` when dynamic mode is enabled. A positive integer number of bytes; a unit suffix such as `100MiB` stops startup. |
| `VNC_LISTEN_HOST`  | `0.0.0.0`       | Host address VNC (noVNC + x11vnc) binds to.                                                                                                                                                                                                                                                              |
| `VNC_LISTEN_PORT`  | `5900`          | Port the noVNC web viewer listens on.                                                                                                                                                                                                                                                                    |
| `VNC_SHOW_CURSOR`  | `true`          | Shows the actual X11 pointer position in the noVNC framebuffer, including automated pointer moves. Set to `false` to hide it. Only `true` and `false` are accepted.                                                                                                                                       |
| `REDIS_URL`        | unset           | Redis connection string for cross-instance cookie sync. See [cluster-mode.md](./cluster-mode.md).                                                                                                                                                                                                        |
| `LOG_LEVEL`        | `INFO`          | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Filter what the JSON logger emits to stderr. An unknown value falls back to `INFO` without an error.                                                                                                                                                                                                                 |
| `LOG_FILE`         | unset           | If set, also write JSON logs to this file with 10MB × 5 backup rotation (in addition to stderr). Useful when you want a persistent log alongside `docker logs`.                                                                                                                                          |
| `PUID` | `1000` | UID used after the entrypoint repairs writable-directory ownership and drops privileges. |
| `PGID` | value of `PUID` | GID used for ownership and the browser process. |
| `NUM_REPLICAS` | `1` in a standalone container | Values greater than 1 restrict top-level HTTP actions and MCP tools to the cluster contract. The Compose deployment defaults to 5. |

The entrypoint also honors `DISPLAY` when deciding whether to start Xvfb. Leave it unset or at `:99` for the supported bundled display; x11vnc targets `:99`. `XAUTHORITY` is an internal X11 authentication setting, not API authentication.

The [cluster compose file](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/docker-compose.cluster.yml) defaults to five browser replicas and reads `NUM_REPLICAS`, `BROWSER_MEMORY_LIMIT` (default `5g`), `BROWSER_MEMORY_RESERVATION` (default `512m`), `QUEUE_TIMEOUT` (default `300`) and `TZ` (default `America/New_York` there, not `UTC`). See [cluster mode](./cluster-mode.md#environment-variables). Setting `REDIS_URL` enables automatic cookie-jar reconciliation every second, including website changes and deletions. Other profile state is not shared.

## CAPTCHA solver providers

Solver support is opt-in. The `nopecha`, `capsolver`, `capmonster`, `nocaptchaai`, `yescaptcha`, and `2captcha` providers use pinned official Firefox extensions. The browser verifies the selected archive's exact size and SHA-256 digest and caches it under `/userdata/.cache/captcha-solvers`. Each launch configures a temporary copy. Anti-Captcha uses its fixed official API endpoints because its official extension is Chrome-only.

Use a Docker secret when possible:

```yaml
services:
  browser:
    image: psyb0t/stealthy-auto-browse
    environment:
      CAPTCHA_SOLVER_PROVIDER: capsolver
      CAPTCHA_SOLVER_API_KEY_FILE: /run/secrets/captcha_solver_api_key
    secrets:
      - captcha_solver_api_key
    volumes:
      - browser-data:/userdata

secrets:
  captcha_solver_api_key:
    file: ./captcha-solver-api-key.txt

volumes:
  browser-data:
```

Direct keys work too, but Docker exposes environment values through container inspection. All providers except NopeCHA require a key. Setting any of the four `CAPTCHA_SOLVER_*` variables, even to an empty string, turns validation on, so a Compose file that passes `CAPTCHA_SOLVER_PROVIDER: ${CAPTCHA_SOLVER_PROVIDER:-}` through stops startup with `CAPTCHA_SOLVER_PROVIDER is required` when it is empty. Leave the variables out entirely to disable solving. Startup fails on conflicting settings or when an extension cannot be downloaded, verified, safely extracted, or configured.

An extension has access to every page and sends supported challenge data to its provider. Firefox can cache its configuration in the profile. Protect `/userdata`, remove it when rotating a key, review the provider's terms, and use solving only where authorized. The project does not promise accuracy, quotas, uptime, or compatibility.

## Examples

**Match timezone to IP location (important for stealth):**

```bash
docker run -d -e TZ=Europe/Bucharest -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Use a proxy:**

```bash
docker run -d -e PROXY_URL=http://user:pass@proxy:8888 -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Use a private [pr0xteus](https://github.com/psyb0t/pr0xteus) WireGuard HTTP proxy:**

[pr0xteus](https://github.com/psyb0t/pr0xteus) v0.11.0+ hands out an authenticated HTTP forward-proxy per lease (alongside a SOCKS5 one). Use the **HTTP** proxy, because pr0xteus leases are credentialed and Camoufox (Firefox) does not reliably support authenticated SOCKS5. First complete pr0xteus's [operator setup and real egress proof](https://github.com/psyb0t/pr0xteus/blob/main/docs/complete-example.md). By default it keeps the control API on `127.0.0.1:8000` and the HTTP proxy on `127.0.0.1:8080`, both on host loopback. Allocate a lease, read its HTTP proxy URL, then run the browser so it can reach that loopback address:

```bash
# Read the bearer token from pr0xteus's local .env (mode 0600, keep it local).
token="$(sed -n 's/^PR0XTEUS_API_TOKEN=//p' ~/.config/pr0xteus/.env)"

allocation="$(
  curl --fail-with-body --request POST \
    --header "Authorization: Bearer $token" \
    --header 'Content-Type: application/json' \
    --data '{"country":"US"}' \
    http://127.0.0.1:8000/v1/proxies
)"
# v0.11.0 returns proxies.socks5 and proxies.http (the old top-level .url is gone).
# Camoufox needs the HTTP one. Firefox can't reliably do authenticated SOCKS5.
proxy_url="$(jq -er '.proxies.http' <<<"$allocation")"

# Share the host network so the browser can reach pr0xteus's loopback proxy, and
# move the browser's own API off 8080 so it doesn't collide with the proxy on 8080.
docker run -d --name browser \
  --network host \
  -e HTTP_LISTEN_HOST=127.0.0.1 \
  -e HTTP_LISTEN_PORT=8090 \
  -e VNC_LISTEN_HOST=127.0.0.1 \
  -e PROXY_URL="$proxy_url" \
  psyb0t/stealthy-auto-browse

unset token allocation proxy_url
```

The browser API is now on `http://127.0.0.1:8090`. `proxy_url` is a short-lived `http://<lease-id>:<lease-secret>@127.0.0.1:8080` credential targeting pr0xteus's controller, which forwards to the selected WireGuard cell. Keep it out of logs and expect it to expire.

When pr0xteus runs on another machine and serves its proxies on a Tailscale name (its [Tailscale sidecar](https://github.com/psyb0t/pr0xteus/blob/main/docs/deploy.md)), the returned URL already points at that name, so the browser needs no host network:

```bash
# The token is PR0XTEUS_API_TOKEN from the pr0xteus .env on the other machine.
read -rsp 'pr0xteus API token: ' token
echo

allocation="$(
  curl --fail-with-body --request POST \
    --header "Authorization: Bearer $token" \
    --header 'Content-Type: application/json' \
    --data '{"country":"US"}' \
    http://pr0xteus.<tailnet>.ts.net/v1/proxies
)"
proxy_url="$(jq -er '.proxies.http' <<<"$allocation")"

docker run -d --name browser \
  -p 127.0.0.1:8080:8080 \
  -e PROXY_URL="$proxy_url" \
  psyb0t/stealthy-auto-browse

unset token allocation proxy_url
```

The Docker host must be on the same tailnet. A lease lasts 15 minutes by default (`TUNNEL_POOL_PROXY_LEASE_TTL`), and `PROXY_URL` is read once when the browser starts, so allocate the lease right before the run and start a new container with a new lease for the next one. This is also the way out when a site refuses after a few signups or requests from one IP: each lease can land on a different exit. Firefox does not send `localhost` through the proxy.

Both proxy URLs default to host loopback, so `--network host` is the simplest way for the browser container to reach them. If you can't share the host network, publish pr0xteus's HTTP proxy on an address the browser container can reach (`PR0XTEUS_HTTP_PROXY_HOST_PORT` / `PR0XTEUS_HTTP_PROXY_PUBLIC_ADDRESS`), or use its Tailscale sidecar, then pass that `.proxies.http` URL as `PROXY_URL`. See pr0xteus's [deploy docs](https://github.com/psyb0t/pr0xteus/blob/main/docs/deploy.md). Request a country/pool that actually matches your configured WireGuard node, then set `TZ`, locale, and other browser settings consistently with that exit. Add `AUTH_TOKEN` before letting another local process control the browser.

**Custom resolution:**

```bash
docker run -d -e XVFB_RESOLUTION=1280x720 -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Mobile viewport (for layout testing, reduces stealth):**

```bash
docker run -d -e USE_VIEWPORT=true -e XVFB_RESOLUTION=375x812 -p 8080:8080 psyb0t/stealthy-auto-browse
```

**Virtual camera and microphone (file-backed `getUserMedia()`):**

```bash
mkdir -p ./media
# Put a supported browser video file at ./media/camera.webm and audio file at ./media/microphone.wav.
docker run -d -p 8080:8080 \
  -v ./media:/media:ro \
  -e VIRTUAL_CAMERA_FILE=camera.webm \
  -e VIRTUAL_MICROPHONE_FILE=microphone.wav \
  psyb0t/stealthy-auto-browse
```

When virtual media is configured, the browser also makes `getUserMedia()` available to HTTP pages so controlled local test fixtures can report camera/microphone results directly. The browser resolves source paths at startup. They must stay inside `VIRTUAL_MEDIA_DIR` (including after symlink resolution), and require a browser restart to change. Treat the mounted media as test input for the pages you navigate to. [Virtual camera and microphone](./api.md#virtual-camera-and-microphone) describes what pages receive.

**Dynamic virtual media (runtime source switching):**

```bash
mkdir -p ./media
docker run -d -p 8080:8080 \
  -v ./media:/media:rw \
  -e VIRTUAL_MEDIA_DYNAMIC=true \
  -e AUTH_TOKEN=your-token-here \
  psyb0t/stealthy-auto-browse
```

Dynamic mode remains file-backed: `set_virtual_media_source` picks an existing file and `upload_virtual_media` stores a new one inside `VIRTUAL_MEDIA_DIR`. For uploads, the mounted directory must be writable by `PUID`/`PGID`; create it as that user, because the entrypoint does not repair its ownership. See [the API reference](./api.md#virtual-camera-and-microphone) for parameters and failure conditions.

## Persistent profiles

Mount a directory to `/userdata` to keep the Firefox profile, cookies, localStorage, and generated fingerprint across container removal and recreation. Restarting the same container retains its writable layer even without a mount. A profile mount does not preserve a live DOM, open workflow, or all sessionStorage state.

```bash
docker run -d \
  -p 8080:8080 \
  -p 5900:5900 \
  -v ./my-profile:/userdata \
  psyb0t/stealthy-auto-browse
```

This is how you maintain a logged-in session without re-authenticating every time the container restarts.

The persisted identity includes the navigator values, font list, GPU and WebGL parameters, speech voices, media devices, and audio seed. The window's size and position are not saved: the browser window is fitted inside the display and the page sees its real dimensions.

Profiles created by older images keep their cookies and identity. On the first start with a newer browser, the saved User-Agent moves to the installed Firefox version, values that stock Firefox decides for itself (Do Not Track, Global Privacy Control, `Accept-Encoding`, history length) are dropped, any missing voices, media devices, or audio seed are added, and a GPU the browser has no WebGL data for is replaced with one that fits the identity. Later starts reuse the updated file.

The image resolves Camoufox's bundled Linux font aliases through an absolute runtime fontconfig path, so `sans-serif`, `serif`, and `monospace` do not collapse to a single fallback family. The saved font list includes the image's installed Linux families and compatibility aliases used by canvas probes. Page canvas, main-thread `OffscreenCanvas`, and worker `OffscreenCanvas` therefore resolve the same fonts.

## Browser extensions

Pre-installed in every container:

| Extension           | What it does                                                                                                                                           |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **uBlock Origin**   | Blocks ads, trackers, and annoyances. Reduces page load noise and prevents tracking scripts from running.                                              |
| **LocalCDN**        | Intercepts requests to common CDNs (Google, Cloudflare, etc.) and serves the resources locally. Prevents CDN providers from tracking you across sites. |
| **ClearURLs**       | Strips tracking parameters from URLs (utm_source, fbclid, gclid, etc.) so your navigation doesn't leak referral data.                                  |
| **Consent-O-Matic** | Automatically handles cookie consent popups. It clicks "reject all" or minimal consent so you don't have to deal with them.                            |

Want to add more? Mount a persistent profile and install them through the browser:

1. Run with `-v ./my-profile:/userdata`
2. Open VNC at `http://localhost:5900/`
3. Navigate to `about:addons` and install whatever you want
4. Extensions persist across restarts via the profile volume

## Window manager

Openbox runs by default as the X11 window manager. It adds title bars and resize handles to popup windows (e.g. OAuth login dialogs) that would otherwise be too small to interact with. The main browser window is sized to fit under its title bar, so the whole page is on screen and the page reports the window's real size and position. Openbox is configured with one desktop, so wheel input over an uncovered screen edge cannot switch away from the browser. It also has keyboard shortcuts on `ctrl+alt` for half-screen and quarter tiling, window states, focusing the browser (`ctrl+alt+b`), the terminal (`ctrl+alt+t`) or a window by number, and opening a terminal; no shortcut uses the Super key. `desktop_keybindings` lists them (see [Desktop actions](api.md#desktop-actions) and the base image's [keyboard shortcuts](https://psyb0t.github.io/docker-auto-linux-desk/keyboard/)). No stealth impact, because the WM operates at the X11 display level, not the browser fingerprint level. Visible through VNC.

## VNC access

noVNC on port 5900 shows the browser live and has no authentication. [Installation and security](installation.md#authentication-and-exposure) covers how to publish it safely.
