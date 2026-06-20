# Cluster Mode

Run a fleet of browser instances behind HAProxy with automatic queuing, sticky sessions, and Redis cookie sync. Useful when you need to handle concurrent requests — each browser handles one request at a time, and the proxy queues the rest until a slot opens.

## What You Get

- **Multiple browser containers** (default 10, configurable via `NUM_REPLICAS`) behind a single entry point on port 8080
- **HAProxy queue-proxy** that holds incoming requests when all instances are busy, instead of returning errors
- **Sticky sessions** via `INSTANCEID` cookie — once a client is routed to a browser instance, it stays there
- **Redis cookie sync** — cookies set on one instance propagate to all others via PubSub. New instances joining the cluster load existing cookies from Redis on startup
- **Script-only enforcement** — both HTTP API and MCP restrict to `run_script` (plus `ping` and `sleep`) when `NUM_REPLICAS > 1`

## Script-Only Mode (v1.0.0+)

When `NUM_REPLICAS > 1`, individual actions like `goto`, `get_text`, `click` are **rejected** on both the HTTP API and MCP server. Only `run_script`, `ping`, and `sleep` are allowed.

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

The MCP server exposes only the `run_script` tool in cluster mode, with a comprehensive description documenting every available action and its parameters so LLMs know what steps to include in their scripts.

**Recording in cluster mode:** `start_recording` and `stop_recording` must live in the **same** `run_script` call so both steps hit the same browser instance (one request = one routing decision). Splitting them across two `run_script` calls breaks because the stop would likely land on a different instance with no active recording. Mount `/recordings` on every browser container in `docker-compose.cluster.yml` (one shared host directory is fine — slugs are namespaced by your caller, not by instance).

## Quick Start

```bash
curl -LO https://raw.githubusercontent.com/psyb0t/docker-stealthy-auto-browse/main/docker-compose.cluster.yml
docker compose -f docker-compose.cluster.yml up -d
```

This starts Redis, browser containers (10 by default), and the HAProxy queue-proxy. The entry point is `http://localhost:8080` — same API as the single-container mode. The MCP endpoint at `/mcp/` also works through the proxy with the same sticky session routing.

### Scale Up

```bash
# Scale to 20 instances
NUM_REPLICAS=20 docker compose -f docker-compose.cluster.yml up -d
```

### Watch the Queue

```bash
docker compose -f docker-compose.cluster.yml logs -f queue-proxy
```

## Environment Variables

| Variable         | Default          | What It Does                                                                                                                                              |
| ---------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `REDIS_URL`      | —                | Redis connection string. Set automatically in cluster mode (`redis://redis:6379`). Set this on standalone containers to join them to a Redis sync cluster. |
| `QUEUE_TIMEOUT`  | `300`            | Seconds a request will wait in the queue before HAProxy returns a 503. Increase for long-running tasks.                                                   |
| `NUM_REPLICAS`   | `10`             | Number of browser instances. Controls both the container replica count and HAProxy server list — single source of truth.                                  |
| `TZ`             | `America/New_York` | Timezone for all browser instances. Set to match your IP's geographic location.                                                                         |

## HAProxy Queue-Proxy

HAProxy sits in front of all browser instances and enforces a concurrency limit of 1 per instance (`maxconn 1` per server). When all instances are handling requests, new requests queue up instead of failing. The `leastconn` balancing algorithm routes to whichever instance has been idle longest.

### Endpoints

| Endpoint           | What It Does                                                            |
| ------------------ | ----------------------------------------------------------------------- |
| `/__queue/health`  | Returns `{"status":"ok"}` — use this for load balancer health checks.  |
| `/__queue/status`  | Returns `{"status":"ok","num_replicas":N}` — **private networks only** (10.x, 172.16.x, 192.168.x, 127.x). Returns 403 from public IPs. |

### Stats Dashboard

HAProxy exposes a stats dashboard on port **8081**. Open `http://localhost:8081/` to see live traffic, queue depth, server health, and request rates per instance.

## Sticky Sessions

HAProxy sets an `INSTANCEID` cookie on every response, containing the name of the server that handled the request (e.g. `browser1`, `browser3`).

**Not sending `INSTANCEID` is intentional for self-contained requests.** If you POST a full script — navigate to a site, type something, press enter, get the HTML — HAProxy picks any free browser and the whole thing happens in one shot. You don't care which instance handled it, and not sending the cookie means you get proper load balancing across the fleet.

**Send `INSTANCEID` when you make more than one request.** Each browser container is a full, stateful browser — open tabs, DOM state, JS variables, scroll position, local storage, everything lives in that specific container. If your second request depends on anything your first request did (navigated to a page, typed in a field, opened a tab), you need to route back to the same instance. The only thing that syncs across instances automatically is cookies (via Redis).

**Let HAProxy assign an instance, then stick to it:**

```bash
# First request — capture the INSTANCEID from the response
INSTANCEID=$(curl -sS -D - -o /dev/null -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -d '{"action": "goto", "url": "https://example.com"}' \
  | grep -i 'set-cookie:.*INSTANCEID' \
  | sed 's/.*INSTANCEID=\([^;]*\).*/\1/' | tr -d '\r')

# Subsequent requests — send INSTANCEID to route to the same browser
curl -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -H "Cookie: INSTANCEID=$INSTANCEID" \
  -d '{"action": "get_text"}'
```

**Target a specific instance directly:**

```bash
curl -X POST http://localhost:8080 \
  -H 'Content-Type: application/json' \
  -H 'Cookie: INSTANCEID=browser3' \
  -d '{"action": "get_text"}'
```

## Redis Cookie Sync

When `REDIS_URL` is set, all cookie operations sync across instances via Redis PubSub:

- `set_cookie` publishes the cookie to the `SABROWSE:UPDATE` channel. All other instances receive it immediately and apply it to their browser context.
- `delete_cookies` publishes a clear event. All instances clear their cookies.
- On startup, each instance loads all existing cookies from Redis (`SABROWSE:COOKIES` hash) before handling any requests.

This means an auth session (like a login cookie) set on `browser1` is immediately available in `browser2`, `browser3`, etc., without any navigation or re-login. If you log into a site on one instance, all instances are logged in.

**Practical consequence:** run a single login sequence on any instance, and the whole fleet is authenticated.

Redis uses append-only file (AOF) persistence with a named volume, so synced cookies survive Redis restarts and `docker compose down`/`up` cycles.

## docker-compose.cluster.yml Overview

```yaml
services:
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis-data:/data

  browser:
    image: psyb0t/stealthy-auto-browse:latest
    scale: ${NUM_REPLICAS:-10}
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
      # interpolated by Docker Compose — no template file needed
      ...
```

Each browser container is limited to 512MB RAM with 8GB swap available. This prevents a single instance from consuming all host memory while allowing swap for peak usage.

All services share a `browse` Docker network. Browser containers are not exposed directly — all traffic goes through the queue-proxy.
