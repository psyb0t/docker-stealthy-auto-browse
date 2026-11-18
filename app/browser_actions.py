"""Browser actions for the stealthy-auto-browse plugin.

auto-linux-desk provides the HTTP API, MCP, script mode, the desktop and the
humanized input. This module holds the browser: the Camoufox session, page
event handlers and every browser action. stealthy_plugin registers them.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import fnmatch
import io
import json
import math
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from autolinuxdesk import actions as desk_actions
from autolinuxdesk import input_actions
from autolinuxdesk.events import EventError
from autolinuxdesk.logger import get_logger
from autolinuxdesk.responses import ActionError, make_response
from browser import (
    COLOR_SCHEME_DEFAULT,
    COLOR_SCHEMES,
    MAIN_WORLD_PREFIX,
    Browser,
    BrowserConfig,
    BrowserError,
)
from browser_input import read_window_offset
from captcha_solver import (
    CAPTCHA_SOLVE_POLL_INTERVAL_DEFAULT_SECONDS,
    CAPTCHA_SOLVE_TIMEOUT_DEFAULT_SECONDS,
)
from challenge_detector import detect_challenges
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
from loaders import find_loader, load_loaders, substitute_url
from PIL import Image
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

log = get_logger(__name__)

# =============================================================================
# GLOBALS
# =============================================================================

# The base Desk owns humanized input, the event bus and the handoff state.
# bind() points these at it before the browser starts.
_desk: Any = None
system: Any = None
event_bus: Any = None
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

# Browser actions that only read, so they still run during a human handoff.
# The base marks its own read-only actions.
HANDOFF_SAFE_ACTIONS = frozenset(
    {
        "get_html",
        "get_page_info",
        "get_text",
        "list_tabs",
        "list_windows",
        "ping",
        "save_screenshot",
    }
)

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
    the request lock, so _page_heal_lock keeps concurrent callers from each
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
    global _context_state_id
    context_id = id(context)
    if _context_state_id == context_id:
        return
    if _context_state_id is not None and _desk is not None and _desk.handoff.active:
        _desk.handoff.active = False
        _desk.handoff.started_at = None
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
    by browser_input.read_window_offset is the only signal of that.
    """
    offset = await read_window_offset(page)
    return offset if offset is not None else {"x": 0, "y": 0}


_HANDOFF_INSTALL_JS = """() => {
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
_HANDOFF_READ_JS = """stop => {
    const handoff = globalThis.__sabHandoff;
    if (!handoff) return {pointer: 0, keyboard: 0, wheel: 0};
    const counts = {...handoff.counts};
    if (stop) {
        handoff.controller.abort();
        delete globalThis.__sabHandoff;
    }
    return counts;
}"""


async def handoff_activity(desk: Any, stop: bool) -> dict[str, int]:
    """Count trusted pointer, keyboard and wheel input during a handoff."""
    page = await get_active_page(open_if_empty=False)
    if not desk.handoff.active:
        if page is None:
            raise ActionError("No active page")
        await page.evaluate(_HANDOFF_INSTALL_JS)
        return {"pointer": 0, "keyboard": 0, "wheel": 0}
    if page is None:
        return {"pointer": 0, "keyboard": 0, "wheel": 0}
    return await page.evaluate(_HANDOFF_READ_JS, stop)


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
    global _network_capture_request_bodies, _network_capture_response_bodies
    global _network_body_limit, _network_rules, _network_route_installed
    global _network_route_context_id
    global _permission_grants, _geolocation, _context_state_id
    action = cmd.get("action", "")

    # Actions that don't need page
    if action == "ping":
        url = browser.state.url if browser else ""
        return make_response(True, {"message": "pong", "url": url})

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
        except (EventError, TypeError, ValueError) as error:
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


# Every action _dispatch_action_inner handles. stealthy_plugin registers each
# one with the base.
BROWSER_ACTIONS = (
    "calibrate",
    "clear_console_log",
    "clear_network_log",
    "clear_network_rules",
    "clear_permissions",
    "clear_storage",
    "click",
    "close_tab",
    "close_window",
    "create_element_reference",
    "delete_cookies",
    "detect_challenge",
    "disable_console_log",
    "disable_network_log",
    "double_click",
    "drag_and_drop",
    "enable_console_log",
    "enable_network_log",
    "enter_fullscreen",
    "eval",
    "exit_fullscreen",
    "export_har",
    "fill",
    "focus_window",
    "get_accessibility_tree",
    "get_by_role",
    "get_computed_style",
    "get_console_log",
    "get_cookies",
    "get_element",
    "get_elements",
    "get_html",
    "get_interactive_elements",
    "get_last_dialog",
    "get_last_download",
    "get_network_log",
    "get_page_info",
    "get_storage",
    "get_text",
    "get_virtual_media_state",
    "getclear_console_log",
    "getclear_network_log",
    "goto",
    "handle_dialog",
    "hover",
    "list_tabs",
    "list_windows",
    "move_resize_window",
    "new_tab",
    "ping",
    "refresh",
    "release_element_reference",
    "right_click",
    "save_screenshot",
    "scroll_to_bottom",
    "set_color_scheme",
    "set_cookie",
    "set_geolocation",
    "set_network_rules",
    "set_permissions",
    "set_storage",
    "set_virtual_media_source",
    "solve_captcha",
    "switch_tab",
    "type",
    "upload_file",
    "upload_virtual_media",
    "wait_for_dom_change",
    "wait_for_element",
    "wait_for_network_idle",
    "wait_for_popup",
    "wait_for_text",
    "wait_for_url",
)


async def dispatch_action(cmd: dict) -> dict:
    """Run any action through the base dispatcher, or a browser action directly."""
    if _desk is not None:
        return await _desk.dispatch(cmd)
    return await _dispatch_action_inner(cmd)


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
# HUMANIZED INPUT ON PAGE TARGETS
# =============================================================================


async def _target_input_command(action: str, cmd: dict, page: Any) -> dict:
    """Turn element targets into the coordinates the base input actions take."""
    if action == "system_drag":
        source_command = cmd.get("source")
        target_command = cmd.get("target")
        if not isinstance(source_command, dict) or not isinstance(target_command, dict):
            raise TargetError("source and target must be target objects")
        source_box = await _target_box(page, source_command)
        target_box = await _target_box(page, target_command)
        return {
            **cmd,
            "source_x": source_box["x"] + source_box["width"] / 2,
            "source_y": source_box["y"] + source_box["height"] / 2,
            "target_x": target_box["x"] + target_box["width"] / 2,
            "target_y": target_box["y"] + target_box["height"] / 2,
        }
    if target_requested(cmd):
        return _target_point_command(cmd, await _target_box(page, cmd))
    return cmd


def page_input_action(action: str, handler: Any) -> Any:
    """Wrap a base input handler: needs a page, accepts element targets."""

    async def run(cmd: dict, desk: Any) -> dict:
        page = await get_active_page()
        if not page:
            return make_response(False, error="No active page")
        try:
            input_command = await _target_input_command(action, cmd, page)
            data = await handler(input_command, desk.surface, desk.system)
        except (TargetError, input_actions.InputActionError) as error:
            return make_response(False, error=str(error))
        return make_response(True, data)

    return run


async def paste_text(cmd: dict, desk: Any) -> dict:
    """The base paste_text, on page coordinates or an element target."""
    page = await get_active_page()
    if not page:
        return make_response(False, error="No active page")
    try:
        input_command = await _target_input_command("paste_text", cmd, page)
    except TargetError as error:
        return make_response(False, error=str(error))
    return await desk_actions.paste_text(input_command, desk)


async def browser_action(cmd: dict, desk: Any) -> dict:
    """Registry handler for every action in the browser dispatch chain."""
    return await _dispatch_action_inner(cmd)


# =============================================================================
# LIFECYCLE, STATE AND SCREENSHOTS
# =============================================================================

_browser_session: Browser | None = None


def bind(desk: Any) -> None:
    """Share the base Desk's input system and event bus."""
    global _desk, system, event_bus
    _desk = desk
    system = desk.system
    event_bus = desk.events


async def start_browser(desk: Any) -> None:
    """Launch Camoufox and measure the viewport before requests are served."""
    global browser, loaders_dir, _browser_session
    config = BrowserConfig.from_environment()
    loaders_dir = os.environ.get("LOADERS_DIR", "/loaders")
    initial_loaders = load_loaders(loaders_dir)
    if initial_loaders:
        log.info(f"Found {len(initial_loaders)} loader(s) in {loaders_dir}")

    log.info("Starting browser")
    _browser_session = Browser(config)
    browser = await _browser_session.__aenter__()
    await browser._get_page()

    await asyncio.sleep(1)
    page = await get_active_page()
    if page:
        _setup_page_handlers(page)
        system.window_offset = await get_window_offset_js(page)
    log.info(f"Window offset: {system.window_offset}")
    log.info("Browser ready")


async def stop_browser(desk: Any) -> None:
    global browser, _browser_session
    session, _browser_session = _browser_session, None
    browser = None
    if session is not None:
        await session.__aexit__(None, None, None)


async def browser_state(desk: Any) -> dict:
    """GET /state: page readiness, URL, title and the viewport offset."""
    if not browser:
        return {"status": "not_ready"}
    page = await get_active_page()
    return {
        "status": "ready" if page else "no_page",
        "url": browser.state.url,
        "title": browser.state.title,
        "window_offset": system.window_offset,
    }


async def browser_screenshot(desk: Any, params: dict) -> bytes:
    """GET /screenshot/browser: the viewport, the full page or one element."""
    from autolinuxdesk.responses import resize_png

    page = await get_active_page()
    if not page:
        raise ActionError("No active page")
    selector = params.get("selector")
    frames = params.getlist("frame")
    full_page = str(params.get("full_page", "false")).lower() == "true"
    if selector and full_page:
        raise ActionError("selector and full_page conflict")
    if selector:
        target = await resolve_target(
            page,
            {"selector": selector, "frames": frames},
            element_registry,
        )
        data = await target.screenshot(type="png")
    else:
        data = await page.screenshot(type="png", full_page=full_page)
    return resize_png(data, params)
