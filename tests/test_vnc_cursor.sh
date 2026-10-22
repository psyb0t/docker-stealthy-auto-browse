#!/bin/bash
# tests/test_vnc_cursor.sh - noVNC remote pointer visibility tests

readonly VNC_CURSOR_MOVE_SETTLE_SECONDS=2
readonly VNC_CURSOR_MIN_CHANGED_PIXELS=20
readonly VNC_CURSOR_MAX_HIDDEN_CHANGED_PIXELS=4

_vnc_wait_for_canvas() {
    local viewer_base="$1"
    local response dimensions

    for _ in $(seq 1 30); do
        # shellcheck disable=SC2016 # JavaScript template literal, not shell expansion.
        if response=$(post_to "$viewer_base" '{"action":"eval","expression":"(() => { const c = document.querySelector(\"#noVNC_canvas\") || document.querySelector(\"canvas\"); return c ? `${c.width}x${c.height}` : \"\"; })()"}'); then
            if ! dimensions=$(echo "$response" | json_get "['data']['result']"); then
                dimensions=""
            fi
            if [[ "$dimensions" == "1920x1080" ]]; then
                return 0
            fi
        fi
        sleep 1
    done

    echo "FAIL: noVNC canvas did not reach 1920x1080" >&2
    return 1
}

_vnc_capture_canvas() {
    local viewer_base="$1"
    local output_path="$2"
    local response data_uri

    response=$(post_to "$viewer_base" '{"action":"eval","expression":"(() => { const c = document.querySelector(\"#noVNC_canvas\") || document.querySelector(\"canvas\"); return c ? c.toDataURL(\"image/png\") : \"\"; })()"}')
    assert_success "$response" "vnc cursor: capture noVNC canvas" >/dev/null || return 1
    data_uri=$(echo "$response" | json_get "['data']['result']")

    if [[ "$data_uri" != data:image/png\;base64,* ]]; then
        echo "FAIL: noVNC canvas capture was not a PNG data URI" >&2
        return 1
    fi

    printf '%s' "${data_uri#*,}" | python3 -c '
import base64
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_bytes(base64.b64decode(sys.stdin.buffer.read(), validate=True))
' "$output_path"
}

_vnc_changed_pixel_count() {
    python3 - "$1" "$2" <<'PY'
import sys

from PIL import Image, ImageChops

before = Image.open(sys.argv[1]).convert("RGB")
after = Image.open(sys.argv[2]).convert("RGB")
difference = ImageChops.difference(before, after)
pixels = difference.get_flattened_data()
print(sum(1 for pixel in pixels if max(pixel) > 8))
PY
}

_run_vnc_cursor_case() {
    local label="$1"
    local setting="$2"
    local expect_visible="$3"
    local target_name="${CONTAINER_NAME}-vnc-target-${label}"
    local viewer_name="${CONTAINER_NAME}-vnc-viewer-${label}"
    local case_dir="$TESTDATA_DIR/vnc-cursor-${label}"
    local target_ip target_base viewer_ip viewer_base response changed_pixels
    local target_args=()

    mkdir -p "$case_dir"
    if [[ -n "$setting" ]]; then
        target_args+=(-e "VNC_SHOW_CURSOR=${setting}")
    fi

    target_ip=$(start_extra_container "$target_name" "${target_args[@]}")
    target_base="http://${target_ip}:${INTERNAL_PORT}"
    if ! wait_for_api "$target_base" 90; then
        echo "FAIL: vnc cursor[$label]: target API not ready" >&2
        docker logs "$target_name" 2>&1 | tail -20 >&2
        stop_extra_container "$target_name"
        return 1
    fi

    viewer_ip=$(start_extra_container "$viewer_name" -e VNC_SHOW_CURSOR=false)
    viewer_base="http://${viewer_ip}:${INTERNAL_PORT}"
    if ! wait_for_api "$viewer_base" 90; then
        echo "FAIL: vnc cursor[$label]: viewer API not ready" >&2
        docker logs "$viewer_name" 2>&1 | tail -20 >&2
        stop_extra_container "$viewer_name"
        stop_extra_container "$target_name"
        return 1
    fi

    response=$(post_to "$target_base" '{"action":"goto","url":"data:text/html,<body style=\"margin:0;background:%2336587a;width:100vw;height:100vh;overflow:hidden\"></body>"}')
    assert_success "$response" "vnc cursor[$label]: open static target" || return 1

    response=$(post_to "$viewer_base" "{\"action\":\"goto\",\"url\":\"http://${target_ip}:5900/vnc.html?autoconnect=true&resize=scale&view_only=true\",\"wait_until\":\"load\"}")
    assert_success "$response" "vnc cursor[$label]: open noVNC viewer" || return 1
    _vnc_wait_for_canvas "$viewer_base" || return 1

    response=$(post_to "$target_base" '{"action":"mouse_move","x":200,"y":200,"speed":2}')
    assert_success "$response" "vnc cursor[$label]: first pointer position" || return 1
    sleep "$VNC_CURSOR_MOVE_SETTLE_SECONDS"
    _vnc_capture_canvas "$viewer_base" "$case_dir/before.png" || return 1

    response=$(post_to "$target_base" '{"action":"mouse_move","x":1400,"y":700,"speed":2}')
    assert_success "$response" "vnc cursor[$label]: second pointer position" || return 1
    sleep "$VNC_CURSOR_MOVE_SETTLE_SECONDS"
    _vnc_capture_canvas "$viewer_base" "$case_dir/after.png" || return 1

    changed_pixels=$(_vnc_changed_pixel_count "$case_dir/before.png" "$case_dir/after.png")
    if [[ "$expect_visible" == "true" && "$changed_pixels" -lt "$VNC_CURSOR_MIN_CHANGED_PIXELS" ]]; then
        echo "FAIL: vnc cursor[$label]: remote pointer did not appear in the noVNC framebuffer (${changed_pixels} changed pixels)" >&2
        return 1
    fi
    if [[ "$expect_visible" == "false" && "$changed_pixels" -gt "$VNC_CURSOR_MAX_HIDDEN_CHANGED_PIXELS" ]]; then
        echo "FAIL: vnc cursor[$label]: hidden pointer changed ${changed_pixels} noVNC framebuffer pixels" >&2
        return 1
    fi

    stop_extra_container "$viewer_name"
    stop_extra_container "$target_name"
    echo "  - ${label}: OK (${changed_pixels} changed framebuffer pixels)"
}

test_vnc_remote_cursor() {
    _run_vnc_cursor_case "default" "" true || return 1
    _run_vnc_cursor_case "enabled" "true" true || return 1
    _run_vnc_cursor_case "disabled" "false" false || return 1
    echo "OK: vnc_remote_cursor"
}

test_vnc_cursor_invalid_setting() {
    local name="${CONTAINER_NAME}-vnc-invalid"
    local exit_code logs

    docker rm -f "$name" >/dev/null 2>&1 || : # Exact test-owned name may remain after an interrupted run.
    docker run -d --name "$name" -e VNC_SHOW_CURSOR=yes "$IMAGE_NAME:$TEST_TAG" >/dev/null
    EXTRA_CONTAINERS+=("$name")

    for _ in $(seq 1 20); do
        if [[ "$(docker inspect -f '{{.State.Running}}' "$name")" == "false" ]]; then
            break
        fi
        sleep 0.25
    done

    exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$name")
    logs=$(docker logs "$name" 2>&1)
    assert_eq "$exit_code" "1" "vnc cursor invalid setting: exit code" || return 1
    assert_eq "$logs" "VNC_SHOW_CURSOR must be true or false" "vnc cursor invalid setting: error" || return 1

    stop_extra_container "$name"
    echo "OK: vnc_cursor_invalid_setting"
}

ALL_TESTS+=(
    test_vnc_remote_cursor
    test_vnc_cursor_invalid_setting
)
