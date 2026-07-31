#!/bin/bash
# tests/test_run_script.sh - run_script action tests (JSON steps, YAML, request lock)

test_run_script_json_steps() {
    local resp

    resp=$(post "{
        \"action\": \"run_script\",
        \"name\": \"test_json\",
        \"steps\": [
            {\"action\": \"goto\", \"url\": \"$TEST_PAGE\", \"wait_until\": \"load\"},
            {\"action\": \"get_text\", \"output_id\": \"text\"},
            {\"action\": \"eval\", \"expression\": \"document.title\", \"output_id\": \"title\"}
        ]
    }")
    assert_success "$resp" "run_script: json steps" || return 1

    local steps_ok name title text_len
    steps_ok=$(echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)['data']
print('true' if d['steps_executed'] == 3 and d['steps_total'] == 3 and d['success'] else 'false')
")
    assert_eq "$steps_ok" "true" "run_script: all 3 steps executed" || return 1

    name=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['name'])")
    assert_eq "$name" "test_json" "run_script: script name" || return 1

    title=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['outputs']['title']['result'])")
    assert_eq "$title" "Test Page" "run_script: output title" || return 1

    text_len=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']['outputs']['text']['text']))")
    if [ "$text_len" -lt 10 ]; then
        echo "FAIL: run_script: text output too short ($text_len)"
        return 1
    fi

    echo "OK: run_script_json_steps (3 steps, title=$title, text_len=$text_len)"
}

test_run_script_yaml() {
    local resp
    resp=$(python3 - "$BASE" "$TEST_PAGE" << 'PYEOF'
import json, sys, urllib.request
base, test_page = sys.argv[1], sys.argv[2]
yaml_content = f"""name: test_yaml
on_error: stop
steps:
  - action: goto
    url: {test_page}
    wait_until: load
  - action: eval
    expression: document.title
    output_id: title
"""
body = json.dumps({"action": "run_script", "yaml": yaml_content}).encode()
req = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=30).read().decode())
PYEOF
    )
    assert_success "$resp" "run_script: yaml" || return 1

    local name title
    name=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['name'])")
    assert_eq "$name" "test_yaml" "run_script: yaml name" || return 1

    title=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['outputs']['title']['result'])")
    assert_eq "$title" "Test Page" "run_script: yaml output title" || return 1

    echo "OK: run_script_yaml (name=$name, title=$title)"
}

test_run_script_on_error_stop() {
    local resp

    resp=$(post '{
        "action": "run_script",
        "on_error": "stop",
        "steps": [
            {"action": "ping"},
            {"action": "eval", "expression": "this will cause no error actually"},
            {"action": "wait_for_element", "selector": "#nonexistent-element-xyz", "timeout": 1},
            {"action": "ping"}
        ]
    }')
    assert_success "$resp" "run_script: on_error stop wrapper" || return 1

    local executed total script_success
    executed=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['steps_executed'])")
    total=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['steps_total'])")
    script_success=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['success'])")

    assert_eq "$script_success" "False" "run_script: on_error stop — script failed" || return 1

    if [ "$executed" -ge "$total" ]; then
        echo "FAIL: run_script: on_error stop should not run all steps (executed=$executed, total=$total)"
        return 1
    fi

    echo "OK: run_script_on_error_stop (executed=$executed/$total, success=$script_success)"
}

test_run_script_on_error_continue() {
    local resp

    resp=$(post '{
        "action": "run_script",
        "on_error": "continue",
        "steps": [
            {"action": "ping"},
            {"action": "wait_for_element", "selector": "#nonexistent-element-xyz", "timeout": 1},
            {"action": "ping", "output_id": "final_ping"}
        ]
    }')
    assert_success "$resp" "run_script: on_error continue wrapper" || return 1

    local executed total
    executed=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['steps_executed'])")
    total=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['steps_total'])")
    assert_eq "$executed" "$total" "run_script: on_error continue — all steps ran" || return 1

    local has_ping
    has_ping=$(echo "$resp" | python3 -c "import sys,json; print('true' if 'final_ping' in json.load(sys.stdin)['data'].get('outputs',{}) else 'false')")
    assert_eq "$has_ping" "true" "run_script: on_error continue — final step output collected" || return 1

    echo "OK: run_script_on_error_continue (executed=$executed/$total)"
}

test_run_script_no_steps() {
    local resp

    # No steps and no yaml — should fail
    resp=$(post '{"action":"run_script"}')
    local err
    err=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
    echo "$err" | grep -qi "required" || { echo "FAIL: run_script: no steps should error"; return 1; }

    echo "OK: run_script_no_steps (error=$err)"
}

test_run_script_control_flow() {
    local resp
    resp=$(python3 - "$BASE" "$TEST_PAGE" << 'PYEOF'
import json
import sys
import urllib.request

base, test_page = sys.argv[1:]
body = {
    "action": "run_script",
    "name": "control_flow_http",
    "steps": [
        {"action": "goto", "url": test_page, "wait_until": "domcontentloaded"},
        {
            "if": {
                "condition": {"type": "element", "selector": "#test-form", "state": "visible"},
                "then": [{"action": "eval", "expression": "'http-then'", "output_id": "branch"}],
                "else": [{"action": "eval", "expression": "'http-else'", "output_id": "branch"}],
            }
        },
        {"repeat": {"count": 2, "steps": [{"action": "sleep", "duration": 0.01}]}},
    ],
}
request = urllib.request.Request(
    base,
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)
print(urllib.request.urlopen(request, timeout=30).read().decode())
PYEOF
    )
    assert_success "$resp" "run_script: control flow HTTP wrapper" || return 1

    echo "$resp" | python3 -c '
import json
import sys

data = json.load(sys.stdin)["data"]
assert data["success"] is True
assert data["steps_executed"] == data["steps_total"] == 3
assert data["outputs"]["branch"]["result"] == "http-then"
assert data["step_results"][1]["data"]["branch"] == "then"
assert len(data["step_results"][2]["data"]["iterations"]) == 2
' || return 1

    local invalid_resp
    invalid_resp=$(post '{"action":"run_script","steps":[{"repeat":{"count":0,"steps":[{"action":"ping"}]}}]}')
    echo "$invalid_resp" | python3 -c '
import json
import sys

response = json.load(sys.stdin)
assert response["success"] is False
assert "repeat count" in response["error"]
' || return 1

    echo "OK: run_script_control_flow (nested controls plus schema rejection)"
}

test_request_lock() {
    # Send two 2s sleeps concurrently — if serialized, total >= 3.5s
    local start end total_ms

    start=$(date +%s%N)

    curl -sf --max-time 10 -X POST "$BASE" -H 'Content-Type: application/json' \
        -d '{"action":"sleep","duration":2}' > /dev/null &
    local pid1=$!

    sleep 0.1

    curl -sf --max-time 10 -X POST "$BASE" -H 'Content-Type: application/json' \
        -d '{"action":"sleep","duration":2}' > /dev/null &
    local pid2=$!

    wait $pid1
    wait $pid2

    end=$(date +%s%N)
    total_ms=$(( (end - start) / 1000000 ))

    if [ "$total_ms" -lt 3500 ]; then
        echo "FAIL: request_lock: two 2s sleeps took ${total_ms}ms (expected >= 3500ms if serialized)"
        return 1
    fi

    # Verify /health is not blocked during a locked request
    curl -sf --max-time 10 -X POST "$BASE" -H 'Content-Type: application/json' \
        -d '{"action":"sleep","duration":3}' > /dev/null &
    local pid3=$!
    sleep 0.2

    local health_start health_end health_ms health
    health_start=$(date +%s%N)
    health=$(curl -sf --max-time 5 "$BASE/health")
    health_end=$(date +%s%N)
    health_ms=$(( (health_end - health_start) / 1000000 ))
    wait $pid3

    assert_eq "$health" "ok" "request_lock: /health not blocked" || return 1

    if [ "$health_ms" -gt 500 ]; then
        echo "FAIL: request_lock: /health took ${health_ms}ms during locked request (expected < 500ms)"
        return 1
    fi

    echo "OK: request_lock (serialized=${total_ms}ms, health=${health_ms}ms)"
}

ALL_TESTS+=(
    test_run_script_json_steps
    test_run_script_yaml
    test_run_script_on_error_stop
    test_run_script_on_error_continue
    test_run_script_no_steps
    test_run_script_control_flow
    test_request_lock
)
