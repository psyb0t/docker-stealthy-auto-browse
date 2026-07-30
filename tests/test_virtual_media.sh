#!/bin/bash
# tests/test_virtual_media.sh - File-backed camera and microphone browser fixture test

test_virtual_media_fixture() (
    local name="${CONTAINER_NAME}-virtual-media"
    local disabled_name="${name}-disabled"
    local limited_name="${name}-limited"
    local auth_name="${name}-auth"
    local readonly_name="${name}-readonly"
    local media_dir
    media_dir=$(mktemp -d "$TESTDATA_DIR/virtual-media.XXXXXX") || return 1

    # shellcheck disable=SC2317 # ShellCheck does not trace handlers invoked by EXIT traps.
    cleanup_virtual_media() {
        stop_extra_container "$auth_name"
        stop_extra_container "$readonly_name"
        stop_extra_container "$limited_name"
        stop_extra_container "$disabled_name"
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
        -t 2 -c:v libvpx-vp9 /media/camera-a.webm; then
        echo "FAIL: virtual_media: could not create camera fixture"
        rm -rf "$media_dir"
        return 1
    fi

    if ! docker run --rm --entrypoint ffmpeg \
        -v "$media_dir:/media" \
        "$IMAGE_NAME:$TEST_TAG" \
        -hide_banner -loglevel error \
        -f lavfi -i sine=frequency=440:sample_rate=48000 \
        -t 2 -c:a pcm_s16le /media/microphone-a.wav; then
        echo "FAIL: virtual_media: could not create microphone fixture"
        rm -rf "$media_dir"
        return 1
    fi

    if ! docker run --rm --entrypoint ffmpeg \
        -v "$media_dir:/media" \
        "$IMAGE_NAME:$TEST_TAG" \
        -hide_banner -loglevel error \
        -f lavfi -i color=c=red:size=160x120:rate=15 \
        -t 2 -c:v libvpx-vp9 /media/camera-b.webm ||
        ! docker run --rm --entrypoint ffmpeg \
            -v "$media_dir:/media" \
            "$IMAGE_NAME:$TEST_TAG" \
            -hide_banner -loglevel error \
            -f lavfi -i color=c=black:size=160x120:rate=15 \
            -t 2 -c:v libvpx-vp9 /media/camera-c.webm ||
        ! docker run --rm --entrypoint ffmpeg \
            -v "$media_dir:/media" \
            "$IMAGE_NAME:$TEST_TAG" \
            -hide_banner -loglevel error \
            -f lavfi -i sine=frequency=880:sample_rate=48000 \
            -t 2 -c:a pcm_s16le /media/microphone-b.wav ||
        ! docker run --rm --entrypoint ffmpeg \
            -v "$media_dir:/media" \
            "$IMAGE_NAME:$TEST_TAG" \
            -hide_banner -loglevel error \
            -f lavfi -i sine=frequency=660:sample_rate=48000 \
            -t 2 -c:a libopus /media/microphone-upload.ogg; then
        echo "FAIL: virtual_media: could not create switchable media fixtures"
        return 1
    fi
    if ! ln -s /etc/passwd "$media_dir/escape.webm"; then
        echo "FAIL: virtual_media: could not create escape symlink fixture"
        return 1
    fi

    _assert_invalid_virtual_media_config() {
        local setting="$1" label="$2"
        if docker run --rm --entrypoint python \
            -e "$setting" \
            "$IMAGE_NAME:$TEST_TAG" \
            -c 'from browser import BrowserConfig; BrowserConfig.from_environment()' \
            >/dev/null 2>&1; then
            echo "FAIL: virtual_media: ${label} unexpectedly started"
            return 1
        fi
        echo "  OK: virtual_media: ${label} fails fast"
    }
    if ! _assert_invalid_virtual_media_config \
        "VIRTUAL_MEDIA_DYNAMIC=maybe" "invalid dynamic-mode value" ||
        ! _assert_invalid_virtual_media_config \
            "VIRTUAL_MEDIA_UPLOAD_MAX_BYTES=0" "non-positive upload limit"; then
        return 1
    fi

    local ip base response result
    ip=$(start_extra_container "$name" \
        -v "$media_dir:/media:rw" \
        -e VIRTUAL_MEDIA_DYNAMIC=true \
        -e VIRTUAL_CAMERA_FILE=/media/camera-a.webm \
        -e VIRTUAL_MICROPHONE_FILE=/media/microphone-a.wav)
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: virtual_media: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    response=$(post_to "$base" '{"action":"get_virtual_media_state"}')
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["success"], result
state = result["data"]
assert state["dynamic"], state
assert state["revision"] == 0, state
assert state["sources"] == {"camera": "camera-a.webm", "microphone": "microphone-a.wav"}, state
'; then
        echo "FAIL: virtual_media: initial state contract: $response"
        return 1
    fi

    response=$(post_to "$base" "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
    if ! assert_success "$response" "virtual_media: fixture navigation"; then
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    response=$(post_to "$base" '{"action":"eval","expression":"fetch(\"https://virtual-media.stealthy.invalid/state\", {cache: \"no-store\"}).then(response => response.json())"}')
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["success"], result
state = result["data"]["result"]
assert state["camera"], state
assert state["microphone"], state
assert state["dynamic"], state
'; then
        echo "FAIL: virtual_media: dynamic state route unavailable: $response"
        return 1
    fi

    response=$(post_to "$base" '{"action":"calibrate"}')
    if ! assert_success "$response" "virtual_media: calibrate OS input"; then
        return 1
    fi
    response=$(post_to "$base" '{"action": "get_element", "selector": "#media-start"}')
    local button_x button_y
    button_x=$(echo "$response" | python3 -c "import json, sys; rect=json.load(sys.stdin)['data']['boundingBox']; print(int(rect['x'] + rect['width'] / 2))")
    button_y=$(echo "$response" | python3 -c "import json, sys; rect=json.load(sys.stdin)['data']['boundingBox']; print(int(rect['y'] + rect['height'] / 2))")
    response=$(post_to "$base" "{\"action\": \"mouse_click\", \"x\": $button_x, \"y\": $button_y}")
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
assert result["audioLevel"] > 0.001, result
assert result["audioChunks"] > 0, result
assert result["audioBytes"] > 1000, result
assert result["videoTrackId"], result
assert result["audioTrackId"], result
'; then
        echo "FAIL: virtual_media: browser fixture result: $result"
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    local initial_measurement="$result"
    _measure_active_tracks() {
        local label="$1"
        response=$(post_to "$base" '{"action":"click","selector":"#media-measure"}')
        if ! assert_success "$response" "virtual_media: ${label} measure click"; then
            return 1
        fi
        for _ in $(seq 1 15); do
            response=$(post_to "$base" '{"action":"get_element","selector":"#media-result"}') || break
            result=$(echo "$response" | python3 -c "import json, sys; print(json.load(sys.stdin)['data']['text'])")
            if [[ "$result" == *'"status":"ok"'* ]]; then
                return 0
            fi
            sleep 1
        done
        echo "FAIL: virtual_media: ${label} browser measurement: ${result:-missing}"
        return 1
    }

    response=$(post_to "$base" '{"action":"set_virtual_media_source","kind":"camera","source":"camera-b.webm"}')
    if ! assert_success "$response" "virtual_media: select camera source"; then
        return 1
    fi
    response=$(post_to "$base" '{"action":"set_virtual_media_source","kind":"microphone","source":"microphone-b.wav"}')
    if ! assert_success "$response" "virtual_media: select microphone source"; then
        return 1
    fi
    if ! _measure_active_tracks "after existing-source switch"; then
        return 1
    fi
    local switched_measurement="$result"
    if ! BEFORE="$initial_measurement" AFTER="$switched_measurement" python3 -c '
import json
import os

before = json.loads(os.environ["BEFORE"])
after = json.loads(os.environ["AFTER"])
assert after["videoTrackId"] == before["videoTrackId"], (before, after)
assert after["audioTrackId"] == before["audioTrackId"], (before, after)
assert after["videoSignal"] != before["videoSignal"], (before, after)
assert after["audioLevel"] > 0.001, after
assert after["audioBytes"] > 1000, after
assert after["audioPeakBin"] != before["audioPeakBin"], (before, after)
'; then
        echo "FAIL: virtual_media: existing-source switch did not update active tracks"
        return 1
    fi

    response=$(UPLOAD_PATH="$media_dir/camera-c.webm" python3 -c '
import base64
import json
import os

with open(os.environ["UPLOAD_PATH"], "rb") as source:
    content_base64 = base64.b64encode(source.read()).decode("ascii")
print(json.dumps({
    "action": "upload_virtual_media",
    "kind": "camera",
    "filename": "camera.webm",
    "content_base64": content_base64,
    "activate": True,
}))
')
    response=$(post_to "$base" "$response")
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["success"], result
filename = result["data"]["filename"]
assert filename.endswith(".webm"), result
assert result["data"]["state"]["sources"]["camera"] == filename, result
'; then
        echo "FAIL: virtual_media: upload and activate camera: $response"
        return 1
    fi
    if ! _measure_active_tracks "after upload switch"; then
        return 1
    fi
    local uploaded_measurement="$result"
    if ! BEFORE="$switched_measurement" AFTER="$uploaded_measurement" python3 -c '
import json
import os

before = json.loads(os.environ["BEFORE"])
after = json.loads(os.environ["AFTER"])
assert after["videoTrackId"] == before["videoTrackId"], (before, after)
assert after["audioTrackId"] == before["audioTrackId"], (before, after)
assert after["videoSignal"] != before["videoSignal"], (before, after)
assert after["audioLevel"] > 0.001, after
assert after["audioBytes"] > 1000, after
'; then
        echo "FAIL: virtual_media: upload did not update active track"
        return 1
    fi

    response=$(UPLOAD_PATH="$media_dir/microphone-upload.ogg" python3 -c '
import base64
import json
import os

with open(os.environ["UPLOAD_PATH"], "rb") as source:
    content_base64 = base64.b64encode(source.read()).decode("ascii")
print(json.dumps({
    "action": "upload_virtual_media",
    "kind": "microphone",
    "filename": "microphone.ogg",
    "content_base64": content_base64,
    "activate": True,
}))
')
    response=$(post_to "$base" "$response")
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert result["success"], result
filename = result["data"]["filename"]
assert filename.endswith(".ogg"), result
assert result["data"]["state"]["sources"]["microphone"] == filename, result
'; then
        echo "FAIL: virtual_media: upload and activate microphone: $response"
        return 1
    fi
    if ! _measure_active_tracks "after audio upload switch"; then
        return 1
    fi
    local audio_uploaded_measurement="$result"
    if ! BEFORE="$uploaded_measurement" AFTER="$audio_uploaded_measurement" python3 -c '
import json
import os

before = json.loads(os.environ["BEFORE"])
after = json.loads(os.environ["AFTER"])
assert after["videoTrackId"] == before["videoTrackId"], (before, after)
assert after["audioTrackId"] == before["audioTrackId"], (before, after)
assert after["audioLevel"] > 0.001, after
assert after["audioBytes"] > 1000, after
assert after["audioPeakBin"] != before["audioPeakBin"], (before, after)
'; then
        echo "FAIL: virtual_media: audio upload did not update active track"
        return 1
    fi

    _assert_action_failure() {
        local action_payload="$1" label="$2"
        response=$(post_to "$base" "$action_payload")
        if echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert not result["success"], result
'; then
            echo "  OK: virtual_media: $label"
            return 0
        fi
        echo "FAIL: virtual_media: $label: $response"
        return 1
    }

    local wrong_stream_upload
    wrong_stream_upload=$(UPLOAD_PATH="$media_dir/microphone-upload.ogg" python3 -c '
import base64
import json
import os

with open(os.environ["UPLOAD_PATH"], "rb") as source:
    content_base64 = base64.b64encode(source.read()).decode("ascii")
print(json.dumps({
    "action": "upload_virtual_media",
    "kind": "camera",
    "filename": "camera.webm",
    "content_base64": content_base64,
}))
')
    if ! _assert_action_failure \
        '{"action":"set_virtual_media_source","kind":"speaker","source":"camera-a.webm"}' \
        "reject invalid kind" ||
        ! _assert_action_failure \
            '{"action":"set_virtual_media_source","kind":"camera","source":"../camera-a.webm"}' \
            "reject traversal source" ||
        ! _assert_action_failure \
            '{"action":"set_virtual_media_source","kind":"camera","source":"/media/camera-a.webm"}' \
            "reject absolute source" ||
        ! _assert_action_failure \
            '{"action":"set_virtual_media_source","kind":"camera","source":"missing.webm"}' \
            "reject missing source" ||
        ! _assert_action_failure \
            '{"action":"set_virtual_media_source","kind":"camera","source":"escape.webm"}' \
            "reject escaping source symlink" ||
        ! _assert_action_failure \
            '{"action":"upload_virtual_media","kind":"camera","filename":"../escape.webm","content_base64":"YQ=="}' \
            "reject traversal filename" ||
        ! _assert_action_failure \
            '{"action":"upload_virtual_media","kind":"camera","filename":"bad.webm","content_base64":"not-base64"}' \
            "reject malformed base64" ||
        ! _assert_action_failure \
            '{"action":"upload_virtual_media","kind":"camera","filename":"camera.webm","content_base64":"","activate":"true"}' \
            "reject non-boolean activation" ||
        ! _assert_action_failure "$wrong_stream_upload" "reject wrong stream kind"; then
        return 1
    fi

    local disabled_ip disabled_base
    disabled_ip=$(start_extra_container "$disabled_name" \
        -v "$media_dir:/media:ro" \
        -e VIRTUAL_MEDIA_DYNAMIC=false \
        -e VIRTUAL_CAMERA_FILE=/media/camera-a.webm)
    disabled_base="http://${disabled_ip}:${INTERNAL_PORT}"
    if ! wait_for_api "$disabled_base" 90; then
        echo "FAIL: virtual_media: disabled-mode API not ready"
        return 1
    fi
    response=$(post_to "$disabled_base" '{"action":"set_virtual_media_source","kind":"camera","source":"camera-b.webm"}')
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert not result["success"], result
'; then
        echo "FAIL: virtual_media: disabled mode accepted source selection: $response"
        return 1
    fi

    local readonly_ip readonly_base
    readonly_ip=$(start_extra_container "$readonly_name" \
        -v "$media_dir:/media:ro" \
        -e VIRTUAL_MEDIA_DYNAMIC=true)
    readonly_base="http://${readonly_ip}:${INTERNAL_PORT}"
    if ! wait_for_api "$readonly_base" 90; then
        echo "FAIL: virtual_media: read-only dynamic API not ready"
        return 1
    fi
    response=$(post_to "$readonly_base" '{"action":"set_virtual_media_source","kind":"camera","source":"camera-b.webm"}')
    if ! assert_success "$response" "virtual_media: read-only source selection"; then
        return 1
    fi
    response=$(post_to "$readonly_base" '{"action":"upload_virtual_media","kind":"camera","filename":"camera.webm","content_base64":"YQ=="}')
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert not result["success"], result
'; then
        echo "FAIL: virtual_media: read-only upload unexpectedly succeeded: $response"
        return 1
    fi

    local limited_ip limited_base
    limited_ip=$(start_extra_container "$limited_name" \
        -v "$media_dir:/media:rw" \
        -e VIRTUAL_MEDIA_DYNAMIC=true \
        -e VIRTUAL_MEDIA_UPLOAD_MAX_BYTES=4)
    limited_base="http://${limited_ip}:${INTERNAL_PORT}"
    if ! wait_for_api "$limited_base" 90; then
        echo "FAIL: virtual_media: limited-mode API not ready"
        return 1
    fi
    response=$(post_to "$limited_base" '{"action":"upload_virtual_media","kind":"camera","filename":"oversize.webm","content_base64":"MTIzNDU="}')
    if ! echo "$response" | python3 -c '
import json
import sys

result = json.load(sys.stdin)
assert not result["success"], result
'; then
        echo "FAIL: virtual_media: size limit accepted oversized upload: $response"
        return 1
    fi

    local auth_ip auth_base auth_code
    auth_ip=$(start_extra_container "$auth_name" \
        -v "$media_dir:/media:ro" \
        -e VIRTUAL_MEDIA_DYNAMIC=true \
        -e AUTH_TOKEN=test-token-only-not-a-secret)
    auth_base="http://${auth_ip}:${INTERNAL_PORT}"
    if ! wait_for_api "$auth_base" 90; then
        echo "FAIL: virtual_media: authenticated API not ready"
        return 1
    fi
    auth_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$auth_base" \
        -H "Content-Type: application/json" \
        -d '{"action":"set_virtual_media_source","kind":"camera","source":"camera-a.webm"}')
    if ! assert_eq "$auth_code" "401" "virtual_media: source action requires auth"; then
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
        "VIRTUAL_CAMERA_FILE=/media/camera-a.webm" \
        "video: true, audio: false" \
        "video"; then
        stop_extra_container "$name"
        rm -rf "$media_dir"
        return 1
    fi

    if ! _assert_single_virtual_source \
        "microphone-only" \
        "VIRTUAL_MICROPHONE_FILE=/media/microphone-a.wav" \
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
