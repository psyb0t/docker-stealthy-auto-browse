---
title: Troubleshooting
description: Diagnose startup errors, rejected authentication, lost profiles, browser recovery, stale element references, and cluster routing failures.
sidebar_position: 10
---

# Troubleshooting

Start with `docker logs <your-browser-container>`. Runtime logs are JSON on stderr; the entrypoint and auxiliary display services also emit startup output. `LOG_LEVEL` controls Python logging, and `LOG_FILE` adds a rotating file sink. [Request correlation headers](api.md#request-correlation-headers) connect an HTTP response to its request logs.

## Startup or access fails

| Symptom | Check and action |
| --- | --- |
| API connection refused | Check container status and logs, the host publish address, and `HTTP_LISTEN_PORT`. Inside another container, `localhost` is not the browser. |
| API returns 401 | Send the exact Bearer header. An `auth_token` query parameter is rejected when authentication is enabled. |
| `/health` returns `ok` but a browser action fails | Health is a lightweight liveness endpoint, not a full browser probe. Read the action's `success` and `error` fields and the logs. |
| Viewer unavailable | Publish noVNC's configured port, normally 5900. API authentication does not authenticate the viewer. |
| `VNC_SHOW_CURSOR must be true or false` | Use the literal lowercase value `true` or `false`. |
| CAPTCHA provider startup failure | Check provider spelling, enable/key conflicts, key file permissions, network access, and archive verification. Never disable checksum verification to make startup pass. |
| Permission denied under mounted data | Match `PUID`/`PGID`, check writable directories, and avoid read-only mounts where ownership repair or Firefox writes are required. |
| Browser crash or repeated recovery | Inspect memory limits and container state. Large displays, many pages, and video consume memory. Kernel diagnostics may be unavailable inside the container. |

## State or targeting surprises

`Unknown element reference` or a stale-reference failure means the stored node is gone, expired, or invalidated by navigation or relaunch. Resolve the target again. References are process-local, limited to 512, and expire after five minutes. Closed shadow roots cannot be targeted.

After closing the last tab, `list_tabs` can report zero pages. The next page-dependent action relaunches the browser on its persistent profile. Cookies can survive; the closed DOM and active page state do not. Browser recovery does not make an entire crashed container recover automatically.

An HTTP 200 response can still contain `success: false`. Inspect the JSON envelope, not just curl's HTTP status. Likewise, a script failing after several actions does not roll back those earlier actions.

Handoff rejects every action except a short read-only list with `human handoff is active` until `end_handoff` runs; `detect_challenge`, `eval` and the waits are refused too. See [Events and human handoff](api.md#events-and-human-handoff) for the list. Viewer network access is configured separately. A new document can replace the page listeners used for activity counts.

Profiles survive container recreation only when `/userdata` is mounted. Avoid sharing the same profile directory among live Firefox instances. Back it up while stopped before upgrades. [Configuration](configuration.md#persistent-profiles) describes identity migration.

## Cluster problems

`Action '...' not available in cluster mode` means an operation was submitted outside `run_script`. Keep the whole sequence inside one script. For dependent requests across scripts, preserve `INSTANCEID` for HTTP or the MCP session header. Redis does not share tabs, localStorage, sessionStorage, DOM, or live workflow state.

Cookie sharing requires every replica to reach the same Redis database. Reconciliation is eventual, not instantaneous. A website can bind sessions to more than cookies, so cookie propagation alone does not guarantee a usable login on another replica. See [Redis cookie sync](cluster-mode.md#redis-cookie-sync).

HAProxy returns 503 after its queue timeout. Increase `QUEUE_TIMEOUT` only when requests need to wait longer for a free browser, or reduce request duration and add capacity. `QUEUE_TIMEOUT` does not extend a long request: HAProxy cuts off a request after 10 minutes with no response data, and `run_script` sends nothing until it finishes. An open `GET /events` stream sends keepalives, never times out, and holds its replica's only connection slot until the client disconnects, so other requests to that replica queue behind it. See [HAProxy queue-proxy](cluster-mode.md#haproxy-queue-proxy). Do not expose the unauthenticated stats dashboard to an untrusted network.
