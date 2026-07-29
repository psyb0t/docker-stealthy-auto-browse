#!/bin/bash
# tests/test_virtual_media.sh - File-backed camera and microphone browser fixture test

test_virtual_media_fixture() (
    local name="${CONTAINER_NAME}-virtual-media"
    local media_dir
    media_dir=$(mktemp -d "$TESTDATA_DIR/virtual-media.XXXXXX") || return 1

    # shellcheck disable=SC2317 # ShellCheck does not trace handlers invoked by EXIT traps.
    cleanup_virtual_media() {
        stop_extra_container "${name}-camera-only"
        stop_extra_container "${name}-microphone-only"
        stop_extra_container "$name"
        rm -rf -- "$media_dir"
    }
    trap cleanup_virtual_media EXIT

    if ! docker run --rm --entrypoint ffmpeg \
        -v "$media_dir:/media" \
        "$IMAGE_NAME:$TEST_TAG" \
        -hide_banner -loglevel error \
        -f lavfi -i testsrc2=size=160x120:rate=15 \
        -t 2 -c:v libvpx-vp9 /media/camera.webm; then
        echo "FAIL: virtual_media: could not create camera fixture"
        rm -rf "$media_dir"
        return 1
    fi

    if ! docker run --rm --entrypoint ffmpeg \
        -v "$media_dir:/media" \
        "$IMAGE_NAME:$TEST_TAG" \
        -hide_banner -loglevel error \
        -f lavfi -i sine=frequency=440:sample_rate=48000 \
        -t 2 -c:a pcm_s16le /media/microphone.wav; then
        echo "FAIL: virtual_media: could not create microphone fixture"
        rm -rf "$media_dir"
        return 1
    fi

    local ip base response result
    ip=$(start_extra_container "$name" \
        -v "$media_dir:/media:ro" \
        -e VIRTUAL_CAMERA_FILE=/media/camera.webm \
        -e VIRTUAL_MICROPHONE_FILE=/media/microphone.wav)
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: virtual_media: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    response=$(post_to "$base" "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
    if ! assert_success "$response" "virtual_media: fixture navigation"; then
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    response=$(post_to "$base" '{"action": "get_element", "selector": "#media-start"}')
    local button_x button_y
    button_x=$(echo "$response" | python3 -c "import json, sys; rect=json.load(sys.stdin)['data']['boundingBox']; print(int(rect['x'] + rect['width'] / 2))")
    button_y=$(echo "$response" | python3 -c "import json, sys; rect=json.load(sys.stdin)['data']['boundingBox']; print(int(rect['y'] + rect['height'] / 2))")
    response=$(post_to "$base" "{\"action\": \"system_click\", \"x\": $button_x, \"y\": $button_y}")
    if ! assert_success "$response" "virtual_media: start fixture with OS input"; then
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    for _ in $(seq 1 15); do
        response=$(post_to "$base" '{"action": "get_element", "selector": "#media-result"}') || break
        result=$(echo "$response" | python3 -c "import json, sys; print(json.load(sys.stdin)['data']['text'])")
        if [[ "$result" == *'"status":"ok"'* ]]; then
            break
        fi
        sleep 1
    done

    if ! echo "$result" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["status"] == "ok", result
assert result["videoWidth"] == 160, result
assert result["videoHeight"] == 120, result
assert result["videoSignal"] > 0, result
assert result["audioChunks"] > 0, result
assert result["audioBytes"] > 1000, result
'; then
        echo "FAIL: virtual_media: browser fixture result: $result"
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    _assert_single_virtual_source() {
        local label="$1" source_env="$2" success_constraints="$3" expected_kind="$4"
        local source_name="${name}-${label}" source_ip source_base response expression payload

        source_ip=$(start_extra_container "$source_name" \
            -v "$media_dir:/media:ro" \
            -e "$source_env")
        source_base="http://${source_ip}:${INTERNAL_PORT}"

        if ! wait_for_api "$source_base" 90; then
            echo "FAIL: virtual_media: ${label} API not ready"
            stop_extra_container "$source_name"
            return 1
        fi

        response=$(post_to "$source_base" "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
        if ! assert_success "$response" "virtual_media: ${label} fixture navigation"; then
            stop_extra_container "$source_name"
            return 1
        fi

        expression="(async () => { const stream = await navigator.mediaDevices.getUserMedia({${success_constraints}}); const kinds = stream.getTracks().map(track => track.kind); stream.getTracks().forEach(track => track.stop()); return kinds; })()"
        payload=$(EXPRESSION="$expression" python3 -c 'import json, os; print(json.dumps({"action": "eval", "expression": os.environ["EXPRESSION"]}))')
        response=$(post_to "$source_base" "$payload")
        if ! echo "$response" | EXPECTED_KIND="$expected_kind" python3 -c '
import json
import os
import sys

result = json.load(sys.stdin)
assert result["success"], result
assert result["data"]["result"] == [os.environ["EXPECTED_KIND"]], result
'; then
            echo "FAIL: virtual_media: ${label} configured source result: $response"
            stop_extra_container "$source_name"
            return 1
        fi

        expression="(async () => { try { const stream = await navigator.mediaDevices.getUserMedia({video: true, audio: true}); stream.getTracks().forEach(track => track.stop()); return 'unexpected-success'; } catch (error) { return error.name; } })()"
        payload=$(EXPRESSION="$expression" python3 -c 'import json, os; print(json.dumps({"action": "eval", "expression": os.environ["EXPRESSION"]}))')
        response=$(post_to "$source_base" "$payload")
        if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["success"], result
assert result["data"]["result"] == "NotFoundError", result
'; then
            echo "FAIL: virtual_media: ${label} missing source fallback: $response"
            stop_extra_container "$source_name"
            return 1
        fi

        stop_extra_container "$source_name"
    }

    if ! _assert_single_virtual_source \
        "camera-only" \
        "VIRTUAL_CAMERA_FILE=/media/camera.webm" \
        "video: true, audio: false" \
        "video"; then
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    if ! _assert_single_virtual_source \
        "microphone-only" \
        "VIRTUAL_MICROPHONE_FILE=/media/microphone.wav" \
        "video: false, audio: true" \
        "audio"; then
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    stop_extra_container "$name"
    rm -rf "$media_dir"
    echo "OK: virtual_media_fixture"
)

ALL_TESTS+=(test_virtual_media_fixture)
