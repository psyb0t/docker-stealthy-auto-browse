#!/usr/bin/env python3
"""
Interactive browser session with HTTP command interface.

Supports both Playwright (JS) clicks and PyAutoGUI (OS-level) clicks.
PyAutoGUI clicks are undetectable by behavioral analysis.

Endpoints:
    POST /                  - Execute command, returns JSON result
    GET /screenshot/browser - Get browser viewport screenshot as PNG
    GET /screenshot/desktop - Get full desktop screenshot as PNG
    GET /state              - Get browser state as JSON
    GET /health             - Health check
    POST /mcp               - MCP Streamable HTTP (AI agent interface)
"""

from __future__ import annotations

import asyncio
import io
import os
import random
import subprocess
import sys
import tempfile
import time
from typing import Any

import uvicorn
import yaml
from browser import Browser, BrowserConfig
from fastapi import FastAPI, Request
from PIL import Image
from recorder import Recorder, RecorderError, cleanup_orphan_tmp_files
from script_runner import load_script, run_script
from starlette.responses import JSONResponse, PlainTextResponse, Response
from system import System

from loaders import find_loader, load_loaders, substitute_url

# =============================================================================
# CONTENT TYPES
# =============================================================================

CONTENT_TYPE_IMAGE_PNG = "image/png"

# =============================================================================
# LOGGING
# =============================================================================


import re
import uuid as _uuid

from logger import (
    get_logger,
    new_trace_id,
    request_id_var,
    trace_id_var,
)

log = get_logger(__name__)

# X-Request-Id shape allowlist — accept anything ≤ 64 chars, alphanumeric +
# `-_.`, no newlines. Untrusted user input becomes a log field so we don't
# accept arbitrary garbage that could break log parsers downstream.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _safe_request_id(incoming: str) -> str:
    """Validate + sanitize incoming X-Request-Id; mint a new one if invalid."""
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return _uuid.uuid4().hex


def log_request(action: str, params: dict | None = None) -> None:
    """Log incoming action with structured fields. Redactor masks tokens."""
    if params:
        log.info("request", extra={"action": action, "params": params})
    else:
        log.info("request", extra={"action": action})


def log_response(success: bool, msg: str = "") -> None:
    """Log outcome of an action."""
    fields: dict[str, Any] = {"outcome": "ok" if success else "fail"}
    if msg:
        fields["detail"] = msg
    if success:
        log.info("response", extra=fields)
    else:
        log.warning("response", extra=fields)


# =============================================================================
# GLOBALS
# =============================================================================

# System-level input (pyautogui)
system = System()

# Screen recorder (ffmpeg x11grab)
recorder = Recorder()

# Global browser instance
browser: Browser | None = None

# Loaders directory path
loaders_dir: str = "/loaders"

# Dialog handling state
_last_dialog: dict | None = None
_next_dialog_action: dict | None = None

# Maps dialog type to available buttons
_DIALOG_BUTTONS: dict[str, list[str]] = {
    "alert": ["ok"],
    "confirm": ["ok", "cancel"],
    "prompt": ["ok", "cancel"],
    "beforeunload": ["leave", "stay"],
}

# Tab management
_active_page: Any = None

# Download tracking
_last_download: dict | None = None

# Network logging
_network_log: list[dict] = []
_network_logging: bool = False
_network_handler_pages: set[int] = set()

# Console logging
_console_log: list[dict] = []
_console_logging: bool = False
_console_handler_pages: set[int] = set()

HTTP_LISTEN_HOST = os.environ.get("HTTP_LISTEN_HOST", "0.0.0.0")
HTTP_LISTEN_PORT = int(os.environ.get("HTTP_LISTEN_PORT", "8080"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip() or None

_NUM_REPLICAS = int(os.environ.get("NUM_REPLICAS", "1"))
_CLUSTER_MODE = _NUM_REPLICAS > 1
_CLUSTER_ALLOWED_ACTIONS = {"run_script", "ping", "sleep"}


# Parse CLI args: --script <path> (path provided by entrypoint from stdin)
SCRIPT_PATH: str | None = None

_args = sys.argv[1:]
if len(_args) >= 2 and _args[0] == "--script":
    SCRIPT_PATH = _args[1]


async def _on_dialog(dialog: Any) -> None:
    """Handle browser dialogs (alert/confirm/prompt/beforeunload)."""
    global _last_dialog, _next_dialog_action

    _last_dialog = {
        "type": dialog.type,
        "message": dialog.message,
        "default_value": dialog.default_value,
        "buttons": _DIALOG_BUTTONS.get(dialog.type, ["ok"]),
    }
    log.info(
        "dialog received",
        extra={"dialog_type": dialog.type, "dialog_message": dialog.message},
    )

    action = _next_dialog_action
    _next_dialog_action = None

    if action and not action.get("accept", True):
        await dialog.dismiss()
        return

    prompt_text = action.get("text") if action else None
    if prompt_text is not None:
        await dialog.accept(prompt_text)
        return

    await dialog.accept()


async def _on_download(download: Any) -> None:
    """Track file downloads."""
    global _last_download
    try:
        path = await download.path()
        _last_download = {
            "url": download.url,
            "filename": download.suggested_filename,
            "path": str(path) if path else None,
        }
    except Exception:
        _last_download = {
            "url": download.url,
            "filename": download.suggested_filename,
            "path": None,
        }
    log.info("download", extra={"filename": download.suggested_filename})


def _on_request(request: Any) -> None:
    """Track network requests when logging is enabled."""
    if not _network_logging:
        return
    _network_log.append(
        {
            "type": "request",
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "timestamp": time.time(),
        }
    )


def _on_response(response: Any) -> None:
    """Track network responses when logging is enabled."""
    if not _network_logging:
        return
    _network_log.append(
        {
            "type": "response",
            "url": response.url,
            "status": response.status,
            "timestamp": time.time(),
        }
    )


def _on_console(message: Any) -> None:
    """Track console messages when logging is enabled."""
    if not _console_logging:
        return
    _console_log.append(
        {
            "type": message.type,
            "text": message.text,
            "location": message.location,
            "timestamp": time.time(),
        }
    )


def _setup_page_handlers(page: Any) -> None:
    """Register all event handlers on a page."""
    page.on("dialog", _on_dialog)
    page.on("download", _on_download)
    page_id = id(page)
    if page_id not in _network_handler_pages:
        page.on("request", _on_request)
        page.on("response", _on_response)
        _network_handler_pages.add(page_id)
    if page_id not in _console_handler_pages:
        page.on("console", _on_console)
        _console_handler_pages.add(page_id)


async def _clear_selection(page: Any) -> None:
    """Clear any text selection left by focus_tab_window's focus drag.

    focus_tab_window transfers keyboard focus with a tiny left-button drag,
    which can select a few characters on a text page. Wipe it so nothing
    stays highlighted after a tab switch. Best-effort — a dead/closed page
    just means there's nothing to clear.
    """
    if page is None:
        return
    try:
        await page.evaluate(
            "window.getSelection && window.getSelection().removeAllRanges()"
        )
    except Exception as e:
        log.warning("clear_selection failed", extra={"error": str(e)})


async def get_active_page() -> Any:
    """Get the currently active page, relaunching browser if it died."""
    global _active_page
    if not browser:
        return None
    # Heal a dead/crashed Camoufox before reading the context. Without this,
    # the cached _active_page survives across browser deaths and every
    # subsequent action fails with "Connection closed while reading from
    # the driver" until the container is manually restarted.
    if not await browser.ensure_healthy():
        return None
    if not browser._context:
        return None
    pages = browser._context.pages
    if not pages:
        _active_page = None
        return None
    # The cached _active_page may belong to the previous (dead) context.
    # Drop it if it's not in the current page list.
    if _active_page is None or _active_page not in pages:
        _active_page = pages[-1]
    return _active_page


async def get_window_offset_js(page) -> dict:
    """Get browser content area offset from screen origin.

    Uses Firefox's mozInnerScreenX/Y which report the real screen position
    of the viewport. These are NOT spoofed by Camoufox (unlike outerHeight/
    innerHeight which are fingerprint-spoofed and give wrong offsets).

    On failure (page closed, JS context destroyed, mozInnerScreen missing
    on a future Firefox), logs a warning and returns (0, 0). The caller
    has no way to distinguish "valid (0,0) on a fullscreen window" from
    "calibrate failed" — the log line is the only signal.
    """
    try:
        result = await page.evaluate("""() => ({
                x: Math.round(window.mozInnerScreenX),
                y: Math.round(window.mozInnerScreenY)
            })""")
    except Exception as e:
        log.warning(
            "calibrate failed (mozInnerScreen unreachable): %s — "
            "returning (0, 0); coordinate translation will be wrong "
            "until calibrate succeeds",
            e,
        )
        return {"x": 0, "y": 0}

    # Defensive sanity check — non-numeric / None means Firefox returned
    # something unexpected (Camoufox change, future Firefox version). Treat
    # the same as an exception: log loudly, fall back to (0, 0).
    x = result.get("x") if isinstance(result, dict) else None
    y = result.get("y") if isinstance(result, dict) else None
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        log.warning(
            "calibrate returned non-numeric offset %r — falling back to "
            "(0, 0); coordinate translation will be wrong",
            result,
        )
        return {"x": 0, "y": 0}
    return {"x": int(x), "y": int(y)}


def make_response(
    success: bool, data: dict | None = None, error: str | None = None
) -> dict:
    """Build response dict."""
    resp: dict = {"success": success, "timestamp": time.time()}
    if data:
        resp["data"] = data
    if error:
        resp["error"] = error
    return resp


# =============================================================================
# ACTION DISPATCH
# =============================================================================


async def dispatch_action(cmd: dict) -> dict:
    """Execute a single action command. Returns response dict."""
    global _next_dialog_action, _active_page, _network_logging, _console_logging
    action = cmd.get("action", "")

    # Actions that don't need page
    if action == "ping":
        url = browser.state.url if browser else ""
        return make_response(True, {"message": "pong", "url": url})

    if action == "close":
        log.info("Shutting down...")
        asyncio.get_event_loop().call_soon(lambda: sys.exit(0))
        return make_response(True, {"message": "closing"})

    if action == "sleep":
        duration = cmd.get("duration", 1)
        await asyncio.sleep(float(duration))
        return make_response(True, {"slept": duration})

    if action == "start_recording":
        mode = cmd.get("mode", "window")
        fps = int(cmd.get("fps", 15))
        show_cursor = bool(cmd.get("show_cursor", True))
        # viewport mode crops to the page content area — use the calibrated
        # window_offset rather than a hardcoded chrome height. window/desktop
        # capture from (0, 0) so they include the full browser frame.
        if mode == "viewport":
            offset_x = int(system.window_offset.get("x", 0))
            offset_y = int(system.window_offset.get("y", 0))
            if offset_x == 0 and offset_y == 0:
                # Re-calibrate on demand if it's never been done OR returned
                # zero (likely a failed calibrate that got swallowed).
                active = await get_active_page()
                if active is not None:
                    fresh = await get_window_offset_js(active)
                    system.window_offset = fresh
                    offset_x = int(fresh.get("x", 0))
                    offset_y = int(fresh.get("y", 0))
        else:
            offset_x = 0
            offset_y = 0
        try:
            info = recorder.start(
                mode=mode,
                fps=fps,
                offset_x=offset_x,
                offset_y=offset_y,
                show_cursor=show_cursor,
            )
        except RecorderError as e:
            return make_response(False, error=str(e))
        return make_response(True, info)

    if action == "stop_recording":
        slug = cmd.get("slug", "")
        if not slug:
            return make_response(False, error="slug required")
        try:
            info = recorder.stop(slug=slug)
        except RecorderError as e:
            return make_response(False, error=str(e))
        return make_response(True, info)

    if action == "recording_status":
        return make_response(True, recorder.status())

    if action == "run_script":
        steps = cmd.get("steps")
        yaml_content = cmd.get("yaml")
        if yaml_content:
            script_data = yaml.safe_load(yaml_content)
            if not script_data or not isinstance(script_data, dict):
                return make_response(False, error="invalid YAML")
            if "steps" not in script_data:
                return make_response(False, error="YAML missing steps")
        elif steps:
            script_data = {
                "name": cmd.get("name", "api_script"),
                "on_error": cmd.get("on_error", "stop"),
                "steps": steps,
            }
        else:
            return make_response(False, error="steps or yaml required")
        result = await run_script(script_data, dispatch_action, stdout=io.StringIO())
        result.pop("_binary", None)
        return make_response(True, result)

    if action == "handle_dialog":
        _next_dialog_action = {
            "accept": cmd.get("accept", True),
            "text": cmd.get("text"),
        }
        return make_response(True, {"configured": _next_dialog_action})

    if action == "get_last_dialog":
        if not _last_dialog:
            return make_response(True, {"dialog": None})
        return make_response(True, {"dialog": _last_dialog})

    # --- Tab management ---

    if action == "list_tabs":
        pages = browser._context.pages if browser and browser._context else []
        tabs = []
        active = await get_active_page()
        for i, p in enumerate(pages):
            tabs.append({"index": i, "url": p.url, "active": p is active})
        return make_response(True, {"tabs": tabs, "count": len(tabs)})

    if action == "new_tab":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        new_page = await browser._context.new_page()
        _setup_page_handlers(new_page)
        _active_page = new_page
        # Foreground the new tab's window so it's what Xvfb renders
        # (screenshots, recordings, VNC). See Browser.focus_tab_window —
        # Firefox opens each page as its own OS window and bring_to_front()
        # doesn't raise it. The new page is always the last one.
        browser.focus_tab_window(len(browser._context.pages) - 1)
        tab_url: str | None = cmd.get("url")
        if tab_url:
            await new_page.goto(
                tab_url, wait_until=cmd.get("wait_until", "domcontentloaded")
            )
        return make_response(
            True,
            {
                "index": len(browser._context.pages) - 1,
                "url": new_page.url,
            },
        )

    if action == "switch_tab":
        index = cmd.get("index")
        if index is None:
            return make_response(False, error="index required")
        pages = browser._context.pages if browser and browser._context else []
        if index < 0 or index >= len(pages):
            return make_response(False, error=f"Invalid tab index: {index}")
        _active_page = pages[index]
        # Raise the tab's OS window so it's the one Xvfb renders. Without
        # this, switch_tab only redirects where Playwright verbs land — the
        # display (and thus screenshots, recordings, VNC) keeps showing
        # whatever window was last raised, so work on the switched-to tab
        # happens off-screen. See Browser.focus_tab_window.
        browser.focus_tab_window(index)
        await _clear_selection(_active_page)
        return make_response(True, {"index": index, "url": _active_page.url})

    if action == "close_tab":
        index = cmd.get("index")
        pages = browser._context.pages if browser and browser._context else []
        if not pages:
            return make_response(False, error="No tabs open")
        if index is not None:
            if index < 0 or index >= len(pages):
                return make_response(False, error=f"Invalid tab index: {index}")
            target = pages[index]
        else:
            target = await get_active_page() or pages[-1]
        await target.close()
        pages = browser._context.pages if browser and browser._context else []
        _active_page = pages[-1] if pages else None
        # Raise whatever tab we fell back to so the display follows the active
        # page instead of showing the closed tab's now-defunct window.
        if _active_page is not None:
            browser.focus_tab_window(len(pages) - 1)
            await _clear_selection(_active_page)
        return make_response(True, {"closed": True, "remaining": len(pages)})

    # --- Cookie management (with optional Redis sync) ---

    if action == "get_cookies":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        urls = cmd.get("urls")
        cookies = await browser.get_cookies_synced(urls)
        return make_response(True, {"cookies": cookies, "count": len(cookies)})

    if action == "set_cookie":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        cookie = {k: v for k, v in cmd.items() if k != "action"}
        await browser.set_cookie_synced(cookie)
        return make_response(True, {"set": cookie.get("name")})

    if action == "delete_cookies":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        await browser.delete_cookies_synced()
        return make_response(True, {"cleared": True})

    # --- Download tracking ---

    if action == "get_last_download":
        return make_response(True, {"download": _last_download})

    # --- Network logging ---

    if action == "enable_network_log":
        _network_logging = True
        page = await get_active_page()
        if page:
            _setup_page_handlers(page)
        return make_response(True, {"enabled": True})

    if action == "disable_network_log":
        _network_logging = False
        return make_response(True, {"enabled": False})

    if action == "get_network_log":
        return make_response(
            True, {"log": list(_network_log), "count": len(_network_log)}
        )

    if action == "clear_network_log":
        _network_log.clear()
        return make_response(True, {"cleared": True})

    if action == "getclear_network_log":
        result = make_response(
            True,
            {"log": list(_network_log), "count": len(_network_log)},
        )
        _network_log.clear()
        return result

    # --- Console logging ---

    if action == "enable_console_log":
        _console_logging = True
        page = await get_active_page()
        if page:
            _setup_page_handlers(page)
        return make_response(True, {"enabled": True})

    if action == "disable_console_log":
        _console_logging = False
        return make_response(True, {"enabled": False})

    if action == "get_console_log":
        return make_response(
            True,
            {"log": list(_console_log), "count": len(_console_log)},
        )

    if action == "clear_console_log":
        _console_log.clear()
        return make_response(True, {"cleared": True})

    if action == "getclear_console_log":
        result = make_response(
            True,
            {"log": list(_console_log), "count": len(_console_log)},
        )
        _console_log.clear()
        return result

    # --- Save screenshot to file (script mode) ---

    if action == "save_screenshot":
        ss_type = cmd.get("type", "browser")

        if ss_type == "desktop":
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                ["scrot", "-o", tmp_path],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                os.unlink(tmp_path)
                return make_response(False, error=f"scrot failed: {result.stderr}")
            with open(tmp_path, "rb") as f:
                data = f.read()
            os.unlink(tmp_path)
        else:
            page = await get_active_page()
            if not page:
                return make_response(False, error="No active page")
            data = await page.screenshot(type="png")

        # Optional resize
        w = cmd.get("width")
        h = cmd.get("height")
        largest = cmd.get("whLargest")
        if w or h or largest:
            img = Image.open(io.BytesIO(data))
            orig_w, orig_h = img.size
            if largest:
                largest = int(largest)
                if orig_w >= orig_h:
                    new_w = largest
                    new_h = int(orig_h * largest / orig_w)
                else:
                    new_h = largest
                    new_w = int(orig_w * largest / orig_h)
            elif w and h:
                new_w, new_h = int(w), int(h)
            elif w:
                new_w = int(w)
                new_h = int(orig_h * new_w / orig_w)
            else:
                new_h = int(h)
                new_w = int(orig_w * new_h / orig_h)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()

        # Write to file if path given
        path = cmd.get("path", "")
        if path:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)

        resp = make_response(True, {"type": ss_type, "size": len(data)})
        if path:
            resp["data"]["path"] = path
        # Attach binary for script runner / MCP to use
        resp["_binary"] = data
        return resp

    # All other actions need a page
    page = await get_active_page()
    if not page:
        return make_response(False, error="No active page")

    if action == "goto":
        url = cmd.get("url", "")
        if not url:
            return make_response(False, error="No URL")

        # Check for matching loader (but not when called from execute_loader
        # to avoid infinite recursion)
        if not cmd.get("_from_loader"):
            loader = find_loader(loaders_dir, url)
            if loader:
                log.info(f"Loader matched: {loader.name}")
                return await execute_loader(loader, url)

        goto_kwargs: dict[str, Any] = {
            "wait_until": cmd.get("wait_until", "domcontentloaded"),
        }
        if cmd.get("referer"):
            goto_kwargs["referer"] = cmd["referer"]
        await page.goto(url, **goto_kwargs)
        return make_response(True, {"url": page.url, "title": await page.title()})

    if action == "refresh":
        # page.reload() times out in Camoufox persistent context, so
        # re-navigate to the current URL instead (same practical effect).
        await page.goto(page.url, wait_until=cmd.get("wait_until", "domcontentloaded"))
        return make_response(True, {"url": page.url, "title": await page.title()})

    if action == "click":
        selector = cmd.get("selector", "")
        if not selector:
            return make_response(False, error="No selector")
        await page.click(selector)
        await asyncio.sleep(0.5)
        return make_response(True, {"clicked": selector})

    if action == "mouse_move":
        x, y = cmd.get("x"), cmd.get("y")
        if x is None or y is None:
            return make_response(False, error="x,y required")
        system.move_mouse(int(x), int(y), cmd.get("duration"))
        return make_response(True, {"moved_to": {"x": x, "y": y}})

    if action == "mouse_click":
        x, y = cmd.get("x"), cmd.get("y")
        system.click(int(x) if x else None, int(y) if y else None)
        if x is None or y is None:
            return make_response(True, {"clicked_at": "current"})
        return make_response(True, {"clicked_at": {"x": x, "y": y}})

    if action == "system_click":
        x, y = cmd.get("x"), cmd.get("y")
        if x is None or y is None:
            return make_response(False, error="x,y required")
        system.move_mouse(int(x), int(y), cmd.get("duration"))
        system.click()
        return make_response(True, {"system_clicked": {"x": x, "y": y}})

    if action == "scroll":
        amount = cmd.get("amount", -3)
        x, y = cmd.get("x"), cmd.get("y")
        system.scroll(
            int(amount),
            int(x) if x is not None else None,
            int(y) if y is not None else None,
        )
        return make_response(True, {"scrolled": amount})

    if action == "scroll_to_bottom":
        delay = cmd.get("delay", 0.4)
        delay_ms = int(float(delay) * 1000)
        await page.evaluate(f"""(async () => {{
            let prev = -1;
            while (window.scrollY !== prev) {{
                prev = window.scrollY;
                window.scrollBy(0, window.innerHeight);
                await new Promise(r => setTimeout(r, {delay_ms}));
            }}
            window.scrollTo(0, 0);
        }})()""")
        return make_response(True, {"scrolled": "bottom"})

    if action == "scroll_to_bottom_humanized":
        min_clicks = int(cmd.get("min_clicks", 2))
        max_clicks = int(cmd.get("max_clicks", 6))
        delay = float(cmd.get("delay", 0.5))
        while True:
            prev = await page.evaluate("window.scrollY")
            clicks = random.randint(min_clicks, max_clicks)
            system.scroll(-clicks)
            jittered = delay * random.uniform(0.7, 1.3)
            await asyncio.sleep(jittered)
            curr = await page.evaluate("window.scrollY")
            if curr == prev:
                break
        await page.evaluate("window.scrollTo(0, 0)")
        return make_response(True, {"scrolled": "bottom_humanized"})

    if action == "calibrate":
        system.window_offset = await get_window_offset_js(page)
        return make_response(True, {"window_offset": system.window_offset})

    if action == "enter_fullscreen":
        is_fullscreen = await page.evaluate("!!document.fullscreenElement")
        if not is_fullscreen:
            await page.evaluate("document.documentElement.requestFullscreen()")
        return make_response(True, {"fullscreen": True, "changed": not is_fullscreen})

    if action == "exit_fullscreen":
        is_fullscreen = await page.evaluate("!!document.fullscreenElement")
        if is_fullscreen:
            await page.evaluate("document.exitFullscreen()")
        return make_response(True, {"fullscreen": False, "changed": is_fullscreen})

    if action == "get_resolution":
        result = system.get_resolution()
        return make_response(True, result)

    if action == "system_type":
        text = cmd.get("text", "")
        interval = cmd.get("interval", 0.08)
        if not text:
            return make_response(False, error="No text")
        system.system_type(text, interval)
        return make_response(True, {"typed_len": len(text)})

    if action == "send_key":
        key = cmd.get("key", "")
        if not key:
            return make_response(False, error="No key")
        system.send_key(key)
        return make_response(True, {"send_key": key})

    if action == "fill":
        selector, value = cmd.get("selector", ""), cmd.get("value", "")
        await page.fill(selector, value)
        return make_response(True, {"filled": selector})

    if action == "type":
        selector = cmd.get("selector", "")
        text = cmd.get("text", "")
        delay = cmd.get("delay", 0.05)
        await page.type(selector, text, delay=int(delay * 1000))
        return make_response(True, {"typed": selector})

    if action == "eval":
        expr = cmd.get("expression", "")
        result = await page.evaluate(expr)
        return make_response(True, {"result": result})

    if action == "get_interactive_elements":
        assert browser is not None
        visible_only = cmd.get("visible_only", True)
        browser._page = page
        elements = await browser.get_interactive_elements(visible_only)
        return make_response(True, {"count": len(elements), "elements": elements})

    if action == "get_text":
        text = await page.inner_text("body")
        return make_response(True, {"text": text[:10000], "length": len(text)})

    if action == "get_html":
        html = await page.content()
        return make_response(True, {"html": html, "length": len(html)})

    # --- Wait conditions ---

    if action == "wait_for_element":
        selector = cmd.get("selector", "")
        if not selector:
            return make_response(False, error="selector required")
        state = cmd.get("state", "visible")
        timeout = cmd.get("timeout", 30)
        await page.wait_for_selector(
            selector, state=state, timeout=int(float(timeout) * 1000)
        )
        return make_response(True, {"selector": selector, "state": state})

    if action == "wait_for_text":
        text = cmd.get("text", "")
        if not text:
            return make_response(False, error="text required")
        timeout = cmd.get("timeout", 30)
        escaped = text.replace("\\", "\\\\").replace("'", "\\'")
        await page.wait_for_function(
            f"document.body.innerText.includes('{escaped}')",
            timeout=int(float(timeout) * 1000),
        )
        return make_response(True, {"text": text, "found": True})

    if action == "wait_for_url":
        pattern = cmd.get("url", "")
        if not pattern:
            return make_response(False, error="url required")
        timeout = cmd.get("timeout", 30)
        await page.wait_for_url(pattern, timeout=int(float(timeout) * 1000))
        return make_response(True, {"url": page.url})

    if action == "wait_for_network_idle":
        timeout = cmd.get("timeout", 30)
        await page.wait_for_load_state(
            "networkidle", timeout=int(float(timeout) * 1000)
        )
        return make_response(True, {"idle": True})

    # --- Storage management ---

    if action == "get_storage":
        storage_type = cmd.get("type", "local")
        if storage_type == "local":
            raw = await page.evaluate("JSON.stringify(localStorage)")
        else:
            raw = await page.evaluate("JSON.stringify(sessionStorage)")
        import json as _json

        items = _json.loads(raw) if raw else {}
        return make_response(True, {"items": items, "type": storage_type})

    if action == "set_storage":
        storage_type = cmd.get("type", "local")
        key = cmd.get("key", "")
        value = cmd.get("value", "")
        if not key:
            return make_response(False, error="key required")
        escaped_key = key.replace("\\", "\\\\").replace("'", "\\'")
        escaped_val = value.replace("\\", "\\\\").replace("'", "\\'")
        if storage_type == "local":
            await page.evaluate(
                f"localStorage.setItem('{escaped_key}', '{escaped_val}')"
            )
        else:
            await page.evaluate(
                f"sessionStorage.setItem('{escaped_key}', '{escaped_val}')"
            )
        return make_response(True, {"set": key, "type": storage_type})

    if action == "clear_storage":
        storage_type = cmd.get("type", "local")
        if storage_type == "local":
            await page.evaluate("localStorage.clear()")
        else:
            await page.evaluate("sessionStorage.clear()")
        return make_response(True, {"cleared": storage_type})

    # --- File upload ---

    if action == "upload_file":
        selector = cmd.get("selector", "")
        file_path = cmd.get("file_path", "")
        if not selector:
            return make_response(False, error="selector required")
        if not file_path:
            return make_response(False, error="file_path required")
        if not os.path.isfile(file_path):
            return make_response(False, error=f"File not found: {file_path}")
        await page.set_input_files(selector, file_path)
        return make_response(
            True,
            {
                "selector": selector,
                "file": os.path.basename(file_path),
                "size": os.path.getsize(file_path),
            },
        )

    return make_response(False, error=f"Unknown action: {action}")


# =============================================================================
# LOADER EXECUTION
# =============================================================================


async def execute_loader(loader, url: str) -> dict:
    """Execute a loader's steps, returning the final result."""
    results = []
    for step in loader.steps:
        step = substitute_url(step, url)
        # Mark goto steps from loaders to prevent infinite recursion
        if step.get("action") == "goto":
            step = {**step, "_from_loader": True}
        log.info(f"  [{loader.name}] {step.get('action', '?')}")
        result = await dispatch_action(step)
        result.pop("_binary", None)
        results.append(result)
        if not result.get("success", True):
            log.info(f"  [{loader.name}] Step failed: {result.get('error')}")
            break
    return make_response(
        True,
        {
            "loader": loader.name,
            "steps_executed": len(results),
            "last_result": results[-1] if results else None,
        },
    )


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Serialize all browser-touching requests so only one runs at a time.
_request_lock = asyncio.Lock()


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Any) -> Any:
    """Reject requests without a valid Bearer token when AUTH_TOKEN is set.

    /health is always allowed so HAProxy health checks work without auth.
    """
    if AUTH_TOKEN and request.url.path != "/health":
        auth = request.headers.get("Authorization", "")
        query_token = request.query_params.get("auth_token", "")
        if auth != f"Bearer {AUTH_TOKEN}" and query_token != AUTH_TOKEN:
            return JSONResponse(
                {"success": False, "error": "Unauthorized"}, status_code=401
            )
    return await call_next(request)


# Registered AFTER auth_middleware on purpose: Starlette executes middlewares
# in REVERSE registration order, so the last @app.middleware decorated
# function ends up OUTERMOST. We want trace_middleware to wrap auth so even
# 401 responses carry a trace_id in logs + an X-Request-Id back to the
# caller.
@app.middleware("http")
async def trace_middleware(request: Request, call_next: Any) -> Any:
    """Seed trace_id + request_id ContextVars on every HTTP request.

    Every log line carries a trace_id; HTTP request lines also carry a
    request_id, and X-Request-Id is echoed back on the response so the caller
    (n8n / MCP client / operator curl) can correlate their side with our logs.
    """
    trace_id = new_trace_id()
    incoming_rid = request.headers.get("X-Request-Id", "") or request.headers.get(
        "x-request-id", ""
    )
    request_id = _safe_request_id(incoming_rid)

    trace_token = trace_id_var.set(trace_id)
    rid_token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        trace_id_var.reset(trace_token)
        request_id_var.reset(rid_token)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.post("/")
async def handle_command(request: Request) -> JSONResponse:
    """POST / - Execute a command."""
    try:
        cmd = await request.json()
    except Exception as e:
        log.error("invalid request JSON", extra={"error": str(e)})
        return JSONResponse(make_response(False, error=f"Invalid JSON: {e}"))

    action = cmd.get("action", "")

    if _CLUSTER_MODE and action not in _CLUSTER_ALLOWED_ACTIONS:
        return JSONResponse(make_response(
            False,
            error=(
                f"Action '{action}' not available in cluster mode. "
                "Use run_script to execute multiple actions atomically."
            ),
        ))

    params = {k: v for k, v in cmd.items() if k != "action"}
    log_request(action, params if params else None)

    async with _request_lock:
        try:
            result = await dispatch_action(cmd)
            result.pop("_binary", None)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse(make_response(False, error=str(e)))


def _resize_png(data: bytes, request: Request) -> bytes:
    """Resize PNG bytes based on query params: width, height, whLargest."""
    q = request.query_params
    w_str = q.get("width")
    h_str = q.get("height")
    largest_str = q.get("whLargest")

    if not w_str and not h_str and not largest_str:
        return data

    img = Image.open(io.BytesIO(data))
    orig_w, orig_h = img.size

    if largest_str:
        largest = int(largest_str)
        if orig_w >= orig_h:
            new_w = largest
            new_h = int(orig_h * largest / orig_w)
        else:
            new_h = largest
            new_w = int(orig_w * largest / orig_h)
    elif w_str and h_str:
        new_w = int(w_str)
        new_h = int(h_str)
    elif w_str:
        new_w = int(w_str)
        new_h = int(orig_h * new_w / orig_w)
    else:
        new_h = int(h_str)  # type: ignore[arg-type]
        new_w = int(orig_w * new_h / orig_h)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/screenshot/browser")
async def handle_screenshot_browser(request: Request) -> Response:
    """GET /screenshot/browser - Return browser viewport PNG screenshot."""
    page = await get_active_page()
    if not page:
        return PlainTextResponse("No active page", status_code=503)

    try:
        data = await page.screenshot(type="png")
        data = _resize_png(data, request)
        return Response(content=data, media_type=CONTENT_TYPE_IMAGE_PNG)
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)


@app.get("/screenshot/desktop")
async def handle_screenshot_desktop(request: Request) -> Response:
    """GET /screenshot/desktop - Return full desktop PNG screenshot using scrot."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        result = subprocess.run(
            ["scrot", "-o", tmp_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return PlainTextResponse(f"scrot failed: {result.stderr}", status_code=500)

        with open(tmp_path, "rb") as f:
            data = f.read()

        os.unlink(tmp_path)
        data = _resize_png(data, request)
        return Response(content=data, media_type=CONTENT_TYPE_IMAGE_PNG)
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)


@app.get("/state")
async def handle_state() -> JSONResponse:
    """GET /state - Return browser state."""
    if not browser:
        return JSONResponse({"status": "not_ready"})

    page = await get_active_page()
    return JSONResponse(
        {
            "status": "ready" if page else "no_page",
            "url": browser.state.url,
            "title": browser.state.title,
            "window_offset": system.window_offset,
        }
    )


@app.get("/health")
async def handle_health() -> PlainTextResponse:
    """GET /health - Health check."""
    return PlainTextResponse("ok")


# =============================================================================
# MCP SERVER (mounted at /mcp)
# =============================================================================

try:
    from mcp_server import mcp, set_dispatcher

    mcp_app = mcp.http_app(transport="streamable-http", path="/")
    app.mount("/mcp", mcp_app)
    app.router.lifespan_context = mcp_app.router.lifespan_context
    _mcp_available = True
except ImportError:
    _mcp_available = False


# =============================================================================
# SERVER LIFECYCLE
# =============================================================================


async def _run_script_mode(script_path: str, config: BrowserConfig) -> None:
    """Run a YAML script and exit."""
    global browser, loaders_dir

    # Capture real stdout for JSON output, redirect default stdout
    # to stderr so library warnings (Xlib etc) don't pollute JSON
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    # Redirect logs to stderr so stdout stays clean for JSON
    from logger import configure_output

    configure_output(sys.stderr)

    # Still load loaders so goto steps can trigger them
    loaders_dir = os.environ.get("LOADERS_DIR", "/loaders")

    try:
        script_data = load_script(script_path)
    except Exception as e:
        log.info(f"Failed to load script: {e}")
        sys.exit(1)

    log.info("Starting browser (script mode)")

    async with Browser(config) as b:
        browser = b
        await browser._get_page()

        system.init()
        log.info("PyAutoGUI ready")

        await asyncio.sleep(1)
        page = await get_active_page()
        if page:
            _setup_page_handlers(page)
            system.window_offset = await get_window_offset_js(page)
        log.info(f"Window offset: {system.window_offset}")
        log.info("Browser ready")

        result = await run_script(script_data, dispatch_action, real_stdout)
        sys.exit(0 if result["success"] else 1)


async def main() -> None:
    global browser, loaders_dir

    config = BrowserConfig()

    # Sweep stale ffmpeg tmp files left by a previous crashed run.
    cleanup_orphan_tmp_files()

    if SCRIPT_PATH:
        await _run_script_mode(SCRIPT_PATH, config)
        return

    # Set loaders directory
    loaders_dir = os.environ.get("LOADERS_DIR", "/loaders")
    initial_loaders = load_loaders(loaders_dir)
    if initial_loaders:
        log.info(f"Found {len(initial_loaders)} loader(s) in {loaders_dir}")

    log.info("Starting browser")

    async with Browser(config) as b:
        browser = b
        await browser._get_page()

        system.init()
        log.info("PyAutoGUI ready")

        await asyncio.sleep(1)
        page = await get_active_page()
        if page:
            _setup_page_handlers(page)
            system.window_offset = await get_window_offset_js(page)
        log.info(f"Window offset: {system.window_offset}")

        # Register MCP dispatcher
        if _mcp_available:
            set_dispatcher(dispatch_action, _request_lock)
            log.info("MCP server mounted at /mcp")

        log.info("Browser ready")

        uvi_config = uvicorn.Config(
            app,
            host=HTTP_LISTEN_HOST,
            port=HTTP_LISTEN_PORT,
            log_level="warning",
            lifespan="on",
        )
        server = uvicorn.Server(uvi_config)
        if AUTH_TOKEN:
            log.info("API key auth enabled")
        log.info(f"API listening on {HTTP_LISTEN_HOST}:{HTTP_LISTEN_PORT}")
        try:
            await server.serve()
        finally:
            # Don't leave ffmpeg orphaned + tmp file lingering on shutdown.
            recorder.abort()

    log.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
