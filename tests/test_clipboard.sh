#!/bin/bash
# tests/test_clipboard.sh - X11 clipboard API and browser integration tests

readonly CLIPBOARD_MAX_BYTES=1048576

_clipboard_payload() {
    python3 -c 'import json,sys; print(json.dumps({"action":"set_clipboard","text":sys.argv[1]}))' "$1"
}

test_clipboard_roundtrip() {
    local text response actual characters bytes
    # shellcheck disable=SC2016 # The command substitution is clipboard test data.
    text=$'quotes: "single" and '\''double'\''\nUnicode: café λ 🙂\nshell: $(touch /tmp/never-created)'

    response=$(post "$(_clipboard_payload "$text")")
    assert_success "$response" "clipboard: set complex text" || return 1
    if docker logs "$CONTAINER_NAME" 2>&1 | grep -Fq "$text"; then
        echo "FAIL: clipboard contents appeared in container logs" >&2
        return 1
    fi

    response=$(post '{"action":"get_clipboard"}')
    assert_success "$response" "clipboard: get complex text" || return 1
    actual=$(echo "$response" | json_get "['data']['text']")
    characters=$(echo "$response" | json_get "['data']['length']")
    bytes=$(echo "$response" | json_get "['data']['bytes']")
    assert_eq "$actual" "$text" "clipboard: exact complex text" || return 1
    assert_eq "$characters" "$(printf '%s' "$text" | wc -m | tr -d ' ')" "clipboard: character count" || return 1
    assert_eq "$bytes" "$(printf '%s' "$text" | wc -c | tr -d ' ')" "clipboard: byte count" || return 1

    response=$(post "$(_clipboard_payload "")")
    assert_success "$response" "clipboard: set empty text" || return 1
    response=$(post '{"action":"get_clipboard"}')
    actual=$(echo "$response" | json_get "['data']['text']")
    assert_eq "$actual" "" "clipboard: empty text round-trip" || return 1

    echo "OK: clipboard_roundtrip"
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

test_clipboard_validation() {
    local entry label payload expected response error
    local cases=(
        'missing|{"action":"set_clipboard"}|text required'
        'null|{"action":"set_clipboard","text":null}|text must be a string'
        'boolean|{"action":"set_clipboard","text":true}|text must be a string'
        'number|{"action":"set_clipboard","text":42}|text must be a string'
        'object|{"action":"set_clipboard","text":{}}|text must be a string'
        'list|{"action":"set_clipboard","text":[]}|text must be a string'
    )

    for entry in "${cases[@]}"; do
        IFS='|' read -r label payload expected <<<"$entry"
        response=$(post "$payload")
        error=$(echo "$response" | json_get "['error']")
        assert_eq "$error" "$expected" "clipboard validation[$label]" || return 1
    done

    python3 - "$BASE" "$CLIPBOARD_MAX_BYTES" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1]
limit = int(sys.argv[2])

def post(text: str) -> dict:
    body = json.dumps({"action": "set_clipboard", "text": text}).encode()
    request = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)

at_limit = post("a" * limit)
assert at_limit["success"] is True, at_limit
assert at_limit["data"] == {"length": limit, "bytes": limit}, at_limit

over_limit = post("a" * (limit + 1))
assert over_limit["success"] is False, over_limit
assert over_limit["error"] == f"clipboard text exceeds {limit} bytes", over_limit
PY

    echo "OK: clipboard_validation"
}

test_clipboard_run_script() {
    local response actual text
    text="run_script clipboard should never be logged"
    response=$(post "$(python3 -c 'import json,sys; print(json.dumps({"action":"run_script","steps":[{"action":"set_clipboard","text":sys.argv[1]},{"action":"get_clipboard","output_id":"clipboard"}]}))' "$text")")
    assert_success "$response" "clipboard run_script" || return 1
    actual=$(echo "$response" | json_get "['data']['outputs']['clipboard']['text']")
    assert_eq "$actual" "$text" "clipboard run_script output" || return 1
    if docker logs "$CONTAINER_NAME" 2>&1 | grep -Fq "$text"; then
        echo "FAIL: run_script clipboard contents appeared in container logs" >&2
        return 1
    fi
    echo "OK: clipboard_run_script"
}

ALL_TESTS+=(
    test_clipboard_roundtrip
    test_clipboard_browser_copy_paste
    test_clipboard_validation
    test_clipboard_run_script
)
