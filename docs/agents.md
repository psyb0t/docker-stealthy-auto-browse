---
title: MCP and agent integrations
description: Connect MCP clients with Bearer authentication, install Claude, Codex and OpenClaw integrations, and choose dedicated or generic tools.
nav_order: 4
---

# MCP and agent integrations

The MCP server uses Streamable HTTP at `http://localhost:8080/mcp/`, on the same port as the [HTTP API](api.md). Connect a compatible client and configure `Authorization: Bearer <token>` when `AUTH_TOKEN` is set. There is no separate stdio server in the container.

## Published integrations

```bash
# Claude Code
claude plugin marketplace add psyb0t/agents
claude plugin install stealthy-auto-browse@psyb0t

# Codex
codex plugin marketplace add psyb0t/agents
codex plugin add stealthy-auto-browse@psyb0t

# OpenClaw skill
openclaw skills install @psyb0t/stealthy-auto-browse

# OpenClaw MCP bridge
openclaw plugins install clawhub:@psyb0t/stealthy-auto-browse
```

The [source skill](https://github.com/psyb0t/docker-stealthy-auto-browse/tree/main/.agents/skills/stealthy-auto-browse) and [plugin manifests](https://github.com/psyb0t/docker-stealthy-auto-browse/tree/main/.agents) describe the integration. Set `STEALTHY_AUTO_BROWSE_URL` to the reachable browser URL where the integration uses it, and provide `AUTH_TOKEN` privately. `localhost` inside another container is that container, not the browser; use a shared-network service name or a reachable host address.

## Dedicated tools

Single-instance mode exposes these tools from [mcp_server.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/mcp_server.py):

| Purpose | Tools |
| --- | --- |
| Navigation and reading | `goto`, `get_text`, `get_html`, `get_interactive_elements`, `screenshot`, `eval_js`. |
| System input | `system_click`, `system_type`, `send_key`, `mouse_move`, `scroll`, `human_pause`. |
| DOM input and waits | `click`, `fill`, `wait_for_element`, `wait_for_text`. |
| Clipboard | `set_clipboard` for text, `set_clipboard_data` for rich formats, `get_clipboard`. |
| Events and handoff | `get_events`, `wait_for_event`, `begin_handoff`, `handoff_status`, `end_handoff`. |
| Challenges | `detect_challenge`, `solve_captcha`. The latter requires Anti-Captcha configuration. |
| Full action surface | `browser_action`, `run_script`. |

MCP tool names sometimes differ from HTTP action names: `eval_js` calls `eval`, and `screenshot` calls `save_screenshot`. `browser_action` takes an action name and a `params` object, so actions without dedicated tools remain available. Consult the connected tool schema for its accepted arguments and the [HTTP reference](api.md) for action behavior.

## Multi-step work and human takeover

Use `run_script` to keep dependent steps in one serialized request. Atomic means no other API action interleaves with the script on that browser; it does not roll back page changes when a step fails. [Script control flow](script-mode.md#control-flow) supports explicit conditions and bounded loops.

For visible manual work, call `begin_handoff`, give the person the protected noVNC address, read `handoff_status` as needed, then call `end_handoff` to resume mutating automation. This pauses automation, not network access. It does not add viewer authentication or a temporary tunnel. Activity counts describe trusted events observed by listeners on the page where handoff began; navigation can replace those listeners.

In [cluster mode](cluster-mode.md), MCP exposes only `run_script`. HTTP still permits `run_script`, `ping`, and `sleep` at the top level. Put every dependent operation inside one script, or preserve the relevant session routing across requests. Use a standalone browser for interactive handoff; leaving it active across cluster scripts blocks the later script that would end it.
