#!/bin/bash
# tests/test_navigation.sh - Tests for navigation actions

test_refresh() {
    local resp url title

    resp=$(post '{"action": "refresh"}')
    assert_success "$resp" "refresh" || return 1

    url=$(echo "$resp" | json_get "['data']['url']")
    echo "$url" | grep -q "index.html" || { echo "  FAIL: refresh: expected index.html in URL, got $url"; return 1; }

    title=$(echo "$resp" | json_get "['data']['title']")
    assert_eq "$title" "Test Page" "refresh: title" || return 1
    echo "  OK: refresh (url=$url, title=$title)"
}

test_refresh_wait_until() {
    local resp url

    resp=$(post '{"action": "refresh", "wait_until": "load"}')
    assert_success "$resp" "refresh with wait_until=load" || return 1

    url=$(echo "$resp" | json_get "['data']['url']")
    echo "$url" | grep -q "index.html" || { echo "  FAIL: refresh_wait_until: expected index.html, got $url"; return 1; }
    echo "  OK: refresh with wait_until=load (url=$url)"
}

test_navigation_options_unit() {
    docker run --rm \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -v "$WORKDIR/tests/test_navigation_options.py:/tests/test_navigation_options.py:ro" \
        --entrypoint python \
        "$IMAGE_NAME:$TEST_TAG" \
        /tests/test_navigation_options.py || return 1
    echo "OK: navigation_options_unit (explicit timeout and retry controls)"
}

# --- Table-driven referer tests ---
# Each case: "label|referer_value|expected"
# Empty referer_value means no referer param sent. Expected is what the server echoes back.
REFERER_CASES=(
    "with_referer|https://www.google.com/search?q=test|https://www.google.com/search?q=test"
    "no_referer||"
)

_run_referer_case() {
    local label="$1" referer="$2" expected="$3"
    local referer_url="${WEB_BASE}/referer"

    local goto_json
    if [ -n "$referer" ]; then
        goto_json="{\"action\": \"goto\", \"url\": \"$referer_url\", \"referer\": \"$referer\"}"
    else
        goto_json="{\"action\": \"goto\", \"url\": \"$referer_url\"}"
    fi

    local resp
    resp=$(post "$goto_json")
    assert_success "$resp" "referer[$label]: goto" || return 1

    local text got_referer
    text=$(post '{"action": "get_text"}')
    got_referer=$(echo "$text" | python3 -c "import sys,json; t=json.load(sys.stdin)['data']['text']; d=json.loads(t); print(d['referer'])")
    assert_eq "$got_referer" "$expected" "referer[$label]: value" || return 1

    echo "  - $label: OK (referer='$got_referer')"
}

test_goto_referer() {
    local entry label referer expected
    for entry in "${REFERER_CASES[@]}"; do
        IFS='|' read -r label referer expected <<< "$entry"
        _run_referer_case "$label" "$referer" "$expected" || return 1
    done
    echo "OK: goto_referer (${#REFERER_CASES[@]} cases passed)"
}

ALL_TESTS+=(
    test_refresh
    test_refresh_wait_until
    test_navigation_options_unit
    test_goto_referer
)
