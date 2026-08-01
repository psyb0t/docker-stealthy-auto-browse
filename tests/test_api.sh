#!/bin/bash
# tests/test_api.sh - Basic API endpoint tests

test_health() {
    local resp
    resp=$(curl -sf "$BASE/health")
    assert_eq "$resp" "ok" "health: response body"
}

test_ping() {
    local resp msg
    resp=$(post '{"action": "ping"}')
    assert_success "$resp" "ping" || return 1
    msg=$(echo "$resp" | json_get "['data']['message']")
    assert_eq "$msg" "pong" "ping: message"
}

test_state() {
    local resp status
    resp=$(curl -sf "$BASE/state")
    status=$(echo "$resp" | json_get "['status']")
    assert_eq "$status" "ready" "state: status" || return 1
    # Verify response has expected fields
    echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'url' in d, 'missing url'
assert 'title' in d, 'missing title'
assert 'window_offset' in d, 'missing window_offset'
" || {
        echo "FAIL: state: missing expected fields"
        return 1
    }
    echo "OK: state (status=ready)"
}

test_goto() {
    local resp title
    resp=$(post "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
    assert_success "$resp" "goto" || return 1
    title=$(echo "$resp" | json_get "['data']['title']")
    assert_eq "$title" "Test Page" "goto: page title"
}

# --- Table-driven page content tests ---
# Each case: "label|action_json|field_path|expected_substring"
PAGE_CONTENT_CASES=(
    'get_text|{"action": "get_text"}|data.text|Submit'
    'get_html|{"action": "get_html"}|data.html|test-form'
    'get_html_input|{"action": "get_html"}|data.html|name-input'
    'eval_title|{"action": "eval", "expression": "document.title"}|data.result|Test Page'
    'get_page_info|{"action": "get_page_info"}|data.title|Test Page'
    'get_element|{"action": "get_element", "selector": "#name-input"}|data.tag|input'
    'get_elements|{"action": "get_elements", "selector": "input"}|data.elements.0.tag|input'
    'get_elements_default_limit|{"action": "get_elements", "selector": ".dom-list-item"}|data.count|20'
    'get_computed_style|{"action": "get_computed_style", "selector": "#name-input", "properties": ["display"]}|data.styles.display|inline-block'
)

PAGE_CONTENT_ERROR_CASES=(
    'get_element_missing_selector|{"action": "get_element"}|selector required'
    'get_elements_non_integer_limit|{"action": "get_elements", "selector": "input", "limit": "not-a-number"}|limit must be an integer'
    'get_elements_out_of_range_limit|{"action": "get_elements", "selector": "input", "limit": 101}|limit must be between 1 and 100'
    'get_computed_style_invalid_property|{"action": "get_computed_style", "selector": "input", "properties": ["color;display:none"]}|properties must be CSS property names'
)

test_page_content() {
    local entry label action_json field expected
    for entry in "${PAGE_CONTENT_CASES[@]}"; do
        IFS='|' read -r label action_json field expected <<<"$entry"
        local resp val
        resp=$(post "$action_json")
        assert_success "$resp" "$label" || return 1
        val=$(echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for k in '${field}'.split('.'):
    d = d[int(k)] if k.isdigit() else d[k]
print(d)
")
        echo "$val" | grep -q "$expected" || {
            echo "FAIL: $label: '$expected' not in response"
            return 1
        }
    done

    for entry in "${PAGE_CONTENT_ERROR_CASES[@]}"; do
        IFS='|' read -r label action_json expected <<<"$entry"
        local resp error
        resp=$(post "$action_json")
        error=$(echo "$resp" | python3 -c "import json, sys; print(json.load(sys.stdin).get('error', ''))")
        assert_eq "$error" "$expected" "$label" || return 1
    done

    echo "OK: page_content ($((${#PAGE_CONTENT_CASES[@]} + ${#PAGE_CONTENT_ERROR_CASES[@]})) cases passed)"
}

test_detect_challenge() {
    local absent_resp insert_resp detected_resp network_resp sentinel_before sentinel_after
    absent_resp=$(post "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
    assert_success "$absent_resp" "detect_challenge: goto fixture" || return 1

    absent_resp=$(post '{"action": "detect_challenge"}')
    assert_success "$absent_resp" "detect_challenge: absent response" || return 1
    echo "$absent_resp" | python3 -c '
import json
import sys

data = json.load(sys.stdin)["data"]
assert data == {"detected": False, "status": "absent", "matches": []}
' || return 1

    post '{"action": "enable_network_log"}' >/dev/null
    post '{"action": "clear_network_log"}' >/dev/null
    insert_resp=$(post '{"action": "eval", "expression": "window.addDocumentedChallengeFixtures()"}')
    assert_success "$insert_resp" "detect_challenge: insert documented dynamic fixtures" || return 1
    sentinel_before=$(post '{"action": "eval", "expression": "document.querySelector(\u0027#documented-challenge-fixtures\u0027).getAttribute(\u0027data-detection-sentinel\u0027)"}')
    assert_success "$sentinel_before" "detect_challenge: capture DOM sentinel" || return 1

    detected_resp=$(post '{"action": "detect_challenge"}')
    assert_success "$detected_resp" "detect_challenge: present response" || return 1
    echo "$detected_resp" | python3 -c '
import json
import sys

response = json.load(sys.stdin)
data = response["data"]
assert data["detected"] is True
assert data["status"] == "present"
matches = {item["vendor"]: item for item in data["matches"]}
assert set(matches) == {
    "altcha", "arkose", "aws_waf", "friendlycaptcha", "geetest", "hcaptcha",
    "recaptcha", "turnstile", "unknown",
}
assert matches["turnstile"]["confidence"] == "high"
assert {"iframe", "element", "script"}.issubset(matches["turnstile"]["locations"])
assert matches["recaptcha"]["confidence"] == "high"
assert matches["hcaptcha"]["confidence"] == "high"
assert matches["friendlycaptcha"]["confidence"] == "high"
assert matches["altcha"]["confidence"] == "high"
assert matches["arkose"]["confidence"] == "high"
assert matches["aws_waf"]["confidence"] == "high"
assert matches["geetest"]["confidence"] == "medium"
assert matches["unknown"]["confidence"] == "low"
assert all("fixture_auth_token" not in json.dumps(item) for item in matches.values())
assert "TEST_SITEKEY_DO_NOT_USE" not in json.dumps(data)
assert "TEST_PUBLIC_KEY_DO_NOT_USE" not in json.dumps(data)
' || return 1

    sentinel_after=$(post '{"action": "eval", "expression": "document.querySelector(\u0027#documented-challenge-fixtures\u0027).getAttribute(\u0027data-detection-sentinel\u0027)"}')
    printf "%s\n%s\n" "$sentinel_before" "$sentinel_after" | python3 -c '
import json
import sys

before, after = (json.loads(value)["data"]["result"] for value in sys.stdin.read().splitlines())
assert before == after == "unchanged"
' || return 1

    network_resp=$(post '{"action": "get_network_log"}')
    echo "$network_resp" | python3 -c '
import json
import sys

entries = json.load(sys.stdin)["data"]["log"]
vendor_hosts = {
    "arkoselabs.com", "challenges.cloudflare.com", "hcaptcha.com",
    "recaptcha.net", "google.com", "friendlycaptcha", "jsdelivr.net",
}
assert not any(any(host in json.dumps(entry) for host in vendor_hosts) for entry in entries)
' || return 1
    post '{"action": "disable_network_log"}' >/dev/null

    echo "OK: detect_challenge (absent, documented dynamic fixture catalogue, no DOM/network side effects)"
}

test_detect_challenge_raw_dom_selectors() {
    local result
    result=$(
        python3 - "$BASE" "$TEST_PAGE" <<'PYEOF'
import json
import sys
import urllib.request

base_url, test_page = sys.argv[1:]


def post(payload):
    request = urllib.request.Request(
        base_url + "/",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode())
    assert data["success"] is True, data
    return data["data"]


def assert_selector(name, expression, expected_vendor):
    post({"action": "goto", "url": test_page})
    post({"action": "eval", "expression": expression})
    result = post({"action": "detect_challenge"})
    vendors = {match["vendor"] for match in result["matches"]}
    if expected_vendor is None:
        assert result == {"detected": False, "status": "absent", "matches": []}, (name, result)
    else:
        assert result["detected"] is True, (name, result)
        assert vendors == {expected_vendor}, (name, result)
    assert "private-response-value" not in json.dumps(result), (name, result)


CASES = (
    (
        "iframe title",
        "const n=document.createElement('iframe');n.title='CAPTCHA verification';n.width='300';n.height='65';document.body.append(n)",
        "unknown",
    ),
    (
        "iframe challenge title",
        "const n=document.createElement('iframe');n.title='Challenge available';n.width='300';n.height='65';document.body.append(n)",
        "unknown",
    ),
    (
        "dialog aria label",
        "const n=document.createElement('div');n.setAttribute('role','dialog');n.setAttribute('aria-label','Human verification');n.style.width='300px';n.style.height='65px';document.body.append(n)",
        "unknown",
    ),
    (
        "data captcha",
        "const n=document.createElement('div');n.setAttribute('data-captcha','fixture');n.style.width='300px';n.style.height='65px';document.body.append(n)",
        "unknown",
    ),
    (
        "data challenge",
        "const n=document.createElement('div');n.setAttribute('data-challenge','fixture');n.style.width='300px';n.style.height='65px';document.body.append(n)",
        "unknown",
    ),
    (
        "hidden generic control",
        "const n=document.createElement('div');n.setAttribute('data-captcha','fixture');n.style.display='none';document.body.append(n)",
        None,
    ),
    (
        "visibility-hidden generic control",
        "const n=document.createElement('div');n.setAttribute('data-captcha','fixture');n.style.visibility='hidden';n.style.width='300px';n.style.height='65px';document.body.append(n)",
        None,
    ),
    (
        "zero-sized generic control",
        "const n=document.createElement('div');n.setAttribute('data-captcha','fixture');document.body.append(n)",
        None,
    ),
    (
        "iframe srcdoc content is ignored",
        "const n=document.createElement('iframe');n.srcdoc='<div data-captcha=\"inside-frame\"></div>';n.width='300';n.height='65';document.body.append(n)",
        None,
    ),
    (
        "reCAPTCHA named response",
        "const n=document.createElement('textarea');n.name='g-recaptcha-response';n.value='private-response-value';document.body.append(n)",
        "recaptcha",
    ),
    (
        "hCaptcha named response",
        "const n=document.createElement('textarea');n.name='h-captcha-response';n.value='private-response-value';document.body.append(n)",
        "hcaptcha",
    ),
)

for name, expression, expected_vendor in CASES:
    assert_selector(name, expression, expected_vendor)

print(f"OK: detect_challenge raw DOM selectors ({len(CASES)} cases passed)")
PYEOF
    ) || {
        echo "FAIL: detect_challenge raw DOM selectors"
        return 1
    }
    echo "$result"
}

test_get_interactive_elements() {
    local resp elements
    resp=$(post '{"action": "get_interactive_elements"}')
    assert_success "$resp" "get_interactive_elements" || return 1
    # Should find at least the 2 inputs + 1 button = 3 elements
    elements=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']['elements']))")
    if [ "$elements" -lt 3 ]; then
        echo "FAIL: get_interactive_elements: expected >= 3, got $elements"
        return 1
    fi
    echo "OK: get_interactive_elements ($elements elements found)"
}

test_get_resolution() {
    local resp w h
    resp=$(post '{"action": "get_resolution"}')
    assert_success "$resp" "get_resolution" || return 1

    # Default container runs at 1920x1080
    w=$(echo "$resp" | json_get "['data']['width']")
    h=$(echo "$resp" | json_get "['data']['height']")
    assert_eq "$w" "1920" "get_resolution: width" || return 1
    assert_eq "$h" "1080" "get_resolution: height" || return 1
    echo "OK: get_resolution (1920x1080 verified)"
}

test_calibrate() {
    local resp offset_x offset_y
    resp=$(post '{"action": "calibrate"}')
    assert_success "$resp" "calibrate" || return 1
    offset_x=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['window_offset']['x'])")
    offset_y=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['window_offset']['y'])")
    # x should be 0–1 (openbox may add a 1px border), y should be realistic chrome height (40-100px)
    if [ "$offset_x" -gt 1 ]; then
        echo "FAIL: calibrate: offset x=$offset_x outside expected range 0-1"
        return 1
    fi
    if [ "$offset_y" -lt 40 ] || [ "$offset_y" -gt 100 ]; then
        echo "FAIL: calibrate: offset y=$offset_y outside expected range 40-100"
        return 1
    fi
    echo "OK: calibrate (offset: $offset_x,$offset_y)"
}

ALL_TESTS+=(
    test_health
    test_ping
    test_state
    test_goto
    test_page_content
    test_detect_challenge
    test_detect_challenge_raw_dom_selectors
    test_get_interactive_elements
    test_get_resolution
    test_calibrate
)
