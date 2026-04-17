"""Stealth browser module using Camoufox."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse as _urlparse

try:
    from redis_sync import RedisSync
except ImportError:
    RedisSync = None  # type: ignore

# Path to JS files
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Default user data directory
DEFAULT_USER_DATA_DIR = "/userdata"

# Persisted browser properties file (stores Camoufox config, not raw fingerprint)
BROWSER_PROPS_FILE = Path(DEFAULT_USER_DATA_DIR) / "stealthy-auto-browse-props.json"


def _get_default_viewport() -> tuple[int, int]:
    """Get default viewport size from XVFB_RESOLUTION env var (WxH format)."""
    xvfb_res = os.environ.get("XVFB_RESOLUTION", "1920x1080")
    parts = xvfb_res.split("x")
    width = int(parts[0]) if parts else 1920
    height = int(parts[1]) if len(parts) > 1 else 1080
    return width, height


def _load_persisted_config() -> dict[str, Any] | None:
    """Load persisted Camoufox config from file if exists."""
    if not BROWSER_PROPS_FILE.exists():
        return None

    try:
        with open(BROWSER_PROPS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_config(config: dict[str, Any]) -> None:
    """Save Camoufox config to file for persistence."""
    BROWSER_PROPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BROWSER_PROPS_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _update_config_screen(config: dict[str, Any], width: int, height: int) -> None:
    """Update screen/window dims in config to match current resolution."""
    config["screen.width"] = width
    config["screen.height"] = height
    config["screen.availWidth"] = width
    config["screen.availHeight"] = height
    config["window.outerWidth"] = width
    config["window.outerHeight"] = height
    config["window.innerWidth"] = width
    config["window.innerHeight"] = height - 80  # Account for browser chrome
    config["window.screenX"] = 0
    config["window.screenY"] = 0
    config["screen.availLeft"] = 0
    config["screen.availTop"] = 0


def _generate_camoufox_config(screen_width: int, screen_height: int) -> dict[str, Any]:
    """Generate a Camoufox config with realistic Linux Firefox fingerprint."""
    from browserforge.fingerprints import FingerprintGenerator
    from camoufox.fingerprints import from_browserforge
    from camoufox.pkgman import installed_verstr

    # Generate without screen constraints so browserforge succeeds for any resolution.
    # We override fp.screen.* values below to match our actual Xvfb display.
    fp_gen = FingerprintGenerator(browser="firefox", os="linux")
    fp = fp_gen.generate()

    # Adjust screen/window to match our actual display
    fp.screen.width = screen_width
    fp.screen.height = screen_height
    fp.screen.availWidth = screen_width
    fp.screen.availHeight = screen_height
    fp.screen.outerWidth = screen_width
    fp.screen.outerHeight = screen_height
    fp.screen.innerWidth = screen_width
    fp.screen.innerHeight = screen_height - 80  # Account for browser chrome
    fp.screen.screenX = 0
    fp.screen.availTop = 0
    fp.screen.availLeft = 0

    # Convert to Camoufox config format
    ff_version = installed_verstr().split(".", 1)[0]
    return from_browserforge(fp, ff_version)


class BrowserError(Exception):
    """Browser error."""


@dataclass
class BrowserState:
    """Current browser state."""

    url: str = ""
    title: str = ""
    content: str = ""
    screenshot_b64: str = ""


@dataclass
class BrowserConfig:
    """Browser configuration."""

    timeout: float = 30.0


class Browser:
    """Stealth browser using Camoufox (Firefox-based, no CDP)."""

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._state = BrowserState()
        self._redis_sync = RedisSync() if RedisSync else None

    @property
    def state(self) -> BrowserState:
        """Current browser state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if browser is running."""
        return self._browser is not None

    @property
    def page(self) -> Any:
        """Current page object for direct access."""
        return self._page

    async def start(self) -> None:
        """Start browser."""
        if self.is_running:
            return

        if self._redis_sync:
            await self._redis_sync.start()
        await self._launch_browser()
        if self._redis_sync and self._context:
            await self._redis_sync.set_context(self._context)

    async def stop(self) -> None:
        """Stop browser and cleanup."""
        if self._redis_sync:
            self._redis_sync.clear_context()

        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        if self._redis_sync:
            await self._redis_sync.stop()

        self._state = BrowserState()

    async def goto(self, url: str, wait_until: str = "networkidle") -> BrowserState:
        """Navigate to URL and update state."""
        if not self.is_running:
            await self.start()

        page = await self._get_page()
        timeout_ms = int(self.config.timeout * 1000)

        await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
        await self._update_state()
        return self._state

    async def screenshot(self, full_page: bool = False, quality: int = 80) -> str:
        """Take screenshot, return base64 string."""
        if not self._page:
            return ""

        try:
            data = await self._page.screenshot(
                type="jpeg",
                quality=quality,
                full_page=full_page,
            )
            b64 = base64.b64encode(data).decode()
            self._state.screenshot_b64 = b64
            return b64
        except Exception:
            return ""

    async def refresh(self) -> BrowserState:
        """Refresh current page."""
        if not self._page:
            return self._state

        await self._page.reload()
        await self._update_state()
        return self._state

    async def back(self) -> BrowserState:
        """Go back."""
        if not self._page:
            return self._state

        await self._page.go_back()
        await self._update_state()
        return self._state

    async def forward(self) -> BrowserState:
        """Go forward."""
        if not self._page:
            return self._state

        await self._page.go_forward()
        await self._update_state()
        return self._state

    async def click(self, selector: str) -> None:
        """Click element."""
        if not self._page:
            return

        await self._page.click(selector)
        await self._update_state()

    async def fill(self, selector: str, value: str) -> None:
        """Fill input field."""
        if not self._page:
            return

        await self._page.fill(selector, value)

    async def type(self, selector: str, text: str, delay: float = 0.05) -> None:
        """Type text with delay between keystrokes."""
        if not self._page:
            return

        await self._page.type(selector, text, delay=int(delay * 1000))

    async def wait_for(self, selector: str, state: str = "visible") -> None:
        """Wait for element."""
        if not self._page:
            return

        timeout_ms = int(self.config.timeout * 1000)
        await self._page.wait_for_selector(selector, state=state, timeout=timeout_ms)

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript."""
        if not self._page:
            return None

        return await self._page.evaluate(expression)

    async def get_interactive_elements(self, visible_only: bool = True) -> list[dict]:
        """Get all interactive elements on the page.

        Returns list of element dicts with keys:
            i: index
            tag: HTML tag name
            id: element ID or None
            text: visible text content (truncated to 60 chars)
            selector: CSS selector or XPath
            x, y: center coordinates
            w, h: dimensions
            visible: whether in viewport
        """
        if not self._page:
            return []

        js_path = os.path.join(_SCRIPT_DIR, "get_elements.js")
        with open(js_path) as f:
            js_code = f.read()

        return await self._page.evaluate(js_code, visible_only)

    async def _launch_browser(self) -> None:
        """Launch Camoufox with proper C++ level fingerprint injection."""
        from browserforge.fingerprints import Screen
        from camoufox.utils import launch_options
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        # Get window size from XVFB_RESOLUTION
        width, height = _get_default_viewport()

        # Load or generate Camoufox config
        config = _load_persisted_config()
        if config is None:
            config = _generate_camoufox_config(width, height)
        else:
            # Update screen dimensions to match current XVFB_RESOLUTION
            _update_config_screen(config, width, height)
        _save_config(config)

        # Use system locale or default to en-US
        locale = os.environ.get("LANG", "en_US.UTF-8").split(".")[0].replace("_", "-")
        if locale == "C" or not locale:
            locale = "en-US"

        # Get timezone from TZ env var (set via docker -e TZ=Europe/Bucharest)
        timezone_id = os.environ.get("TZ")

        try:
            # Build launch options with proper fingerprint injection
            # This generates env vars with CAMOU_CONFIG_* for C++ level spoofing
            # Use permissive screen constraints so browserforge's internal
            # fingerprint generation doesn't fail for small Xvfb resolutions.
            # Our persisted config values take precedence via merge_into.
            screen = Screen(
                min_width=1024,
                max_width=1920,
                min_height=768,
                max_height=1080,
            )
            opts = launch_options(
                config=config,  # Pass our persisted config directly
                screen=screen,
                os="linux",
                headless=False,
                locale=locale,
                humanize=True,  # Human-like mouse movement
                i_know_what_im_doing=True,  # We're using our persisted config
            )

            # Add persistent context settings
            opts["user_data_dir"] = DEFAULT_USER_DATA_DIR

            # Handle viewport
            use_viewport = os.environ.get("USE_VIEWPORT", "").lower() == "true"
            if use_viewport:
                opts["viewport"] = {"width": width, "height": height}
            else:
                opts["no_viewport"] = True

            # Set timezone if explicitly configured
            if timezone_id and timezone_id != "UTC":
                opts["timezone_id"] = timezone_id

            # Proxy support
            proxy_url = os.environ.get("PROXY_URL", "")
            if proxy_url:
                p = _urlparse(proxy_url)
                proxy: dict[str, str] = {
                    "server": f"{p.scheme}://{p.hostname}:{p.port}"
                }
                if p.username:
                    proxy["username"] = p.username
                if p.password:
                    proxy["password"] = p.password
                opts["proxy"] = proxy

            # Accept file downloads
            opts["accept_downloads"] = True

            self._context = await self._playwright.firefox.launch_persistent_context(
                **opts
            )
            self._browser = self._context

            # Resize window to fill Xvfb screen using xdotool
            await asyncio.sleep(1)  # Wait for window to appear
            if os.environ.get("USE_OPENBOX") == "true":
                # Resizing all windows while a window manager is running breaks things
                name_filter = "Camoufox"
            else:
                name_filter = ""
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", name_filter],
                capture_output=True,
                text=True,
            )
            for wid in result.stdout.strip().split("\n"):
                if not wid:
                    continue
                subprocess.run(["xdotool", "windowmove", wid, "0", "0"])
                subprocess.run(["xdotool", "windowsize", wid, str(width), str(height)])
        except Exception as e:
            await self.stop()
            raise BrowserError(f"Failed to launch browser: {e}")

    async def _get_page(self) -> Any:
        """Get or create page."""
        if self._page:
            return self._page

        # Reuse existing page from context if available
        pages = self._context.pages
        if pages:
            self._page = pages[0]
            return self._page

        self._page = await self._context.new_page()
        return self._page

    async def _update_state(self) -> None:
        """Update state from current page."""
        if not self._page:
            return

        try:
            self._state.url = self._page.url
            self._state.title = await self._page.title()
            self._state.content = await self._page.inner_text("body")
        except Exception:
            pass

    # --- Redis-backed state sync methods ---

    async def set_cookie_synced(self, cookie: dict[str, Any]) -> None:
        """Set a cookie with Redis sync if enabled."""
        if self._redis_sync and self._context:
            await self._redis_sync.set_cookie(cookie, self._context)
        elif self._context:
            await self._context.add_cookies([cookie])

    async def get_cookies_synced(self, urls: list[str] | None = None) -> list[dict]:
        """Get cookies with Redis sync if enabled."""
        if self._redis_sync and self._context:
            return await self._redis_sync.get_cookies(self._context, urls)
        elif self._context:
            return await self._context.cookies(urls)
        return []

    async def delete_cookies_synced(self) -> None:
        """Delete all cookies with Redis sync if enabled."""
        if self._redis_sync and self._context:
            await self._redis_sync.delete_cookies(self._context)
        elif self._context:
            await self._context.clear_cookies()

    async def __aenter__(self) -> "Browser":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Async context manager exit."""
        await self.stop()
