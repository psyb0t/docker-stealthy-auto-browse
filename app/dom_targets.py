"""Resolve DOM targets consistently across browser actions."""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from typing import Any

ELEMENT_REFERENCE_CAPACITY = 512
ELEMENT_REFERENCE_TTL_SECONDS = 300.0
FRAME_DEPTH_MAX = 8
TARGET_NAME_LENGTH_MAX = 1000
TARGET_SELECTOR_LENGTH_MAX = 4096
TARGET_ROLES = frozenset(
    {
        "alert",
        "alertdialog",
        "application",
        "article",
        "banner",
        "blockquote",
        "button",
        "caption",
        "cell",
        "checkbox",
        "code",
        "columnheader",
        "combobox",
        "complementary",
        "contentinfo",
        "definition",
        "deletion",
        "dialog",
        "directory",
        "document",
        "emphasis",
        "feed",
        "figure",
        "form",
        "generic",
        "grid",
        "gridcell",
        "group",
        "heading",
        "img",
        "insertion",
        "link",
        "list",
        "listbox",
        "listitem",
        "log",
        "main",
        "marquee",
        "math",
        "menu",
        "menubar",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "meter",
        "navigation",
        "none",
        "note",
        "option",
        "paragraph",
        "presentation",
        "progressbar",
        "radio",
        "radiogroup",
        "region",
        "row",
        "rowgroup",
        "rowheader",
        "scrollbar",
        "search",
        "searchbox",
        "separator",
        "slider",
        "spinbutton",
        "status",
        "strong",
        "subscript",
        "superscript",
        "switch",
        "tab",
        "table",
        "tablist",
        "tabpanel",
        "term",
        "textbox",
        "time",
        "timer",
        "toolbar",
        "tooltip",
        "tree",
        "treegrid",
        "treeitem",
    }
)


class TargetError(Exception):
    """A DOM target was invalid, missing, ambiguous, or stale."""


def target_requested(command: dict[str, Any]) -> bool:
    return any(key in command for key in ("selector", "element_id", "role"))


def uses_plain_selector_target(command: dict[str, Any]) -> bool:
    return (
        "element_id" not in command
        and "role" not in command
        and not command.get("frames")
    )


def _frame_selectors(command: dict[str, Any]) -> list[str]:
    frames = command.get("frames", [])
    if not isinstance(frames, list) or not all(
        isinstance(item, str) and item for item in frames
    ):
        raise TargetError("frames must be a list of non-empty selectors")
    if len(frames) > FRAME_DEPTH_MAX:
        raise TargetError(f"frames supports at most {FRAME_DEPTH_MAX} levels")
    if any(len(item) > TARGET_SELECTOR_LENGTH_MAX for item in frames):
        raise TargetError("frame selector is too long")
    return frames


def validate_role(role: Any, name: Any = None) -> None:
    if role not in TARGET_ROLES:
        raise TargetError("role is not a supported ARIA role")
    if name is not None and (
        not isinstance(name, str) or len(name) > TARGET_NAME_LENGTH_MAX
    ):
        raise TargetError("name must be a string of at most 1000 characters")


class ElementRegistry:
    """Hold bounded, expiring Playwright element handles."""

    def __init__(
        self,
        capacity: int = ELEMENT_REFERENCE_CAPACITY,
        ttl_seconds: float = ELEMENT_REFERENCE_TTL_SECONDS,
    ) -> None:
        if capacity < 1 or ttl_seconds <= 0:
            raise ValueError("invalid element registry bounds")
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, Any, Any]] = OrderedDict()

    async def add(self, page: Any, handle: Any) -> str:
        await self._expire()
        element_id = secrets.token_urlsafe(18)
        self._entries[element_id] = (time.monotonic(), page, handle)
        while len(self._entries) > self._capacity:
            _, (_, _, evicted) = self._entries.popitem(last=False)
            await _dispose(evicted)
        return element_id

    async def get(self, page: Any, element_id: str) -> Any:
        if not isinstance(element_id, str) or not element_id:
            raise TargetError("element_id must be a non-empty string")
        await self._expire()
        entry = self._entries.get(element_id)
        if entry is None or entry[1] is not page:
            raise TargetError("element reference is stale or unknown")
        try:
            attached = await entry[2].evaluate("el => el.isConnected")
        except Exception as error:
            await self.remove(element_id)
            raise TargetError("element reference is stale or detached") from error
        if not attached:
            await self.remove(element_id)
            raise TargetError("element reference is stale or detached")
        self._entries.move_to_end(element_id)
        return entry[2]

    async def remove(self, element_id: str) -> None:
        entry = self._entries.pop(element_id, None)
        if entry is not None:
            await _dispose(entry[2])

    async def clear(self, page: Any | None = None) -> None:
        for element_id, (_, entry_page, handle) in list(self._entries.items()):
            if page is None or entry_page is page:
                self._entries.pop(element_id, None)
                await _dispose(handle)

    async def _expire(self) -> None:
        cutoff = time.monotonic() - self._ttl_seconds
        for element_id, (created, _, handle) in list(self._entries.items()):
            if created > cutoff:
                continue
            self._entries.pop(element_id, None)
            await _dispose(handle)


async def _dispose(handle: Any) -> None:
    try:
        await handle.dispose()
    except Exception:
        return


async def resolve_target(
    page: Any,
    command: dict[str, Any],
    registry: ElementRegistry,
    *,
    require: bool = True,
) -> Any:
    """Return a strict locator or a stored element handle."""
    selectors = [
        name for name in ("selector", "element_id", "role") if command.get(name)
    ]
    if not selectors:
        if require:
            raise TargetError("one of selector, element_id, or role is required")
        return None
    if len(selectors) != 1:
        raise TargetError("provide exactly one of selector, element_id, or role")
    frames = _frame_selectors(command)
    if selectors[0] == "element_id":
        if frames:
            raise TargetError("frames cannot be used with element_id")
        return await registry.get(page, command["element_id"])

    scope: Any = page
    for frame_selector in frames:
        scope = scope.frame_locator(frame_selector)
    if selectors[0] == "selector":
        selector = command["selector"]
        if not isinstance(selector, str) or not selector:
            raise TargetError("selector must be a non-empty string")
        if len(selector) > TARGET_SELECTOR_LENGTH_MAX:
            raise TargetError("selector is too long")
        locator = scope.locator(selector)
    else:
        role = command["role"]
        name = command.get("name")
        validate_role(role, name)
        locator = scope.get_by_role(role, name=name)
    count = await locator.count()
    if count != 1:
        raise TargetError(f"target matched {count} elements; expected exactly 1")
    return locator


async def create_reference(
    page: Any,
    command: dict[str, Any],
    registry: ElementRegistry,
) -> dict[str, Any]:
    target = await resolve_target(page, command, registry)
    handle = target if command.get("element_id") else await target.element_handle()
    if handle is None:
        raise TargetError("target is not attached")
    element_id = await registry.add(page, handle)
    details = await handle.evaluate(
        """el => ({
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            text: (el.innerText || el.textContent || '').trim().slice(0, 500),
            connected: el.isConnected
        })"""
    )
    return {"element_id": element_id, **details}
