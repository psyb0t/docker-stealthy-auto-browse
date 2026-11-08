---
title: Installation and security
description: Start the Docker image, protect HTTP and noVNC access, retain browser data, update containers, and remove a deployment safely.
nav_order: 1
---

# Installation and security

Docker is the runtime requirement. The published image is `psyb0t/stealthy-auto-browse:latest`; the repository name includes `docker-`, the image and application name do not. Use Docker Compose for the optional [cluster](cluster-mode.md), or build from source through the [development targets](development.md).

## Start a persistent browser

```bash
mkdir -p ./browser-profile ./recordings
docker run -d --name browser \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:5900:5900 \
  -v ./browser-profile:/userdata \
  -v ./recordings:/recordings \
  -e PUID="$(id -u)" -e PGID="$(id -g)" \
  psyb0t/stealthy-auto-browse:latest
```

The viewer is `http://localhost:5900/`. The HTTP API is on port 8080 and the MCP endpoint is `/mcp/`. The API listens on `0.0.0.0` inside the container so Docker port forwarding works. The explicit `127.0.0.1` publish addresses restrict access on the host.

The entrypoint initially runs as root to adjust writable-directory ownership, then switches to `PUID`/`PGID` through `gosu`. Mount only directories intended for the browser. A broad home-directory mount could expose unrelated credentials, and ownership repair is recursive under `/userdata`, `/loaders`, and `/recordings`.

## Authentication and exposure

Set `AUTH_TOKEN` through a private environment file when other users or processes can reach the API. Keep it out of Git and out of copied example commands. `AUTH_TOKEN` protects HTTP and MCP, but **does not protect noVNC or raw VNC**. noVNC listens on port 5900 by default; the internal x11vnc server uses port 5901 without a password. Do not publish either viewer endpoint publicly. Use an authenticated reverse proxy, an SSH tunnel, or a restricted private network.

```bash
curl --fail-with-body http://localhost:8080/ \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action":"ping"}'
```

Unset `AUTH_TOKEN` means no API authentication. Query-string tokens are rejected when authentication is enabled. `/health` is always unauthenticated and returns `ok`; use `ping` or an actual browser operation when checking browser functionality.

An authenticated caller has broad browser control: page navigation and JavaScript, session cookies, clipboard data, file upload from accessible container paths, and script-mode file output. This is not a multi-tenant sandbox. Use one deployment per trust boundary. Protect profile, media, loader, recording, and Redis storage as sensitive data. Captured HTML, screenshots, HAR bodies, and console output can contain secrets even when request headers are redacted.

JavaScript dialogs are auto-accepted unless `handle_dialog` configures the next one otherwise. Set `accept: false` before an action that might trigger a destructive confirmation.

## Persist and update

`/userdata` holds the Firefox profile and generated identity. `/recordings` holds captured MP4 files. `/loaders` holds trusted YAML actions. `/media` holds optional camera and microphone files. See [configuration](configuration.md) for paths, ownership, fingerprint upgrade behavior, and solver-key storage.

Stop your browser cleanly before backing up its profile. Copying a live Firefox profile can produce an inconsistent backup. Do not share one profile directory between running containers. A Docker container restart retains its writable layer, but removal and recreation require a mounted profile to preserve it. SessionStorage and live page state do not become durable merely because a profile is mounted.

To update, pull `psyb0t/stealthy-auto-browse:latest`, stop and remove your existing browser container, then recreate it with the same mounts and settings. `docker restart` does not switch to a newly pulled image. Review the [changelog](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/CHANGELOG.md) first and keep a profile backup before browser upgrades.

To uninstall, stop and remove only your browser container. Its bind-mounted data remains on the host. Remove that data separately only if you intend to discard saved sessions and recordings. For Compose, `down` retains named volumes unless explicitly told to remove them.
