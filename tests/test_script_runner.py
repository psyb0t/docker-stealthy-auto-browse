"""Dependency-free unit tests for script control flow."""

from __future__ import annotations

import asyncio
import io
import math
import os
import sys
from typing import Any

sys.path.insert(0, "/app")

from script_runner import ScriptValidationError, run_script, validate_script


class FakeDispatcher:
    """Minimal browser-action boundary used to test script semantics."""

    def __init__(self) -> None:
        self.loop_count = 0
        self.repeat_count = 0

    async def __call__(self, step: dict[str, Any]) -> dict[str, Any]:
        action = step["action"]
        if action == "fail":
            return {"success": False, "error": "expected failure"}
        if action == "set_output":
            return {"success": True, "data": {"result": True}}
        if action == "increment_repeat":
            self.repeat_count += 1
            return {"success": True, "data": {"result": self.repeat_count}}
        if action == "increment_loop":
            self.loop_count += 1
            return {"success": True, "data": {"result": self.loop_count}}
        if action == "echo":
            return {"success": True, "data": {"value": step["value"]}}
        if action == "eval":
            return {"success": True, "data": {"result": self._evaluate(step["expression"])} }
        return {"success": True, "data": {"action": action}}

    def _evaluate(self, expression: str) -> Any:
        if expression == "location.href":
            return "http://fixture/index.html"
        if "document.querySelector" in expression or "innerText.includes" in expression:
            return True
        if expression == "loop < 2":
            return self.loop_count < 2
        if expression == "true":
            return True
        if expression == "not_boolean":
            return "true"
        raise AssertionError(f"unexpected expression: {expression}")


def assert_validation_error(script: dict[str, Any], expected: str) -> None:
    try:
        validate_script(script)
    except ScriptValidationError as error:
        assert expected in str(error)
        return
    raise AssertionError("expected ScriptValidationError")


def test_validation_rejects_invalid_controls() -> None:
    action = {"action": "ping"}
    cases = [
        ({"steps": []}, "non-empty"),
        ({"steps": [{"action": "ping", "output_id": []}]}, "output_id"),
        ({"steps": [{"repeat": {"count": 0, "steps": [action]}}]}, "between"),
        ({"steps": [{"while": {"condition": {"type": "javascript", "expression": "true"}, "steps": [action]}}]}, "max_iterations"),
        ({"steps": [{"if": {"condition": {"type": "unknown"}, "then": [action]}}]}, "unsupported"),
        ({"steps": [{"if": {"condition": {"type": "text", "text": "x", "timeout": 61}, "then": [action]}}]}, "outside range"),
        ({"steps": [{"if": {"condition": {"type": "text", "text": "x", "timeout": math.nan}, "then": [action]}}]}, "finite"),
        ({"steps": [{"if": {"condition": {"type": "output", "output_id": "x"}, "then": [action]}}]}, "exactly one"),
    ]
    for script, expected in cases:
        assert_validation_error(script, expected)


async def test_control_flow_execution() -> None:
    previous_value = os.environ.get("CONTROL_FLOW_VALUE")
    os.environ["CONTROL_FLOW_VALUE"] = "nested-value"
    try:
        script = {
            "steps": [
                {"action": "set_output", "output_id": "ready"},
                {
                    "if": {
                        "condition": {
                            "type": "all",
                            "conditions": [
                                {"type": "element", "selector": "#quoted'selector"},
                                {"type": "text", "text": "Submit"},
                                {"type": "url", "matches": "*index.html"},
                                {"type": "javascript", "expression": "true"},
                            ],
                        },
                        "then": [{"action": "echo", "value": "${env.CONTROL_FLOW_VALUE}", "output_id": "branch"}],
                    }
                },
                {
                    "if": {
                        "condition": {"type": "output", "output_id": "ready", "path": ["result"], "equals": True},
                        "then": [{"action": "echo", "value": "output", "output_id": "output_branch"}],
                    }
                },
                {"repeat": {"count": 3, "steps": [{"action": "increment_repeat", "output_id": "repeat"}]}},
                {
                    "while": {
                        "condition": {"type": "javascript", "expression": "loop < 2"},
                        "max_iterations": 3,
                        "steps": [{"action": "increment_loop", "output_id": "loop"}],
                    }
                },
            ]
        }
        result = await run_script(script, FakeDispatcher(), stdout=io.StringIO())
    finally:
        if previous_value is None:
            del os.environ["CONTROL_FLOW_VALUE"]
        else:
            os.environ["CONTROL_FLOW_VALUE"] = previous_value
    assert result["success"]
    assert result["steps_executed"] == result["steps_total"] == 5
    assert result["outputs"]["branch"]["value"] == "nested-value"
    assert result["outputs"]["output_branch"]["value"] == "output"
    assert result["outputs"]["repeat"]["result"] == 3
    assert result["outputs"]["loop"]["result"] == 2
    assert len(result["step_results"][3]["data"]["iterations"]) == 3
    assert len(result["step_results"][4]["data"]["iterations"]) == 2


async def test_failure_and_loop_guards() -> None:
    failure = await run_script(
        {
            "on_error": "continue",
            "steps": [
                {"repeat": {"count": 2, "steps": [{"action": "fail"}]}},
                {"action": "echo", "value": "after", "output_id": "after"},
            ],
        },
        FakeDispatcher(),
        stdout=io.StringIO(),
    )
    assert not failure["success"]
    assert failure["outputs"]["after"]["value"] == "after"
    loop_limit = await run_script(
        {
            "steps": [{"while": {"condition": {"type": "javascript", "expression": "true"}, "max_iterations": 1, "steps": [{"action": "echo", "value": "one"}]}}]
        },
        FakeDispatcher(),
        stdout=io.StringIO(),
    )
    assert not loop_limit["success"]
    assert loop_limit["step_results"][0]["error"] == "while loop reached max_iterations"
    invalid_boolean = await run_script(
        {"steps": [{"if": {"condition": {"type": "javascript", "expression": "not_boolean"}, "then": [{"action": "ping"}]}}]},
        FakeDispatcher(),
        stdout=io.StringIO(),
    )
    assert not invalid_boolean["success"]
    assert invalid_boolean["step_results"][0]["error"] == "condition evaluation failed"


def main() -> None:
    test_validation_rejects_invalid_controls()
    asyncio.run(test_control_flow_execution())
    asyncio.run(test_failure_and_loop_guards())
    print("OK: script_runner unit tests")


if __name__ == "__main__":
    main()
