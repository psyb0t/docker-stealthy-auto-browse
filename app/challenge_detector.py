"""Read-only detection of documented browser challenge integrations."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from logger import get_logger

logger = get_logger(__name__)

_MAX_MATCHES = 20
_MAX_EVIDENCE = 8
_MAX_FRAMES = 8
_MAX_ELEMENTS = 8
_MAX_SNAPSHOT_ITEMS = 100
_MAX_RESOURCE_PATH_LENGTH = 256
_REDACTED_RESOURCE_PATH = "/redacted"
_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_KNOWN_ELEMENT_VENDORS = {
    "altcha": ("altcha", "high", "widget-container"),
    "friendlycaptcha": ("friendlycaptcha", "high", "widget-container"),
    "hcaptcha": ("hcaptcha", "high", "widget-container"),
    "recaptcha": ("recaptcha", "high", "widget-container"),
    "turnstile": ("turnstile", "high", "widget-container"),
}
_KNOWN_GLOBAL_VENDORS = {
    "aws_waf": ("aws_waf", "high", "published-api"),
    "geetest": ("geetest", "medium", "published-api"),
}
_GENERIC_ELEMENT_MARKER = "generic-challenge"

_SNAPSHOT_SCRIPT = r"""() => {
  const maxItems = 100;
  const maxTextLength = 256;
  const sanitiseResource = (value) => {
    if (!value) return null;
    try {
      const parsed = new URL(value, document.baseURI);
      return { host: parsed.hostname.toLowerCase(), path: parsed.pathname.slice(0, maxTextLength) };
    } catch (_) {
      return null;
    }
  };
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return Boolean(rect.width && rect.height && style.display !== "none" && style.visibility !== "hidden");
  };
  const box = (node) => {
    const rect = node.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  };
  const elements = [];
  const addElements = (selector, marker, requiresVisible) => {
    for (const node of document.querySelectorAll(selector)) {
      if (elements.length >= maxItems) break;
      if (requiresVisible && !visible(node)) continue;
      elements.push({ marker, visible: visible(node), bounding_box: box(node) });
    }
  };
  addElements(".cf-turnstile", "turnstile", false);
  addElements(".g-recaptcha, [name='g-recaptcha-response']", "recaptcha", false);
  addElements(".h-captcha, [name='h-captcha-response']", "hcaptcha", false);
  addElements(".frc-captcha", "friendlycaptcha", false);
  addElements("altcha-widget", "altcha", false);
  addElements("iframe[title*='captcha' i], iframe[title*='challenge' i]", "generic-challenge", true);
  addElements("[role='dialog'][aria-label*='captcha' i], [role='dialog'][aria-label*='verification' i]", "generic-challenge", true);
  addElements("[data-captcha], [data-challenge]", "generic-challenge", true);

  return {
    iframes: Array.from(document.querySelectorAll("iframe")).slice(0, maxItems).map((node) => ({
      resource: sanitiseResource(node.getAttribute("src")),
      bounding_box: box(node),
      visible: visible(node),
    })),
    scripts: Array.from(document.scripts).slice(0, maxItems).map((node) => ({
      resource: sanitiseResource(node.getAttribute("src")),
    })),
    elements,
    globals: {
      aws_waf: Object.prototype.hasOwnProperty.call(window, "AwsWafCaptcha"),
      geetest: Object.prototype.hasOwnProperty.call(window, "initGeetest4"),
    },
  };
}"""

_SCROLL_INTO_VIEW_SCRIPT = r"""(target) => {
  if (!target || typeof target !== "object") return false;
  const { x, y, width, height } = target;
  if (
    ![x, y, width, height].every(Number.isFinite) ||
    width <= 0 ||
    height <= 0
  ) return false;

  const isInViewport = (top) => (
    x < window.innerWidth &&
    x + width > 0 &&
    top < window.innerHeight &&
    top + height > 0
  );
  if (isInViewport(y)) return true;

  const documentY = window.scrollY + y;
  const maximumScrollY = Math.max(
    0,
    document.documentElement.scrollHeight - window.innerHeight,
  );
  const desiredScrollY = Math.max(
    0,
    Math.min(maximumScrollY, documentY - (window.innerHeight - height) / 2),
  );
  window.scrollTo(0, desiredScrollY);
  return isInViewport(documentY - window.scrollY);
}"""


def _bounded_strings(values: Iterable[str], limit: int) -> list[str]:
    output: list[str] = []
    for value in values:
        if not isinstance(value, str) or value in output:
            continue
        output.append(value)
        if len(output) == limit:
            break
    return output


def _sanitise_resource(resource: Any) -> dict[str, str] | None:
    if not isinstance(resource, dict):
        return None
    host = resource.get("host")
    path = resource.get("path")
    if not isinstance(host, str) or not isinstance(path, str):
        return None
    parsed = urlsplit(f"https://{host}{path}")
    if not parsed.hostname or parsed.username or parsed.password:
        return None
    sanitised_host = parsed.hostname.lower()
    sanitised_path = parsed.path[:_MAX_RESOURCE_PATH_LENGTH]
    if sanitised_host == "iframe.arkoselabs.com" or sanitised_host.endswith("-api.arkoselabs.com"):
        sanitised_path = _REDACTED_RESOURCE_PATH
    return {"host": sanitised_host, "path": sanitised_path}


def _sanitise_bounding_box(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    box: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        coordinate = value.get(key)
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            return None
        if not math.isfinite(coordinate):
            return None
        box[key] = round(float(coordinate), 2)
    return box


def _vendor_for_resource(resource: dict[str, str]) -> tuple[str, str, str] | None:
    host = resource["host"]
    path = resource["path"]
    if host == "challenges.cloudflare.com" and (
        path.startswith("/turnstile/") or path.startswith("/cdn-cgi/challenge-platform/")
    ):
        return ("turnstile", "high", "documented-host-path")
    if host in {"www.google.com", "www.recaptcha.net"} and path.startswith("/recaptcha/"):
        return ("recaptcha", "high", "documented-host-path")
    if host == "js.hcaptcha.com" and path.startswith("/1/api.js"):
        return ("hcaptcha", "high", "documented-host-path")
    if host == "iframe.arkoselabs.com" or host.endswith("-api.arkoselabs.com"):
        return ("arkose", "high", "documented-host-path")
    if "@friendlycaptcha/" in path:
        return ("friendlycaptcha", "medium", "documented-script-path")
    return None


def _first_visible_scroll_target(snapshot: Any) -> dict[str, float] | None:
    """Return the first visible detected frame or widget with geometry."""
    if not isinstance(snapshot, dict):
        return None

    iframes = snapshot.get("iframes")
    if isinstance(iframes, list):
        for candidate in iframes[:_MAX_SNAPSHOT_ITEMS]:
            if (
                not isinstance(candidate, dict)
                or candidate.get("visible") is not True
            ):
                continue
            resource = _sanitise_resource(candidate.get("resource"))
            if not resource or not _vendor_for_resource(resource):
                continue
            bounding_box = _sanitise_bounding_box(
                candidate.get("bounding_box")
            )
            if bounding_box:
                return bounding_box

    elements = snapshot.get("elements")
    if not isinstance(elements, list):
        return None
    for candidate in elements[:_MAX_SNAPSHOT_ITEMS]:
        if (
            not isinstance(candidate, dict)
            or candidate.get("visible") is not True
        ):
            continue
        marker = candidate.get("marker")
        if marker not in _KNOWN_ELEMENT_VENDORS and marker != _GENERIC_ELEMENT_MARKER:
            continue
        bounding_box = _sanitise_bounding_box(candidate.get("bounding_box"))
        if bounding_box:
            return bounding_box
    return None


async def _scroll_into_view(page: Any, snapshot: Any) -> bool:
    target = _first_visible_scroll_target(snapshot)
    if not target:
        return False
    try:
        return bool(await page.evaluate(_SCROLL_INTO_VIEW_SCRIPT, target))
    except Exception as error:
        logger.warning(
            "challenge scroll failed",
            extra={
                "reason": "page_evaluate_failed",
                "error_type": type(error).__name__,
            },
        )
        return False


def _add_match(
    matches: dict[str, dict[str, Any]],
    vendor: str,
    confidence: str,
    location: str,
    evidence: str,
    resource: dict[str, str] | None = None,
    bounding_box: dict[str, float] | None = None,
) -> None:
    match = matches.setdefault(
        vendor,
        {
            "vendor": vendor,
            "confidence": confidence,
            "locations": [],
            "evidence": [],
            "frames": [],
            "elements": [],
        },
    )
    if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[match["confidence"]]:
        match["confidence"] = confidence
    match["locations"] = _bounded_strings([*match["locations"], location], _MAX_EVIDENCE)
    match["evidence"] = _bounded_strings([*match["evidence"], evidence], _MAX_EVIDENCE)
    if location == "iframe" and resource and len(match["frames"]) < _MAX_FRAMES:
        frame = {**resource}
        if bounding_box:
            frame["bounding_box"] = bounding_box
        if frame not in match["frames"]:
            match["frames"].append(frame)
    if location == "element" and bounding_box and len(match["elements"]) < _MAX_ELEMENTS:
        element = {"bounding_box": bounding_box}
        if element not in match["elements"]:
            match["elements"].append(element)


def classify_challenge_snapshot(snapshot: Any) -> dict[str, Any]:
    """Classify a pre-sanitised page snapshot without returning page-controlled text."""
    if not isinstance(snapshot, dict):
        return {"detected": False, "status": "absent", "matches": []}

    matches: dict[str, dict[str, Any]] = {}
    for location_key, location in (("iframes", "iframe"), ("scripts", "script")):
        candidates = snapshot.get(location_key)
        if not isinstance(candidates, list):
            continue
        for candidate in candidates[:_MAX_SNAPSHOT_ITEMS]:
            if not isinstance(candidate, dict):
                continue
            resource = _sanitise_resource(candidate.get("resource"))
            if not resource:
                continue
            vendor = _vendor_for_resource(resource)
            if not vendor:
                continue
            name, confidence, evidence = vendor
            bounding_box = _sanitise_bounding_box(candidate.get("bounding_box"))
            _add_match(matches, name, confidence, location, evidence, resource, bounding_box)

    elements = snapshot.get("elements")
    if isinstance(elements, list):
        for candidate in elements[:_MAX_SNAPSHOT_ITEMS]:
            if not isinstance(candidate, dict):
                continue
            marker = candidate.get("marker")
            if marker == _GENERIC_ELEMENT_MARKER and not candidate.get("visible"):
                continue
            vendor = _KNOWN_ELEMENT_VENDORS.get(marker)
            if vendor:
                name, confidence, evidence = vendor
            elif marker == _GENERIC_ELEMENT_MARKER:
                name, confidence, evidence = ("unknown", "low", "generic-visible-marker")
            else:
                continue
            _add_match(
                matches,
                name,
                confidence,
                "element",
                evidence,
                bounding_box=_sanitise_bounding_box(candidate.get("bounding_box")),
            )

    globals_snapshot = snapshot.get("globals")
    if isinstance(globals_snapshot, dict):
        for marker, vendor in _KNOWN_GLOBAL_VENDORS.items():
            if globals_snapshot.get(marker) is not True:
                continue
            name, confidence, evidence = vendor
            _add_match(matches, name, confidence, "page", evidence)

    output = list(matches.values())[:_MAX_MATCHES]
    for match in output:
        if not match["frames"]:
            del match["frames"]
        if not match["elements"]:
            del match["elements"]
    return {
        "detected": bool(output),
        "status": "present" if output else "absent",
        "matches": output,
    }


async def detect_challenges(
    page: Any,
    *,
    scroll_into_view: bool = False,
) -> dict[str, Any]:
    """Return a bounded challenge summary and optionally reveal its target."""
    try:
        snapshot = await page.evaluate(_SNAPSHOT_SCRIPT)
    except Exception as error:
        logger.warning(
            "challenge snapshot failed",
            extra={
                "reason": "page_evaluate_failed",
                "error_type": type(error).__name__,
            },
        )
        result = {"detected": False, "status": "unknown", "matches": []}
        if scroll_into_view:
            result["scrolled_into_view"] = False
        return result

    result = classify_challenge_snapshot(snapshot)
    if scroll_into_view:
        result["scrolled_into_view"] = await _scroll_into_view(page, snapshot)
    return result
