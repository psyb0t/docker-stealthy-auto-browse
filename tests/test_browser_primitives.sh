#!/bin/bash
# tests/test_browser_primitives.sh - browser primitive integration tests

test_dom_targets_and_references() {
    local response element_id
    assert_success "$(post "{\"action\":\"goto\",\"url\":\"$TEST_PAGE\"}")" "primitives: goto" || return 1

    assert_success "$(post '{"action":"click","role":"button","name":"Role target"}')" "primitives: role click" || return 1
    response=$(post '{"action":"get_element","selector":"#primitive-result"}')
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "role-clicked" "primitives: role result" || return 1

    assert_success "$(post '{"action":"click","selector":"#shadow-host #shadow-button"}')" "primitives: shadow click" || return 1
    response=$(post '{"action":"get_element","selector":"#primitive-result"}')
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "shadow-clicked" "primitives: shadow result" || return 1

    assert_success "$(post '{"action":"click","frames":["#primitive-frame"],"selector":"#frame-button"}')" "primitives: frame click" || return 1
    response=$(post '{"action":"get_element","selector":"#primitive-result"}')
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "frame-clicked" "primitives: frame result" || return 1

    response=$(post '{"action":"create_element_reference","selector":"#role-button"}')
    assert_success "$response" "primitives: create reference" || return 1
    element_id=$(echo "$response" | json_get "['data']['element_id']")
    response=$(post "{\"action\":\"get_element\",\"element_id\":\"$element_id\"}")
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "Role target" "primitives: reference lookup" || return 1

    response=$(post '{"action":"get_accessibility_tree","selector":"#browser-primitives"}')
    assert_success "$response" "primitives: accessibility tree" || return 1
    echo "$response" | python3 -c 'import json,sys; assert "Role target" in json.load(sys.stdin)["data"]["snapshot"]' || return 1

    response=$(post '{"action":"get_by_role","role":"button","name":"Role target"}')
    assert_success "$response" "primitives: role lookup" || return 1
    assert_eq "$(echo "$response" | json_get "['data']['count']")" "1" "primitives: role lookup count" || return 1

    assert_success "$(post '{"action":"eval","expression":"setTimeout(() => document.getElementById(\"mutation-target\").textContent = \"changed\", 100)"}')" "primitives: schedule mutation" || return 1
    response=$(post '{"action":"wait_for_dom_change","selector":"#mutation-target","condition":"text","timeout":2}')
    assert_success "$response" "primitives: DOM change" || return 1
    assert_eq "$(echo "$response" | json_get "['data']['changed']")" "True" "primitives: DOM changed" || return 1

    assert_success "$(post '{"action":"drag_and_drop","source":{"selector":"#drag-source"},"target":{"selector":"#drag-target"}}')" "primitives: DOM drag" || return 1
    response=$(post '{"action":"get_element","selector":"#primitive-result"}')
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "dragged" "primitives: DOM drag result" || return 1
    assert_success "$(post '{"action":"system_drag","source":{"selector":"#drag-source"},"target":{"selector":"#drag-target"}}')" "primitives: system drag" || return 1
    response=$(post '{"action":"get_element","selector":"#primitive-result"}')
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "dragged" "primitives: system drag result" || return 1

    response=$(post '{"action":"click","selector":"#role-button","role":"button"}')
    assert_eq "$(echo "$response" | json_get "['success']")" "False" "primitives: conflicting target rejected" || return 1
    assert_success "$(post '{"action":"refresh","retry_count":0}')" "primitives: invalidate reference" || return 1
    response=$(post "{\"action\":\"get_element\",\"element_id\":\"$element_id\"}")
    assert_eq "$(echo "$response" | json_get "['success']")" "False" "primitives: stale reference rejected" || return 1
    echo "OK: dom_targets_and_references"
}

test_pointer_handoff_and_windows() {
    local response popup_id
    assert_success "$(post '{"action":"eval","expression":"window.pointerCounts={dbl:0,ctx:0,down:0,up:0}; const e=document.getElementById(\"role-button\"); e.ondblclick=()=>window.pointerCounts.dbl++; e.oncontextmenu=(x)=>{x.preventDefault();window.pointerCounts.ctx++}; e.onmousedown=()=>window.pointerCounts.down++; e.onmouseup=()=>window.pointerCounts.up++"}')" "primitives: pointer listeners" || return 1
    assert_success "$(post '{"action":"system_hover","selector":"#role-button"}')" "primitives: system hover" || return 1
    assert_success "$(post '{"action":"system_double_click","selector":"#role-button"}')" "primitives: system double click" || return 1
    assert_success "$(post '{"action":"system_right_click","selector":"#role-button"}')" "primitives: system right click" || return 1
    assert_success "$(post '{"action":"system_mouse_down","selector":"#role-button"}')" "primitives: mouse down" || return 1
    assert_success "$(post '{"action":"system_mouse_up"}')" "primitives: mouse up" || return 1
    response=$(post '{"action":"eval","expression":"window.pointerCounts"}')
    echo "$response" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]["result"]; assert d["dbl"] >= 1 and d["ctx"] >= 1 and d["down"] >= 3 and d["up"] >= 3, d' || return 1

    assert_success "$(post '{"action":"begin_handoff"}')" "primitives: begin handoff" || return 1
    response=$(post '{"action":"fill","selector":"#name-input","value":"blocked"}')
    assert_eq "$(echo "$response" | json_get "['error']")" "human handoff is active" "primitives: handoff blocks mutation" || return 1
    if ! docker exec --user browser -e HOME=/home/browser -e DISPLAY=:99 "$CONTAINER_NAME" \
        python3 -c 'import pyautogui; pyautogui.moveTo(650, 500, duration=0.2)'; then
        post '{"action":"end_handoff"}' >/dev/null
        return 1
    fi
    response=$(post '{"action":"handoff_status"}')
    assert_success "$(post '{"action":"end_handoff"}')" "primitives: end handoff" || return 1
    assert_success "$response" "primitives: handoff status" || return 1
    echo "$response" | python3 -c 'import json,sys; assert json.load(sys.stdin)["data"]["activity"]["pointer"] > 0' || return 1

    assert_success "$(post '{"action":"click","selector":"#popup-button"}')" "primitives: popup click" || return 1
    response=$(post '{"action":"wait_for_popup","after_id":0,"timeout":2}')
    assert_success "$response" "primitives: popup event" || return 1
    popup_id=$(echo "$response" | json_get "['data']['popup']['id']")
    test -n "$popup_id" || return 1
    response=$(post '{"action":"list_tabs"}')
    assert_success "$response" "primitives: list popup page" || return 1
    echo "$response" | python3 -c 'import json,sys; assert json.load(sys.stdin)["data"]["count"] >= 2' || return 1
    response=$(post '{"action":"list_windows"}')
    assert_success "$response" "primitives: list windows" || return 1
    echo "$response" | python3 -c 'import json,sys; assert json.load(sys.stdin)["data"]["count"] >= 1' || return 1
    assert_success "$(post '{"action":"focus_window","index":0}')" "primitives: focus window" || return 1
    assert_success "$(post '{"action":"close_window","index":1}')" "primitives: close window" || return 1
    echo "OK: pointer_handoff_and_windows"
}

test_network_permissions_events_and_capture() {
    local response
    assert_success "$(post '{"action":"set_permissions","permissions":["geolocation"],"origin":"http://localhost"}')" "primitives: permissions" || return 1
    assert_success "$(post '{"action":"set_geolocation","latitude":45.75,"longitude":21.23,"accuracy":10}')" "primitives: geolocation" || return 1

    assert_success "$(post '{"action":"set_network_rules","rules":[{"url":"**/intercept-me","action":"fulfill","status":200,"content_type":"text/html","body":"<title>Intercepted</title><p id=network-rule>worked</p>"}]}')" "primitives: set network rule" || return 1
    response=$(post '{"action":"goto","url":"http://fixture:8081/intercept-me","retry_count":0}')
    assert_success "$response" "primitives: intercepted navigation" || return 1
    assert_eq "$(echo "$response" | json_get "['data']['title']")" "Intercepted" "primitives: intercept result" || return 1
    assert_success "$(post '{"action":"clear_network_rules"}')" "primitives: clear network rules" || return 1
    assert_success "$(post "{\"action\":\"set_network_rules\",\"rules\":[{\"url\":\"**/redirect-me\",\"action\":\"redirect\",\"redirect_url\":\"$TEST_PAGE\"}]}")" "primitives: redirect rule" || return 1
    assert_success "$(post '{"action":"goto","url":"http://fixture:8081/redirect-me","retry_count":0}')" "primitives: redirected navigation" || return 1
    assert_success "$(post '{"action":"clear_network_rules"}')" "primitives: clear redirect rule" || return 1
    response=$(post '{"action":"set_network_rules","rules":[{"url":"**/*","action":"redirect","redirect_url":"file:///etc/passwd"}]}')
    assert_eq "$(echo "$response" | json_get "['success']")" "False" "primitives: unsafe redirect rejected" || return 1

    assert_success "$(post '{"action":"set_network_rules","rules":[{"url":"http://localhost/geolocation-test","action":"fulfill","status":200,"content_type":"text/html","body":"<title>Geolocation test</title>"}]}')" "primitives: geolocation page rule" || return 1
    assert_success "$(post '{"action":"goto","url":"http://localhost/geolocation-test","retry_count":0}')" "primitives: geolocation page" || return 1

    response=$(post '{"action":"eval","expression":"new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(p=>resolve({latitude:p.coords.latitude,longitude:p.coords.longitude}),e=>reject(e.message)))"}')
    assert_success "$response" "primitives: geolocation read" || return 1
    echo "$response" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]["result"]; assert d == {"latitude":45.75,"longitude":21.23}, d' || return 1
    assert_success "$(post '{"action":"clear_network_rules"}')" "primitives: clear geolocation rule" || return 1
    assert_success "$(post "{\"action\":\"goto\",\"url\":\"$TEST_PAGE\"}")" "primitives: return fixture" || return 1
    response=$(post '{"action":"set_geolocation","latitude":"nan","longitude":0}')
    assert_eq "$(echo "$response" | json_get "['success']")" "False" "primitives: non-finite geolocation rejected" || return 1

    assert_success "$(post '{"action":"clear_network_log"}')" "primitives: clear network log" || return 1
    assert_success "$(post '{"action":"enable_network_log","capture_response_bodies":true,"max_body_bytes":4096}')" "primitives: body capture" || return 1
    assert_success "$(post '{"action":"refresh","retry_count":0}')" "primitives: capture refresh" || return 1
    response=$(post '{"action":"get_network_log"}')
    echo "$response" | python3 -c 'import json,sys; items=json.load(sys.stdin)["data"]["log"]; assert any("body_base64" in x for x in items if x["type"]=="response")' || return 1
    response=$(post '{"action":"export_har"}')
    assert_success "$response" "primitives: export HAR" || return 1
    echo "$response" | python3 -c 'import json,sys; h=json.load(sys.stdin)["data"]["har"]["log"]; assert h["version"]=="1.2" and h["entries"]; assert all(isinstance(e["request"],dict) and isinstance(e["response"],dict) for e in h["entries"])' || return 1

    response=$(post '{"action":"get_events","types":["navigation"],"limit":10}')
    assert_success "$response" "primitives: get events" || return 1
    echo "$response" | python3 -c 'import json,sys; assert json.load(sys.stdin)["data"]["count"] >= 1' || return 1

    curl -sf "$BASE/screenshot/browser?selector=%23role-button" > /tmp/sab-element-shot.png || return 1
    python3 -c 'from PIL import Image; im=Image.open("/tmp/sab-element-shot.png"); assert im.format=="PNG" and im.width>0 and im.height>0' || return 1
    response=$(post '{"action":"save_screenshot","selector":"#role-button","whLargest":64}')
    assert_success "$response" "primitives: action element screenshot" || return 1
    response=$(post '{"action":"save_screenshot","full_page":true,"whLargest":128}')
    assert_success "$response" "primitives: full-page screenshot" || return 1

    response=$(post '{"action":"run_script","steps":[{"action":"get_by_role","role":"button","name":"Role target","output_id":"role"},{"action":"get_events","limit":5,"output_id":"events"}]}')
    assert_success "$response" "primitives: run_script surfaces" || return 1
    echo "OK: network_permissions_events_and_capture"
}

test_clipboard_mime() {
    local response png
    png="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    assert_success "$(post '{"action":"set_clipboard","format":"html","text":"<b>clipboard</b>"}')" "primitives: HTML clipboard set" || return 1
    response=$(post '{"action":"get_clipboard","format":"html"}')
    assert_eq "$(echo "$response" | json_get "['data']['text']")" "<b>clipboard</b>" "primitives: HTML clipboard" || return 1
    assert_success "$(post "{\"action\":\"set_clipboard\",\"format\":\"image/png\",\"content_base64\":\"$png\"}")" "primitives: PNG clipboard set" || return 1
    response=$(post '{"action":"get_clipboard","format":"image/png"}')
    assert_eq "$(echo "$response" | json_get "['data']['content_base64']")" "$png" "primitives: PNG clipboard" || return 1
    assert_success "$(post '{"action":"set_clipboard","format":"files","files":["/app/main.py"]}')" "primitives: file clipboard set" || return 1
    response=$(post '{"action":"get_clipboard","format":"files"}')
    echo "$response" | python3 -c 'import json,sys; assert json.load(sys.stdin)["data"]["files"] == ["file:///app/main.py"]' || return 1
    echo "OK: clipboard_mime"
}

ALL_TESTS+=(
    test_dom_targets_and_references
    test_pointer_handoff_and_windows
    test_network_permissions_events_and_capture
    test_clipboard_mime
)
