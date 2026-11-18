"""Load stealthy-auto-browse into auto-linux-desk.

auto-linux-desk imports this module because AUTO_LINUX_DESK_PLUGINS names it,
then calls register() once before the server starts or a script runs. It
adds the browser actions and MCP tools, makes the page viewport the input
surface, and starts Camoufox on startup.
"""

from __future__ import annotations

from typing import Any

import browser_actions
import mcp_server
from autolinuxdesk import input_actions
from browser_input import BrowserSurface

CLUSTER_MODE_ERROR = (
    "Action '{action}' not available in cluster mode. "
    "Use run_script to execute multiple actions atomically."
)


def _cluster_guard(action: Any, cmd: dict) -> str | None:
    """Behind a load balancer only run_script, ping and sleep stay on one replica."""
    if browser_actions._CLUSTER_MODE and action not in browser_actions._CLUSTER_ALLOWED_ACTIONS:
        return CLUSTER_MODE_ERROR.format(action=action)
    return None


def register(registry: Any) -> None:
    browser_actions.bind(registry.desk)
    registry.set_surface(BrowserSurface(browser_actions.get_active_page))

    # ping replaces the base one: it also reports the page URL.
    for name in browser_actions.BROWSER_ACTIONS:
        registry.register_action(
            name,
            browser_actions.browser_action,
            handoff_safe=name in browser_actions.HANDOFF_SAFE_ACTIONS,
            replace=name in registry.actions,
        )

    # Humanized input on the page: element targets become coordinates, and
    # coordinates are relative to the viewport.
    page_input = {**input_actions.ACTIONS, **input_actions.SCROLL_POSITION_ACTIONS}
    for name, handler in page_input.items():
        registry.register_action(
            name,
            browser_actions.page_input_action(name, handler),
            replace=name in registry.actions,
        )
    registry.register_action("paste_text", browser_actions.paste_text, replace=True)

    registry.set_handoff_activity(browser_actions.handoff_activity)
    registry.register_request_guard(_cluster_guard)
    registry.register_state(browser_actions.browser_state)
    registry.register_screenshot("browser", browser_actions.browser_screenshot)
    registry.on_startup(browser_actions.start_browser)
    registry.on_shutdown(browser_actions.stop_browser)
    mcp_server.register(registry)
