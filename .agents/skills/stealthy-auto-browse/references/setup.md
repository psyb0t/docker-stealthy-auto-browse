# Setup

## Authorized Use Reminder

This skill is intended for authorized QA, compatibility testing, and defensive security research against sites you own or have written permission to test. Read the "Authorized Use Only" section of `SKILL.md` before configuring this for any non-trivial deployment.

## Requirements

- Docker
- curl

## Secure Defaults — Apply These First

Before running anything beyond a throwaway local smoke test:

1. **Bind to loopback.** Map ports as `-p 127.0.0.1:8080:8080` (and `127.0.0.1:5900:5900` if you need the viewer) so the API is not reachable from other machines on the network.
2. **Set `AUTH_TOKEN` to a strong random value.** `-e AUTH_TOKEN=$(openssl rand -hex 32)`. Required as soon as the service is reachable beyond localhost. Pass it via `Authorization: Bearer <key>` headers.
3. **Pin the image by digest.** Tags are mutable — pin the digest you've reviewed:
   ```bash
   docker pull psyb0t/stealthy-auto-browse:v1.0.0
   docker inspect --format='{{index .RepoDigests 0}}' psyb0t/stealthy-auto-browse:v1.0.0
   # → psyb0t/stealthy-auto-browse@sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c
   ```
   Use the `@sha256:...` form in every `docker run` and compose file. Re-pull and re-pin only when consciously upgrading.
4. **Don't mount the docker socket.** Don't run with `--privileged`. Don't grant the container egress beyond what the test target needs (use a Docker network with restricted egress where supported).
5. **Don't persist real session data.** If you mount `/userdata`, use a dedicated test account, encrypt the volume host-side if it leaves the machine, and shred (`rm -rf`) it when the test concludes.
6. **Disable the noVNC viewer if you don't need it.** Don't publish port 5900. If you do publish it for local debugging, bind it to `127.0.0.1` and never expose it on a public interface — the viewer gives full keyboard/mouse control of the browser, including any logged-in sessions.

## Quick Start (localhost, no auth — for smoke tests only)

```bash
DIGEST=sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c

docker run -d --name browser \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:5900:5900 \
  psyb0t/stealthy-auto-browse@$DIGEST
```

**Verify:** `curl http://127.0.0.1:8080/health` returns `ok` when the browser is ready (~10s first boot).
**Watch the browser:** `http://127.0.0.1:5900/` in your own browser.

## Recommended Run (auth enabled, hardened)

```bash
DIGEST=sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c
TOKEN=$(openssl rand -hex 32)
echo "AUTH_TOKEN=$TOKEN" > .env.browser   # gitignored
chmod 600 .env.browser

docker run -d --name browser \
  -p 127.0.0.1:8080:8080 \
  --env-file .env.browser \
  -e HTTP_LISTEN_HOST=0.0.0.0 \
  --cap-drop=ALL \
  --cap-add=SYS_ADMIN \
  --security-opt=no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid \
  psyb0t/stealthy-auto-browse@$DIGEST
```

Notes on the hardening flags:

- `HTTP_LISTEN_HOST=0.0.0.0` makes the API reachable inside the container on all interfaces, but the `-p 127.0.0.1:8080:8080` mapping confines it to host loopback. This is the correct combination.
- `SYS_ADMIN` is needed by the embedded Firefox sandbox; everything else is dropped.
- Adjust `--read-only` if you need `/userdata` mounted (you'll have to mount that path writable).
- Same applies to `/recordings` if you use screen recording — mount it writable, which means dropping or adjusting `--read-only` for that path too.
- Dynamic virtual-media uploads also need a writable `VIRTUAL_MEDIA_DIR`; static virtual media may remain read-only.

## Environment Variables

| Variable | Default | What It Does |
|----------|---------|-------------|
| `XVFB_RESOLUTION` | `1920x1080` | Virtual display resolution. Any size works (e.g. `1920x1920`, `2560x1440`); the framebuffer is allocated to match, so screen recording captures this exact size. Larger = more memory. |
| `XVFB_DEPTH` | `24` | Color depth (16/24/32). |
| `TZ` | `UTC` | Match your IP location for realistic test fingerprints. |
| `PROXY_URL` | — | HTTP proxy for all browser traffic. Format: `http://user:pass@host:port`. Use only proxies you own or are authorized to use. |
| `LOADERS_DIR` | `/loaders` | Directory for page loader YAML files. |
| `USE_VIEWPORT` | `false` | Playwright viewport control. Required for width < ~450px. Makes automation easier to fingerprint. |
| `HTTP_LISTEN_HOST` | `0.0.0.0` | HTTP API bind address inside the container. Combine with a `127.0.0.1:8080:8080` port mapping to confine to localhost on the host. |
| `HTTP_LISTEN_PORT` | `8080` | HTTP API port. |
| `AUTH_TOKEN` | — | **Set this for any non-trivial deployment.** Without it, anyone who can reach the port can control the browser. With it, requests need an `Authorization: Bearer <key>` header. |
| `VIRTUAL_MEDIA_DIR` | `/media` | Directory containing virtual camera/microphone media. Configured and dynamic source paths must resolve within it. Mount it read-only for static sources; dynamic uploads require it to be writable. |
| `VIRTUAL_CAMERA_FILE` | — | Video file returned as the video track from page `getUserMedia()`. Relative to `VIRTUAL_MEDIA_DIR` or an absolute path inside it. A request for video without this source fails with `NotFoundError`. |
| `VIRTUAL_MICROPHONE_FILE` | — | Audio file returned as the audio track from page `getUserMedia()`. Relative to `VIRTUAL_MEDIA_DIR` or an absolute path inside it. A request for audio without this source fails with `NotFoundError`. |
| `VIRTUAL_MEDIA_DYNAMIC` | `false` | Enables runtime file-backed source selection and upload for virtual camera/microphone tests. |
| `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` | `50 MiB` | Maximum decoded upload size accepted by `upload_virtual_media` in dynamic mode. |
| `VNC_LISTEN_HOST` | `0.0.0.0` | VNC bind address inside the container. As above, prefer a `127.0.0.1:5900:5900` host port mapping. |
| `VNC_LISTEN_PORT` | `5900` | noVNC web viewer port. **The viewer has no authentication of its own** — only publish to localhost. |
| `PUID` | `1000` | Run the container as this UID. Ownership of `/userdata`, `/loaders`, `/recordings` is fixed up to match at startup. |
| `PGID` | value of `PUID` | Run the container as this GID. |
| `LANG` | `en_US.UTF-8` | Browser locale/language. |
| `LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR` for the JSON logger. |
| `LOG_FILE` | — | If set, also write JSON logs to this file (10MB x 5 rotation) in addition to stderr. |

## Common Configurations

```bash
DIGEST=sha256:7ce5d42ddb3b7fdbfb4af2d4bf6072f5a862d5dd2b64c7feb496e493f587223c

# Match timezone for realistic fingerprint during testing
docker run -d -p 127.0.0.1:8080:8080 \
  -e TZ=Europe/Bucharest \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# Route through your authorized proxy
docker run -d -p 127.0.0.1:8080:8080 \
  -e PROXY_URL=http://user:pass@proxy.you-own.example:8888 \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# Custom resolution
docker run -d -p 127.0.0.1:8080:8080 \
  -e XVFB_RESOLUTION=1280x720 \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# Persistent profile — TEST ACCOUNTS ONLY, shred when done
mkdir -p ./profile && chmod 700 ./profile
docker run -d -p 127.0.0.1:8080:8080 \
  -v ./profile:/userdata \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST
# When the test run ends:
docker rm -f browser && rm -rf ./profile

# Custom listen ports
docker run -d -p 127.0.0.1:9090:9090 -p 127.0.0.1:6900:6900 \
  -e HTTP_LISTEN_PORT=9090 -e VNC_LISTEN_PORT=6900 \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# With page loaders
docker run -d -p 127.0.0.1:8080:8080 \
  -v ./my-loaders:/loaders \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# With screen recording — /recordings must be mounted writable or
# start_recording fails fast
mkdir -p ./recordings
docker run -d -p 127.0.0.1:8080:8080 \
  -v ./recordings:/recordings \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# File-backed virtual camera and microphone for an authorized WebRTC test
mkdir -p ./media
docker run -d -p 127.0.0.1:8080:8080 \
  -v ./media:/media:ro \
  -e VIRTUAL_CAMERA_FILE=camera.webm \
  -e VIRTUAL_MICROPHONE_FILE=microphone.wav \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST

# Dynamic file-backed media for an authorized compatibility test.
# The media mount must be writable only when uploads are needed.
docker run -d -p 127.0.0.1:8080:8080 \
  -v ./media:/media:rw \
  -e VIRTUAL_MEDIA_DYNAMIC=true \
  --env-file .env.browser \
  psyb0t/stealthy-auto-browse@$DIGEST
```

With `VIRTUAL_MEDIA_DYNAMIC=true`, use authenticated `set_virtual_media_source`
requests to choose an existing relative source name under `VIRTUAL_MEDIA_DIR`, or
authenticated `upload_virtual_media` requests to store bounded base64 content and
optionally activate it. `kind` is `"camera"` or `"microphone"`; an upload filename
must be a safe basename with a declared media type matching that kind. It supplies
only the extension: the service generates and returns a collision-safe stored basename
and never overwrites a named source. Decoded uploads are checked with `ffprobe` for
the requested video or audio stream before storage or activation. Uploads are capped
by `VIRTUAL_MEDIA_UPLOAD_MAX_BYTES` (50 MiB by default). A source switch preserves
the tracks that a page already received from `getUserMedia()`.

Only regular files contained by `VIRTUAL_MEDIA_DIR` are accepted. Dynamic mode does
not accept arbitrary host paths, remote URLs, WebSocket streams, or other live
ingress. When `AUTH_TOKEN` is set, send the same `Authorization: Bearer <key>` header
used for every other action.

## OpenClaw / ClawHub Config

```bash
export STEALTHY_AUTO_BROWSE_URL=http://127.0.0.1:8080
export AUTH_TOKEN=$(cat .env.browser | grep AUTH_TOKEN | cut -d= -f2)
```

Or via `~/.openclaw/openclaw.json`:

```json
{
  "skills": {
    "entries": {
      "stealthy-auto-browse": {
        "env": {
          "STEALTHY_AUTO_BROWSE_URL": "http://127.0.0.1:8080",
          "AUTH_TOKEN": "your-token-here"
        }
      }
    }
  }
}
```

Keep `~/.openclaw/openclaw.json` mode `600` if it contains real tokens, or read the token from a separate file the config refers to.

## Cluster Mode Setup

Run multiple browser instances behind HAProxy with request queuing, sticky sessions, and Redis cookie sync. The number of instances is controlled by `NUM_REPLICAS` (default: 10):

**Do not pipe the compose file from a moving branch.** Download it, review it, and pin to a release tag or commit SHA you've audited:

```bash
# Pick a release tag (or commit SHA) you've reviewed.
RELEASE=v1.0.0

# Download to a local file — do NOT pipe directly into docker compose.
curl -fsSL -o docker-compose.cluster.yml \
  "https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/${RELEASE}/docker-compose.cluster.yml"

# Read it. Confirm the images, networks, and volume mounts match what you expect.
$EDITOR docker-compose.cluster.yml

# Pin each image in the compose file to a digest you've verified before bringing it up.
docker compose -f docker-compose.cluster.yml up -d
```

Entry point is `http://127.0.0.1:8080` — same API and MCP endpoint as single-container mode. Bind HAProxy's published port to `127.0.0.1` in the compose file unless you have a deliberate reason for broader exposure.

Set `STEALTHY_AUTO_BROWSE_URL=http://127.0.0.1:8080` and `AUTH_TOKEN=...` as usual.

## Pre-installed Extensions

- **uBlock Origin** — ad/tracker blocking
- **LocalCDN** — serves CDN resources locally
- **ClearURLs** — strips tracking parameters
- **Consent-O-Matic** — auto-handles cookie popups

## Tear-Down

When a test run is done:

```bash
docker rm -f browser           # stop + remove the container
rm -rf ./profile               # shred persisted session data if you mounted /userdata
rm -f .env.browser             # rotate / remove the AUTH_TOKEN file
```

Rotate any test-account credentials that were exercised by the run if those accounts are used by humans too.
