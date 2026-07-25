#!/usr/bin/env node
// stealthy-auto-browse MCP bridge. A thin stdio<->HTTP proxy: forwards MCP
// over stdio to a running stealthy-auto-browse server's Streamable-HTTP
// endpoint (`$STEALTHY_AUTO_BROWSE_URL/mcp/`), authenticating with
// `$AUTH_TOKEN` when the server requires it.
//
// stdout IS the MCP protocol channel, so diagnostics go to stderr only — the
// sole output here is a fatal pre-launch console.error (user-facing CLI
// output). The token is passed to the proxy as an argv header, never logged.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const MCP_PATH = "/mcp/";

const base = process.env.STEALTHY_AUTO_BROWSE_URL;

if (!base) {
  console.error(
    `[stealthy-auto-browse-mcp] Missing STEALTHY_AUTO_BROWSE_URL.

Point this bridge at your running stealthy-auto-browse server, e.g.:
  export STEALTHY_AUTO_BROWSE_URL=http://localhost:8080

stealthy-auto-browse is self-hosted — see https://github.com/psyb0t/docker-stealthy-auto-browse`,
  );
  process.exit(1);
}

const url = `${base.replace(/\/+$/, "")}${MCP_PATH}`;
const token = process.env.AUTH_TOKEN;
const proxyEntry = require.resolve("mcp-remote/dist/proxy.js");

const args = [proxyEntry, url, "--transport", "http-only"];
if (token) {
  args.push("--header", `Authorization: Bearer ${token}`);
}
args.push(...process.argv.slice(2));

const result = spawnSync(process.execPath, args, { stdio: "inherit" });
process.exit(result.status ?? 1);
