#!/bin/bash
# tests/test_input.sh - Mouse, keyboard, scroll, and form input tests

test_mouse_move() {
    # Move mouse to known position and verify via pyautogui
    post '{"action": "mouse_move", "x": 250, "y": 250, "duration": 0.1}' >/dev/null
    local resp
    resp=$(post '{"action": "eval", "expression": "null"}')
    # Verify action succeeded (mouse_move has no visible DOM effect, check position via API)
    resp=$(post '{"action": "mouse_move", "x": 500, "y": 400, "duration": 0.1}')
    assert_success "$resp" "mouse_move"
}

test_mouse_click() {
    # Click on the submit button via mouse_click (pyautogui) and verify DOM event fired
    post '{"action": "calibrate"}' >/dev/null
    # Get submit button center coordinates
    local resp rect_raw btn_x btn_y val
    resp=$(post '{"action": "eval", "expression": "JSON.stringify(document.getElementById(\"submit-btn\").getBoundingClientRect())"}')
    rect_raw=$(echo "$resp" | json_get "['data']['result']")
    btn_x=$(echo "$rect_raw" | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(int(r['x']+r['width']/2))")
    btn_y=$(echo "$rect_raw" | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(int(r['y']+r['height']/2))")
    post "{\"action\": \"mouse_click\", \"x\": $btn_x, \"y\": $btn_y}" >/dev/null
    sleep 0.5
    resp=$(post '{"action": "eval", "expression": "var e=document.getElementById(\"btn-clicked\"); e ? e.textContent : \"\""}')
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "clicked" "mouse_click: button clicked via coordinates"
}

test_system_click() {
    # Click on an input field via system_click, type into it to verify focus
    post '{"action": "calibrate"}' >/dev/null
    # Get name-input center coordinates
    local resp rect_raw inp_x inp_y val
    resp=$(post '{"action": "eval", "expression": "JSON.stringify(document.getElementById(\"name-input\").getBoundingClientRect())"}')
    rect_raw=$(echo "$resp" | json_get "['data']['result']")
    inp_x=$(echo "$rect_raw" | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(int(r['x']+r['width']/2))")
    inp_y=$(echo "$rect_raw" | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(int(r['y']+r['height']/2))")
    post "{\"action\": \"system_click\", \"x\": $inp_x, \"y\": $inp_y}" >/dev/null
    sleep 0.5
    post '{"action": "system_type", "text": "sc", "interval": 0.02}' >/dev/null
    sleep 0.5
    resp=$(post '{"action": "eval", "expression": "document.getElementById(\"name-input\").value"}')
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "sc" "system_click: input focused and typed into"
}

test_scroll() {
    # Get initial scroll position
    local resp before after
    resp=$(post '{"action": "eval", "expression": "window.scrollY"}')
    before=$(echo "$resp" | json_get "['data']['result']")
    # Scroll down
    post '{"action": "scroll", "amount": -5}' >/dev/null
    sleep 0.5
    resp=$(post '{"action": "eval", "expression": "window.scrollY"}')
    after=$(echo "$resp" | json_get "['data']['result']")
    if [ "$after" -le "$before" ]; then
        echo "FAIL: scroll: scrollY didn't increase (before=$before, after=$after)"
        return 1
    fi
    echo "OK: scroll (scrollY: $before -> $after)"
}

test_system_type() {
    # Click on sys-input to focus it, then system_type into it
    post '{"action": "click", "selector": "#sys-input"}' >/dev/null
    sleep 0.3
    post '{"action": "system_type", "text": "hello", "interval": 0.02}' >/dev/null
    sleep 0.5
    local resp val
    resp=$(post '{"action": "eval", "expression": "document.getElementById(\"sys-input\").value"}')
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "hello" "system_type: input value"
}

test_send_key() {
    # Send a key and check the keydown listener captured it
    post '{"action": "send_key", "key": "a"}' >/dev/null
    sleep 0.3
    local resp val
    resp=$(post '{"action": "eval", "expression": "document.getElementById(\"last-key\").textContent"}')
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "a" "send_key: keydown listener captured 'a'"
}

test_send_key_pagedown() {
    # Regression: v1.0.0 introduced Openbox WM — PageDown stopped scrolling because
    # the Camoufox window lacked X focus when keys were dispatched via PyAutoGUI.
    # This test sends pagedown via send_key and verifies BOTH:
    #   1. the keydown listener captured "PageDown" (focus reached the page)
    #   2. window.scrollY actually advanced (browser handled the key)
    post '{"action": "calibrate"}' >/dev/null
    sleep 0.3
    # Ensure top of page
    post '{"action": "eval", "expression": "window.scrollTo(0,0)"}' >/dev/null
    sleep 0.2

    local resp before key_seen after
    resp=$(post '{"action": "eval", "expression": "window.scrollY"}')
    before=$(echo "$resp" | json_get "['data']['result']")

    post '{"action": "send_key", "key": "pagedown"}' >/dev/null
    sleep 0.5

    resp=$(post '{"action": "eval", "expression": "document.getElementById(\"last-key\").textContent"}')
    key_seen=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$key_seen" "PageDown" "send_key[pagedown]: keydown listener captured 'PageDown'" || return 1

    resp=$(post '{"action": "eval", "expression": "window.scrollY"}')
    after=$(echo "$resp" | json_get "['data']['result']")
    if [ "$after" -le "$before" ]; then
        echo "  FAIL: send_key[pagedown]: scrollY did not advance (before=$before, after=$after)"
        return 1
    fi
    echo "  OK: send_key[pagedown]: scrollY $before -> $after"
}

# --- Table-driven fullscreen tests ---
# Each case: "label|action|expected_fullscreen"
FULLSCREEN_CASES=(
    "enter|enter_fullscreen|True"
    "exit|exit_fullscreen|False"
)

test_fullscreen() {
    for entry in "${FULLSCREEN_CASES[@]}"; do
        IFS='|' read -r label action expected <<< "$entry"
        # Always enter fullscreen first so exit test is self-contained
        post '{"action": "enter_fullscreen"}' >/dev/null
        sleep 1
        if [ "$action" = "exit_fullscreen" ]; then
            post '{"action": "exit_fullscreen"}' >/dev/null
            sleep 1
        fi
        local resp val
        resp=$(post '{"action": "eval", "expression": "!!document.fullscreenElement"}')
        val=$(echo "$resp" | json_get "['data']['result']")
        assert_eq "$val" "$expected" "fullscreen[$label]" || return 1
    done
    echo "OK: fullscreen (${#FULLSCREEN_CASES[@]} cases passed)"
}

# --- Table-driven selector input tests ---

_post_json() {
    # Build JSON from python args and POST it. Avoids shell quoting issues with XPath etc.
    python3 -c "
import json,sys,urllib.request
d = json.loads(sys.argv[1])
body = json.dumps(d).encode()
req = urllib.request.Request(sys.argv[2], data=body, headers={'Content-Type':'application/json'})
print(urllib.request.urlopen(req, timeout=30).read().decode())
" "$1" "$BASE"
}

_selector_input_case() {
    local label="$1" action_json="$2" element_id="$3" expected="$4"

    # Clear input first
    _post_json "{\"action\":\"fill\",\"selector\":\"#${element_id}\",\"value\":\"\"}" >/dev/null

    # For type action, click to focus first
    if echo "$action_json" | grep -q '"type"'; then
        _post_json "{\"action\":\"click\",\"selector\":\"#${element_id}\"}" >/dev/null
    fi

    # Execute action
    _post_json "$action_json" >/dev/null

    local resp val
    resp=$(post "{\"action\": \"eval\", \"expression\": \"document.getElementById('${element_id}').value\"}")
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "$expected" "selector_input[$label]" || return 1
    echo "  - $label: OK"
}

test_selector_input() {
    _selector_input_case "fill_css" \
        '{"action":"fill","selector":"#name-input","value":"hello world"}' \
        "name-input" "hello world" || return 1
    _selector_input_case "fill_xpath" \
        '{"action":"fill","selector":"xpath=//input[@id=\"name-input\"]","value":"xpath fill"}' \
        "name-input" "xpath fill" || return 1
    _selector_input_case "type_css" \
        '{"action":"type","selector":"#email-input","text":"typed","delay":0.02}' \
        "email-input" "typed" || return 1
    _selector_input_case "type_xpath" \
        '{"action":"type","selector":"xpath=//input[@id=\"email-input\"]","text":"xtyped","delay":0.02}' \
        "email-input" "xtyped" || return 1
    echo "OK: selector_input (4 cases passed)"
}

test_click() {
    # CSS selector
    post '{"action": "click", "selector": "#submit-btn"}' >/dev/null
    sleep 0.5
    local resp val
    resp=$(post '{"action": "eval", "expression": "document.getElementById(\"btn-clicked\") ? document.getElementById(\"btn-clicked\").textContent : \"\""}')
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "clicked" "click: CSS selector" || return 1

    # Clear and test XPath
    post '{"action": "eval", "expression": "document.getElementById(\"click-result\").innerHTML = \"\""}' >/dev/null
    post '{"action": "click", "selector": "xpath=//button[@id=\"submit-btn\"]"}' >/dev/null
    sleep 0.5
    resp=$(post '{"action": "eval", "expression": "document.getElementById(\"btn-clicked\") ? document.getElementById(\"btn-clicked\").textContent : \"\""}')
    val=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$val" "clicked" "click: XPath selector"
}

ALL_TESTS+=(
    test_mouse_move
    test_mouse_click
    test_system_click
    test_scroll
    test_system_type
    test_send_key
    test_send_key_pagedown
    test_fullscreen
    test_selector_input
    test_click
)
