# Configuration

## Environment Variables

| Variable           | Default         | What It Does                                                                                                                                                                                                                                                                                             |
| ------------------ | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `XVFB_RESOLUTION`  | `1920x1080`     | Virtual display resolution. The browser runs at this size and the Xvfb framebuffer is allocated to match, so any width/height works (e.g. `1280x720`, `1920x1920`, `2560x1440`). Larger framebuffers use more memory and are slower to software-render, but there is no hard cap. Screen recording captures this exact size — set a square/tall resolution here if that's what you need to record. |
| `XVFB_DEPTH`       | `24`            | Color depth of the virtual display (16, 24, or 32 bit). 24 is fine for everything.                                                                                                                                                                                                                       |
| `TZ`               | `UTC`           | **Timezone — this one matters for stealth.** Bot detectors compare your browser's timezone against your IP's geographic location. If your IP says you're in Romania but your timezone says UTC, that's a red flag. Set this to match your IP: `Europe/Bucharest`, `America/New_York`, `Asia/Tokyo`, etc. |
| `LANG`             | `en_US.UTF-8`   | Browser locale/language. Override with `-e LANG=fr_FR.UTF-8` etc. to change the browser's locale.                                                                                                                                                                                                        |
| `USE_VIEWPORT`     | `false`         | Enables Playwright viewport control. Required if you need widths below ~450px (Firefox minimum without it). **Reduces stealth** because it adds Playwright viewport management. Only use for mobile layout testing on sites that don't have bot detection.                                               |
| `LOADERS_DIR`      | `/loaders`      | Directory the container scans for page loader YAML files. See [page-loaders.md](./page-loaders.md).                                                                                                                                                                                                      |
| `PROXY_URL`        | —               | Routes all browser traffic through a proxy. Both `http://user:pass@host:port` and `socks5://host:port` work, but **Camoufox (Firefox) does not reliably support _authenticated_ SOCKS5** (username/password) — for any exit that needs credentials, use an HTTP proxy. Unauthenticated SOCKS5 is fine. Use an authorized exit whose location matches the browser fingerprint you are testing. |
| `HTTP_LISTEN_HOST` | `0.0.0.0`       | Host address the HTTP API binds to.                                                                                                                                                                                                                                                                      |
| `HTTP_LISTEN_PORT` | `8080`          | Port the HTTP API listens on.                                                                                                                                                                                                                                                                            |
| `AUTH_TOKEN`       | —               | If set, all requests (except `/health`) require an `Authorization: Bearer <token>` header. Applies to both HTTP API and MCP.                                                                                                                                                                             |
| `VIRTUAL_MEDIA_DIR` | `/media` | Directory containing virtual media files. Configured source paths and dynamic source names must resolve inside this directory. Mount it read-only for static media; dynamic uploads require it to be writable. |
| `VIRTUAL_CAMERA_FILE` | — | Video file to return as the video track from page `getUserMedia()`. Absolute or relative to `VIRTUAL_MEDIA_DIR`; validated at startup. |
| `VIRTUAL_MICROPHONE_FILE` | — | Audio file to return as the audio track from page `getUserMedia()`. Absolute or relative to `VIRTUAL_MEDIA_DIR`; validated at startup. |
| `VIRTUAL_MEDIA_DYNAMIC` | `false` | Enables runtime selection and bounded upload of file-backed virtual camera and microphone sources. Existing static-media behavior is unchanged when false. Uploads require a safe filename with a declared video/audio type matching the requested kind, are stored under generated collision-safe basenames, and are `ffprobe`-checked for the requested stream before storage or activation. |
| `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` | `50 MiB` | Maximum decoded upload size accepted by `upload_virtual_media` when dynamic mode is enabled. |
| `VNC_LISTEN_HOST`  | `0.0.0.0`       | Host address VNC (noVNC + x11vnc) binds to.                                                                                                                                                                                                                                                              |
| `VNC_LISTEN_PORT`  | `5900`          | Port the noVNC web viewer listens on.                                                                                                                                                                                                                                                                    |
| `REDIS_URL`        | —               | Redis connection string for cross-instance cookie sync. See [cluster-mode.md](./cluster-mode.md).                                                                                                                                                                                                        |
| `LOG_LEVEL`        | `INFO`          | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. Filter what the JSON logger emits to stderr.                                                                                                                                                                                                                 |
| `LOG_FILE`         | —               | If set, ALSO write JSON logs to this file with 10MB × 5 backup rotation (in addition to stderr). Useful when you want a persistent log alongside `docker logs`.                                                                                                                                          |

The cluster compose file defaults to five browser replicas and reads `NUM_REPLICAS`, `BROWSER_MEMORY_LIMIT` (default `5g`), and `BROWSER_MEMORY_RESERVATION` (default `512m`) to size the fleet. See [cluster mode](./cluster-mode.md#environment-variables).

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

[pr0xteus](https://github.com/psyb0t/pr0xteus) v0.11.0+ hands out an authenticated HTTP forward-proxy per lease (alongside a SOCKS5 one). Use the **HTTP** proxy: pr0xteus leases are credentialed, and Camoufox (Firefox) does not reliably support authenticated SOCKS5. First complete pr0xteus's [operator setup and real egress proof](https://github.com/psyb0t/pr0xteus/blob/main/docs/complete-example.md). By default it keeps the control API on `127.0.0.1:8000` and the HTTP proxy on `127.0.0.1:8080`, both on host loopback. Allocate a lease, read its HTTP proxy URL, then run the browser so it can reach that loopback address:

```bash
# Read the bearer token from pr0xteus's local .env (mode 0600 — keep it local).
token="$(sed -n 's/^PR0XTEUS_API_TOKEN=//p' ~/.config/pr0xteus/.env)"

allocation="$(
  curl --fail-with-body --request POST \
    --header "Authorization: Bearer $token" \
    --header 'Content-Type: application/json' \
    --data '{"country":"US"}' \
    http://127.0.0.1:8000/v1/proxies
)"
# v0.11.0 returns proxies.socks5 and proxies.http (the old top-level .url is gone).
# Camoufox needs the HTTP one — Firefox can't reliably do authenticated SOCKS5.
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

The browser API is now on `http://127.0.0.1:8090`. `proxy_url` is a short-lived `http://<lease-id>:<lease-secret>@127.0.0.1:8080` credential targeting pr0xteus's controller, which forwards to the selected WireGuard cell — keep it out of logs and expect it to expire.

Both proxy URLs default to host loopback, so `--network host` is the simplest way for the browser container to reach them. If you can't share the host network, publish pr0xteus's HTTP proxy on an address the browser container can reach (`PR0XTEUS_HTTP_PROXY_HOST_PORT` / `PR0XTEUS_HTTP_PROXY_PUBLIC_ADDRESS`), or use its Tailscale sidecar, then pass that `.proxies.http` URL as `PROXY_URL` — see pr0xteus's [deploy docs](https://github.com/psyb0t/pr0xteus/blob/main/docs/deploy.md). Request a country/pool that actually matches your configured WireGuard node, then set `TZ`, locale, and other browser settings consistently with that exit. Add `AUTH_TOKEN` before letting another local process control the browser.

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

The files are supplied only to page `navigator.mediaDevices.getUserMedia()` calls; they do not create native devices in `enumerateDevices()`. When virtual media is configured, `getUserMedia()` is also made available to HTTP pages so controlled local test fixtures can report camera/microphone results directly. Requests for a kind without a configured source fail with `NotFoundError` rather than using hardware; virtual tracks retain the source file's format and do not emulate incompatible exact media constraints. Source paths are resolved at browser startup, must stay inside `VIRTUAL_MEDIA_DIR` (including after symlink resolution), and require a browser restart to change. Treat the mounted media as test input for the pages you navigate to.

**Dynamic virtual media (runtime source switching):**

```bash
mkdir -p ./media
docker run -d -p 8080:8080 \
  -v ./media:/media:rw \
  -e VIRTUAL_MEDIA_DYNAMIC=true \
  -e AUTH_TOKEN=your-token-here \
  psyb0t/stealthy-auto-browse
```

Dynamic mode remains file-backed: `set_virtual_media_source` accepts an existing relative source name, and `upload_virtual_media` stores a bounded base64 upload inside `VIRTUAL_MEDIA_DIR`. Its filename must be a safe basename with a declared media type matching the requested kind; it supplies only the extension. The service generates and returns a collision-safe stored basename rather than overwriting a named source, and checks the decoded file with `ffprobe` for the requested stream before storage or activation. The directory must be writable for uploads; the decoded payload limit is `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` (50 MiB by default). A page that has already received a virtual stream keeps the same camera/microphone track identities while the source changes. Dynamic mode does not accept arbitrary host paths, remote URLs, WebSocket streams, or other live media ingress. Both actions use the normal Bearer authentication when `AUTH_TOKEN` is set. See [the API reference](./api.md#virtual-camera-and-microphone) for parameters and failure conditions.

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
