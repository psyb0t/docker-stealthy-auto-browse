---
title: Home
description: Control a Dockerized Camoufox browser through HTTP or MCP, with OS-level input, persistent profiles, screenshots, and recording.
nav_order: 0
permalink: /
seo:
  type: WebSite
---

# stealthy-auto-browse

One container runs Camoufox, a graphical display, an HTTP API, an MCP server, and a noVNC viewer. A client sends browser actions. The browser keeps its tabs and session state, and you can watch or take over through noVNC.

Use it for authorized browser automation, compatibility testing, and QA. It offers both Playwright element actions and real X11 pointer, keyboard, and wheel input. Neither mode guarantees that a site will accept the session or classify it as human.

## Start here

```bash
docker run -d --name browser \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:5900:5900 \
  psyb0t/stealthy-auto-browse:latest
```

Open `http://localhost:5900/` for the viewer. Send HTTP commands to `http://localhost:8080/`, or connect an MCP client to `http://localhost:8080/mcp/`. This example is loopback-only and unauthenticated. See [installation and security](installation.md) before making it reachable from another machine.

```bash
curl --fail-with-body http://localhost:8080/ \
  -H 'Content-Type: application/json' \
  -d '{"action":"goto","url":"https://example.com"}'
```

## What it supports

| Area | Features and reference |
| --- | --- |
| Browser control | [Navigation, tabs and windows, dialogs, permissions, geolocation, cookies, and storage](api.md). |
| Element targeting | [CSS, XPath, accessible roles, iframe chains, open shadow roots, and reusable element references](api.md#element-targets). |
| Input | [DOM actions, OS-level mouse gestures, typing, scrolling, and visible reading pauses](api.md#humanized-input). |
| Capture | [Text, HTML, accessibility snapshots, PNG screenshots, MP4 recording, network logs, and HAR export](api.md). |
| Browser events | [Bounded event history, waits, SSE delivery, and human handoff](api.md#events-and-human-handoff). |
| Clipboard and media | [Text, HTML, PNG, file lists, file-backed camera and microphone streams, and runtime source switching](api.md). |
| Challenges | [Best-effort detection](api.md#challenge-detection), [optional solver providers](configuration.md#captcha-solver-providers), and human takeover. |
| Workflows | [Atomic multi-step requests and bounded YAML control flow](script-mode.md), plus [URL-triggered page loaders](page-loaders.md). |
| Session identity | [Persistent profiles and fingerprint upgrades](configuration.md#persistent-profiles), [input models and their limits](stealth.md). |
| Clients | [HTTP and MCP setup, dedicated MCP tools, and published agent integrations](agents.md). |
| Replicas | [HAProxy queues, sticky sessions, and Redis cookie sharing](cluster-mode.md). Browser state other than cookies remains per replica. |

## Operate and contribute

[Configuration](configuration.md) owns settings and defaults. [Troubleshooting](troubleshooting.md) covers startup, authentication, browser recovery, and cluster problems. [Development](development.md) explains the source layout, build, deterministic tests, and opt-in external checks.

The [source repository](https://github.com/psyb0t/docker-stealthy-auto-browse) contains the [license](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/LICENSE) and [changelog](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/CHANGELOG.md).
