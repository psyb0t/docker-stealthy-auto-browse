"""Run validated YAML browser scripts and emit JSON results."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import math
import os
import re
import sys
import time
from typing import Any, Awaitable, Callable

import yaml
from logger import get_logger

log = get_logger(__name__)

_CONTROL_TYPES = {"if", "repeat", "while"}
_CONDITION_TYPES = {"all", "any", "element", "javascript", "not", "output", "text", "url"}
_ELEMENT_STATES = {"attached", "detached", "hidden", "visible"}
_STOP_ON_ERROR = "stop"
_MAX_CONDITION_TIMEOUT_SECONDS = 60
_MAX_CONTROL_FLOW_DEPTH = 8
_MAX_CONDITION_DEPTH = 8
_MAX_LOOP_ITERATIONS = 100
_MAX_LOOP_STEP_EXECUTIONS = 1_000
_CONDITION_POLL_INTERVAL_SECONDS = 0.1


class ScriptValidationError(ValueError):
    """Script structure cannot be safely executed."""


class _ConditionEvaluationError(RuntimeError):
    """A page-state condition could not be evaluated."""


class _ExecutionState:
    """State shared by nested steps in one script invocation."""

    def __init__(self) -> None:
        self.outputs: dict[str, Any] = {}
        self.loop_step_executions = 0


def load_script(path: str) -> dict[str, Any]:
    """Load and validate a YAML script from file."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Script not found: {path}")
    with open(path, encoding="utf-8") as script_file:
        data = yaml.safe_load(script_file)
    validate_script(data)
    return data


def validate_script(script_data: Any) -> None:
    """Validate script structure before it reaches browser action dispatch."""
    if not script_data or not isinstance(script_data, dict):
        raise ScriptValidationError("Invalid script: expected a YAML mapping")
    steps = script_data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ScriptValidationError("Invalid script: steps must be a non-empty list")
    _validate_steps(steps, depth=0, allow_empty=False)


def _validate_steps(steps: Any, depth: int, allow_empty: bool) -> None:
    if not isinstance(steps, list):
        raise ScriptValidationError("Invalid script: step block must be a list")
    if not allow_empty and not steps:
        raise ScriptValidationError("Invalid script: step block must not be empty")
    for step in steps:
        _validate_step(step, depth)


def _validate_step(step: Any, depth: int) -> None:
    if not isinstance(step, dict):
        raise ScriptValidationError("Invalid script: every step must be a mapping")
    control_types = _CONTROL_TYPES.intersection(step)
    if len(control_types) > 1:
        raise ScriptValidationError("Invalid script: a step may contain one control node")
    if control_types and "action" in step:
        raise ScriptValidationError("Invalid script: a control node cannot include action")
    if not control_types:
        action = step.get("action")
        if not isinstance(action, str) or not action:
            raise ScriptValidationError("Invalid script: action step requires a non-empty action")
        if "output_id" in step:
            _validate_non_empty_string(step["output_id"], "action output_id")
        return
    if depth >= _MAX_CONTROL_FLOW_DEPTH:
        raise ScriptValidationError("Invalid script: control-flow nesting limit exceeded")
    control_type = next(iter(control_types))
    if set(step) != {control_type}:
        raise ScriptValidationError("Invalid script: control nodes cannot have sibling fields")
    control = step[control_type]
    if not isinstance(control, dict):
        raise ScriptValidationError("Invalid script: control node must be a mapping")
    if control_type == "if":
        _validate_if_control(control, depth)
        return
    if control_type == "repeat":
        _validate_repeat_control(control, depth)
        return
    _validate_while_control(control, depth)


def _validate_if_control(control: dict[str, Any], depth: int) -> None:
    allowed = {"condition", "else", "then"}
    if set(control) - allowed or "condition" not in control or "then" not in control:
        raise ScriptValidationError("Invalid script: if requires condition and then")
    _validate_condition(control["condition"], allow_timeout=True)
    _validate_steps(control["then"], depth + 1, allow_empty=True)
    if "else" in control:
        _validate_steps(control["else"], depth + 1, allow_empty=True)


def _validate_repeat_control(control: dict[str, Any], depth: int) -> None:
    if set(control) != {"count", "steps"}:
        raise ScriptValidationError("Invalid script: repeat requires only count and steps")
    _validate_loop_bound(control["count"], "repeat count")
    _validate_steps(control["steps"], depth + 1, allow_empty=False)


def _validate_while_control(control: dict[str, Any], depth: int) -> None:
    if set(control) != {"condition", "max_iterations", "steps"}:
        raise ScriptValidationError(
            "Invalid script: while requires condition, max_iterations, and steps"
        )
    _validate_condition(control["condition"], allow_timeout=True)
    _validate_loop_bound(control["max_iterations"], "while max_iterations")
    _validate_steps(control["steps"], depth + 1, allow_empty=False)


def _validate_loop_bound(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScriptValidationError(f"Invalid script: {label} must be an integer")
    if value < 1 or value > _MAX_LOOP_ITERATIONS:
        raise ScriptValidationError(
            f"Invalid script: {label} must be between 1 and {_MAX_LOOP_ITERATIONS}"
        )


def _validate_condition(
    condition: Any,
    allow_timeout: bool,
    depth: int = 0,
) -> None:
    if not isinstance(condition, dict):
        raise ScriptValidationError("Invalid script: condition must be a mapping")
    if depth >= _MAX_CONDITION_DEPTH:
        raise ScriptValidationError("Invalid script: condition nesting limit exceeded")
    condition_type = condition.get("type")
    if not isinstance(condition_type, str) or condition_type not in _CONDITION_TYPES:
        raise ScriptValidationError("Invalid script: unsupported condition type")
    allowed_fields = {
        "all": {"conditions", "timeout", "type"},
        "any": {"conditions", "timeout", "type"},
        "element": {"selector", "state", "timeout", "type"},
        "javascript": {"expression", "timeout", "type"},
        "not": {"condition", "timeout", "type"},
        "output": {"equals", "exists", "output_id", "path", "timeout", "type"},
        "text": {"text", "timeout", "type"},
        "url": {"matches", "timeout", "type"},
    }[condition_type]
    if set(condition) - allowed_fields:
        raise ScriptValidationError("Invalid script: unsupported condition field")
    if "timeout" in condition:
        if not allow_timeout:
            raise ScriptValidationError("Invalid script: nested conditions cannot set timeout")
        _validate_timeout(condition["timeout"])
    if condition_type == "element":
        _validate_non_empty_string(condition.get("selector"), "element selector")
        state = condition.get("state", "visible")
        if not isinstance(state, str) or state not in _ELEMENT_STATES:
            raise ScriptValidationError("Invalid script: unsupported element state")
        return
    if condition_type == "text":
        _validate_non_empty_string(condition.get("text"), "text condition text")
        return
    if condition_type == "url":
        _validate_non_empty_string(condition.get("matches"), "url condition matches")
        return
    if condition_type == "javascript":
        _validate_non_empty_string(condition.get("expression"), "javascript condition expression")
        return
    if condition_type == "output":
        _validate_non_empty_string(condition.get("output_id"), "output condition output_id")
        has_equals = "equals" in condition
        has_exists = "exists" in condition
        if has_equals == has_exists:
            raise ScriptValidationError(
                "Invalid script: output condition requires exactly one of equals or exists"
            )
        if has_exists and not isinstance(condition["exists"], bool):
            raise ScriptValidationError("Invalid script: output condition exists must be boolean")
        _validate_output_path(condition.get("path", []))
        return
    if condition_type in {"all", "any"}:
        conditions = condition.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise ScriptValidationError(
                "Invalid script: all and any require a non-empty conditions list"
            )
        for nested_condition in conditions:
            _validate_condition(
                nested_condition,
                allow_timeout=False,
                depth=depth + 1,
            )
        return
    if "condition" not in condition:
        raise ScriptValidationError("Invalid script: not requires condition")
    _validate_condition(
        condition["condition"],
        allow_timeout=False,
        depth=depth + 1,
    )


def _validate_timeout(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScriptValidationError("Invalid script: condition timeout must be a number")
    if not math.isfinite(value):
        raise ScriptValidationError("Invalid script: condition timeout must be finite")
    if value < 0 or value > _MAX_CONDITION_TIMEOUT_SECONDS:
        raise ScriptValidationError("Invalid script: condition timeout is outside range")


def _validate_output_path(path: Any) -> None:
    if not isinstance(path, list):
        raise ScriptValidationError("Invalid script: output condition path must be a list")
    for segment in path:
        if isinstance(segment, bool):
            raise ScriptValidationError("Invalid script: output condition path is invalid")
        if isinstance(segment, str) and segment:
            continue
        if isinstance(segment, int) and segment >= 0:
            continue
        raise ScriptValidationError("Invalid script: output condition path is invalid")


def _validate_non_empty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ScriptValidationError(f"Invalid script: {label} must be a non-empty string")


def _substitute_env(value: str) -> str:
    """Replace ${env.VAR} placeholders with environment variable values."""
    return re.sub(
        r"\$\{env\.([^}]+)\}",
        lambda match: os.environ.get(match.group(1), ""),
        value,
    )


def substitute_step_vars(step: dict[str, Any]) -> dict[str, Any]:
    """Replace environment placeholders recursively in a step or control block."""
    return _substitute_value(step)


def _substitute_value(value: Any) -> Any:
    if isinstance(value, str):
        return _substitute_env(value)
    if isinstance(value, list):
        return [_substitute_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_value(item) for key, item in value.items()}
    return value


def _extract_output(result: dict[str, Any]) -> Any:
    """Extract a script output, encoding binary screenshots as a data URI."""
    raw = result.get("_binary")
    if raw:
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    return result.get("data")


async def run_script(
    script_data: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    stdout: Any = None,
) -> dict[str, Any]:
    """Execute validated script steps and return a JSON-serializable results mapping."""
    validate_script(script_data)
    if stdout is None:
        stdout = sys.stdout
    name = script_data.get("name", "unnamed")
    on_error = script_data.get("on_error", _STOP_ON_ERROR)
    steps = script_data["steps"]
    state = _ExecutionState()
    start_time = time.monotonic()
    log.info("script started", extra={"script_name": name, "steps_total": len(steps)})
    step_results, all_success, _ = await _execute_steps(
        steps,
        dispatch_fn,
        on_error,
        state,
        in_loop=False,
    )
    duration = round(time.monotonic() - start_time, 3)
    output: dict[str, Any] = {
        "name": name,
        "success": all_success,
        "steps_executed": len(step_results),
        "steps_total": len(steps),
        "duration": duration,
        "step_results": step_results,
    }
    if state.outputs:
        output["outputs"] = state.outputs
    log.info(
        "script finished",
        extra={
            "script_name": name,
            "steps_executed": len(step_results),
            "steps_total": len(steps),
            "success": all_success,
            "duration": duration,
        },
    )
    print(json.dumps(output, indent=2, default=str), file=stdout, flush=True)
    return output


async def _execute_steps(
    steps: list[dict[str, Any]],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    on_error: Any,
    state: _ExecutionState,
    *,
    in_loop: bool,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Execute one block and return its results, success state, and stop state."""
    step_results: list[dict[str, Any]] = []
    all_success = True
    for index, raw_step in enumerate(steps, start=1):
        if in_loop:
            state.loop_step_executions += 1
            if state.loop_step_executions > _MAX_LOOP_STEP_EXECUTIONS:
                limit_result = {"success": False, "error": "loop step execution limit exceeded"}
                step_results.append(_format_step_result(index, "loop_limit", 0.0, limit_result))
                return step_results, False, True
        step = substitute_step_vars(raw_step)
        action = _step_label(step)
        start_time = time.monotonic()
        log.info("script step started", extra={"step": index, "action": action})
        if action in _CONTROL_TYPES:
            result = await _execute_control(step, dispatch_fn, on_error, state, in_loop)
        else:
            result = await _execute_action(step, dispatch_fn, state)
        duration = round(time.monotonic() - start_time, 3)
        step_results.append(_format_step_result(index, action, duration, result))
        if result.get("success", False):
            log.info(
                "script step finished",
                extra={"step": index, "action": action, "duration": duration},
            )
            continue
        all_success = False
        log.warning("script step failed", extra={"step": index, "action": action})
        if on_error == _STOP_ON_ERROR:
            return step_results, False, True
    return step_results, all_success, False


async def _execute_action(
    step: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    state: _ExecutionState,
) -> dict[str, Any]:
    try:
        result = await dispatch_fn(step)
    except Exception:
        log.exception("script action dispatch failed", extra={"action": step["action"]})
        result = {"success": False, "error": "action dispatch failed"}
    if not isinstance(result, dict):
        result = {"success": False, "error": "action dispatch returned an invalid result"}
    output_id = step.get("output_id")
    if output_id and result.get("success"):
        state.outputs[output_id] = _extract_output(result)
    return result


async def _execute_control(
    step: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    on_error: Any,
    state: _ExecutionState,
    in_loop: bool,
) -> dict[str, Any]:
    control_type = _step_label(step)
    control = step[control_type]
    if control_type == "if":
        return await _execute_if(control, dispatch_fn, on_error, state, in_loop)
    if control_type == "repeat":
        return await _execute_repeat(control, dispatch_fn, on_error, state)
    return await _execute_while(control, dispatch_fn, on_error, state)


async def _execute_if(
    control: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    on_error: Any,
    state: _ExecutionState,
    in_loop: bool,
) -> dict[str, Any]:
    try:
        matched = await _wait_for_condition(control["condition"], dispatch_fn, state.outputs)
    except _ConditionEvaluationError:
        return {"success": False, "error": "condition evaluation failed"}
    branch = "then" if matched else "else"
    branch_steps = control.get(branch, [])
    nested_results, success, _ = await _execute_steps(
        branch_steps,
        dispatch_fn,
        on_error,
        state,
        in_loop=in_loop,
    )
    return {
        "success": success,
        "data": {
            "matched": matched,
            "branch": branch if branch_steps else "none",
            "step_results": nested_results,
        },
    }


async def _execute_repeat(
    control: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    on_error: Any,
    state: _ExecutionState,
) -> dict[str, Any]:
    iterations: list[dict[str, Any]] = []
    all_success = True
    for iteration in range(1, control["count"] + 1):
        nested_results, success, stopped = await _execute_steps(
            control["steps"],
            dispatch_fn,
            on_error,
            state,
            in_loop=True,
        )
        iterations.append({"iteration": iteration, "step_results": nested_results})
        if success:
            continue
        all_success = False
        if stopped:
            break
    return {
        "success": all_success,
        "data": {"iterations": iterations, "count": control["count"]},
    }


async def _execute_while(
    control: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    on_error: Any,
    state: _ExecutionState,
) -> dict[str, Any]:
    iterations: list[dict[str, Any]] = []
    all_success = True
    for iteration in range(1, control["max_iterations"] + 1):
        try:
            matched = await _wait_for_condition(
                control["condition"], dispatch_fn, state.outputs
            )
        except _ConditionEvaluationError:
            return {"success": False, "error": "condition evaluation failed"}
        if not matched:
            return {"success": all_success, "data": {"iterations": iterations}}
        nested_results, success, stopped = await _execute_steps(
            control["steps"],
            dispatch_fn,
            on_error,
            state,
            in_loop=True,
        )
        iterations.append({"iteration": iteration, "step_results": nested_results})
        if not success:
            all_success = False
        if not success and stopped:
            return {"success": False, "data": {"iterations": iterations}}
    try:
        still_matched = await _wait_for_condition(
            control["condition"], dispatch_fn, state.outputs
        )
    except _ConditionEvaluationError:
        return {"success": False, "error": "condition evaluation failed"}
    if still_matched:
        return {
            "success": False,
            "error": "while loop reached max_iterations",
            "data": {"iterations": iterations},
        }
    return {"success": all_success, "data": {"iterations": iterations}}


async def _wait_for_condition(
    condition: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    outputs: dict[str, Any],
) -> bool:
    deadline = time.monotonic() + float(condition.get("timeout", 0))
    while True:
        if await _condition_matches(condition, dispatch_fn, outputs):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_CONDITION_POLL_INTERVAL_SECONDS, remaining))


async def _condition_matches(
    condition: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
    outputs: dict[str, Any],
) -> bool:
    condition_type = condition["type"]
    if condition_type == "all":
        for nested_condition in condition["conditions"]:
            if not await _condition_matches(nested_condition, dispatch_fn, outputs):
                return False
        return True
    if condition_type == "any":
        for nested_condition in condition["conditions"]:
            if await _condition_matches(nested_condition, dispatch_fn, outputs):
                return True
        return False
    if condition_type == "not":
        return not await _condition_matches(condition["condition"], dispatch_fn, outputs)
    if condition_type == "output":
        return _output_condition_matches(condition, outputs)
    if condition_type == "element":
        return await _element_condition_matches(condition, dispatch_fn)
    if condition_type == "text":
        return await _page_bool(
            "document.body !== null && document.body.innerText.includes("
            f"{json.dumps(condition['text'])})",
            dispatch_fn,
        )
    if condition_type == "url":
        url = await _page_result("location.href", dispatch_fn)
        if not isinstance(url, str):
            raise _ConditionEvaluationError("URL condition returned a non-string")
        return fnmatch.fnmatchcase(url, condition["matches"])
    return await _page_bool(condition["expression"], dispatch_fn)


async def _element_condition_matches(
    condition: dict[str, Any],
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
) -> bool:
    selector = json.dumps(condition["selector"])
    state = condition.get("state", "visible")
    expression = f"""(() => {{
        const element = document.querySelector({selector});
        if (!element) return {str(state in {'detached', 'hidden'}).lower()};
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const visible = style.display !== 'none' && style.visibility !== 'hidden'
            && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
        if ({json.dumps(state)} === 'attached') return true;
        if ({json.dumps(state)} === 'detached') return false;
        if ({json.dumps(state)} === 'hidden') return !visible;
        return visible;
    }})()"""
    return await _page_bool(expression, dispatch_fn)


async def _page_bool(
    expression: str,
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
) -> bool:
    result = await _page_result(expression, dispatch_fn)
    if not isinstance(result, bool):
        raise _ConditionEvaluationError("condition must evaluate to boolean")
    return result


async def _page_result(
    expression: str,
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[dict]],
) -> Any:
    try:
        response = await dispatch_fn({"action": "eval", "expression": expression})
    except Exception as error:
        raise _ConditionEvaluationError("condition dispatch failed") from error
    if not isinstance(response, dict) or not response.get("success"):
        raise _ConditionEvaluationError("condition dispatch failed")
    data = response.get("data")
    if not isinstance(data, dict) or "result" not in data:
        raise _ConditionEvaluationError("condition response is invalid")
    return data["result"]


def _output_condition_matches(condition: dict[str, Any], outputs: dict[str, Any]) -> bool:
    exists, value = _get_output_path(
        outputs,
        condition["output_id"],
        condition.get("path", []),
    )
    if "exists" in condition:
        return exists is condition["exists"]
    return exists and value == condition["equals"]


def _get_output_path(
    outputs: dict[str, Any],
    output_id: str,
    path: list[Any],
) -> tuple[bool, Any]:
    if output_id not in outputs:
        return False, None
    value = outputs[output_id]
    for segment in path:
        if isinstance(value, dict) and isinstance(segment, str) and segment in value:
            value = value[segment]
            continue
        if isinstance(value, list) and isinstance(segment, int) and segment < len(value):
            value = value[segment]
            continue
        return False, None
    return True, value


def _step_label(step: dict[str, Any]) -> str:
    for control_type in _CONTROL_TYPES:
        if control_type in step:
            return control_type
    return step["action"]


def _format_step_result(
    index: int,
    action: str,
    duration: float,
    result: dict[str, Any],
) -> dict[str, Any]:
    serialized_result = dict(result)
    serialized_result.pop("_binary", None)
    return {"step": index, "action": action, "duration": duration, **serialized_result}
