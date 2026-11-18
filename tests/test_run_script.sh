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
    resp=$(
        python3 - "$BASE" "$TEST_PAGE" <<'PYEOF'
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

test_run_script_control_flow() {
    local resp
    resp=$(
        python3 - "$BASE" "$TEST_PAGE" <<'PYEOF'
import json
import sys
import urllib.request

base, test_page = sys.argv[1:]
body = {
    "action": "run_script",
    "name": "control_flow_http",
    "steps": [
        {"action": "goto", "url": test_page, "wait_until": "domcontentloaded"},
        {"action": "eval", "expression": "window.addDocumentedChallengeFixtures()"},
        {
            "action": "eval",
            "expression": "document.querySelector('#documented-challenge-fixtures').style.marginTop='4000px'",
        },
        {"action": "detect_challenge", "scroll_into_view": True, "output_id": "challenge"},
        {
            "action": "eval",
            "expression": "(() => { const rect = document.querySelector('#documented-challenge-fixtures').getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= innerHeight })()",
            "output_id": "challenge_in_viewport",
        },
        {
            "if": {
                "condition": {"type": "output", "output_id": "challenge", "path": ["detected"], "equals": True},
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
assert data["steps_executed"] == data["steps_total"] == 7
assert data["outputs"]["challenge"]["status"] == "present"
assert data["outputs"]["challenge"]["scrolled_into_view"] is True
assert data["outputs"]["challenge_in_viewport"]["result"] is True
assert {match["vendor"] for match in data["outputs"]["challenge"]["matches"]} == {
    "altcha", "arkose", "aws_waf", "friendlycaptcha", "geetest", "hcaptcha",
    "recaptcha", "turnstile", "unknown",
}
assert "TEST_PUBLIC_KEY_DO_NOT_USE" not in json.dumps(data)
assert data["outputs"]["branch"]["result"] == "http-then"
assert data["step_results"][5]["data"]["branch"] == "then"
assert len(data["step_results"][6]["data"]["iterations"]) == 2
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

ALL_TESTS+=(
    test_run_script_json_steps
    test_run_script_yaml
    test_run_script_control_flow
)
