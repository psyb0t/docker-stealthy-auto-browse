#!/bin/bash
# tests/test_recording.sh - ffmpeg screen recording tests
# Recorder is a global on the browser container, so these run sequentially
# against the main container. Files land in /recordings inside the container
# and we pull them via docker cp.

_rec_dump() {
    # Copy /recordings/{slug}.mp4 out of the test container into a host tmpdir
    local slug="$1"
    local outdir="$2"
    mkdir -p "$outdir"
    docker cp "$CONTAINER_NAME:/recordings/${slug}.mp4" "$outdir/${slug}.mp4" 2>/dev/null
}

_rec_size() {
    docker exec "$CONTAINER_NAME" stat -c '%s' "/recordings/$1.mp4" 2>/dev/null || echo 0
}

_is_mp4() {
    # MP4 has "ftyp" at offset 4. Check via dd inside the container.
    local slug="$1"
    local magic
    magic=$(docker exec "$CONTAINER_NAME" bash -c \
        "dd if=/recordings/${slug}.mp4 bs=1 count=4 skip=4 2>/dev/null")
    [ "$magic" = "ftyp" ]
}

test_recording_viewport_uses_calibration() {
    # viewport mode must capture using the calibrated window_offset
    # (mozInnerScreenX/Y) rather than a hardcoded chrome strip. Verify by:
    # 1. calibrating
    # 2. starting a viewport recording
    # 3. checking start_recording's capture_size matches xvfb_size - window_offset
    docker exec "$CONTAINER_NAME" bash -c 'rm -f /recordings/*.mp4' 2>/dev/null

    local resp ox oy cw ch xvfb_w xvfb_h expected_w expected_h
    resp=$(post '{"action": "calibrate"}')
    ox=$(echo "$resp" | json_get "['data']['window_offset']['x']")
    oy=$(echo "$resp" | json_get "['data']['window_offset']['y']")
    if [ -z "$ox" ] || [ -z "$oy" ]; then
        echo "  FAIL: calibrate did not return window_offset"
        return 1
    fi

    resp=$(post '{"action": "get_resolution"}')
    xvfb_w=$(echo "$resp" | json_get "['data']['width']")
    xvfb_h=$(echo "$resp" | json_get "['data']['height']")

    resp=$(post '{"action": "start_recording", "mode": "viewport", "fps": 5}')
    cw=$(echo "$resp" | json_get "['data']['capture_size']['width']")
    ch=$(echo "$resp" | json_get "['data']['capture_size']['height']")
    sleep 0.5
    post '{"action": "stop_recording", "slug": "viewport-test"}' >/dev/null

    # libx264 requires even dimensions, so capture_size may be rounded down by 1
    expected_w=$((xvfb_w - ox))
    expected_h=$((xvfb_h - oy))
    [ $((expected_w % 2)) -ne 0 ] && expected_w=$((expected_w - 1))
    [ $((expected_h % 2)) -ne 0 ] && expected_h=$((expected_h - 1))

    assert_eq "$cw" "$expected_w" \
        "viewport recording: width = xvfb_w - calibrated_x (= $expected_w)" || return 1
    assert_eq "$ch" "$expected_h" \
        "viewport recording: height = xvfb_h - calibrated_y (= $expected_h)"
}

# Regression: viewport recording reused the offset from the last calibrate,
# so after fullscreen it cropped off the top of the page.
test_recording_viewport_follows_fullscreen() {
    docker exec "$CONTAINER_NAME" bash -c 'rm -f /recordings/*.mp4' 2>/dev/null

    local resp cw ch xvfb_w xvfb_h fs
    post '{"action": "calibrate"}' >/dev/null
    post '{"action": "enter_fullscreen"}' >/dev/null
    sleep 1.5
    fs=$(post '{"action": "eval", "expression": "!!document.fullscreenElement"}' | json_get "['data']['result']")
    assert_eq "$fs" "True" "viewport recording: page is fullscreen" || return 1

    resp=$(post '{"action": "get_resolution"}')
    xvfb_w=$(echo "$resp" | json_get "['data']['width']")
    xvfb_h=$(echo "$resp" | json_get "['data']['height']")

    resp=$(post '{"action": "start_recording", "mode": "viewport", "fps": 5}')
    cw=$(echo "$resp" | json_get "['data']['capture_size']['width']")
    ch=$(echo "$resp" | json_get "['data']['capture_size']['height']")
    sleep 0.5
    post '{"action": "stop_recording", "slug": "viewport-fullscreen"}' >/dev/null
    post '{"action": "exit_fullscreen"}' >/dev/null

    # In fullscreen the page covers the whole screen, so nothing is cropped
    # (libx264 needs even sizes, so an odd screen size rounds down by 1).
    [ $((xvfb_w % 2)) -ne 0 ] && xvfb_w=$((xvfb_w - 1))
    [ $((xvfb_h % 2)) -ne 0 ] && xvfb_h=$((xvfb_h - 1))
    assert_eq "$cw" "$xvfb_w" "viewport recording after fullscreen: full width" || return 1
    assert_eq "$ch" "$xvfb_h" "viewport recording after fullscreen: full height" || return 1
    echo "  OK: viewport recording follows fullscreen without calibrate"
}

ALL_TESTS+=(
    test_recording_viewport_uses_calibration
    test_recording_viewport_follows_fullscreen
)
