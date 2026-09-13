#!/bin/bash
# tests/test_resolution.sh - Display resolution and persistent profile tests

# --- Table-driven resolution tests ---
# Each case: "label|WxH|use_viewport|check_browser|check_desktop"
# JS screen dims and API dims are always checked.
RESOLUTION_CASES=(
    "1280x720|1280x720|false|false|true"
    "800x600-viewport|800x600|true|true|true"
    "375x812-phone|375x812|true|true|false"
    "800x800-square|800x800|false|false|true"
)

_run_resolution_case() {
    local label="$1"
    local resolution="$2"
    local use_viewport="$3"
    local check_browser="$4"
    local check_desktop="$5"

    local w="${resolution%%x*}"
    local h="${resolution##*x}"
    local name="${CONTAINER_NAME}-res-${label}"
    local tmpdir="$TESTDATA_DIR/res-${label}"
    mkdir -p "$tmpdir"

    local docker_args=(-e "XVFB_RESOLUTION=${resolution}")
    if [ "$use_viewport" = "true" ]; then
        docker_args+=(-e USE_VIEWPORT=true)
    fi

    local ip base
    ip=$(start_extra_container "$name" "${docker_args[@]}")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: resolution[${label}]: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        return 1
    fi

    post_to "$base" "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}" >/dev/null
    sleep 1

    # JS screen dimensions
    local resp js_w js_h
    resp=$(post_to "$base" '{"action": "eval", "expression": "screen.width"}')
    js_w=$(echo "$resp" | json_get "['data']['result']")
    resp=$(post_to "$base" '{"action": "eval", "expression": "screen.height"}')
    js_h=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$js_w" "$w" "resolution[${label}]: JS screen.width" || { stop_extra_container "$name"; return 1; }
    assert_eq "$js_h" "$h" "resolution[${label}]: JS screen.height" || { stop_extra_container "$name"; return 1; }

    # Desktop screenshot
    if [ "$check_desktop" = "true" ]; then
        curl -sf "$base/screenshot/desktop" -o "$tmpdir/desktop.png"
        local dims
        dims=$(png_dimensions "$tmpdir/desktop.png")
        assert_eq "$dims" "${resolution}" "resolution[${label}]: desktop screenshot" || { stop_extra_container "$name"; return 1; }
    fi

    # Browser screenshot (only with USE_VIEWPORT)
    if [ "$check_browser" = "true" ]; then
        curl -sf "$base/screenshot/browser" -o "$tmpdir/browser.png"
        local browser_dims
        browser_dims=$(png_dimensions "$tmpdir/browser.png")
        assert_eq "$browser_dims" "${resolution}" "resolution[${label}]: browser screenshot" || { stop_extra_container "$name"; return 1; }
    fi

    # API
    resp=$(post_to "$base" '{"action": "get_resolution"}')
    local api_w api_h
    api_w=$(echo "$resp" | json_get "['data']['width']")
    api_h=$(echo "$resp" | json_get "['data']['height']")
    assert_eq "$api_w" "$w" "resolution[${label}]: API width" || { stop_extra_container "$name"; return 1; }
    assert_eq "$api_h" "$h" "resolution[${label}]: API height" || { stop_extra_container "$name"; return 1; }

    stop_extra_container "$name"
    echo "  - ${label}: OK (JS ${resolution}, desktop ${resolution}, API ${resolution})"
}

test_resolution_matrix() {
    local entry label res viewport check_browser
    for entry in "${RESOLUTION_CASES[@]}"; do
        IFS='|' read -r label res viewport check_browser check_desktop <<< "$entry"
        _run_resolution_case "$label" "$res" "$viewport" "$check_browser" "$check_desktop" || return 1
    done
    echo "OK: resolution_matrix (${#RESOLUTION_CASES[@]} cases passed)"
}

test_persistent_profile_resolution() {
    # Test that changing XVFB_RESOLUTION with a persistent profile works.
    # 1. Start container at 800x800, let it generate a fingerprint config
    # 2. Stop it, start a new container at 1920x1080 with the same profile volume
    # 3. Verify the new container reports 1920x1080 (not the old 800x800)
    local name1="${CONTAINER_NAME}-persist-1"
    local name2="${CONTAINER_NAME}-persist-2"
    local profile_dir="$TESTDATA_DIR/persist-userdata"
    local tmpdir="$TESTDATA_DIR/persist-screenshots"
    local fingerprint_payload='{"action":"eval","expression":"(() => { const c = document.createElement(\"canvas\"); const x = c.getContext(\"2d\"); const widths = {}; for (const family of [\"sans-serif\",\"serif\",\"monospace\"]) { x.font = \"32px \" + family; widths[family] = x.measureText(\"mmmmmmmmmmlliWWWWWWWWWW0123456789\").width; } const g = document.createElement(\"canvas\").getContext(\"webgl\"); const d = g.getExtension(\"WEBGL_debug_renderer_info\"); return {userAgent:navigator.userAgent,platform:navigator.platform,widths:widths,webgl:{vendor:g.getParameter(d.UNMASKED_VENDOR_WEBGL),renderer:g.getParameter(d.UNMASKED_RENDERER_WEBGL),extensions:g.getSupportedExtensions()}}; })()"}'
    mkdir -p "$profile_dir" "$tmpdir"

    # --- Phase 1: generate profile at 800x800 ---
    local ip base
    ip=$(start_extra_container "$name1" \
        -e "XVFB_RESOLUTION=800x800" \
        -v "$profile_dir:/userdata")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: persistent_profile_resolution: phase 1 API not ready"
        docker logs "$name1" 2>&1 | tail -20
        stop_extra_container "$name1"
        return 1
    fi

    # Verify profile was created
    if [ ! -f "$profile_dir/stealthy-auto-browse-props.json" ]; then
        echo "FAIL: persistent_profile_resolution: fingerprint config not created"
        stop_extra_container "$name1"
        return 1
    fi

    # Verify 800x800 via JS
    post_to "$base" "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}" >/dev/null
    sleep 1
    local resp js_w js_h phase1_fingerprint phase2_fingerprint
    resp=$(post_to "$base" '{"action": "eval", "expression": "screen.width"}')
    js_w=$(echo "$resp" | json_get "['data']['result']")
    resp=$(post_to "$base" '{"action": "eval", "expression": "screen.height"}')
    js_h=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$js_w" "800" "persistent_profile_resolution: phase 1 JS screen.width" || { stop_extra_container "$name1"; return 1; }
    assert_eq "$js_h" "800" "persistent_profile_resolution: phase 1 JS screen.height" || { stop_extra_container "$name1"; return 1; }
    phase1_fingerprint=$(post_to "$base" "$fingerprint_payload" | jq -cS '.data.result')
    if [ -z "$phase1_fingerprint" ] || [ "$phase1_fingerprint" = "null" ]; then
        echo "FAIL: persistent_profile_resolution: phase 1 fingerprint unavailable"
        stop_extra_container "$name1"
        return 1
    fi

    # Desktop screenshot should be 800x800
    curl -sf "$base/screenshot/desktop" -o "$tmpdir/desktop_800x800.png"
    local dims
    dims=$(png_dimensions "$tmpdir/desktop_800x800.png")
    assert_eq "$dims" "800x800" "persistent_profile_resolution: phase 1 desktop screenshot" || { stop_extra_container "$name1"; return 1; }

    stop_extra_container "$name1"

    # --- Phase 2: reuse profile at 1920x1080 ---
    ip=$(start_extra_container "$name2" \
        -v "$profile_dir:/userdata")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: persistent_profile_resolution: phase 2 API not ready"
        docker logs "$name2" 2>&1 | tail -20
        stop_extra_container "$name2"
        return 1
    fi

    post_to "$base" "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}" >/dev/null
    sleep 1

    # JS should report 1920x1080 (updated from persisted config)
    resp=$(post_to "$base" '{"action": "eval", "expression": "screen.width"}')
    js_w=$(echo "$resp" | json_get "['data']['result']")
    resp=$(post_to "$base" '{"action": "eval", "expression": "screen.height"}')
    js_h=$(echo "$resp" | json_get "['data']['result']")
    assert_eq "$js_w" "1920" "persistent_profile_resolution: phase 2 JS screen.width" || { stop_extra_container "$name2"; return 1; }
    assert_eq "$js_h" "1080" "persistent_profile_resolution: phase 2 JS screen.height" || { stop_extra_container "$name2"; return 1; }
    phase2_fingerprint=$(post_to "$base" "$fingerprint_payload" | jq -cS '.data.result')
    assert_eq "$phase2_fingerprint" "$phase1_fingerprint" \
        "persistent_profile_resolution: fingerprint surface after restart" || {
        stop_extra_container "$name2"
        return 1
    }

    # Desktop screenshot should be 1920x1080
    curl -sf "$base/screenshot/desktop" -o "$tmpdir/desktop_1920x1080.png"
    dims=$(png_dimensions "$tmpdir/desktop_1920x1080.png")
    assert_eq "$dims" "1920x1080" "persistent_profile_resolution: phase 2 desktop screenshot" || { stop_extra_container "$name2"; return 1; }

    # API should report 1920x1080
    resp=$(post_to "$base" '{"action": "get_resolution"}')
    local api_w api_h
    api_w=$(echo "$resp" | json_get "['data']['width']")
    api_h=$(echo "$resp" | json_get "['data']['height']")
    assert_eq "$api_w" "1920" "persistent_profile_resolution: phase 2 API width" || { stop_extra_container "$name2"; return 1; }
    assert_eq "$api_h" "1080" "persistent_profile_resolution: phase 2 API height" || { stop_extra_container "$name2"; return 1; }

    stop_extra_container "$name2"
    echo "OK: persistent_profile_resolution (800x800 -> 1920x1080 with same profile)"
}

ALL_TESTS+=(
    test_resolution_matrix
    test_persistent_profile_resolution
)
