---
title: Cluster mode
description: Run browser replicas behind HAProxy with queued requests, sticky routing, Redis cookie synchronization, and memory controls.
nav_order: 7
---

# Cluster mode

Run a fleet of browser instances behind HAProxy with automatic queuing, sticky sessions, and Redis cookie sync. Each browser serializes actions, and the ordinary HTTP backend queues connections when its per-browser slots are occupied.

Automatic website-cookie reconciliation was added after v3.5.1. Images tagged v3.5.1 or older only publish explicit cookie API writes. Use an image built from the updated source, or `latest` after its successful build, for automatic sharing.

## What you get

- **Multiple browser containers** (default 5, configurable via `NUM_REPLICAS`) behind a single entry point on port 8080
- **HAProxy queue-proxy** that holds incoming requests when all instances are busy, instead of returning errors
- **Sticky sessions** via `INSTANCEID` cookie. Once HAProxy routes a client to a browser instance, it stays there
- **Redis cookie sync**: website and API cookie changes reconcile through Redis. New instances load shared cookies on startup.
- **Script-only enforcement**: MCP exposes only `run_script` when `NUM_REPLICAS > 1`; HTTP also permits `ping` and `sleep`.

## Script-only mode (v1.0.0+)

When `NUM_REPLICAS > 1`, both the HTTP API and MCP server **reject** individual actions like `goto`, `get_text`, and `click`. MCP exposes only `run_script`. HTTP allows `run_script`, `ping`, and `sleep`.

**Why:** Sequential calls (goto → get_text → click) may hit different browser instances if the client or MCP gateway doesn't maintain session stickiness (e.g., LiteLLM doesn't propagate the `Mcp-Session-Id` header between tool calls). This causes stale content bugs where `get_text` returns the previous page's content because it landed on a different instance that never navigated.

**Solution:** `run_script` executes all steps atomically in a single HTTP request. One request = one routing decision = one browser instance handles the entire sequence. No session stickiness required.

```bash
# This works in cluster mode:
curl -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "run_script",
    "steps": [
      {"action": "goto", "url": "https://example.com"},
      {"action": "get_text", "output_id": "text"}
    ]
  }'

# This is REJECTED in cluster mode:
curl -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -d '{"action": "goto", "url": "https://example.com"}'
# → {"success": false, "error": "Action 'goto' not available in cluster mode. Use run_script to execute multiple actions atomically."}
```

The MCP server exposes only the `run_script` tool in cluster mode, with a description that documents every available action and its parameters so LLMs know what steps to include in their scripts.

Interactive handoff is intended for a standalone browser. If a cluster script leaves `begin_handoff` active when it returns, a later `run_script` is itself blocked by the handoff guard, while standalone `end_handoff` is rejected by the cluster action restriction. Do not leave handoff active across cluster requests. Use a separately controlled standalone browser for an interactive manual session.

**Recording in cluster mode:** `start_recording` and `stop_recording` must live in the same `run_script` call so both steps hit the same browser instance. Mount `/recordings` on every browser container in [docker-compose.cluster.yml](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/docker-compose.cluster.yml). Prefer per-replica storage or globally unique caller slugs; filename collision handling is local to each recorder, not a distributed naming service.

## Quick start

```bash
curl -LO https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/main/docker-compose.cluster.yml
docker compose -f docker-compose.cluster.yml up -d
```

This starts Redis, five browser containers by default, and the HAProxy queue-proxy. The entry point is `http://localhost:8080`, with the same API as single-container mode. The MCP endpoint at `/mcp/` also works through the proxy with the same sticky session routing.

### Scale up

```bash
# Scale to 20 instances
NUM_REPLICAS=20 docker compose -f docker-compose.cluster.yml up -d
```

### Watch the queue

```bash
docker compose -f docker-compose.cluster.yml logs -f queue-proxy
```

## Environment variables

| Variable         | Default          | What it does                                                                                                                                              |
| ---------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `REDIS_URL`      | unset            | Redis connection string. Set automatically in cluster mode (`redis://redis:6379`). Set this on standalone containers to join them to a Redis sync cluster. |
| `QUEUE_TIMEOUT`  | `300`            | Seconds a request will wait in the queue before HAProxy returns a 503. Increase for long-running tasks.                                                   |
| `NUM_REPLICAS`   | `5`              | Number of browser instances. Controls both the container replica count and HAProxy server list. Single source of truth.                                   |
| `TZ`             | `America/New_York` | Timezone for all browser instances. Set to match your IP's geographic location.                                                                         |
| `BROWSER_MEMORY_LIMIT` | `5g` | Per-browser hard memory limit used by `docker-compose.cluster.yml`. Lower it only after validating your workload; 512MB can OOM-kill a browser at the default display size. |
| `BROWSER_MEMORY_RESERVATION` | `512m` | Per-browser memory reservation used by `docker-compose.cluster.yml`. |

## HAProxy queue-proxy

HAProxy's ordinary HTTP backend limits each browser server to one connection (`maxconn 1`). When those slots are occupied, requests queue until the configured timeout. `leastconn` chooses a server with the fewest active connections, not the oldest idle server. The separate MCP backend omits `maxconn 1` to support persistent protocol connections. Each browser still serializes action execution internally.

### Endpoints

| Endpoint           | What it does                                                            |
| ------------------ | ----------------------------------------------------------------------- |
| `/__queue/health`  | Returns `{"status":"ok"}`. Use this for load balancer health checks.   |
| `/__queue/status`  | Returns `{"status":"ok","num_replicas":N}`. **Private networks only** (10.x, 172.16.x, 192.168.x, 127.x). Returns 403 from public IPs. |

### Stats dashboard

HAProxy exposes a stats dashboard on port **8081**. Open `http://localhost:8081/` to see live traffic, queue depth, server health, and request rates per instance.

## Sticky sessions

HAProxy's ordinary browser backend adds an `INSTANCEID` response cookie containing the server name (for example `browser1` or `browser3`). Send that cookie on subsequent HTTP action requests. The separate MCP backend uses the `Mcp-Session-Id` header instead; queue-health and stats responses do not assign a browser instance.

**Not sending `INSTANCEID` is intentional for self-contained requests.** If you POST a full script (navigate to a site, type something, press enter, get the HTML), HAProxy picks any free browser and the whole thing happens in one shot. You don't care which instance handled it, and not sending the cookie means you get proper load balancing across the fleet.

**Send `INSTANCEID` when you make more than one request.** Each browser container is a full, stateful browser. Open tabs, DOM state, JS variables, scroll position, local storage, everything lives in that specific container. If your second request depends on anything your first request did (navigated to a page, typed in a field, opened a tab), you need to route back to the same instance. The only thing that syncs across instances automatically is cookies (via Redis).

**Let HAProxy assign an instance, then stick to it:**

```bash
# First request: capture the INSTANCEID from the response
INSTANCEID=$(curl -sS -D - -o /dev/null -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -d '{"action":"run_script","steps":[{"action":"goto","url":"https://example.com"}]}' \
  | grep -i 'set-cookie:.*INSTANCEID' \
  | sed 's/.*INSTANCEID=\([^;]*\).*/\1/' | tr -d '\r')

# Later requests: send INSTANCEID to route to the same browser
curl -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -H "Cookie: INSTANCEID=$INSTANCEID" \
  -d '{"action":"run_script","steps":[{"action":"get_text","output_id":"text"}]}'
```

**Target a specific instance directly:**

```bash
curl -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -H 'Cookie: INSTANCEID=browser3' \
  -d '{"action":"run_script","steps":[{"action":"get_text","output_id":"text"}]}'
```

## Redis cookie sync

When `REDIS_URL` is set, each browser observes its cookie jar every second. Website `Set-Cookie` responses, JavaScript `document.cookie` changes, HttpOnly cookies, API changes, individual deletions, and expiry are reconciled with the shared Redis hash. No extra `get_cookies` call on the source browser is required.

- Cookie identity is the combination of name, domain, and path. A changed cookie replaces that identity; a deletion does not clear unrelated cookies.
- A Redis transaction updates `SABROWSE:COOKIES` and publishes on `SABROWSE:UPDATE`. Peers reconcile the authoritative hash after notifications and on their regular poll, so missed notifications can recover.
- Received changes update the local observation baseline and are not republished as new website changes. Expired records are removed rather than reinstalled.
- Concurrent independent cookie changes merge. If the shared value changed since a replica's last successful observation, that committed value wins over a conflicting stale browser snapshot. Explicit API `set_cookie` writes are intentional and can replace that value at their Redis serialization point. This is eventual consistency, not a distributed browser transaction.
- On startup, shared cookies override matching profile cookies. Profile-only cookies seed missing shared entries. An old profile can therefore reintroduce a cookie absent from Redis; use clean profiles when starting a fleet whose shared session was deliberately cleared.
- Browser detach and shutdown do not publish an empty jar. Redis or browser errors retry without advancing the last successful observation, and diagnostics omit cookie values and Redis credentials.

Cookie-based sessions can become usable on other replicas after propagation. There is no instantaneous handoff guarantee. Sites may also require localStorage, sessionStorage, device-bound state, or another server-side session condition. Those are not synchronized. Cookies created and removed entirely between observations may never be seen. Upgrade all replicas together; older releases do not implement automatic jar reconciliation.

Redis uses append-only file (AOF) persistence with a named volume, so synced cookies survive Redis restarts and `docker compose down`/`up` cycles.

## docker-compose.cluster.yml overview

```yaml
services:
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis-data:/data

  browser:
    image: psyb0t/stealthy-auto-browse:latest
    scale: ${NUM_REPLICAS:-5}
    environment:
      - TZ=${TZ:-America/New_York}
      - REDIS_URL=redis://redis:6379

  queue-proxy:
    image: haproxy:lts-alpine
    configs:
      - source: haproxy_cfg
        target: /etc/haproxy/haproxy.cfg
    ports:
      - "8080:8080"
      - "8081:8081"

configs:
  haproxy_cfg:
    content: |
      # HAProxy config with ${NUM_REPLICAS} and ${QUEUE_TIMEOUT}
      # interpolated by Docker Compose, no template file needed
      ...
```

Each browser container defaults to a 5 GB RAM limit and 512 MB reservation. `memswap_limit: 8g` is the combined RAM-plus-swap ceiling, not an extra 8 GB of swap; swap also depends on the host. Override `BROWSER_MEMORY_LIMIT` and `BROWSER_MEMORY_RESERVATION` when sizing the fleet. A 512 MB hard limit can OOM-kill Camoufox at the default display size.

All services share a `browse` Docker network. The compose file does not expose browser containers directly. All traffic goes through the queue-proxy.

## Exposure and persistence limits

The provided [docker-compose.cluster.yml](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/docker-compose.cluster.yml) publishes ports 8080 and 8081 on all host interfaces. It does not pass `AUTH_TOKEN` into browser replicas, mount persistent per-browser profiles, or publish noVNC viewers. Add an operator-owned override to bind those ports to loopback or a protected network and configure authentication before exposing the API. The stats dashboard has no built-in authentication. Redis holds sensitive cookies and all replicas sharing that database belong to one trust boundary.
