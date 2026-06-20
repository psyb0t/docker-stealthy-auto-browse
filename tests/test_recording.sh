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

test_recording_basic() {
    local outdir="$TESTDATA_DIR/recordings"

    # Wipe any leftover state from a previous test_recording_* run
    docker exec "$CONTAINER_NAME" bash -c 'rm -f /recordings/*.mp4' 2>/dev/null

    # Start a window recording at low fps to keep CPU/test time small
    local resp val
    resp=$(post '{"action": "start_recording", "mode": "window", "fps": 10}')
    assert_success "$resp" "start_recording" || return 1

    # Status should report active
    resp=$(post '{"action": "recording_status"}')
    val=$(echo "$resp" | json_get "['data']['active']")
    assert_eq "$val" "True" "recording_status: active during recording" || return 1

    # Let it capture a moment
    sleep 1.5

    # Stop with a slug
    resp=$(post '{"action": "stop_recording", "slug": "basic-test"}')
    assert_success "$resp" "stop_recording" || return 1

    # Status should be inactive
    resp=$(post '{"action": "recording_status"}')
    val=$(echo "$resp" | json_get "['data']['active']")
    assert_eq "$val" "False" "recording_status: inactive after stop" || return 1

    # File should exist, be non-trivial size, and be a valid MP4 container
    local size
    size=$(_rec_size "basic-test")
    if [ "$size" -lt 1024 ]; then
        echo "  FAIL: recording: file too small (size=$size)"
        return 1
    fi
    echo "  OK: recording: file size $size bytes"

    if ! _is_mp4 "basic-test"; then
        echo "  FAIL: recording: file is not a valid MP4 (no ftyp box)"
        return 1
    fi
    echo "  OK: recording: valid MP4 container"

    _rec_dump "basic-test" "$outdir"
}

test_recording_stop_without_start() {
    # Stop without an active recording = clean error response
    local resp err
    resp=$(post '{"action": "stop_recording", "slug": "noop"}')
    err=$(echo "$resp" | json_get "['error']")
    if [ -z "$err" ]; then
        echo "  FAIL: stop_recording: expected error when no recording is active, got: $resp"
        return 1
    fi
    echo "  OK: stop_recording: rejected without active recording"
}

test_recording_double_start() {
    # Second start while one is active = clean error response
    docker exec "$CONTAINER_NAME" bash -c 'rm -f /recordings/*.mp4' 2>/dev/null

    post '{"action": "start_recording", "mode": "window", "fps": 5}' >/dev/null
    local resp err
    resp=$(post '{"action": "start_recording", "mode": "window", "fps": 5}')
    err=$(echo "$resp" | json_get "['error']")
    post '{"action": "stop_recording", "slug": "cleanup"}' >/dev/null
    if [ -z "$err" ]; then
        echo "  FAIL: start_recording: expected error on double-start, got: $resp"
        return 1
    fi
    echo "  OK: start_recording: rejected concurrent second start"
}

test_recording_bad_slug() {
    docker exec "$CONTAINER_NAME" bash -c 'rm -f /recordings/*.mp4' 2>/dev/null

    post '{"action": "start_recording", "mode": "window", "fps": 5}' >/dev/null
    sleep 0.5
    local resp err
    # Path-traversal attempt — must be rejected
    resp=$(post '{"action": "stop_recording", "slug": "../../etc/passwd"}')
    err=$(echo "$resp" | json_get "['error']")
    if [ -z "$err" ]; then
        echo "  FAIL: stop_recording: expected error on bad slug, got: $resp"
        # Clean up recording state
        post '{"action": "stop_recording", "slug": "cleanup"}' >/dev/null
        return 1
    fi
    echo "  OK: stop_recording: rejected slug with path traversal"
    # Recorder remains active since stop failed — clean it up with a valid slug
    post '{"action": "stop_recording", "slug": "cleanup"}' >/dev/null
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

test_recording_slug_collision() {
    docker exec "$CONTAINER_NAME" bash -c 'rm -f /recordings/*.mp4' 2>/dev/null

    # First recording → coll.mp4
    post '{"action": "start_recording", "mode": "window", "fps": 5}' >/dev/null
    sleep 0.8
    local resp path1 path2
    resp=$(post '{"action": "stop_recording", "slug": "coll"}')
    path1=$(echo "$resp" | json_get "['data']['path']")
    assert_eq "$path1" "/recordings/coll.mp4" "collision: first run path" || return 1

    # Second with same slug → coll-2.mp4
    post '{"action": "start_recording", "mode": "window", "fps": 5}' >/dev/null
    sleep 0.8
    resp=$(post '{"action": "stop_recording", "slug": "coll"}')
    path2=$(echo "$resp" | json_get "['data']['path']")
    assert_eq "$path2" "/recordings/coll-2.mp4" "collision: second run path"
}

ALL_TESTS+=(
    test_recording_basic
    test_recording_stop_without_start
    test_recording_double_start
    test_recording_bad_slug
    test_recording_slug_collision
)
