# @psyb0t/stealthy-auto-browse

An OpenClaw/MCP plugin that connects your agent to a self-hosted
[stealthy-auto-browse](https://github.com/psyb0t/docker-stealthy-auto-browse)
stealth browser automation container over the
[Model Context Protocol](https://modelcontextprotocol.io).

stealthy-auto-browse already serves a Streamable-HTTP MCP endpoint at
`/mcp/`. This package is a thin stdio↔HTTP bridge (via
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote)) for MCP clients that
speak local stdio servers — it forwards everything to your running
stealthy-auto-browse instance and authenticates with your bearer token when
the server requires one.

> stealthy-auto-browse is **self-hosted**. This plugin does not ship the
> browser container — it connects to a stealthy-auto-browse server that
> **you** run. See the
> [docker-stealthy-auto-browse repo](https://github.com/psyb0t/docker-stealthy-auto-browse)
> to stand one up.

## Tools

All stealthy-auto-browse MCP tools become available to your agent: navigate
(`goto`), read the page (`get_text`, `get_html`, `get_interactive_elements`),
take screenshots (`screenshot`), Playwright DOM input (`click`, `fill`),
OS-level input via PyAutoGUI (`system_click`, `system_type`, `send_key`,
`mouse_move`, `scroll`), run JavaScript (`eval_js`), wait conditions
(`wait_for_element`, `wait_for_text`), bundle multiple steps atomically
(`run_script`), and a generic `browser_action` fallback for everything else
(cookies, tabs, storage, dialogs, downloads, network/console logging, screen
recording).

## Configuration

| Env var | Required | Description |
|---|---|---|
| `STEALTHY_AUTO_BROWSE_URL` | yes | Base URL of your running stealthy-auto-browse server, e.g. `http://localhost:8080`. The bridge appends `/mcp/`. |
| `AUTH_TOKEN` | no | Bearer token — only if the stealthy-auto-browse server was started with `AUTH_TOKEN` set. |

## Install

Install it into your OpenClaw agent from ClawHub:

```bash
openclaw plugins install clawhub:@psyb0t/stealthy-auto-browse
```

Then set `STEALTHY_AUTO_BROWSE_URL` (and `AUTH_TOKEN` if your server uses
auth) in the plugin's environment.

## Native remote MCP (no install)

If your MCP client already supports **remote** Streamable-HTTP servers, you
don't need this bridge — point the client straight at
`$STEALTHY_AUTO_BROWSE_URL/mcp/` with an `Authorization: Bearer <token>`
header.

## License

MIT. See [LICENSE](LICENSE).
