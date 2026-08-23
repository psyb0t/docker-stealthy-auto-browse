#!/bin/bash
# tests/test_console.sh - Console log capture tests

test_console_log() {
    # Enable console logging
    local resp
    resp=$(post '{"action": "enable_console_log"}')
    assert_success "$resp" "console_log: enable" || return 1

    # Trigger some console messages
    post '{"action": "eval", "expression": "console.log(\"hello-log\")"}' > /dev/null
    post '{"action": "eval", "expression": "console.error(\"hello-error\")"}' > /dev/null
    post '{"action": "eval", "expression": "console.warn(\"hello-warn\")"}' > /dev/null
    post '{"action": "eval", "expression": "console.info(\"hello-info\")"}' > /dev/null
    sleep 0.5

    # Get console log
    resp=$(post '{"action": "get_console_log"}')
    assert_success "$resp" "console_log: get" || return 1

    local count
    count=$(echo "$resp" | json_get "['data']['count']")
    if [ "$count" -lt 4 ]; then
        echo "  FAIL: console_log: expected >= 4 entries, got $count"
        return 1
    fi
    echo "  OK: console_log: captured $count entries"

    # Verify log types
    local has_log has_error has_warn
    has_log=$(echo "$resp" | python3 -c "import sys,json; entries=json.load(sys.stdin)['data']['log']; print('yes' if any(e['type']=='log' and 'hello-log' in e['text'] for e in entries) else 'no')")
    has_error=$(echo "$resp" | python3 -c "import sys,json; entries=json.load(sys.stdin)['data']['log']; print('yes' if any(e['type']=='error' and 'hello-error' in e['text'] for e in entries) else 'no')")
    has_warn=$(echo "$resp" | python3 -c "import sys,json; entries=json.load(sys.stdin)['data']['log']; print('yes' if any(e['type']=='warning' and 'hello-warn' in e['text'] for e in entries) else 'no')")
    has_info=$(echo "$resp" | python3 -c "import sys,json; entries=json.load(sys.stdin)['data']['log']; print('yes' if any(e['type']=='info' and 'hello-info' in e['text'] for e in entries) else 'no')")
    assert_eq "$has_log" "yes" "console_log: has log entry" || return 1
    assert_eq "$has_error" "yes" "console_log: has error entry" || return 1
    assert_eq "$has_warn" "yes" "console_log: has warn entry" || return 1
    assert_eq "$has_info" "yes" "console_log: has info entry" || return 1

    # Clear
    resp=$(post '{"action": "clear_console_log"}')
    assert_success "$resp" "console_log: clear" || return 1

    resp=$(post '{"action": "get_console_log"}')
    count=$(echo "$resp" | json_get "['data']['count']")
    assert_eq "$count" "0" "console_log: cleared to 0" || return 1

    # Disable
    resp=$(post '{"action": "disable_console_log"}')
    assert_success "$resp" "console_log: disable" || return 1

    echo "OK: console_log (enable, capture log/error/warn, clear, disable)"
}

test_console_log_disabled() {
    # Make sure disabled state doesn't capture
    post '{"action": "disable_console_log"}' > /dev/null
    post '{"action": "clear_console_log"}' > /dev/null

    post '{"action": "eval", "expression": "console.log(\"should-not-capture\")"}' > /dev/null
    sleep 0.3

    local resp count
    resp=$(post '{"action": "get_console_log"}')
    count=$(echo "$resp" | json_get "['data']['count']")
    assert_eq "$count" "0" "console_log_disabled: nothing captured" || return 1

    echo "OK: console_log_disabled (no capture when disabled)"
}

test_console_log_script_mode() {
    local out
    out=$(docker run --rm -i \
        -e TEST_URL="$TEST_PAGE" \
        "$IMAGE_NAME:$TEST_TAG" --script \
        < "$WORKDIR/tests/fixtures/scripts/console_log.yaml" \
        2>/dev/null)

    if [ -z "$out" ]; then
        echo "  FAIL: console_log_script: no output"
        return 1
    fi

    local success
    success=$(echo "$out" | json_get "['success']")
    assert_eq "$success" "True" "console_log_script: success" || return 1

    # Check console output has entries
    local count
    count=$(echo "$out" | json_get "['outputs']['console']['count']")
    if [ "$count" -lt 3 ]; then
        echo "  FAIL: console_log_script: expected >= 3 entries, got $count"
        return 1
    fi
    echo "  OK: console_log_script: $count entries"

    # Verify log message captured
    local has_log
    has_log=$(echo "$out" | python3 -c "import sys,json; entries=json.load(sys.stdin)['outputs']['console']['log']; print('yes' if any('test-log-message' in e['text'] for e in entries) else 'no')")
    assert_eq "$has_log" "yes" "console_log_script: has test-log-message" || return 1

    echo "OK: console_log_script (captured in script mode with output_id)"
}

test_console_log_getclear() {
    post '{"action": "enable_console_log"}' > /dev/null
    post '{"action": "clear_console_log"}' > /dev/null
    post '{"action": "eval", "expression": "console.log(\"getclear-test\")"}' > /dev/null
    sleep 0.3

    # getclear returns entries AND clears
    local resp
    resp=$(post '{"action": "getclear_console_log"}')
    assert_success "$resp" "getclear: success" || return 1

    local count
    count=$(echo "$resp" | json_get "['data']['count']")
    if [ "$count" -lt 1 ]; then
        echo "  FAIL: getclear: expected >= 1 entries, got $count"
        return 1
    fi
    echo "  OK: getclear: got $count entries"

    # Should be empty now
    resp=$(post '{"action": "get_console_log"}')
    count=$(echo "$resp" | json_get "['data']['count']")
    assert_eq "$count" "0" "getclear: log cleared after getclear" || return 1

    post '{"action": "disable_console_log"}' > /dev/null
    echo "OK: console_log_getclear (returns and clears atomically)"
}

ALL_TESTS+=(
    test_console_log
    test_console_log_disabled
    test_console_log_script_mode
    test_console_log_getclear
)
