#!/bin/bash
# tests/test_script.sh - Script execution mode tests

SCRIPT_FIXTURES="$WORKDIR/tests/fixtures/scripts"

_script_run() {
    local script_name="$1"
    shift
    docker run --rm -i \
        -e TEST_URL="$TEST_PAGE" \
        "$@" \
        "$IMAGE_NAME:$TEST_TAG" --script \
        2>/dev/null < "$SCRIPT_FIXTURES/$script_name"
}

test_script_basic() {
    local out
    out=$(_script_run basic.yaml)
    if [ -z "$out" ]; then
        echo "  FAIL: script_basic: no output"
        return 1
    fi

    local success
    success=$(echo "$out" | json_get "['success']")
    if [ "$success" != "True" ]; then
        echo "  FAIL: script_basic: success=$success"
        return 1
    fi
    echo "  OK: script_basic: success=True"

    local steps_executed
    steps_executed=$(echo "$out" | json_get "['steps_executed']")
    assert_eq "$steps_executed" "5" "script_basic: steps_executed" || return 1

    # Check outputs exist
    local title
    title=$(echo "$out" | json_get "['outputs']['title']['result']")
    assert_eq "$title" "Test Page" "script_basic: title output" || return 1

    # Check screenshot is base64
    local ss_prefix
    ss_prefix=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['outputs']['screenshot'][:22])")
    assert_eq "$ss_prefix" "data:image/png;base64," "script_basic: screenshot is base64" || return 1

    # Check page_text output
    local has_text
    has_text=$(echo "$out" | python3 -c "import sys,json; print('yes' if 'Submit' in json.load(sys.stdin)['outputs']['page_text']['text'] else 'no')")
    assert_eq "$has_text" "yes" "script_basic: page_text contains Submit" || return 1

    echo "OK: script_basic (5 steps, outputs: screenshot + text + title)"
}

test_script_on_error_continue() {
    local out
    out=$(_script_run on_error_continue.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "False" "script_on_error_continue: overall success=False" || return 1

    local steps_executed
    steps_executed=$(echo "$out" | json_get "['steps_executed']")
    assert_eq "$steps_executed" "3" "script_on_error_continue: all 3 steps ran" || return 1

    # Step 2 failed but step 3 still collected output
    local title
    title=$(echo "$out" | json_get "['outputs']['title']['result']")
    assert_eq "$title" "Test Page" "script_on_error_continue: title collected after error" || return 1

    echo "OK: script_on_error_continue (continued past error, collected output)"
}

test_script_on_error_stop() {
    local out
    out=$(_script_run on_error_stop.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "False" "script_on_error_stop: overall success=False" || return 1

    local steps_executed
    steps_executed=$(echo "$out" | json_get "['steps_executed']")
    assert_eq "$steps_executed" "2" "script_on_error_stop: stopped at step 2" || return 1

    # should_not_run output must not exist
    local has_output
    has_output=$(echo "$out" | python3 -c "import sys,json; print('yes' if 'should_not_run' in json.load(sys.stdin).get('outputs', {}) else 'no')")
    assert_eq "$has_output" "no" "script_on_error_stop: skipped step not in outputs" || return 1

    echo "OK: script_on_error_stop (stopped after error, skipped remaining)"
}

test_script_save_to_file() {
    local out
    out=$(_script_run save_to_file.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "script_save_to_file: success" || return 1

    # Check path is in step result
    local path
    path=$(echo "$out" | json_get "['step_results'][1]['data']['path']")
    assert_eq "$path" "/tmp/test_screenshot.png" "script_save_to_file: path in result" || return 1

    # Check output_id also captured base64
    local ss_prefix
    ss_prefix=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['outputs']['screenshot'][:22])")
    assert_eq "$ss_prefix" "data:image/png;base64," "script_save_to_file: also in outputs" || return 1

    echo "OK: script_save_to_file (file path + base64 output)"
}

test_script_exit_code_success() {
    _script_run basic.yaml > /dev/null
    local code=$?
    assert_eq "$code" "0" "script_exit_code_success: exit 0" || return 1
    echo "OK: script_exit_code_success (exit code 0)"
}

test_script_exit_code_failure() {
    _script_run on_error_stop.yaml > /dev/null
    local code=$?
    assert_eq "$code" "1" "script_exit_code_failure: exit 1" || return 1
    echo "OK: script_exit_code_failure (exit code 1)"
}

test_script_env_substitution() {
    local out
    out=$(_script_run basic.yaml)

    # The goto step used ${env.TEST_URL} - verify it resolved
    local url
    url=$(echo "$out" | json_get "['step_results'][0]['data']['url']")
    if ! echo "$url" | grep -q "index.html"; then
        echo "  FAIL: script_env_substitution: url=$url"
        return 1
    fi
    echo "OK: script_env_substitution (\${env.TEST_URL} resolved to $url)"
}

test_script_clean_stdout() {
    local raw
    raw=$(_script_run basic.yaml)

    # First char must be '{' (clean JSON, no noise)
    local first_char
    first_char=$(echo "$raw" | head -c1)
    assert_eq "$first_char" "{" "script_clean_stdout: starts with {" || return 1

    # Must be valid JSON
    echo "$raw" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null
    local code=$?
    assert_eq "$code" "0" "script_clean_stdout: valid JSON" || return 1

    echo "OK: script_clean_stdout (pure JSON on stdout)"
}

test_script_desktop_screenshot() {
    local out
    out=$(_script_run desktop_screenshot.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "script_desktop_screenshot: success" || return 1

    local ss_prefix
    ss_prefix=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['outputs']['desktop'][:22])")
    assert_eq "$ss_prefix" "data:image/png;base64," "script_desktop_screenshot: is base64" || return 1

    echo "OK: script_desktop_screenshot (desktop type screenshot as base64)"
}

test_script_screenshot_resize() {
    local out
    out=$(_script_run screenshot_resize.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "script_screenshot_resize: success" || return 1

    # Full screenshot should be larger than resized ones
    local full_size resized_size
    full_size=$(echo "$out" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['outputs']['full']))")
    resized_size=$(echo "$out" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['outputs']['resized_largest']))")
    if [ "$resized_size" -ge "$full_size" ]; then
        echo "  FAIL: script_screenshot_resize: resized ($resized_size) >= full ($full_size)"
        return 1
    fi
    echo "  OK: resized_largest smaller than full"

    # All 4 screenshots should be base64
    local count
    count=$(echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)['outputs']
print(sum(1 for k in ['full','resized_largest','resized_width','resized_both'] if d[k].startswith('data:image/png;base64,')))
")
    assert_eq "$count" "4" "script_screenshot_resize: all 4 are base64 PNGs" || return 1

    echo "OK: script_screenshot_resize (full + 3 resize modes)"
}

test_script_no_outputs() {
    local out
    out=$(_script_run no_outputs.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "script_no_outputs: success" || return 1

    # outputs key should not be present
    local has_outputs
    has_outputs=$(echo "$out" | python3 -c "import sys,json; print('yes' if 'outputs' in json.load(sys.stdin) else 'no')")
    assert_eq "$has_outputs" "no" "script_no_outputs: no outputs key" || return 1

    echo "OK: script_no_outputs (no outputs key when no output_id used)"
}

test_script_multi_action() {
    local out
    out=$(_script_run multi_action.yaml)

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "script_multi_action: success" || return 1

    # Tabs
    local tab_count
    tab_count=$(echo "$out" | json_get "['outputs']['tabs']['count']")
    assert_eq "$tab_count" "1" "script_multi_action: tabs count" || return 1

    # Cookies - we set one, should have at least 1
    local cookie_count
    cookie_count=$(echo "$out" | json_get "['outputs']['cookies']['count']")
    if [ "$cookie_count" -lt 1 ]; then
        echo "  FAIL: script_multi_action: cookie_count=$cookie_count"
        return 1
    fi
    echo "  OK: cookies (count=$cookie_count)"

    # Storage
    local storage_val
    storage_val=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['outputs']['storage']['items']['test_key'])")
    assert_eq "$storage_val" "test_val" "script_multi_action: storage value" || return 1

    # Network log
    local net_count
    net_count=$(echo "$out" | json_get "['outputs']['network']['count']")
    if [ "$net_count" -lt 1 ]; then
        echo "  FAIL: script_multi_action: network log empty"
        return 1
    fi
    echo "  OK: network log (count=$net_count)"

    # Resolution
    local res_w
    res_w=$(echo "$out" | json_get "['outputs']['resolution']['width']")
    assert_eq "$res_w" "1920" "script_multi_action: resolution width" || return 1

    # Calibration
    local has_offset
    has_offset=$(echo "$out" | python3 -c "import sys,json; print('yes' if 'window_offset' in json.load(sys.stdin)['outputs']['calibration'] else 'no')")
    assert_eq "$has_offset" "yes" "script_multi_action: calibration has offset" || return 1

    # Interactive elements
    local elem_count
    elem_count=$(echo "$out" | json_get "['outputs']['elements']['count']")
    if [ "$elem_count" -lt 1 ]; then
        echo "  FAIL: script_multi_action: no interactive elements"
        return 1
    fi
    echo "  OK: interactive elements (count=$elem_count)"

    # HTML
    local has_form
    has_form=$(echo "$out" | python3 -c "import sys,json; print('yes' if 'test-form' in json.load(sys.stdin)['outputs']['html']['html'] else 'no')")
    assert_eq "$has_form" "yes" "script_multi_action: html contains form" || return 1

    # Fill result
    local filled
    filled=$(echo "$out" | json_get "['outputs']['filled_value']['result']")
    assert_eq "$filled" "test name" "script_multi_action: fill worked" || return 1

    # Ping
    local pong
    pong=$(echo "$out" | json_get "['outputs']['ping']['message']")
    assert_eq "$pong" "pong" "script_multi_action: ping" || return 1

    echo "OK: script_multi_action (waits, tabs, cookies, storage, network, resolution, elements, html, fill, ping)"
}

test_script_loaders() {
    # Pipe script via stdin with loaders mounted
    local out
    out=$(docker run --rm -i \
        -e TEST_URL="$TEST_PAGE" \
        -v "$WORKDIR/tests/fixtures/loaders:/loaders:ro" \
        "$IMAGE_NAME:$TEST_TAG" --script \
        2>/dev/null < "$SCRIPT_FIXTURES/loaders_work.yaml")

    if [ -z "$out" ]; then
        echo "  FAIL: script_loaders: no output"
        return 1
    fi

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "script_loaders: success" || return 1

    # The loader sets #loader-marker text to "loader-executed"
    local marker
    marker=$(echo "$out" | json_get "['outputs']['marker']['result']")
    assert_eq "$marker" "loader-executed" "script_loaders: loader fired" || return 1

    echo "OK: script_loaders (page loader triggered in script mode)"
}

test_script_runner_unit() {
    docker run --rm \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -v "$WORKDIR/tests/test_script_runner.py:/tests/test_script_runner.py:ro" \
        --entrypoint python \
        "$IMAGE_NAME:$TEST_TAG" \
        /tests/test_script_runner.py || return 1
    echo "OK: script_runner_unit (validation and control-flow semantics)"
}

test_script_control_flow() {
    local out
    out=$(_script_run control_flow.yaml -e EXPECTED_TEXT=Submit)
    if [ -z "$out" ]; then
        echo "  FAIL: script_control_flow: no output"
        return 1
    fi

    echo "$out" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["success"] is True
assert result["steps_executed"] == result["steps_total"] == 9
outputs = result["outputs"]
assert outputs["page_condition_branch"]["result"] == "element-text-url-javascript"
assert outputs["output_condition_branch"]["result"] == "output-then"
assert outputs["false_condition_branch"]["result"] == "else-ran"
assert outputs["repeat_count"]["result"] == 3
assert outputs["while_count"]["result"] == 2
state = json.loads(outputs["control_flow_state"]["result"])
assert state == {"element": "then", "output": "then", "elseBranch": "else", "repeat": 3, "while": 2}
assert result["step_results"][2]["data"]["branch"] == "then"
assert result["step_results"][4]["data"]["branch"] == "else"
assert len(result["step_results"][5]["data"]["iterations"]) == 3
assert len(result["step_results"][7]["data"]["iterations"]) == 2
' || {
        echo "  FAIL: script_control_flow: unexpected result"
        return 1
    }

    echo "OK: script_control_flow (all condition types, branches, repeat, while)"
}

ALL_TESTS+=(
    test_script_basic
    test_script_on_error_continue
    test_script_on_error_stop
    test_script_save_to_file
    test_script_exit_code_success
    test_script_exit_code_failure
    test_script_env_substitution
    test_script_clean_stdout
    test_script_desktop_screenshot
    test_script_screenshot_resize
    test_script_no_outputs
    test_script_multi_action
    test_script_loaders
    test_script_runner_unit
    test_script_control_flow
)
