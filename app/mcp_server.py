"""MCP server for browser automation via Streamable HTTP transport.

Exposes browser actions as MCP tools so AI agents can drive the browser
over the Model Context Protocol. Mounted at /mcp on the main HTTP server.

When NUM_REPLICAS > 1 (cluster mode), only run_script is exposed to guarantee
all steps execute on the same browser instance behind the load balancer.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable, Coroutine

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from logger import get_logger
from mcp.types import TextContent

log = get_logger(__name__)

_num_replicas = int(os.environ.get("NUM_REPLICAS", "1"))
_cluster_mode = _num_replicas > 1

_INSTRUCTIONS_SINGLE = (
    "Stealth browser automation in Docker. Camoufox (custom Firefox) with "
    "zero Chrome DevTools Protocol exposure and real OS-level mouse/keyboard "
    "input via PyAutoGUI — undetectable by bot detection. "
    "Passes Cloudflare, CreepJS, BrowserScan, Pixelscan, and all major bot detectors. "
    "READING PAGE CONTENT: always use get_text() or get_html() first — they are fast and "
    "token-efficient. Only take a screenshot() when the page content cannot be understood "
    "from text alone (e.g. visual layout, images, canvas). "
    "SCREENSHOTS: always pass whLargest=512 unless fine detail is required — "
    "full-resolution screenshots waste tokens and provide no extra information for most tasks. "
    "CLICKING: always use click() with a CSS selector first — it is fast and reliable. "
    "Only use system_click() as a last resort when the site detects DOM event injection, "
    "and only after calling calibrate() to ensure correct coordinate mapping. "
    "Use get_interactive_elements to find selectors and coordinates. "
    "Use run_script to execute multi-step workflows atomically."
)

_INSTRUCTIONS_CLUSTER = (
    "Stealth browser automation cluster. Multiple browser replicas behind a load balancer. "
    "IMPORTANT: only run_script is available — all steps execute atomically on ONE browser "
    "instance. This guarantees state consistency (navigation, cookies, page content) across "
    "steps. Build your workflow as a list of steps in a single run_script call. "
    "See the run_script tool documentation for all available actions and their parameters."
)

mcp = FastMCP(
    "stealthy-auto-browse",
    instructions=_INSTRUCTIONS_CLUSTER if _cluster_mode else _INSTRUCTIONS_SINGLE,
)

_dispatch: Callable[[dict], Coroutine[Any, Any, dict]] | None = None
_lock: asyncio.Lock | None = None


def set_dispatcher(
    fn: Callable[[dict], Coroutine[Any, Any, dict]],
    lock: asyncio.Lock | None = None,
) -> None:
    """Register the dispatch_action function and request lock from main.py."""
    global _dispatch, _lock
    _dispatch = fn
    _lock = lock


async def _call(action: str, **params: Any) -> dict:
    """Call dispatch_action with an action and params."""
    if not _dispatch:
        return {"success": False, "error": "Browser not ready"}
    filtered = {k: v for k, v in params.items() if v is not None}
    log.info(">> %s %s", action, filtered if filtered else "")
    cmd: dict[str, Any] = {"action": action}
    cmd.update(filtered)
    if _lock:
        async with _lock:
            result = await _dispatch(cmd)
    else:
        result = await _dispatch(cmd)
    if result.get("success", False):
        log.info("<< %s OK", action)
    else:
        log.warning("<< %s FAIL: %s", action, result.get("error", "?"))
    return result


def _text_result(result: dict) -> str:
    """Convert dispatch_action result to text for MCP response."""
    r = {k: v for k, v in result.items() if k != "_binary"}
    return json.dumps(r, default=str)


# =========================================================================
# run_script — always registered (the ONLY tool in cluster mode)
# =========================================================================


@mcp.tool
async def run_script(
    steps: list[dict],
    name: str = "mcp_script",
    on_error: str = "stop",
) -> str:
    """Run multiple browser actions as a single atomic script.

    All steps execute sequentially on the SAME browser instance under a single
    request lock. This is the only safe way to do multi-step workflows in a
    load-balanced cluster.

    Each step is a dict with "action" and its parameters. Add "output_id" to
    any action step to collect its result in the response outputs dict. Steps
    can also use control nodes: {"if": {"condition": ..., "then": [...]}}
    selects a branch; {"repeat": {"count": 1, "steps": [...]}} repeats a
    bounded body; and {"while": {"condition": ..., "max_iterations": 1,
    "steps": [...]}} runs only within its explicit bound. Conditions support
    element, text, URL-glob, JavaScript-boolean, and prior-output checks.

    steps example: [{"action": "goto", "url": "https://example.com",
    "wait_until": "networkidle"}, {"action": "get_text", "output_id":
    "page_text"}, {"action": "save_screenshot", "output_id": "shot",
    "whLargest": 512}]

    on_error: "stop" (default) halts on first failure, "continue" keeps going.

    AVAILABLE ACTIONS AND PARAMETERS:

    NAVIGATION:
        goto: Navigate to a URL.
            - url (str, required): Target URL.
            - wait_until (str): "domcontentloaded" (default), "load", "networkidle".
            - referer (str): Optional Referer header value.
        refresh: Reload current page.
            - wait_until (str): "domcontentloaded" (default), "load", "networkidle".

    PAGE CONTENT:
        get_text: Get visible text from current page (truncated to 10,000 chars).
            No parameters.
        get_html: Get full HTML source of the current page.
            No parameters.
        get_page_info: Get current URL, title, ready state, viewport, document, and scroll data.
            No parameters.
        get_element: Get one element's tag, text, attributes, bounding box, and visibility.
            - selector (str, required): CSS selector or XPath.
        get_elements: Get a bounded list of matching element summaries.
            - selector (str, required): CSS selector or XPath.
            - limit (int): 1-100, default 20.
        get_computed_style: Get selected computed CSS properties for one element.
            - selector (str, required): CSS selector or XPath.
            - properties (list[str]): Up to 50 CSS property names. Defaults to display,
              visibility, color, and background-color.
        get_virtual_media_state: Report whether configured virtual camera and microphone
            sources are active for getUserMedia(), plus dynamic-source state.
            No parameters.
        set_virtual_media_source: In opt-in dynamic-media mode, select an existing
            contained file for future and already-acquired virtual tracks.
            - kind (str, required): "camera" or "microphone".
            - source (str, required): Safe relative file path below VIRTUAL_MEDIA_DIR.
        upload_virtual_media: In opt-in dynamic-media mode, store a bounded base64
            audio or video file under VIRTUAL_MEDIA_DIR and optionally activate it.
            - kind (str, required): "camera" or "microphone".
            - filename (str, required): Safe basename for the stored file.
            - content_base64 (str, required): Strict base64 media bytes.
            - activate (bool): Activate the uploaded source. Default false.
        get_interactive_elements: Find all interactive elements (buttons, links, inputs).
            - visible_only (bool): Only viewport-visible elements. Default true.
            Returns: list of elements with x, y, width, height, text, selector.
        eval: Execute JavaScript and return result.
            - expression (str, required): JS expression to evaluate.

    CLICKING (prefer click over system_click):
        click: Click element by CSS selector or XPath. Fast and reliable.
            - selector (str, required): CSS selector or "xpath=..." expression.
        system_click: Click at viewport coordinates using OS-level mouse. Undetectable
            but requires calibrate first.
            - x (int, required): Viewport X coordinate.
            - y (int, required): Viewport Y coordinate.
            - duration (float): Mouse movement time in seconds.
        mouse_click: Click at absolute screen coordinates (or current position).
            - x (int): Screen X coordinate.
            - y (int): Screen Y coordinate.

    TEXT INPUT:
        fill: Set input field value instantly (clears first, no keystrokes).
            - selector (str, required): CSS selector of input element.
            - value (str, required): Value to set.
        type: Type into element with per-key delay (generates keystroke events).
            - selector (str, required): CSS selector of input element.
            - text (str, required): Text to type.
            - delay (float): Delay between keys in seconds. Default 0.05.
        system_type: Type with real OS-level keystrokes (undetectable).
            - text (str, required): Text to type.
            - interval (float): Average delay between keys. Default 0.08.
        send_key: Send keyboard key or combo.
            - key (str, required): Key name or combo, e.g. "enter", "ctrl+a",
              "ctrl+shift+t", "tab", "escape", "backspace".

    MOUSE:
        mouse_move: Move mouse to viewport coordinates (human-like movement).
            - x (int, required): Viewport X coordinate.
            - y (int, required): Viewport Y coordinate.
            - duration (float): Movement time in seconds.
        scroll: Scroll using mouse wheel.
            - amount (int): Scroll amount. Negative=down, positive=up. Default -3.
            - x (int): X coordinate to move to before scrolling.
            - y (int): Y coordinate to move to before scrolling.
        scroll_to_bottom: Scroll to page bottom programmatically.
            - delay (float): Delay between scroll steps in seconds. Default 0.4.
        scroll_to_bottom_humanized: Scroll to bottom with randomized mouse wheel.
            - min_clicks (int): Min wheel clicks per step. Default 2.
            - max_clicks (int): Max wheel clicks per step. Default 6.
            - delay (float): Base delay between steps. Default 0.5.

    SCREENSHOTS:
        save_screenshot: Capture screenshot (prefer get_text/get_html instead).
            - type (str): "browser" (default) or "desktop".
            - width (int): Resize to width (keeps aspect ratio).
            - height (int): Resize to height (keeps aspect ratio).
            - whLargest (int): Resize largest dimension to this. Use 512 for LLMs.
            - path (str): Optional file path to save PNG to disk.

    WAIT CONDITIONS:
        wait_for_element: Wait for element to reach a state.
            - selector (str, required): CSS selector or XPath.
            - state (str): "visible" (default), "hidden", "attached", "detached".
            - timeout (float): Max wait in seconds. Default 30.
        wait_for_text: Wait for text to appear on page.
            - text (str, required): Substring to wait for.
            - timeout (float): Max wait in seconds. Default 30.
        wait_for_url: Wait for URL to match pattern.
            - url (str, required): URL pattern to match.
            - timeout (float): Max wait in seconds. Default 30.
        wait_for_network_idle: Wait for no network activity.
            - timeout (float): Max wait in seconds. Default 30.
        sleep: Pause execution.
            - duration (float): Seconds to sleep. Default 1.

    TABS:
        list_tabs: List all open tabs with index, URL, and active status.
        new_tab: Open a new tab.
            - url (str): Optional URL to navigate to.
            - wait_until (str): Navigation wait condition.
        switch_tab: Switch to a tab by index.
            - index (int, required): Tab index.
        close_tab: Close a tab.
            - index (int): Tab index. Closes active tab if omitted.

    COOKIES:
        get_cookies: Get all cookies (or for specific URLs).
            - urls (list[str]): Optional list of URLs to filter by.
        set_cookie: Set a cookie.
            - name (str, required): Cookie name.
            - value (str, required): Cookie value.
            - domain (str): Cookie domain.
            - path (str): Cookie path.
            - expires (float): Expiry timestamp.
            - httpOnly (bool): HTTP-only flag.
            - secure (bool): Secure flag.
            - sameSite (str): "Strict", "Lax", or "None".
        delete_cookies: Delete all cookies.

    STORAGE:
        get_storage: Get localStorage or sessionStorage contents.
            - type (str): "local" (default) or "session".
        set_storage: Set a storage item.
            - type (str): "local" (default) or "session".
            - key (str, required): Item key.
            - value (str, required): Item value.
        clear_storage: Clear all storage items.
            - type (str): "local" (default) or "session".

    DIALOGS:
        handle_dialog: Configure how to handle the next browser dialog.
            - accept (bool): Accept (true, default) or dismiss (false).
            - text (str): Text to enter for prompt dialogs.
        get_last_dialog: Get info about the last dialog that appeared.

    FILE UPLOAD:
        upload_file: Upload a file to a file input element.
            - selector (str, required): CSS selector of file input.
            - file_path (str, required): Path to file on disk.

    DOWNLOADS:
        get_last_download: Get info about the last downloaded file.

    NETWORK LOGGING:
        enable_network_log: Start recording network requests/responses.
        disable_network_log: Stop recording.
        get_network_log: Get recorded network log entries.
        clear_network_log: Clear the network log.
        getclear_network_log: Get and clear in one call.

    CONSOLE LOGGING:
        enable_console_log: Start recording console messages.
        disable_console_log: Stop recording.
        get_console_log: Get recorded console messages.
        clear_console_log: Clear the console log.
        getclear_console_log: Get and clear in one call.

    DISPLAY:
        calibrate: Detect browser window offset for system_click coordinates.
        get_resolution: Get current display resolution.
        enter_fullscreen: Enter browser fullscreen mode.
        exit_fullscreen: Exit browser fullscreen mode.

    RECORDING (ffmpeg x11grab — captures Xvfb to /recordings/<slug>.mp4 with mouse cursor):
        start_recording: Begin a recording. Requires /recordings volume mount.
            One active recording per container; second start while active errors.
            - mode (str): "window" (default, full Camoufox window incl. chrome),
              "viewport" (crops chrome using calibrated window_offset),
              "desktop" (entire Xvfb screen).
            - fps (int): 1-60, default 15.
            - show_cursor (bool): True (default) draws the OS-level mouse cursor
              in the recording. False omits it for pure page-pixel captures.
        stop_recording: Stop active recording and rename tmp file to <slug>.mp4.
            - slug (str, required): [a-zA-Z0-9][a-zA-Z0-9_-]{0,62}. Collisions
              auto-rename to <slug>-2, <slug>-3, etc.
        recording_status: Check whether a recording is active and its elapsed time.

    UTILITY:
        ping: Health check. Returns current URL.
    """
    return _text_result(
        await _call("run_script", steps=steps, name=name, on_error=on_error)
    )


# =========================================================================
# Individual tools — only registered in single-instance mode
# =========================================================================

if not _cluster_mode:

    @mcp.tool
    async def goto(
        url: str,
        wait_until: str = "domcontentloaded",
        referer: str | None = None,
    ) -> str:
        """Navigate the browser to a URL.

        Args:
            url: The URL to navigate to.
            wait_until: When to consider navigation done.
                "domcontentloaded" (default), "load", or "networkidle".
            referer: Optional HTTP Referer header value.
        """
        return _text_result(
            await _call("goto", url=url, wait_until=wait_until, referer=referer)
        )

    @mcp.tool
    async def get_text() -> str:
        """Get all visible text content from the current page (truncated to 10,000 chars)."""
        return _text_result(await _call("get_text"))

    @mcp.tool
    async def get_html() -> str:
        """Get the full HTML source of the current page."""
        return _text_result(await _call("get_html"))

    @mcp.tool
    async def get_interactive_elements(visible_only: bool = True) -> str:
        """Find all interactive elements on the page (buttons, links, inputs, etc.).

        Returns each element's viewport coordinates (x, y), dimensions,
        text content, and CSS selector. Use the coordinates with system_click.

        Args:
            visible_only: Only return elements visible in the viewport.
        """
        return _text_result(
            await _call("get_interactive_elements", visible_only=visible_only)
        )

    @mcp.tool
    async def screenshot(
        screenshot_type: str = "browser",
        width: int | None = None,
        height: int | None = None,
        whLargest: int | None = None,
    ) -> ToolResult:
        """Take a screenshot of the browser viewport or full desktop.

        LAST RESORT — prefer get_text() or get_html() instead. Screenshots cost
        significantly more tokens and are only needed when visual layout or images
        matter. When you do take a screenshot, always use whLargest=512 unless you
        specifically need fine detail — full resolution is wasteful.

        Args:
            screenshot_type: "browser" for page viewport, "desktop" for full virtual screen.
            width: Resize to this width (keeps aspect ratio if height omitted).
            height: Resize to this height (keeps aspect ratio if width omitted).
            whLargest: Resize so the largest dimension is this many pixels. Use 512 by default.
        """
        result = await _call(
            "save_screenshot",
            type=screenshot_type,
            width=width,
            height=height,
            whLargest=whLargest,
        )
        binary = result.pop("_binary", None)
        if binary:
            img = Image(data=binary, format="png")
            return ToolResult(content=[img])
        return ToolResult(content=[TextContent(type="text", text=_text_result(result))])

    @mcp.tool
    async def system_click(x: int, y: int, duration: float | None = None) -> str:
        """Click at viewport coordinates using real OS-level mouse movement.

        PREFER click() with a CSS selector instead — it is faster and more reliable.
        Only use system_click when: (1) the site detects DOM event injection and
        blocks it, or (2) you have already called calibrate and confirmed the window
        offset is correct. Without calibration the coordinates will be wrong and
        the click will land in the wrong place.

        Args:
            x: Viewport X coordinate (from get_interactive_elements).
            y: Viewport Y coordinate (from get_interactive_elements).
            duration: Mouse movement time in seconds (random 0.2-0.6 if omitted).
        """
        return _text_result(await _call("system_click", x=x, y=y, duration=duration))

    @mcp.tool
    async def system_type(text: str, interval: float = 0.08) -> str:
        """Type text with real OS-level keystrokes (undetectable).

        Each keystroke has a randomized delay to mimic human typing.
        You must focus an input field first (e.g. with system_click).

        Args:
            text: The text to type.
            interval: Average delay between keystrokes in seconds.
        """
        return _text_result(await _call("system_type", text=text, interval=interval))

    @mcp.tool
    async def send_key(key: str) -> str:
        """Send a keyboard key or combo.

        Examples: "enter", "tab", "escape", "backspace", "ctrl+a", "ctrl+shift+t".

        Args:
            key: Key name or combo using PyAutoGUI key names.
        """
        return _text_result(await _call("send_key", key=key))

    @mcp.tool
    async def mouse_move(x: int, y: int, duration: float | None = None) -> str:
        """Move the mouse to viewport coordinates with human-like movement (no click).

        Use to hover over elements (trigger dropdowns, tooltips).

        Args:
            x: Viewport X coordinate.
            y: Viewport Y coordinate.
            duration: Movement time in seconds.
        """
        return _text_result(await _call("mouse_move", x=x, y=y, duration=duration))

    @mcp.tool
    async def scroll(
        amount: int = -3, x: int | None = None, y: int | None = None
    ) -> str:
        """Scroll using the mouse wheel.

        Args:
            amount: Scroll amount. Negative = scroll down, positive = scroll up.
            x: Optional X coordinate to move mouse to before scrolling.
            y: Optional Y coordinate to move mouse to before scrolling.
        """
        return _text_result(await _call("scroll", amount=amount, x=x, y=y))

    @mcp.tool
    async def click(selector: str) -> str:
        """Click an element by CSS selector or XPath. PREFER THIS over system_click.

        Reliable, fast, and works for the vast majority of sites. Only fall back to
        system_click if the site explicitly detects and blocks DOM event injection.
        XPath example: "xpath=//button[@id='submit']".

        Args:
            selector: CSS selector or XPath (prefix with "xpath=").
        """
        return _text_result(await _call("click", selector=selector))

    @mcp.tool
    async def fill(selector: str, value: str) -> str:
        """Set an input field's value instantly by CSS selector.

        Clears existing content first. Does not generate individual keystrokes.

        Args:
            selector: CSS selector of the input element.
            value: The value to set.
        """
        return _text_result(await _call("fill", selector=selector, value=value))

    @mcp.tool
    async def eval_js(expression: str) -> str:
        """Execute JavaScript in the page context and return the result.

        Examples: "document.title", "document.querySelectorAll('a').length".

        Args:
            expression: JavaScript expression to evaluate.
        """
        return _text_result(await _call("eval", expression=expression))

    @mcp.tool
    async def wait_for_element(
        selector: str, state: str = "visible", timeout: float = 30
    ) -> str:
        """Wait for an element to reach a state.

        Args:
            selector: CSS selector or XPath.
            state: "visible" (default), "hidden", "attached", or "detached".
            timeout: Max wait time in seconds.
        """
        return _text_result(
            await _call(
                "wait_for_element", selector=selector, state=state, timeout=timeout
            )
        )

    @mcp.tool
    async def wait_for_text(text: str, timeout: float = 30) -> str:
        """Wait for specific text to appear anywhere on the page.

        Args:
            text: Substring to wait for.
            timeout: Max wait time in seconds.
        """
        return _text_result(await _call("wait_for_text", text=text, timeout=timeout))

    @mcp.tool
    async def browser_action(action: str, params: dict | None = None) -> str:
        """Execute any browser action not covered by the other tools.

        Useful for: cookies (get_cookies, set_cookie, delete_cookies),
        tabs (list_tabs, new_tab, switch_tab, close_tab),
        storage (get_storage, set_storage, clear_storage),
        dialogs (handle_dialog, get_last_dialog),
        downloads (get_last_download, upload_file),
        virtual media (get_virtual_media_state, set_virtual_media_source,
        upload_virtual_media),
        network logging (enable_network_log, get_network_log, etc.),
        console logging (enable_console_log, get_console_log, etc.),
        display (calibrate, get_resolution, enter_fullscreen, exit_fullscreen),
        recording (start_recording, stop_recording, recording_status),
        scrolling (scroll_to_bottom, scroll_to_bottom_humanized),
        navigation (refresh), wait (wait_for_url, wait_for_network_idle),
        utility (ping, sleep).

        Args:
            action: The action name (e.g. "get_cookies", "set_cookie").
            params: Optional dict of action parameters.
        """
        p = params or {}
        return _text_result(await _call(action, **p))
