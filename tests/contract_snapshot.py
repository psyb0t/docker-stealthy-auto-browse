"""Record the public contract of a stealthy-auto-browse image as JSON.

Usage: python3 tests/contract_snapshot.py <image> <output.json>

Starts the image, then records what clients depend on: the MCP server name,
instructions and every tool with its full input schema, the response shape
of safe actions, HTTP route behavior, the container user and home, exposed
ports, the startup banner and script-mode output. Values that change between
runs (timestamps, IDs, URLs of blank pages) are reduced to their type, so two
snapshots of the same contract compare equal.

Diff the snapshots of two images to see what a change did to the contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_PORT_WAIT_S = 120
_SAFE_ACTIONS = [
    {"action": "ping"},
    {"action": "get_resolution"},
    {"action": "recording_status"},
    {"action": "handoff_status"},
    {"action": "get_events", "limit": 1},
    {"action": "get_clipboard", "format": "html"},
    {"action": "list_tabs"},
    {"action": "list_windows"},
    {"action": "get_page_info"},
    {"action": "get_text"},
    {"action": "calibrate"},
    {"action": "get_cookies"},
    {"action": "get_last_dialog"},
    {"action": "get_last_download"},
    {"action": "get_virtual_media_state"},
    {"action": "get_console_log"},
    {"action": "get_network_log"},
    {"action": "sleep", "duration": 0},
    {"action": "no_such_action"},
    {"action": "system_click"},
    {"action": "system_type"},
    {"action": "send_key"},
    {"action": "scroll", "amount": 0},
    {"action": "run_script"},
    {"action": "end_handoff"},
    {"action": "stop_recording"},
    {"action": "set_clipboard"},
    {"action": "kill_launched"},
]
_SCRIPT = """name: contract
steps:
  - action: ping
    output_id: pong
  - action: get_resolution
"""
_MCP_PROBE = r"""
import json, urllib.request

URL = "http://localhost:8080/mcp/"
session = None

def rpc(method, params, request_id):
    global session
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        body["id"] = request_id
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    if session:
        request.add_header("Mcp-Session-Id", session)
    with urllib.request.urlopen(request, timeout=60) as response:
        session = response.headers.get("Mcp-Session-Id") or session
        raw = response.read().decode()
    if request_id is None:
        return None
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])["result"]
    return json.loads(raw)["result"]

init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "contract", "version": "1"}}, 1)
rpc("notifications/initialized", {}, None)
tools = rpc("tools/list", {}, 2)["tools"]
print(json.dumps({
    "server_name": init["serverInfo"]["name"],
    "instructions": init.get("instructions"),
    "tools": {tool["name"]: {"description": tool.get("description"),
                             "input_schema": tool.get("inputSchema")}
              for tool in tools},
}))
"""


def _shape(value: Any) -> Any:
    """Keys and value types, recursively; lists keep the shape of their first item."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kwargs)


def _http(method: str, url: str, body: bytes | None = None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _post(base: str, cmd: dict) -> dict:
    status, _, raw = _http("POST", base + "/", json.dumps(cmd).encode())
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")
    result = {"status": status, "shape": _shape(parsed)}
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        result["error"] = parsed["error"]
    return result


def _wait_ready(base: str) -> None:
    deadline = time.time() + _PORT_WAIT_S
    while time.time() < deadline:
        try:
            status, _, raw = _http("POST", base + "/", b'{"action":"ping"}')
            if status == 200 and json.loads(raw).get("success"):
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise SystemExit("container did not become ready")


def _container_ip(name: str) -> str:
    out = _run(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name]
    )
    return out.stdout.strip()


def snapshot(image: str) -> dict:
    name = f"contract-snapshot-{int(time.time())}"
    started = _run(["docker", "run", "-d", "--name", name, image])
    if started.returncode != 0:
        raise SystemExit(started.stderr)
    try:
        base = f"http://{_container_ip(name)}:8080"
        _wait_ready(base)
        time.sleep(2)
        result: dict[str, Any] = {}

        mcp = _run(["docker", "exec", name, "python", "-c", _MCP_PROBE])
        result["mcp"] = json.loads(mcp.stdout) if mcp.returncode == 0 else mcp.stderr

        result["actions"] = {cmd["action"] + json.dumps(cmd, sort_keys=True): _post(base, cmd)
                             for cmd in _SAFE_ACTIONS}
        status, _, raw = _http("POST", base + "/", b"{")
        result["invalid_json"] = {"status": status, "shape": _shape(json.loads(raw))}

        routes = {}
        for path in ("/health", "/state", "/screenshot/browser", "/screenshot/desktop",
                     "/screenshot/desktop?whLargest=64", "/nope"):
            status, headers, raw = _http("GET", base + path)
            entry: dict[str, Any] = {"status": status,
                                     "content_type": headers.get("content-type", "")}
            if headers.get("content-type", "").startswith("application/json"):
                entry["shape"] = _shape(json.loads(raw))
            elif path == "/health":
                entry["body"] = raw.decode()
            entry["has_request_id"] = "x-request-id" in {k.lower() for k in headers}
            routes[path] = entry
        result["routes"] = routes

        user = _run(["docker", "exec", name, "sh", "-c",
                     "getent passwd 1000; getent group 1000; stat -c '%U:%G %u:%g' /home/browser "
                     "/userdata /loaders /recordings"])
        result["user"] = user.stdout.splitlines()
        result["proc_user"] = _run(
            ["docker", "exec", name, "sh", "-c",
             "ps -o user= -p $(pgrep -f 'autolinuxdesk|main.py' | head -1)"]
        ).stdout.strip()
        config = json.loads(_run(["docker", "inspect", image]).stdout)[0]["Config"]
        result["exposed_ports"] = sorted(config.get("ExposedPorts") or {})
        result["labels"] = config.get("Labels") or {}
        result["workdir"] = config.get("WorkingDir")
        logs = _run(["docker", "logs", name])
        result["banner"] = [line for line in logs.stdout.splitlines()
                            if line.strip() and "WARNING" not in line]
        return result
    finally:
        _run(["docker", "rm", "-f", name])


def script_mode(image: str) -> dict:
    ran = _run(["docker", "run", "--rm", "-i", image, "--script"], input=_SCRIPT, timeout=300)
    try:
        parsed = json.loads(ran.stdout)
    except ValueError:
        return {"exit": ran.returncode, "stdout": ran.stdout[-2000:]}
    return {"exit": ran.returncode, "shape": _shape(parsed)}


def main() -> None:
    image, output = sys.argv[1], sys.argv[2]
    result = snapshot(image)
    result["script_mode"] = script_mode(image)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
