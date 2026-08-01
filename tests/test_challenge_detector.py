"""Dependency-free unit tests for the read-only challenge detector."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

sys.path.insert(0, "/app")

from challenge_detector import classify_challenge_snapshot, detect_challenges

ABSENT_RESULT = {"detected": False, "status": "absent", "matches": []}
UNKNOWN_RESULT = {"detected": False, "status": "unknown", "matches": []}
DOCUMENTED_VENDORS = {
    "altcha",
    "arkose",
    "aws_waf",
    "friendlycaptcha",
    "geetest",
    "hcaptcha",
    "recaptcha",
    "turnstile",
}
TURNSTILE_RESOURCE = {
    "host": "challenges.cloudflare.com",
    "path": "/turnstile/v0/api.js",
}
ARKOSE_RESOURCE = {"host": "iframe.arkoselabs.com", "path": "/redacted"}


def _matches_by_vendor(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {match["vendor"]: match for match in result["matches"]}


def test_documented_resource_catalogue_and_sanitisation() -> None:
    result = classify_challenge_snapshot(
        {
            "iframes": [
                {
                    "resource": {
                        "host": "CHALLENGES.CLOUDFLARE.COM",
                        "path": "/turnstile/v0/api.js?auth_token=must-not-appear#secret",
                    },
                    "bounding_box": {"x": 1, "y": 2, "width": 300, "height": 65},
                },
                {
                    "resource": {
                        "host": "challenges.cloudflare.com",
                        "path": "/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1",
                    }
                },
                {
                    "resource": {
                        "host": "www.recaptcha.net",
                        "path": "/recaptcha/api2/anchor",
                    }
                },
                {
                    "resource": {
                        "host": "iframe.arkoselabs.com",
                        "path": "/TEST_PUBLIC_KEY_DO_NOT_USE/lightbox.html",
                    }
                },
            ],
            "scripts": [
                {
                    "resource": {
                        "host": "www.google.com",
                        "path": "/recaptcha/api.js?render=explicit",
                    }
                },
                {"resource": {"host": "js.hcaptcha.com", "path": "/1/api.js"}},
                {
                    "resource": {
                        "host": "cdn.jsdelivr.net",
                        "path": "/npm/@friendlycaptcha/sdk/site.min.js",
                    }
                },
                {
                    "resource": {
                        "host": "client-api.arkoselabs.com",
                        "path": "/v2/TEST_PUBLIC_KEY_DO_NOT_USE/api.js",
                    }
                },
            ],
        }
    )

    matches = _matches_by_vendor(result)
    assert result["detected"] is True
    assert result["status"] == "present"
    assert set(matches) == {"turnstile", "recaptcha", "arkose", "hcaptcha", "friendlycaptcha"}
    assert matches["turnstile"]["confidence"] == "high"
    assert matches["turnstile"]["frames"][0] == {
        **TURNSTILE_RESOURCE,
        "bounding_box": {"x": 1.0, "y": 2.0, "width": 300.0, "height": 65.0},
    }
    assert matches["arkose"]["frames"][0] == ARKOSE_RESOURCE
    assert matches["friendlycaptcha"]["confidence"] == "medium"
    assert "auth_token" not in str(result)
    assert "must-not-appear" not in str(result)
    assert "secret" not in str(result)
    assert "TEST_PUBLIC_KEY_DO_NOT_USE" not in str(result)


def test_documented_widget_markers_globals_and_generic_visibility() -> None:
    result = classify_challenge_snapshot(
        {
            "elements": [
                {"marker": "turnstile", "visible": True, "bounding_box": {"x": 1, "y": 1, "width": 1, "height": 1}},
                {"marker": "recaptcha", "visible": False, "bounding_box": {"x": 2, "y": 2, "width": 2, "height": 2}},
                {"marker": "hcaptcha", "visible": True, "bounding_box": {"x": 3, "y": 3, "width": 3, "height": 3}},
                {"marker": "friendlycaptcha", "visible": True, "bounding_box": {"x": 4, "y": 4, "width": 4, "height": 4}},
                {"marker": "altcha", "visible": True, "bounding_box": {"x": 5, "y": 5, "width": 5, "height": 5}},
                {"marker": "generic-challenge", "visible": True, "bounding_box": {"x": 6, "y": 6, "width": 6, "height": 6}},
                {"marker": "generic-challenge", "visible": False, "bounding_box": {"x": 7, "y": 7, "width": 7, "height": 7}},
            ],
            "globals": {"aws_waf": True, "geetest": True},
        }
    )

    matches = _matches_by_vendor(result)
    assert set(matches) == DOCUMENTED_VENDORS - {"arkose"} | {"unknown"}
    assert matches["unknown"]["confidence"] == "low"
    assert matches["unknown"]["elements"] == [
        {"bounding_box": {"x": 6.0, "y": 6.0, "width": 6.0, "height": 6.0}}
    ]
    assert matches["aws_waf"]["confidence"] == "high"
    assert matches["geetest"]["confidence"] == "medium"
    assert matches["recaptcha"]["confidence"] == "high"


def test_malformed_data_geometry_deduplication_and_bounds() -> None:
    recognised_with_bad_box = classify_challenge_snapshot(
        {
            "iframes": [
                {
                    "resource": TURNSTILE_RESOURCE,
                    "bounding_box": {"x": float("nan"), "y": 0, "width": 1, "height": 1},
                },
                {"resource": {"host": "attacker@challenges.cloudflare.com", "path": "/turnstile/v0/api.js"}},
                {"resource": {"host": 1, "path": []}},
            ],
            "elements": [{"marker": "generic-challenge", "visible": True, "bounding_box": {"x": True, "y": 0, "width": 1, "height": 1}}],
            "globals": {"aws_waf": "true", "geetest": 1},
        }
    )
    turnstile = _matches_by_vendor(recognised_with_bad_box)["turnstile"]
    assert "bounding_box" not in turnstile["frames"][0]
    assert "elements" not in turnstile
    assert "elements" not in _matches_by_vendor(recognised_with_bad_box)["unknown"]

    duplicate_frames = [
        {"resource": {"host": "challenges.cloudflare.com", "path": f"/turnstile/{index}"}}
        for index in range(9)
    ]
    deduplicated = classify_challenge_snapshot({"iframes": duplicate_frames + [duplicate_frames[0]]})
    assert len(_matches_by_vendor(deduplicated)["turnstile"]["frames"]) == 8

    same_box = {"x": 1, "y": 2, "width": 300, "height": 65}
    duplicate_resource = {"resource": TURNSTILE_RESOURCE, "bounding_box": same_box}
    exact_duplicates = classify_challenge_snapshot({"iframes": [duplicate_resource, duplicate_resource]})
    assert len(_matches_by_vendor(exact_duplicates)["turnstile"]["frames"]) == 1

    distinct_geometry = classify_challenge_snapshot(
        {
            "iframes": [
                duplicate_resource,
                {"resource": TURNSTILE_RESOURCE, "bounding_box": {**same_box, "x": 2}},
            ]
        }
    )
    assert len(_matches_by_vendor(distinct_geometry)["turnstile"]["frames"]) == 2

    ignored_after_limit = [{"resource": {"host": "example.invalid", "path": "/"}}] * 100
    ignored_after_limit.append({"resource": TURNSTILE_RESOURCE})
    assert classify_challenge_snapshot({"iframes": ignored_after_limit}) == ABSENT_RESULT

    ignored_elements_after_limit = [{"marker": "unrelated", "visible": True}] * 100
    ignored_elements_after_limit.append({"marker": "turnstile", "visible": True})
    assert classify_challenge_snapshot({"elements": ignored_elements_after_limit}) == ABSENT_RESULT


def test_resource_boundaries_and_false_positives() -> None:
    unrecognised_resources = [
        {"host": "cdn.challenges.cloudflare.com", "path": "/turnstile/v0/api.js"},
        {"host": "www.google.com.evil.invalid", "path": "/recaptcha/api.js"},
        {"host": "assets.recaptcha.net", "path": "/recaptcha/api.js"},
        {"host": "cdn.js.hcaptcha.com", "path": "/1/api.js"},
        {"host": "challenges.cloudflare.com", "path": "/other/path"},
        {"host": "www.google.com", "path": "/not-recaptcha/api.js"},
        {"host": "js.hcaptcha.com", "path": "/2/api.js"},
        {"host": "cdn.jsdelivr.net", "path": "/npm/friendlycaptcha/sdk.js"},
    ]
    assert classify_challenge_snapshot({"scripts": [{"resource": resource} for resource in unrecognised_resources]}) == ABSENT_RESULT

    long_resource = {
        "host": "challenges.cloudflare.com",
        "path": "/turnstile/" + "x" * 512 + "?auth_token=must-not-appear#secret",
    }
    long_result = classify_challenge_snapshot({"iframes": [{"resource": long_resource}]})
    returned_path = _matches_by_vendor(long_result)["turnstile"]["frames"][0]["path"]
    assert len(returned_path) == 256
    assert "auth_token" not in returned_path
    assert "secret" not in returned_path


def test_absent_and_malformed_snapshots_have_stable_empty_schema() -> None:
    assert classify_challenge_snapshot(None) == ABSENT_RESULT
    assert classify_challenge_snapshot({"iframes": [None, {"resource": {"host": 1, "path": []}}]}) == ABSENT_RESULT
    assert classify_challenge_snapshot({"iframes": "not-a-list", "scripts": {}, "elements": None, "globals": []}) == ABSENT_RESULT


class SnapshotPage:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.expressions: list[tuple[str, Any]] = []

    async def evaluate(self, expression: str, argument: Any = None) -> Any:
        self.expressions.append((expression, argument))
        if "window.scrollTo" in expression:
            return True
        return self.snapshot


class FailingPage:
    async def evaluate(self, expression: str) -> Any:
        assert "document.querySelectorAll" in expression
        raise RuntimeError("fixture evaluation error")


async def test_page_evaluation_success_and_failure() -> None:
    page = SnapshotPage({"iframes": [{"resource": TURNSTILE_RESOURCE}]})
    result = await detect_challenges(page)
    assert result["status"] == "present"
    assert "new URL(value, document.baseURI)" in page.expressions[0][0]
    assert "document.querySelectorAll" in page.expressions[0][0]
    assert await detect_challenges(FailingPage()) == UNKNOWN_RESULT


async def test_scroll_into_view_uses_visible_detected_geometry() -> None:
    page = SnapshotPage(
        {
            "iframes": [
                {
                    "resource": TURNSTILE_RESOURCE,
                    "visible": True,
                    "bounding_box": {
                        "x": 10,
                        "y": 1200,
                        "width": 300,
                        "height": 65,
                    },
                }
            ]
        }
    )
    result = await detect_challenges(page, scroll_into_view=True)
    assert result["scrolled_into_view"] is True
    assert len(page.expressions) == 2
    assert "window.scrollTo" in page.expressions[1][0]
    assert page.expressions[1][1] == {
        "x": 10.0,
        "y": 1200.0,
        "width": 300.0,
        "height": 65.0,
    }

    hidden_page = SnapshotPage(
        {
            "elements": [
                {
                    "marker": "turnstile",
                    "visible": False,
                    "bounding_box": {"x": 10, "y": 1200, "width": 300, "height": 65},
                }
            ]
        }
    )
    hidden_result = await detect_challenges(hidden_page, scroll_into_view=True)
    assert hidden_result["detected"] is True
    assert hidden_result["scrolled_into_view"] is False
    assert len(hidden_page.expressions) == 1

    unknown_result = await detect_challenges(FailingPage(), scroll_into_view=True)
    assert unknown_result == {**UNKNOWN_RESULT, "scrolled_into_view": False}


def main() -> None:
    test_documented_resource_catalogue_and_sanitisation()
    test_documented_widget_markers_globals_and_generic_visibility()
    test_malformed_data_geometry_deduplication_and_bounds()
    test_resource_boundaries_and_false_positives()
    test_absent_and_malformed_snapshots_have_stable_empty_schema()
    asyncio.run(test_page_evaluation_success_and_failure())
    asyncio.run(test_scroll_into_view_uses_visible_detected_geometry())
    print("OK: challenge detector unit tests")


if __name__ == "__main__":
    main()
