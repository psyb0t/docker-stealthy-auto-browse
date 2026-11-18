#!/bin/bash
# tests/test_clipboard.sh - X11 clipboard API and browser integration tests

_clipboard_payload() {
    python3 -c 'import json,sys; print(json.dumps({"action":"set_clipboard","text":sys.argv[1]}))' "$1"
}

test_clipboard_browser_copy_paste() {
    local paste_text copy_text response actual
    paste_text="API to browser paste λ🙂"
    copy_text="browser copy to API café"

    response=$(post "$(_clipboard_payload "$paste_text")")
    assert_success "$response" "clipboard browser: seed paste text" || return 1
    assert_success "$(post '{"action":"click","selector":"#sys-input"}')" "clipboard browser: focus paste input" || return 1
    assert_success "$(post '{"action":"send_key","key":"ctrl+v","instant":true}')" "clipboard browser: paste" || return 1
    response=$(post '{"action":"eval","expression":"document.getElementById(\"sys-input\").value"}')
    actual=$(echo "$response" | json_get "['data']['result']")
    assert_eq "$actual" "$paste_text" "clipboard browser: pasted value" || return 1

    response=$(python3 -c 'import json,sys; print(json.dumps({"action":"fill","selector":"#name-input","value":sys.argv[1]}))' "$copy_text")
    assert_success "$(post "$response")" "clipboard browser: fill copy input" || return 1
    assert_success "$(post '{"action":"click","selector":"#name-input"}')" "clipboard browser: focus copy input" || return 1
    assert_success "$(post '{"action":"send_key","key":"ctrl+a","instant":true}')" "clipboard browser: select text" || return 1
    assert_success "$(post '{"action":"send_key","key":"ctrl+c","instant":true}')" "clipboard browser: copy text" || return 1
    response=$(post '{"action":"get_clipboard"}')
    actual=$(echo "$response" | json_get "['data']['text']")
    assert_eq "$actual" "$copy_text" "clipboard browser: copied value" || return 1

    echo "OK: clipboard_browser_copy_paste"
}

ALL_TESTS+=(
    test_clipboard_browser_copy_paste
)
