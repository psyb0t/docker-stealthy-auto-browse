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
" || { echo "FAIL: state: missing expected fields"; return 1; }
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
        IFS='|' read -r label action_json field expected <<< "$entry"
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
        echo "$val" | grep -q "$expected" || { echo "FAIL: $label: '$expected' not in response"; return 1; }
    done

    for entry in "${PAGE_CONTENT_ERROR_CASES[@]}"; do
        IFS='|' read -r label action_json expected <<< "$entry"
        local resp error
        resp=$(post "$action_json")
        error=$(echo "$resp" | python3 -c "import json, sys; print(json.load(sys.stdin).get('error', ''))")
        assert_eq "$error" "$expected" "$label" || return 1
    done

    echo "OK: page_content ($(( ${#PAGE_CONTENT_CASES[@]} + ${#PAGE_CONTENT_ERROR_CASES[@]} )) cases passed)"
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
    test_get_interactive_elements
    test_get_resolution
    test_calibrate
)
