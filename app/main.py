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
import base64
import contextvars
import fnmatch
import hmac
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
import yaml
from browser import (
    COLOR_SCHEME_DEFAULT,
    COLOR_SCHEMES,
    MAIN_WORLD_PREFIX,
    Browser,
    BrowserConfig,
    BrowserError,
)
from clipboard import Clipboard, ClipboardError, redact_clipboard_params
from browser_events import BrowserEventBus, BrowserEventError
from dom_targets import (
    FRAME_DEPTH_MAX,
    TARGET_SELECTOR_LENGTH_MAX,
    ElementRegistry,
    TargetError,
    create_reference,
    resolve_target,
    target_requested,
    uses_plain_selector_target,
    validate_role,
)
from captcha_solver import (
    CAPTCHA_SOLVE_POLL_INTERVAL_DEFAULT_SECONDS,
    CAPTCHA_SOLVE_TIMEOUT_DEFAULT_SECONDS,
)
from fastapi import FastAPI, Request
from PIL import Image
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from recorder import Recorder, RecorderError, cleanup_orphan_tmp_files
from challenge_detector import detect_challenges
from script_runner import ScriptValidationError, load_script, run_script, validate_script
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
import input_actions
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
        log.info(
            "request",
            extra={"action": action, "params": redact_clipboard_params(action, params)},
        )
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

clipboard = Clipboard()
event_bus = BrowserEventBus()
element_registry = ElementRegistry()

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
_page_heal_lock = asyncio.Lock()

# Download tracking
_last_download: dict | None = None

# Network logging
_network_log: list[dict] = []
_network_logging: bool = False
_network_handler_pages: set[int] = set()
_network_rules: list[dict[str, Any]] = []
_network_route_installed = False
_network_route_context_id: int | None = None
_network_capture_request_bodies = False
_network_capture_response_bodies = False
_network_body_limit = 256 * 1024
_network_request_ids: dict[int, int] = {}
_network_request_sequence = 0
_NETWORK_BODY_LIMIT_MAX = 1024 * 1024
_NETWORK_LOG_CAPACITY = 5000
_SENSITIVE_HEADER_NAMES = frozenset(
    {"authorization", "cookie", "proxy-authorization", "set-cookie", "x-api-key"}
)
_PERMISSIONS = frozenset(
    {
        "accessibility-events",
        "accelerometer",
        "ambient-light-sensor",
        "background-sync",
        "camera",
        "clipboard-read",
        "clipboard-write",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "midi-sysex",
        "notifications",
        "payment-handler",
    }
)
_GEOLOCATION_ACCURACY_MAX = 100_000.0
_permission_grants: list[tuple[list[str], str | None]] = []
_geolocation: dict[str, float] | None = None
_context_state_id: int | None = None

# Console logging
_console_log: list[dict] = []
_console_logging: bool = False
_console_handler_pages: set[int] = set()
_base_handler_pages: set[int] = set()

_handoff_active = False
_handoff_started_at: float | None = None
_HANDOFF_ALLOWED_ACTIONS = {
    "end_handoff",
    "get_events",
    "get_html",
    "get_page_info",
    "get_text",
    "handoff_status",
    "list_tabs",
    "list_windows",
    "ping",
    "recording_status",
    "save_screenshot",
    "sleep",
    "wait_for_event",
}

HTTP_LISTEN_HOST = os.environ.get("HTTP_LISTEN_HOST", "0.0.0.0")
HTTP_LISTEN_PORT = int(os.environ.get("HTTP_LISTEN_PORT", "8080"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip() or None

_NUM_REPLICAS = int(os.environ.get("NUM_REPLICAS", "1"))
_CLUSTER_MODE = _NUM_REPLICAS > 1
_CLUSTER_ALLOWED_ACTIONS = {"run_script", "ping", "sleep"}
_NAVIGATION_INTERRUPTED_ERROR = "interrupted by another navigation"

# Where the eval action runs. "page" sees the page's own JavaScript state;
# "isolated" sees only the DOM, and page code cannot observe or hook it.
EVAL_WORLD_PAGE = "page"
EVAL_WORLD_ISOLATED = "isolated"
EVAL_WORLDS = (EVAL_WORLD_PAGE, EVAL_WORLD_ISOLATED)

NAVIGATION_TIMEOUT_DEFAULT_SECONDS = 30.0
NAVIGATION_RETRY_COUNT_DEFAULT = 1
NAVIGATION_RETRY_DELAY_DEFAULT_SECONDS = 1.0

_NAVIGATION_MIN_TIMEOUT_SECONDS = 0.1
_NAVIGATION_MAX_TIMEOUT_SECONDS = 120.0
_NAVIGATION_MIN_RETRY_COUNT = 0
_NAVIGATION_MAX_RETRY_COUNT = 2
_NAVIGATION_MIN_RETRY_DELAY_SECONDS = 0.0
_NAVIGATION_MAX_RETRY_DELAY_SECONDS = 10.0
_NAVIGATION_MAX_TOTAL_SECONDS = 120.0
_NAVIGATION_BACKOFF_MULTIPLIER = 2
_MILLISECONDS_PER_SECOND = 1_000


@dataclass(frozen=True)
class NavigationOptions:
    """Validated navigation controls expressed in seconds."""

    timeout_seconds: float
    retry_count: int
    retry_delay_seconds: float

    @property
    def timeout_milliseconds(self) -> float:
        """Return the Playwright timeout value in milliseconds."""
        return self.timeout_seconds * _MILLISECONDS_PER_SECOND


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
    event_bus.publish("dialog", dict(_last_dialog))
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
    event_bus.publish("download", dict(_last_download or {}))


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: "[REDACTED]" if name.lower() in _SENSITIVE_HEADER_NAMES else value
        for name, value in headers.items()
    }


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if any(marker in lowered for marker in ("auth", "key", "password", "secret", "session", "token")):
            value = "[REDACTED]"
        query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _append_network_item(item: dict[str, Any]) -> None:
    _network_log.append(item)
    if len(_network_log) > _NETWORK_LOG_CAPACITY:
        del _network_log[: len(_network_log) - _NETWORK_LOG_CAPACITY]


def _on_request(request: Any) -> None:
    """Track network requests when logging is enabled."""
    global _network_request_sequence
    _network_request_sequence += 1
    request_id = _network_request_sequence
    _network_request_ids[id(request)] = request_id
    if len(_network_request_ids) > _NETWORK_LOG_CAPACITY:
        _network_request_ids.pop(next(iter(_network_request_ids)))
    item = {
        "type": "request",
        "request_id": request_id,
        "url": _redact_url(request.url),
        "method": request.method,
        "resource_type": request.resource_type,
        "timestamp": time.time(),
        "headers": _redact_headers(request.headers),
    }
    if _network_capture_request_bodies and request.post_data is not None:
        encoded = request.post_data.encode("utf-8")
        item["body"] = encoded[:_network_body_limit].decode("utf-8", errors="replace")
        item["body_truncated"] = len(encoded) > _network_body_limit
    event_bus.publish("request", item)
    if _network_logging:
        _append_network_item(item)


async def _on_response(response: Any) -> None:
    """Track network responses when logging is enabled."""
    request_id = _network_request_ids.pop(id(response.request), None)
    item = {
        "type": "response",
        "request_id": request_id,
        "url": _redact_url(response.url),
        "status": response.status,
        "timestamp": time.time(),
        "headers": _redact_headers(await response.all_headers()),
    }
    if _network_capture_response_bodies:
        try:
            body = await response.body()
            limited = body[:_network_body_limit]
            item["body_base64"] = base64.b64encode(limited).decode("ascii")
            item["body_truncated"] = len(body) > _network_body_limit
        except Exception as error:
            log.warning(
                "response body capture failed",
                extra={"error": str(error)},
            )
    event_bus.publish("response", item)
    if _network_logging:
        _append_network_item(item)


async def _route_network_request(route: Any, request: Any) -> None:
    for rule in _network_rules:
        if not fnmatch.fnmatch(request.url, rule["url"]):
            continue
        if rule.get("method") and request.method != rule["method"]:
            continue
        if rule.get("resource_type") and request.resource_type != rule["resource_type"]:
            continue
        if rule["action"] in {"abort", "block"}:
            await route.abort(rule.get("error_code", "blockedbyclient"))
            return
        if rule["action"] == "redirect":
            await route.continue_(url=rule["redirect_url"])
            return
        if rule["action"] == "fulfill":
            fulfill_options = {
                "status": rule.get("status", 200),
                "body": rule.get("body", ""),
            }
            if "headers" in rule:
                fulfill_options["headers"] = rule["headers"]
            if "content_type" in rule:
                fulfill_options["content_type"] = rule["content_type"]
            await route.fulfill(**fulfill_options)
            return
        continue_options = {}
        if "headers" in rule:
            continue_options["headers"] = {**request.headers, **rule["headers"]}
        if "post_data" in rule:
            continue_options["post_data"] = rule["post_data"]
        await route.continue_(**continue_options)
        return
    await route.continue_()


def _on_console(message: Any) -> None:
    """Track console messages when logging is enabled."""
    item = {
        "type": message.type,
        "text": message.text,
        "location": message.location,
        "timestamp": time.time(),
    }
    event_bus.publish("console", item)
    if _console_logging:
        _console_log.append(item)


def _on_page_close(page: Any) -> None:
    event_bus.publish("page_closed", {"url": page.url})
    contextvars.copy_context().run(
        asyncio.create_task,
        element_registry.clear(page),
    )


def _on_frame_navigated(page: Any, frame: Any) -> None:
    if frame is page.main_frame:
        event_bus.publish("navigation", {"url": frame.url})
        contextvars.copy_context().run(
            asyncio.create_task,
            element_registry.clear(page),
        )


def _on_popup(popup: Any) -> None:
    _setup_page_handlers(popup)
    event_bus.publish("popup", {"url": popup.url})


def _setup_page_handlers(page: Any) -> None:
    """Register all event handlers on a page."""
    page_id = id(page)
    if page_id not in _base_handler_pages:
        page.on("dialog", _on_dialog)
        page.on("download", _on_download)
        page.on("close", lambda: _on_page_close(page))
        page.on("framenavigated", lambda frame: _on_frame_navigated(page, frame))
        page.on("popup", _on_popup)
        _base_handler_pages.add(page_id)
    if page_id not in _network_handler_pages:
        page.on("request", _on_request)
        page.on("response", _on_response)
        _network_handler_pages.add(page_id)
    if page_id not in _console_handler_pages:
        page.on("console", _on_console)
        _console_handler_pages.add(page_id)


# Full-viewport transparent overlay at max z-index. focus_tab_window clicks
# the page at screen (5, 200) to transfer keyboard focus to the content; that
# point lands on top-left content where logos / nav links live, so a bare
# click would activate them and navigate the tab. Injecting this overlay
# first makes the click land on an inert div instead; removing it afterward
# leaves the page untouched.
_FOCUS_CLICK_OVERLAY_INJECT = (
    "(function(){"
    "if(document.getElementById('__sab_focus_overlay'))return 'exists';"
    "var d=document.createElement('div');"
    "d.id='__sab_focus_overlay';"
    "d.style.cssText='position:fixed;top:0;left:0;width:100vw;height:100vh;"
    "z-index:2147483647;background:transparent;margin:0;padding:0';"
    "(document.body||document.documentElement).appendChild(d);"
    "return 'in';})()"
)
_FOCUS_CLICK_OVERLAY_REMOVE = (
    "(function(){var d=document.getElementById('__sab_focus_overlay');"
    "if(d)d.remove();return 'out';})()"
)


async def _focus_active_tab(index: int, page: Any) -> None:
    """Raise + focus the tab window, guarding the focus click with an overlay.

    Injects a transparent max-z-index overlay on `page` so focus_tab_window's
    left-click can't activate any link/button under the cursor, raises +
    focuses the window, then removes the overlay. Best-effort throughout — a
    failed inject/remove degrades to a plain focus click, never breaks the
    tab operation.
    """
    if not browser:
        return
    injected = False
    if page is not None:
        try:
            await page.evaluate(_FOCUS_CLICK_OVERLAY_INJECT)
            injected = True
        except Exception as e:
            log.warning("focus overlay inject failed", extra={"error": str(e)})
    try:
        browser.focus_tab_window(index)
    finally:
        if injected:
            try:
                await page.evaluate(_FOCUS_CLICK_OVERLAY_REMOVE)
            except Exception as e:
                log.warning(
                    "focus overlay remove failed", extra={"error": str(e)}
                )


async def _relaunch_for_empty_pool() -> Any:
    """Relaunch the browser after its last tab closed and return the new page.

    Closing the last tab (via close_tab or the site's own window.close())
    leaves Camoufox running with no window, and the health probe still
    passes. Each tab is its own OS window, so with none left Firefox refuses
    new_page ("window is null") and only a relaunch recovers.
    """
    global _active_page
    log.warning(
        "no open tabs, relaunching the browser",
        extra={"reason": "empty_page_pool"},
    )
    if not await browser.relaunch():
        return None
    page = await browser._get_page()
    _setup_page_handlers(page)
    _active_page = page
    await _focus_active_tab(len(browser._context.pages) - 1, page)
    return page


async def get_active_page(open_if_empty: bool = True) -> Any:
    """Get the currently active page, relaunching browser if it died.

    When the browser is alive but every tab is closed, it is relaunched
    unless open_if_empty is False, which read-only callers such as list_tabs
    use to report the real pool. The screenshot and state routes run outside
    _request_lock, so _page_heal_lock keeps concurrent callers from each
    starting a relaunch.
    """
    global _active_page
    if not browser:
        return None
    async with _page_heal_lock:
        # Heal a dead/crashed Camoufox before reading the context. Without
        # this, the cached _active_page survives across browser deaths and
        # every subsequent action fails with "Connection closed while reading
        # from the driver" until the container is manually restarted.
        if not await browser.ensure_healthy():
            return None
        if not browser._context:
            return None
        await _ensure_network_route(browser._context)
        await _ensure_context_state(browser._context)
        pages = browser._context.pages
        if not pages:
            _active_page = None
            if not open_if_empty:
                return None
            return await _relaunch_for_empty_pool()
        # The cached _active_page may belong to the previous (dead) context.
        # Drop it if it's not in the current page list.
        if _active_page is None or _active_page not in pages:
            _active_page = pages[-1]
        return _active_page


async def _ensure_network_route(context: Any) -> None:
    global _network_route_installed, _network_route_context_id
    context_id = id(context)
    if not _network_rules or _network_route_context_id == context_id:
        return
    await context.route("**/*", _route_network_request)
    _network_route_installed = True
    _network_route_context_id = context_id


async def _ensure_context_state(context: Any) -> None:
    global _context_state_id, _handoff_active, _handoff_started_at
    context_id = id(context)
    if _context_state_id == context_id:
        return
    if _context_state_id is not None and _handoff_active:
        _handoff_active = False
        _handoff_started_at = None
        event_bus.publish("handoff_ended", {"reason": "browser_relaunch"})
    for permissions, origin in _permission_grants:
        await context.grant_permissions(permissions, origin=origin)
    if _geolocation is not None:
        await context.set_geolocation(_geolocation)
    _context_state_id = context_id


def _navigation_number(
    command: dict[str, Any],
    field: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Read one finite, bounded navigation control from an action command."""
    value = command.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")

    parsed_value = float(value)
    if not math.isfinite(parsed_value):
        raise ValueError(f"{field} must be finite")
    if parsed_value < minimum or parsed_value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed_value


def _navigation_options(command: dict[str, Any]) -> NavigationOptions:
    """Build bounded, explicit navigation controls for one action command."""
    timeout_seconds = _navigation_number(
        command,
        "timeout",
        NAVIGATION_TIMEOUT_DEFAULT_SECONDS,
        _NAVIGATION_MIN_TIMEOUT_SECONDS,
        _NAVIGATION_MAX_TIMEOUT_SECONDS,
    )
    retry_delay_seconds = _navigation_number(
        command,
        "retry_delay",
        NAVIGATION_RETRY_DELAY_DEFAULT_SECONDS,
        _NAVIGATION_MIN_RETRY_DELAY_SECONDS,
        _NAVIGATION_MAX_RETRY_DELAY_SECONDS,
    )
    retry_count = command.get("retry_count", NAVIGATION_RETRY_COUNT_DEFAULT)
    if isinstance(retry_count, bool) or not isinstance(retry_count, int):
        raise ValueError("retry_count must be an integer")
    if (
        retry_count < _NAVIGATION_MIN_RETRY_COUNT
        or retry_count > _NAVIGATION_MAX_RETRY_COUNT
    ):
        raise ValueError(
            "retry_count must be between "
            f"{_NAVIGATION_MIN_RETRY_COUNT} and {_NAVIGATION_MAX_RETRY_COUNT}"
        )

    retry_wait_seconds = retry_delay_seconds * (
        _NAVIGATION_BACKOFF_MULTIPLIER**retry_count - 1
    )
    worst_case_seconds = timeout_seconds * (retry_count + 1) + retry_wait_seconds
    if worst_case_seconds > _NAVIGATION_MAX_TOTAL_SECONDS:
        raise ValueError(
            "timeout, retry_count, and retry_delay exceed the "
            f"{_NAVIGATION_MAX_TOTAL_SECONDS}-second navigation budget"
        )

    return NavigationOptions(
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        retry_delay_seconds=retry_delay_seconds,
    )


async def _navigate_once(
    page: Any,
    url: str,
    navigation_options: NavigationOptions,
    **goto_kwargs: Any,
) -> None:
    """Navigate once, retaining Firefox's interrupted-navigation recovery."""
    try:
        await page.goto(url, **goto_kwargs)
    except Exception as error:
        if _NAVIGATION_INTERRUPTED_ERROR not in str(error).lower():
            raise

        log.warning("navigation interrupted while page was settling; retrying")
        await page.wait_for_load_state(
            "domcontentloaded",
            timeout=navigation_options.timeout_milliseconds,
        )
        await page.goto(url, **goto_kwargs)


async def _navigate(
    page: Any,
    url: str,
    navigation_options: NavigationOptions,
    **goto_kwargs: Any,
) -> None:
    """Navigate with explicit timeout and bounded timeout retries."""
    goto_kwargs["timeout"] = navigation_options.timeout_milliseconds
    for retry_index in range(navigation_options.retry_count + 1):
        try:
            await _navigate_once(page, url, navigation_options, **goto_kwargs)
            return
        except PlaywrightTimeoutError:
            if retry_index == navigation_options.retry_count:
                raise

            retry_delay_seconds = navigation_options.retry_delay_seconds * (
                _NAVIGATION_BACKOFF_MULTIPLIER**retry_index
            )
            log.warning(
                "navigation timed out; retrying",
                extra={
                    "retry_index": retry_index + 1,
                    "retry_delay_seconds": retry_delay_seconds,
                },
            )
            if retry_delay_seconds:
                await asyncio.sleep(retry_delay_seconds)


async def get_window_offset_js(page) -> dict:
    """Get the browser content area offset from the screen origin.

    Falls back to (0, 0) when the offset cannot be read; the warning logged
    by input_actions.read_window_offset is the only signal of that.
    """
    offset = await input_actions.read_window_offset(page)
    return offset if offset is not None else {"x": 0, "y": 0}


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


def _event_types(command: dict[str, Any]) -> set[str] | None:
    raw_types = command.get("types")
    if raw_types is None:
        return None
    if not isinstance(raw_types, list) or not all(
        isinstance(item, str) and item for item in raw_types
    ):
        raise BrowserEventError("types must be a list of non-empty strings")
    return set(raw_types)


async def _handoff_activity(*, stop: bool) -> dict[str, int]:
    page = await get_active_page(open_if_empty=False)
    if page is None:
        return {"pointer": 0, "keyboard": 0, "wheel": 0}
    result = await page.evaluate(
        """stop => {
            const handoff = globalThis.__sabHandoff;
            if (!handoff) return {pointer: 0, keyboard: 0, wheel: 0};
            const counts = {...handoff.counts};
            if (stop) {
                handoff.controller.abort();
                delete globalThis.__sabHandoff;
            }
            return counts;
        }""",
        stop,
    )
    return result


async def _target_box(page: Any, command: dict[str, Any]) -> dict[str, float]:
    target = await resolve_target(page, command, element_registry)
    box = await target.bounding_box()
    if box is None or box["width"] <= 0 or box["height"] <= 0:
        raise TargetError("target is not visible")
    return box


def _target_point_command(command: dict[str, Any], box: dict[str, float]) -> dict:
    return {
        **command,
        "x": box["x"] + box["width"] / 2,
        "y": box["y"] + box["height"] / 2,
        "w": box["width"],
        "h": box["height"],
    }


# =============================================================================
# ACTION DISPATCH
# =============================================================================


async def _dispatch_action_inner(cmd: dict) -> dict:
    """Execute a single action command. Returns response dict."""
    global _next_dialog_action, _active_page, _network_logging, _console_logging
    global _handoff_active, _handoff_started_at
    global _network_capture_request_bodies, _network_capture_response_bodies
    global _network_body_limit, _network_rules, _network_route_installed
    global _network_route_context_id
    global _permission_grants, _geolocation, _context_state_id
    action = cmd.get("action", "")

    if _handoff_active and action not in _HANDOFF_ALLOWED_ACTIONS:
        return make_response(False, error="human handoff is active")

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

    if action == "get_events":
        try:
            events = event_bus.get(
                after_id=int(cmd.get("after_id", 0)),
                event_types=_event_types(cmd),
                limit=int(cmd.get("limit", 100)),
            )
        except (BrowserEventError, TypeError, ValueError) as error:
            return make_response(False, error=str(error))
        return make_response(True, {"events": events, "count": len(events)})

    if action == "wait_for_event":
        try:
            events = await event_bus.wait(
                after_id=int(cmd.get("after_id", 0)),
                event_types=_event_types(cmd),
                timeout=float(cmd.get("timeout", 30)),
            )
        except (BrowserEventError, TypeError, ValueError) as error:
            return make_response(False, error=str(error))
        return make_response(True, {"events": events, "count": len(events)})

    if action == "begin_handoff":
        if _handoff_active:
            return make_response(False, error="human handoff is already active")
        page = await get_active_page(open_if_empty=False)
        if page is None:
            return make_response(False, error="No active page")
        await page.evaluate(
            """() => {
                const controller = new AbortController();
                const counts = {pointer: 0, keyboard: 0, wheel: 0};
                const trusted = key => event => {
                    if (event.isTrusted) counts[key]++;
                };
                document.addEventListener('pointermove', trusted('pointer'), {
                    capture: true, signal: controller.signal,
                });
                document.addEventListener('pointerdown', trusted('pointer'), {
                    capture: true, signal: controller.signal,
                });
                document.addEventListener('keydown', trusted('keyboard'), {
                    capture: true, signal: controller.signal,
                });
                document.addEventListener('wheel', trusted('wheel'), {
                    capture: true, signal: controller.signal,
                });
                globalThis.__sabHandoff = {controller, counts};
            }"""
        )
        _handoff_active = True
        _handoff_started_at = time.time()
        event_bus.publish("handoff_started", {})
        return make_response(True, {"active": True, "started_at": _handoff_started_at})

    if action == "handoff_status":
        activity = await _handoff_activity(stop=False) if _handoff_active else None
        return make_response(
            True,
            {
                "active": _handoff_active,
                "started_at": _handoff_started_at,
                "activity": activity,
            },
        )

    if action == "end_handoff":
        if not _handoff_active:
            return make_response(False, error="human handoff is not active")
        duration = time.time() - (_handoff_started_at or time.time())
        activity = await _handoff_activity(stop=True)
        _handoff_active = False
        _handoff_started_at = None
        event_bus.publish(
            "handoff_ended",
            {"duration": duration, "activity": activity},
        )
        return make_response(
            True,
            {"active": False, "duration": duration, "activity": activity},
        )

    if action == "set_clipboard":
        clipboard_format = cmd.get("format", "text")
        try:
            if clipboard_format == "text":
                if "text" not in cmd:
                    return make_response(False, error="text required")
                return make_response(True, await clipboard.set_text(cmd.get("text")))
            if clipboard_format == "image/png":
                value = cmd.get("content_base64")
            elif clipboard_format == "files":
                value = cmd.get("files")
            else:
                value = cmd.get("text")
            return make_response(True, await clipboard.set(clipboard_format, value))
        except ClipboardError as error:
            return make_response(False, error=str(error))

    if action == "get_clipboard":
        try:
            clipboard_format = cmd.get("format", "text")
            if clipboard_format == "text":
                return make_response(True, await clipboard.get_text())
            return make_response(True, await clipboard.get(clipboard_format))
        except ClipboardError as error:
            return make_response(False, error=str(error))

    if action == "start_recording":
        mode = cmd.get("mode", "window")
        fps = int(cmd.get("fps", 15))
        show_cursor = bool(cmd.get("show_cursor", True))
        # viewport mode crops to the page content area at its current screen
        # position, re-measured now because fullscreen and tab windows move
        # it. window/desktop capture from (0, 0) to include the browser frame.
        if mode == "viewport":
            active = await get_active_page()
            fresh = await input_actions.read_window_offset(active) if active else None
            if fresh is not None:
                system.window_offset = fresh
            offset_x = int(system.window_offset.get("x", 0))
            offset_y = int(system.window_offset.get("y", 0))
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

    if action == "solve_captcha":
        if browser is None:
            return make_response(False, error="Browser not ready")
        try:
            result = await browser.solve_captcha(
                cmd.get("task"),
                cmd.get("timeout", CAPTCHA_SOLVE_TIMEOUT_DEFAULT_SECONDS),
                cmd.get(
                    "poll_interval",
                    CAPTCHA_SOLVE_POLL_INTERVAL_DEFAULT_SECONDS,
                ),
            )
        except BrowserError as error:
            return make_response(False, error=str(error))
        return make_response(True, result)

    if action == "run_script":
        steps = cmd.get("steps")
        yaml_content = cmd.get("yaml")
        if yaml_content:
            if not isinstance(yaml_content, str):
                return make_response(False, error="yaml must be a string")
            try:
                script_data = yaml.safe_load(yaml_content)
            except yaml.YAMLError:
                return make_response(False, error="invalid YAML")
        elif steps:
            script_data = {
                "name": cmd.get("name", "api_script"),
                "on_error": cmd.get("on_error", "stop"),
                "steps": steps,
            }
        else:
            return make_response(False, error="steps or yaml required")
        try:
            validate_script(script_data)
        except ScriptValidationError as error:
            return make_response(False, error=str(error))
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

    # Runtime controls are independent of the active page. A caller can
    # select a source before a page requests getUserMedia().
    if action == "get_virtual_media_state":
        if browser is None:
            return make_response(False, error="Browser not ready")
        return make_response(True, browser.virtual_media_state())

    if action == "set_virtual_media_source":
        if browser is None:
            return make_response(False, error="Browser not ready")
        kind = cmd.get("kind")
        source = cmd.get("source")
        if not isinstance(kind, str):
            return make_response(False, error="kind must be a string")
        if not isinstance(source, str):
            return make_response(False, error="source must be a string")
        try:
            state = browser.set_virtual_media_source(kind, source)
            await browser.notify_virtual_media_source_change()
            return make_response(True, state)
        except BrowserError as error:
            return make_response(False, error=str(error))

    if action == "upload_virtual_media":
        if browser is None:
            return make_response(False, error="Browser not ready")
        kind = cmd.get("kind")
        filename = cmd.get("filename")
        content_b64 = cmd.get("content_base64")
        activate = cmd.get("activate", False)
        if not isinstance(kind, str):
            return make_response(False, error="kind must be a string")
        if not isinstance(filename, str):
            return make_response(False, error="filename must be a string")
        if not isinstance(content_b64, str):
            return make_response(False, error="content_base64 must be a string")
        if not isinstance(activate, bool):
            return make_response(False, error="activate must be a boolean")
        try:
            result = browser.upload_virtual_media(
                kind,
                filename,
                content_b64,
                activate=activate,
            )
            if activate:
                await browser.notify_virtual_media_source_change()
        except BrowserError as error:
            return make_response(False, error=str(error))
        return make_response(True, result)

    # --- Tab management ---

    if action == "list_tabs":
        pages = browser._context.pages if browser and browser._context else []
        tabs = []
        active = await get_active_page(open_if_empty=False)
        for i, p in enumerate(pages):
            tabs.append({"index": i, "url": p.url, "active": p is active})
        return make_response(True, {"tabs": tabs, "count": len(tabs)})

    if action == "new_tab":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        if browser._context.pages:
            new_page = await browser._context.new_page()
            _setup_page_handlers(new_page)
            # Applied here as well as by the context `page` handler, so the
            # tab is already in the scheme when it navigates below.
            await browser.apply_color_scheme(new_page)
            _active_page = new_page
            # Foreground the new tab's window so it's what Xvfb renders
            # (screenshots, recordings, VNC). See Browser.focus_tab_window.
            # Firefox opens each page as its own OS window and bring_to_front()
            # doesn't raise it. The new page is always the last one; it's still
            # blank here (navigation happens below) so the focus click is safe.
            await _focus_active_tab(len(browser._context.pages) - 1, new_page)
        else:
            # With no window left Firefox refuses new_page, so the relaunch in
            # get_active_page opens, sets up, and focuses the tab instead.
            new_page = await get_active_page()
            if new_page is None:
                return make_response(False, error="No active page")
        tab_url: str | None = cmd.get("url")
        if tab_url:
            await _navigate(
                new_page,
                tab_url,
                _navigation_options(cmd),
                wait_until=cmd.get("wait_until", "domcontentloaded"),
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
        await _focus_active_tab(index, _active_page)
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
            await _focus_active_tab(len(pages) - 1, _active_page)
        return make_response(True, {"closed": True, "remaining": len(pages)})

    if action == "list_windows":
        if browser is None:
            return make_response(False, error="Browser not ready")
        active = await get_active_page(open_if_empty=False)
        pages = browser._context.pages if browser._context else []
        windows = browser.list_windows()
        for window in windows:
            index = window["index"]
            window["url"] = pages[index].url if index < len(pages) else ""
            window["active"] = index < len(pages) and pages[index] is active
        return make_response(True, {"windows": windows, "count": len(windows)})

    if action == "focus_window":
        index = cmd.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return make_response(False, error="index must be an integer")
        pages = browser._context.pages if browser and browser._context else []
        if index < 0 or index >= len(pages):
            return make_response(False, error="window index out of range")
        _active_page = pages[index]
        await _focus_active_tab(index, _active_page)
        return make_response(True, {"index": index, "url": _active_page.url})

    if action == "move_resize_window":
        if browser is None:
            return make_response(False, error="Browser not ready")
        try:
            values = {
                key: int(cmd[key]) for key in ("index", "x", "y", "width", "height")
            }
        except (KeyError, TypeError, ValueError):
            return make_response(
                False,
                error="index, x, y, width, and height must be integers",
            )
        try:
            result = browser.move_resize_window(**values)
        except BrowserError as error:
            return make_response(False, error=str(error))
        return make_response(True, result)

    if action == "wait_for_popup":
        try:
            events = await event_bus.wait(
                after_id=int(cmd.get("after_id", 0)),
                event_types={"popup"},
                timeout=float(cmd.get("timeout", 30)),
            )
        except (BrowserEventError, TypeError, ValueError) as error:
            return make_response(False, error=str(error))
        return make_response(True, {"popup": events[0] if events else None})

    if action == "close_window":
        close_command = {"action": "close_tab"}
        if "index" in cmd:
            close_command["index"] = cmd["index"]
        return await dispatch_action(close_command)

    if action == "set_permissions":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        permissions = cmd.get("permissions")
        origin = cmd.get("origin")
        if not isinstance(permissions, list) or not all(
            isinstance(item, str) and item for item in permissions
        ):
            return make_response(False, error="permissions must be a list of strings")
        if not permissions or any(item not in _PERMISSIONS for item in permissions):
            return make_response(False, error="permissions contains an unsupported value")
        if origin is not None:
            if not isinstance(origin, str):
                return make_response(False, error="origin must be a string")
            parsed_origin = urlsplit(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.query
                or parsed_origin.fragment
                or parsed_origin.path not in {"", "/"}
            ):
                return make_response(False, error="origin must be a bare HTTP(S) origin")
        await browser._context.grant_permissions(permissions, origin=origin)
        _permission_grants = [
            grant for grant in _permission_grants if grant[1] != origin
        ]
        _permission_grants.append((list(permissions), origin))
        _context_state_id = id(browser._context)
        return make_response(True, {"permissions": permissions, "origin": origin})

    if action == "clear_permissions":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        await browser._context.clear_permissions()
        _permission_grants = []
        _context_state_id = id(browser._context)
        return make_response(True, {"cleared": True})

    if action == "set_geolocation":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        try:
            latitude = float(cmd["latitude"])
            longitude = float(cmd["longitude"])
            accuracy = float(cmd.get("accuracy", 0))
        except (KeyError, TypeError, ValueError):
            return make_response(False, error="invalid geolocation values")
        if not all(math.isfinite(value) for value in (latitude, longitude, accuracy)):
            return make_response(False, error="geolocation values must be finite")
        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
            and 0 <= accuracy <= _GEOLOCATION_ACCURACY_MAX
        ):
            return make_response(False, error="geolocation values are out of range")
        geolocation = {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
        }
        await browser._context.set_geolocation(geolocation)
        _geolocation = geolocation
        _context_state_id = id(browser._context)
        return make_response(True, geolocation)

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
        capture_request_bodies = cmd.get("capture_request_bodies", False)
        capture_response_bodies = cmd.get("capture_response_bodies", False)
        if not isinstance(capture_request_bodies, bool) or not isinstance(
            capture_response_bodies, bool
        ):
            return make_response(False, error="body capture options must be booleans")
        try:
            body_limit = int(cmd.get("max_body_bytes", 256 * 1024))
        except (TypeError, ValueError):
            return make_response(False, error="max_body_bytes must be an integer")
        if body_limit < 1 or body_limit > _NETWORK_BODY_LIMIT_MAX:
            return make_response(
                False,
                error=f"max_body_bytes must be between 1 and {_NETWORK_BODY_LIMIT_MAX}",
            )
        _network_capture_request_bodies = capture_request_bodies
        _network_capture_response_bodies = capture_response_bodies
        _network_body_limit = body_limit
        _network_logging = True
        page = await get_active_page()
        if page:
            _setup_page_handlers(page)
        return make_response(
            True,
            {
                "enabled": True,
                "capture_request_bodies": capture_request_bodies,
                "capture_response_bodies": capture_response_bodies,
                "max_body_bytes": body_limit,
            },
        )

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

    if action == "set_network_rules":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        rules = cmd.get("rules")
        if not isinstance(rules, list) or not rules or len(rules) > 100:
            return make_response(
                False,
                error="rules must be a non-empty list of at most 100 items",
            )
        validated_rules: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                return make_response(False, error="each network rule must be an object")
            url_pattern = rule.get("url")
            rule_action = rule.get("action", "continue")
            if not isinstance(url_pattern, str) or not url_pattern:
                return make_response(False, error="each network rule requires url")
            if rule_action not in {
                "abort",
                "block",
                "continue",
                "fulfill",
                "redirect",
            }:
                return make_response(False, error="network rule action is invalid")
            method = rule.get("method")
            if method is not None and (
                not isinstance(method, str) or not method or len(method) > 20
            ):
                return make_response(False, error="network rule method is invalid")
            if method is not None:
                rule = {**rule, "method": method.upper()}
            resource_type = rule.get("resource_type")
            if resource_type is not None and (
                not isinstance(resource_type, str)
                or not resource_type
                or len(resource_type) > 50
            ):
                return make_response(False, error="network resource_type is invalid")
            headers = rule.get("headers")
            if headers is not None and (
                not isinstance(headers, dict)
                or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in headers.items()
                )
            ):
                return make_response(False, error="network rule headers must be strings")
            if headers is not None and any(
                not key
                or any(character in key + value for character in "\r\n")
                for key, value in headers.items()
            ):
                return make_response(False, error="network rule headers are invalid")
            if rule_action == "redirect":
                redirect_url = rule.get("redirect_url")
                if not isinstance(redirect_url, str):
                    return make_response(False, error="redirect_url is required")
                parsed_redirect = urlsplit(redirect_url)
                if (
                    parsed_redirect.scheme not in {"http", "https"}
                    or not parsed_redirect.hostname
                    or parsed_redirect.username is not None
                    or parsed_redirect.password is not None
                ):
                    return make_response(False, error="redirect_url must be HTTP(S) without credentials")
            if rule_action == "fulfill":
                status = rule.get("status", 200)
                body = rule.get("body", "")
                if not isinstance(status, int) or not 100 <= status <= 599:
                    return make_response(False, error="fulfill status is out of range")
                if not isinstance(body, str) or len(body.encode()) > _NETWORK_BODY_LIMIT_MAX:
                    return make_response(False, error="fulfill body is invalid or too large")
            validated_rules.append(dict(rule))
        if _network_route_installed:
            await browser._context.unroute("**/*", _route_network_request)
        _network_rules = validated_rules
        await browser._context.route("**/*", _route_network_request)
        _network_route_installed = True
        _network_route_context_id = id(browser._context)
        return make_response(True, {"count": len(validated_rules)})

    if action == "clear_network_rules":
        if not browser or not browser._context:
            return make_response(False, error="No browser context")
        if _network_route_installed:
            await browser._context.unroute("**/*", _route_network_request)
        _network_route_installed = False
        _network_route_context_id = None
        _network_rules = []
        return make_response(True, {"cleared": True})

    if action == "export_har":
        requests_by_id = {
            item.get("request_id"): item
            for item in _network_log
            if item["type"] == "request" and item.get("request_id") is not None
        }
        responses_by_id = {
            item.get("request_id"): item
            for item in _network_log
            if item["type"] == "response" and item.get("request_id") is not None
        }
        entries = []
        for request_id, request_item in requests_by_id.items():
            response_item = responses_by_id.get(request_id, {})
            request_headers = [
                {"name": name, "value": value}
                for name, value in request_item["headers"].items()
            ]
            response_headers = [
                {"name": name, "value": value}
                for name, value in response_item.get("headers", {}).items()
            ]
            response_body = response_item.get("body_base64", "")
            content = {
                "size": len(base64.b64decode(response_body)) if response_body else 0,
                "mimeType": response_item.get("headers", {}).get("content-type", ""),
            }
            if response_body:
                content.update({"text": response_body, "encoding": "base64"})
            entries.append(
                {
                    "startedDateTime": datetime.fromtimestamp(
                        request_item["timestamp"],
                        timezone.utc,
                    ).isoformat().replace("+00:00", "Z"),
                    "time": 0,
                    "request": {
                        "method": request_item["method"],
                        "url": request_item["url"],
                        "httpVersion": "",
                        "cookies": [],
                        "headers": request_headers,
                        "queryString": [
                            {"name": key, "value": value}
                            for key, value in parse_qsl(
                                urlsplit(request_item["url"]).query,
                                keep_blank_values=True,
                            )
                        ],
                        "headersSize": -1,
                        "bodySize": len(request_item.get("body", "").encode()),
                    },
                    "response": {
                        "status": response_item.get("status", 0),
                        "statusText": "",
                        "httpVersion": "",
                        "cookies": [],
                        "headers": response_headers,
                        "content": content,
                        "redirectURL": "",
                        "headersSize": -1,
                        "bodySize": content["size"],
                    },
                    "cache": {},
                    "timings": {"send": -1, "wait": -1, "receive": -1},
                }
            )
        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "stealthy-auto-browse", "version": "1"},
                "entries": entries,
            }
        }
        path = cmd.get("path")
        if path is not None:
            if not isinstance(path, str) or not path:
                return make_response(False, error="path must be a non-empty string")
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as output:
                json.dump(har, output, separators=(",", ":"))
        return make_response(True, {"har": har, "path": path, "count": len(entries)})

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
        if ss_type not in {"browser", "desktop"}:
            return make_response(False, error="type must be browser or desktop")
        full_page = cmd.get("full_page", False)
        if not isinstance(full_page, bool):
            return make_response(False, error="full_page must be a boolean")
        if target_requested(cmd) and full_page:
            return make_response(False, error="element target and full_page conflict")
        if ss_type == "desktop" and (target_requested(cmd) or full_page):
            return make_response(
                False,
                error="desktop screenshots do not accept element targets or full_page",
            )

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
            if target_requested(cmd):
                try:
                    target = await resolve_target(page, cmd, element_registry)
                except TargetError as error:
                    return make_response(False, error=str(error))
                data = await target.screenshot(type="png")
            else:
                data = await page.screenshot(type="png", full_page=full_page)

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

        navigation_options = _navigation_options(cmd)
        goto_kwargs: dict[str, Any] = {
            "wait_until": cmd.get("wait_until", "domcontentloaded"),
        }
        if cmd.get("referer"):
            goto_kwargs["referer"] = cmd["referer"]
        await _navigate(page, url, navigation_options, **goto_kwargs)
        return make_response(True, {"url": page.url, "title": await page.title()})

    if action == "refresh":
        # page.reload() times out in Camoufox persistent context, so
        # re-navigate to the current URL instead (same practical effect).
        await _navigate(
            page,
            page.url,
            _navigation_options(cmd),
            wait_until=cmd.get("wait_until", "domcontentloaded"),
        )
        return make_response(True, {"url": page.url, "title": await page.title()})

    if action == "click":
        if uses_plain_selector_target(cmd):
            selector = cmd.get("selector", "")
            if not selector:
                return make_response(False, error="No selector")
            await page.click(selector)
            await asyncio.sleep(0.5)
            return make_response(True, {"clicked": selector})
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        await target.click()
        await asyncio.sleep(0.5)
        return make_response(True, {"clicked": True})

    if action in {"hover", "double_click", "right_click"}:
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        if action == "hover":
            await target.hover()
        elif action == "double_click":
            await target.dblclick()
        else:
            await target.click(button="right")
        return make_response(True, {action: True})

    if action == "drag_and_drop":
        source_command = cmd.get("source")
        target_command = cmd.get("target")
        if not isinstance(source_command, dict) or not isinstance(target_command, dict):
            return make_response(False, error="source and target must be target objects")
        try:
            source = await resolve_target(page, source_command, element_registry)
            destination = await resolve_target(page, target_command, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        await source.drag_to(destination)
        return make_response(True, {"dragged": True})

    if action in input_actions.ACTIONS:
        try:
            input_command = cmd
            if action == "system_drag":
                source_command = cmd.get("source")
                target_command = cmd.get("target")
                if not isinstance(source_command, dict) or not isinstance(
                    target_command, dict
                ):
                    return make_response(
                        False,
                        error="source and target must be target objects",
                    )
                source_box = await _target_box(page, source_command)
                target_box = await _target_box(page, target_command)
                input_command = {
                    **cmd,
                    "source_x": source_box["x"] + source_box["width"] / 2,
                    "source_y": source_box["y"] + source_box["height"] / 2,
                    "target_x": target_box["x"] + target_box["width"] / 2,
                    "target_y": target_box["y"] + target_box["height"] / 2,
                }
            elif target_requested(cmd):
                box = await _target_box(page, cmd)
                input_command = _target_point_command(cmd, box)
            data = await input_actions.ACTIONS[action](input_command, page, system)
        except TargetError as e:
            return make_response(False, error=str(e))
        except input_actions.InputActionError as e:
            return make_response(False, error=str(e))
        return make_response(True, data)

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

    if action == "calibrate":
        system.window_offset = await get_window_offset_js(page)
        return make_response(True, {"window_offset": system.window_offset})

    # Fullscreen is requested from the page's own world, as a page would.
    if action == "enter_fullscreen":
        is_fullscreen = await page.evaluate("!!document.fullscreenElement")
        if not is_fullscreen:
            await page.evaluate(
                MAIN_WORLD_PREFIX + "document.documentElement.requestFullscreen()"
            )
        return make_response(True, {"fullscreen": True, "changed": not is_fullscreen})

    if action == "exit_fullscreen":
        is_fullscreen = await page.evaluate("!!document.fullscreenElement")
        if is_fullscreen:
            await page.evaluate(MAIN_WORLD_PREFIX + "document.exitFullscreen()")
        return make_response(True, {"fullscreen": False, "changed": is_fullscreen})

    if action == "get_resolution":
        result = system.get_resolution()
        return make_response(True, result)

    if action == "set_color_scheme":
        assert browser is not None
        try:
            pages = await browser.set_color_scheme(cmd.get("scheme", ""))
        except ValueError:
            allowed = ", ".join((*COLOR_SCHEMES, COLOR_SCHEME_DEFAULT))
            return make_response(False, error=f"scheme must be one of: {allowed}")
        return make_response(True, {"scheme": browser.color_scheme, "pages": pages})

    if action == "fill":
        if uses_plain_selector_target(cmd):
            selector = cmd.get("selector", "")
            await page.fill(selector, cmd.get("value", ""))
            return make_response(True, {"filled": selector})
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        await target.fill(cmd.get("value", ""))
        return make_response(True, {"filled": True})

    if action == "type":
        if uses_plain_selector_target(cmd):
            selector = cmd.get("selector", "")
            delay = float(cmd.get("delay", 0.05))
            await page.type(
                selector,
                cmd.get("text", ""),
                delay=int(delay * 1000),
            )
            return make_response(True, {"typed": selector})
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        delay = float(cmd.get("delay", 0.05))
        await target.press_sequentially(cmd.get("text", ""), delay=int(delay * 1000))
        return make_response(True, {"typed": True})

    if action == "eval":
        expr = cmd.get("expression", "")
        world = cmd.get("world", EVAL_WORLD_PAGE)
        if world not in EVAL_WORLDS:
            return make_response(
                False,
                error=f"world must be one of: {', '.join(EVAL_WORLDS)}",
            )
        if world == EVAL_WORLD_PAGE:
            expr = MAIN_WORLD_PREFIX + expr
        result = await page.evaluate(expr)
        return make_response(True, {"result": result})

    if action == "create_element_reference":
        try:
            reference = await create_reference(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        return make_response(True, reference)

    if action == "release_element_reference":
        element_id = cmd.get("element_id")
        if not isinstance(element_id, str) or not element_id:
            return make_response(False, error="element_id required")
        await element_registry.remove(element_id)
        return make_response(True, {"released": element_id})

    if action == "get_accessibility_tree":
        target_command = dict(cmd)
        if not target_requested(target_command):
            target_command["selector"] = "body"
        try:
            target = await resolve_target(page, target_command, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        if target_command.get("element_id"):
            return make_response(
                False,
                error="get_accessibility_tree does not support element_id targets",
            )
        snapshot = await target.aria_snapshot()
        return make_response(True, {"snapshot": snapshot})

    if action == "get_by_role":
        role = cmd.get("role")
        try:
            validate_role(role, cmd.get("name"))
        except TargetError as error:
            return make_response(False, error=str(error))
        try:
            limit = int(cmd.get("limit", 20))
        except (TypeError, ValueError):
            return make_response(False, error="limit must be an integer")
        if limit < 1 or limit > 100:
            return make_response(False, error="limit must be between 1 and 100")
        frames = cmd.get("frames", [])
        scope: Any = page
        if not isinstance(frames, list) or not all(
            isinstance(item, str) and item for item in frames
        ):
            return make_response(False, error="frames must be a list of selectors")
        for frame_selector in frames:
            scope = scope.frame_locator(frame_selector)
        locator = scope.get_by_role(role, name=cmd.get("name"))
        count = min(await locator.count(), limit)
        elements = []
        for index in range(count):
            handle = await locator.nth(index).element_handle()
            if handle is None:
                continue
            element_id = await element_registry.add(page, handle)
            details = await handle.evaluate(
                "el => ({tag: el.tagName.toLowerCase(), text: "
                "(el.innerText || el.textContent || '').trim().slice(0, 500)})"
            )
            elements.append({"element_id": element_id, **details})
        return make_response(True, {"elements": elements, "count": len(elements)})

    if action == "wait_for_dom_change":
        condition = cmd.get("condition", "text")
        allowed_conditions = {
            "added",
            "removed",
            "text",
            "attribute",
            "visible",
            "hidden",
            "moved",
        }
        if condition not in allowed_conditions:
            return make_response(False, error="invalid DOM change condition")
        if condition == "attribute" and not isinstance(cmd.get("attribute"), str):
            return make_response(False, error="attribute is required for attribute changes")
        try:
            timeout_ms = int(float(cmd.get("timeout", 30)) * 1000)
        except (TypeError, ValueError):
            return make_response(False, error="timeout must be a number")
        if timeout_ms < 1 or timeout_ms > 300_000:
            return make_response(False, error="timeout must be between 0 and 300")
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            if condition in {"added", "removed"} and cmd.get("selector"):
                scope: Any = page
                for frame_selector in cmd.get("frames", []):
                    scope = scope.frame_locator(frame_selector)
                if condition == "removed":
                    if await scope.locator(cmd["selector"]).count() == 0:
                        return make_response(
                            True,
                            {"condition": condition, "changed": True},
                        )
                    return make_response(False, error=str(error))
                await scope.locator(cmd["selector"]).first.wait_for(
                    state="attached",
                    timeout=timeout_ms,
                )
                return make_response(True, {"condition": condition, "changed": True})
            return make_response(False, error=str(error))
        if condition == "added":
            return make_response(True, {"condition": condition, "changed": True})
        result = await target.evaluate(
            """(element, options) => new Promise((resolve, reject) => {
                const initialText = element.textContent;
                const initialAttribute = options.attribute
                    ? element.getAttribute(options.attribute) : null;
                const initialRect = element.getBoundingClientRect().toJSON();
                const visible = () => {
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return !!(rect.width && rect.height && style.visibility !== 'hidden'
                        && style.display !== 'none');
                };
                const initialVisible = visible();
                const changed = () => {
                    if (options.condition === 'removed') return !element.isConnected;
                    if (options.condition === 'text') return element.textContent !== initialText;
                    if (options.condition === 'attribute') {
                        return element.getAttribute(options.attribute) !== initialAttribute;
                    }
                    if (options.condition === 'visible') return !initialVisible && visible();
                    if (options.condition === 'hidden') return initialVisible && !visible();
                    if (options.condition === 'moved') {
                        const rect = element.getBoundingClientRect();
                        return rect.x !== initialRect.x || rect.y !== initialRect.y
                            || rect.width !== initialRect.width
                            || rect.height !== initialRect.height;
                    }
                    return false;
                };
                const observer = new MutationObserver(() => {
                    if (changed()) finish(true);
                });
                const resizeObserver = new ResizeObserver(() => {
                    if (changed()) finish(true);
                });
                const timer = setTimeout(() => finish(false), options.timeout);
                const finish = value => {
                    clearTimeout(timer);
                    observer.disconnect();
                    resizeObserver.disconnect();
                    resolve(value);
                };
                observer.observe(document, {
                    subtree: true,
                    childList: true,
                    characterData: true,
                    attributes: true,
                });
                resizeObserver.observe(element);
            })""",
            {
                "condition": condition,
                "attribute": cmd.get("attribute"),
                "timeout": timeout_ms,
            },
        )
        return make_response(True, {"condition": condition, "changed": bool(result)})

    if action == "get_interactive_elements":
        assert browser is not None
        visible_only = cmd.get("visible_only", True)
        browser._page = page
        elements = await browser.get_interactive_elements(visible_only)
        for element in elements:
            selector = element.get("selector")
            if not selector:
                continue
            try:
                handle = await page.locator(selector).first.element_handle()
            except PlaywrightError as error:
                log.warning(
                    "interactive element reference failed",
                    extra={"error": str(error)},
                )
                continue
            if handle is not None:
                element["element_id"] = await element_registry.add(page, handle)
        return make_response(True, {"count": len(elements), "elements": elements})

    if action == "get_text":
        text = await page.inner_text("body")
        return make_response(True, {"text": text[:10000], "length": len(text)})

    if action == "get_html":
        html = await page.content()
        return make_response(True, {"html": html, "length": len(html)})

    if action == "get_page_info":
        info = await page.evaluate("""() => ({
            url: location.href,
            title: document.title,
            readyState: document.readyState,
            viewport: { width: innerWidth, height: innerHeight },
            document: {
                width: document.documentElement.scrollWidth,
                height: document.documentElement.scrollHeight,
            },
            scroll: { x: scrollX, y: scrollY },
        })""")
        return make_response(True, info)

    if action == "detect_challenge":
        scroll_into_view = cmd.get("scroll_into_view", False)
        if not isinstance(scroll_into_view, bool):
            return make_response(False, error="scroll_into_view must be a boolean")
        challenge_result = await detect_challenges(
            page,
            scroll_into_view=scroll_into_view,
        )
        event_bus.publish(
            "challenge_result",
            {
                "status": challenge_result.get("status"),
                "detected": challenge_result.get("detected"),
            },
        )
        return make_response(True, challenge_result)

    if action == "get_element":
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        element = await target.evaluate("""node => {
            const rect = node.getBoundingClientRect();
            return {
                tag: node.tagName.toLowerCase(),
                text: (node.innerText || "").slice(0, 10000),
                attributes: Object.fromEntries([...node.attributes].map(a => [a.name, a.value])),
                boundingBox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                visible: Boolean(rect.width && rect.height),
            };
        }""")
        if cmd.get("element_id"):
            element["element_id"] = cmd["element_id"]
        else:
            handle = await target.element_handle()
            if handle is None:
                return make_response(False, error="target is not attached")
            element["element_id"] = await element_registry.add(page, handle)
        return make_response(True, element)

    if action == "get_elements":
        selector = cmd.get("selector", "")
        if not isinstance(selector, str) or not selector:
            return make_response(False, error="selector required")
        if len(selector) > TARGET_SELECTOR_LENGTH_MAX:
            return make_response(False, error="selector is too long")
        try:
            limit = int(cmd.get("limit", 20))
        except (TypeError, ValueError):
            return make_response(False, error="limit must be an integer")
        if limit < 1 or limit > 100:
            return make_response(False, error="limit must be between 1 and 100")
        frames = cmd.get("frames", [])
        if not isinstance(frames, list) or not all(
            isinstance(item, str) and item for item in frames
        ):
            return make_response(False, error="frames must be a list of selectors")
        if len(frames) > FRAME_DEPTH_MAX:
            return make_response(
                False,
                error=f"frames supports at most {FRAME_DEPTH_MAX} levels",
            )
        scope: Any = page
        for frame_selector in frames:
            scope = scope.frame_locator(frame_selector)
        locator = scope.locator(selector)
        count = min(await locator.count(), limit)
        elements = []
        for index in range(count):
            handle = await locator.nth(index).element_handle()
            if handle is None:
                continue
            element = await handle.evaluate(
                """node => {
                const rect = node.getBoundingClientRect();
                return {
                    tag: node.tagName.toLowerCase(),
                    text: (node.innerText || "").slice(0, 1000),
                    attributes: Object.fromEntries([...node.attributes].map(a => [a.name, a.value])),
                    boundingBox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                    visible: Boolean(rect.width && rect.height),
                };
            }"""
            )
            element["element_id"] = await element_registry.add(page, handle)
            elements.append(element)
        return make_response(True, {"count": len(elements), "elements": elements})

    if action == "get_computed_style":
        properties = cmd.get("properties", ["display", "visibility", "color", "background-color"])
        if not isinstance(properties, list) or not all(
            isinstance(property_name, str) and re.fullmatch(r"[a-zA-Z-]{1,64}", property_name)
            for property_name in properties
        ):
            return make_response(False, error="properties must be CSS property names")
        if len(properties) > 50:
            return make_response(False, error="properties may contain at most 50 entries")
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        styles = await target.evaluate(
            """(node, names) => {
                const computed = getComputedStyle(node);
                return Object.fromEntries(names.map(name => [name, computed.getPropertyValue(name)]));
            }""",
            properties,
        )
        return make_response(True, {"styles": styles})

    # --- Wait conditions ---

    if action == "wait_for_element":
        state = cmd.get("state", "visible")
        timeout = cmd.get("timeout", 30)
        try:
            if cmd.get("element_id"):
                target = await resolve_target(page, cmd, element_registry)
            else:
                scope: Any = page
                frames = cmd.get("frames", [])
                if not isinstance(frames, list):
                    raise TargetError("frames must be a list of selectors")
                for frame_selector in frames:
                    scope = scope.frame_locator(frame_selector)
                if cmd.get("selector"):
                    target = scope.locator(cmd["selector"]).first
                elif cmd.get("role"):
                    target = scope.get_by_role(cmd["role"], name=cmd.get("name")).first
                else:
                    raise TargetError("one of selector, element_id, or role is required")
        except TargetError as error:
            return make_response(False, error=str(error))
        await target.wait_for(state=state, timeout=int(float(timeout) * 1000))
        return make_response(True, {"state": state})

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
        file_path = cmd.get("file_path", "")
        if not file_path:
            return make_response(False, error="file_path required")
        if not os.path.isfile(file_path):
            return make_response(False, error=f"File not found: {file_path}")
        try:
            target = await resolve_target(page, cmd, element_registry)
        except TargetError as error:
            return make_response(False, error=str(error))
        await target.set_input_files(file_path)
        return make_response(
            True,
            {
                "file": os.path.basename(file_path),
                "size": os.path.getsize(file_path),
            },
        )

    return make_response(False, error=f"Unknown action: {action}")


async def dispatch_action(cmd: dict) -> dict:
    """Execute an action and publish bounded failure metadata."""
    action = cmd.get("action", "")
    try:
        result = await _dispatch_action_inner(cmd)
    except Exception as error:
        event_bus.publish(
            "action_failure",
            {"action": action, "error_type": type(error).__name__},
        )
        raise
    if not result.get("success", False):
        event_bus.publish("action_failure", {"action": action})
    return result


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
        if "auth_token" in request.query_params:
            return JSONResponse(
                {"success": False, "error": "Unauthorized"}, status_code=401
            )
        auth = request.headers.get("Authorization", "")
        expected_auth = f"Bearer {AUTH_TOKEN}"
        if not hmac.compare_digest(auth.encode(), expected_auth.encode()):
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
        selector = request.query_params.get("selector")
        frames = request.query_params.getlist("frame")
        full_page = request.query_params.get("full_page", "false").lower() == "true"
        if selector and full_page:
            return PlainTextResponse(
                "selector and full_page conflict",
                status_code=400,
            )
        if selector:
            target = await resolve_target(
                page,
                {"selector": selector, "frames": frames},
                element_registry,
            )
            data = await target.screenshot(type="png")
        else:
            data = await page.screenshot(type="png", full_page=full_page)
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


@app.get("/events")
async def handle_events(request: Request) -> Response:
    """Stream process-local browser events as server-sent events."""
    try:
        after_id = int(request.query_params.get("after_id", "0"))
        event_types = set(request.query_params.getlist("type")) or None
        event_bus.get(after_id=after_id, event_types=event_types, limit=1)
    except (BrowserEventError, TypeError, ValueError) as error:
        return JSONResponse(
            make_response(False, error=str(error)),
            status_code=400,
        )

    async def stream() -> Any:
        cursor = after_id
        while not await request.is_disconnected():
            events = await event_bus.wait(
                after_id=cursor,
                event_types=event_types,
                timeout=15,
            )
            if not events:
                yield ": keepalive\n\n"
                continue
            for event in events:
                cursor = max(cursor, event["id"])
                payload = json.dumps(event, separators=(",", ":"))
                yield f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


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

    config = BrowserConfig.from_environment()

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
