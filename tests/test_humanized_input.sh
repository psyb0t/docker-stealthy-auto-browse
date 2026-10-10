#!/bin/bash
# tests/test_humanized_input.sh - What pages and bot detectors see from the
# humanized OS-level input actions.
#
# Each test drives a real browser container through the HTTP API against
# tests/fixtures/input_probe.html, served by the input detector container
# (tests/apps/input_detectors). The page records every trusted input event;
# tests/input_checks.py judges the log against human motor data and the
# detectors (motion-attestation, Gaitcha) score it. A robotic control session
# (raw pyautogui: straight constant-speed moves, zero-hold clicks and keys,
# burst wheel) goes through the same checks and must be caught, which proves
# the checks can tell the difference.

readonly HI_DETECTORS_IMAGE="stealthy-auto-browse-input-detectors:test"
readonly HI_DETECTORS_PORT=8099
readonly HI_PROBE_PATH="/probe/input_probe.html"
readonly HI_RESULTS_DIR="$RESULTS_DIR/humanized-input"
readonly HI_API_WAIT_ATTEMPTS=90
readonly HI_PROBE_WAIT_ATTEMPTS=30
# Enough strokes that the rate checks below are stable run to run.
readonly HI_CLICK_COUNT=30
readonly HI_CLICK_ORDER_SEED=7
readonly HI_SCROLL_NOTCHES=8
readonly HI_HEALTH_MAX_SECONDS=1.0
readonly HI_HEALTH_POLLS=5
readonly HI_TARGET_IDS=(target-0 target-1 target-2 target-3 target-4 target-5 target-6 target-7)
readonly HI_TYPED_TEXT=$'Hello World, THIS is a test: 42% done (ok?)\nCafé ñandú costs €5 & more!'
# Three sentences: at the 2% typo rate the chance of no typo at all is ~1e-4.
readonly HI_TYPO_SENTENCE="the quick brown fox jumps over the lazy dog while the busy farmer counts sheep near the old stone bridge and hums a quiet song about rivers mountains forests and distant summer evenings"
readonly HI_TYPO_TEXT="$HI_TYPO_SENTENCE $HI_TYPO_SENTENCE $HI_TYPO_SENTENCE"
readonly HI_TYPO_INTERVAL=0.06
readonly HI_LONG_TEXT="steady typing keeps the request lock busy while health checks still answer quickly because input runs in a worker thread and never blocks the event loop of the server at all"
readonly HI_X_ENV=(-u browser -e DISPLAY=:99 -e XAUTHORITY=/home/browser/.Xauthority)

# --- Helpers ---

# _hi_post <json> <base>: POST through python so text needs no shell escaping.
_hi_post() {
    python3 -c '
import sys, urllib.request
req = urllib.request.Request(sys.argv[2], data=sys.argv[1].encode(), headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=300).read().decode())
' "$1" "$2"
}

# _hi_json <python literal>: print it as a JSON document.
_hi_json() {
    python3 -c 'import ast, json, sys; print(json.dumps(ast.literal_eval(sys.argv[1])))' "$1"
}

# _hi_eval_js <base> <expression>: evaluate in the page, print the result.
_hi_eval_js() {
    local body
    body=$(python3 -c 'import json, sys; print(json.dumps({"action": "eval", "expression": sys.argv[1]}))' "$2")
    _hi_post "$body" "$1" | json_get "['data']['result']"
}

_hi_ip() {
    docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$1"
}

# _hi_start_detectors <name>: build and start the detector container; prints its base URL.
_hi_start_detectors() {
    local name="$1" ip
    if ! docker build -q -t "$HI_DETECTORS_IMAGE" "$WORKDIR/tests/apps/input_detectors" >/dev/null; then
        echo "FAIL: humanized input: detector image build failed" >&2
        return 1
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --init --read-only \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --memory 512m --cpus 1 --pids-limit 256 \
        --tmpfs /tmp:rw,noexec,nosuid,size=64m \
        -v "$FIXTURES_DIR:/fixtures:ro" \
        "$HI_DETECTORS_IMAGE" >/dev/null
    EXTRA_CONTAINERS+=("$name")
    ip=$(_hi_ip "$name")
    if ! wait_for_server "http://$ip:$HI_DETECTORS_PORT/health" 30; then
        echo "FAIL: humanized input: detector server not ready" >&2
        docker logs --tail 20 "$name" >&2
        return 1
    fi
    echo "http://$ip:$HI_DETECTORS_PORT"
}

# _hi_start_browser <name>: start a browser container; prints its API base URL.
_hi_start_browser() {
    local name="$1" ip
    ip=$(start_extra_container "$name")
    if ! wait_for_api "http://$ip:8080" "$HI_API_WAIT_ATTEMPTS"; then
        echo "FAIL: humanized input: browser API not ready" >&2
        docker logs --tail 20 "$name" >&2
        return 1
    fi
    echo "http://$ip:8080"
}

# _hi_open_probe <base> <probe_url>: load the probe page fresh and calibrate.
_hi_open_probe() {
    local base="$1" url="$2" ready="" i
    assert_success "$(_hi_post "$(_hi_json "{'action': 'goto', 'url': '$url'}")" "$base")" "probe: goto" >/dev/null || return 1
    for ((i = 0; i < HI_PROBE_WAIT_ATTEMPTS; i++)); do
        ready=$(_hi_eval_js "$base" "!!(window.__probe && window.__probe.ready)")
        [ "$ready" = "True" ] && break
        sleep 0.5
    done
    if [ "$ready" != "True" ]; then
        echo "  FAIL: probe page never became ready"
        return 1
    fi
    if [ "$(_hi_eval_js "$base" "JSON.parse(window.__probe.harvest()).ma === null")" = "True" ]; then
        echo "  FAIL: motion-attestation collector did not load in the probe page"
        return 1
    fi
    assert_success "$(_hi_post '{"action": "calibrate"}' "$base")" "probe: calibrate" >/dev/null || return 1
}

# _hi_harvest <base> <file>: save what the probe page recorded.
_hi_harvest() {
    _hi_eval_js "$1" "window.__probe.harvest()" >"$2"
}

# _hi_score <detectors_container> <harvest> <scores>
_hi_score() {
    if ! docker exec -i "$1" node /app/score.mjs <"$2" >"$3"; then
        echo "  FAIL: detector scoring failed"
        return 1
    fi
}

# _hi_judge <mode> <harvest> <scores> <expect> [extra args...]
_hi_judge() {
    local mode="$1" harvest="$2" scores="$3" expect="$4"
    shift 4
    if ! python3 "$WORKDIR/tests/input_checks.py" "$mode" "$harvest" "$scores" --expect "$expect" "$@"; then
        echo "  FAIL: $mode session judged wrong (expected $expect)"
        return 1
    fi
    echo "  OK: $mode session judged $expect"
}

# _hi_target_rect <base> <id>: "cx cy w h" in viewport coords.
_hi_target_rect() {
    _hi_eval_js "$1" "(() => { const r = document.getElementById('$2').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, r.width, r.height].join(' '); })()"
}

# _hi_window_offset <base>: "x y" offset from viewport to screen coords.
_hi_window_offset() {
    _hi_post '{"action": "calibrate"}' "$1" |
        python3 -c 'import json, sys; o = json.load(sys.stdin)["data"]["window_offset"]; print(o["x"], o["y"])'
}

# _hi_robot <browser_container> <python>: run raw pyautogui inside the container.
_hi_robot() {
    docker exec "${HI_X_ENV[@]}" "$1" python3 -c "import pyautogui; pyautogui.FAILSAFE = False; $2"
}

# _hi_click_sequence: HI_CLICK_COUNT target ids in a shuffled order without
# immediate repeats, so the session is not a repeating pattern.
_hi_click_sequence() {
    python3 -c '
import random, sys
ids, count, seed = sys.argv[1].split(), int(sys.argv[2]), int(sys.argv[3])
rng, out = random.Random(seed), []
while len(out) < count:
    pick = rng.choice(ids)
    if not out or out[-1] != pick:
        out.append(pick)
print(" ".join(out))
' "${HI_TARGET_IDS[*]}" "$HI_CLICK_COUNT" "$HI_CLICK_ORDER_SEED"
}

# _hi_click_target <base> <id>: system_click a probe element with its size.
_hi_click_target() {
    local base="$1" id="$2" cx cy w h resp
    read -r cx cy w h <<<"$(_hi_target_rect "$base" "$id")"
    resp=$(_hi_post "$(_hi_json "{'action': 'system_click', 'x': $cx, 'y': $cy, 'w': $w, 'h': $h}")" "$base")
    if ! echo "$resp" | grep -qE '"success":\s*true'; then
        echo "  FAIL: system_click $id: $resp"
        return 1
    fi
}

_hi_field_value() {
    _hi_eval_js "$1" "document.getElementById('$2').value"
}

# _hi_type <base> <text> [extra params json]: system_type into the focused field.
_hi_type() {
    local body
    body=$(python3 -c 'import json, sys; d = {"action": "system_type", "text": sys.argv[1]}; d.update(json.loads(sys.argv[2] or "{}")); print(json.dumps(d))' "$2" "${3:-}")
    _hi_post "$body" "$1"
}

_hi_backspaces() {
    python3 -c 'import json, sys; h = json.load(open(sys.argv[1])); print(sum(1 for e in h["events"] if e["type"] == "keydown" and e["key"] == "Backspace"))' "$1"
}

# --- Tests ---

test_humanized_mouse() {
    local det_name="stealthy-auto-browse-test-hi-mouse-det"
    local browser_name="stealthy-auto-browse-test-hi-mouse"
    local det base probe id cx cy w h ox oy robot_moves="" sequence
    mkdir -p "$HI_RESULTS_DIR"

    det=$(_hi_start_detectors "$det_name") || return 1
    base=$(_hi_start_browser "$browser_name") || return 1
    probe="$det$HI_PROBE_PATH"
    read -r -a sequence <<<"$(_hi_click_sequence)"

    _hi_open_probe "$base" "$probe" || return 1
    for id in "${sequence[@]}"; do
        _hi_click_target "$base" "$id" || return 1
    done
    _hi_harvest "$base" "$HI_RESULTS_DIR/mouse-human.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/mouse-human.json" "$HI_RESULTS_DIR/mouse-human.scores.json" || return 1
    _hi_judge mouse "$HI_RESULTS_DIR/mouse-human.json" "$HI_RESULTS_DIR/mouse-human.scores.json" human || return 1

    # Robotic control: straight constant-speed moves, zero-hold clicks at dead centre.
    _hi_open_probe "$base" "$probe" || return 1
    read -r ox oy <<<"$(_hi_window_offset "$base")"
    for id in "${sequence[@]}"; do
        read -r cx cy w h <<<"$(_hi_target_rect "$base" "$id")"
        robot_moves+="pyautogui.moveTo($cx + $ox, $cy + $oy, duration=0.3); pyautogui.click(); "
    done
    _hi_robot "$browser_name" "$robot_moves" || {
        echo "  FAIL: robotic control session could not run"
        return 1
    }
    _hi_harvest "$base" "$HI_RESULTS_DIR/mouse-robot.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/mouse-robot.json" "$HI_RESULTS_DIR/mouse-robot.scores.json" || return 1
    _hi_judge mouse "$HI_RESULTS_DIR/mouse-robot.json" "$HI_RESULTS_DIR/mouse-robot.scores.json" robot || return 1

    stop_extra_container "$browser_name"
    stop_extra_container "$det_name"
    echo "OK: humanized mouse (human session passes, robotic control is caught)"
}

test_humanized_typing() {
    local det_name="stealthy-auto-browse-test-hi-typing-det"
    local browser_name="stealthy-auto-browse-test-hi-typing"
    local det base probe resp backspaces case id reason expected
    mkdir -p "$HI_RESULTS_DIR"

    det=$(_hi_start_detectors "$det_name") || return 1
    base=$(_hi_start_browser "$browser_name") || return 1
    probe="$det$HI_PROBE_PATH"

    # Capitals, symbols, a newline and non-ASCII characters, exactly as asked.
    _hi_open_probe "$base" "$probe" || return 1
    _hi_click_target "$base" textarea || return 1
    resp=$(_hi_type "$base" "$HI_TYPED_TEXT")
    assert_success "$resp" "system_type textarea" || return 1
    expected=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$HI_TYPED_TEXT")
    assert_eq "$(_hi_eval_js "$base" "JSON.stringify(document.getElementById('textarea').value)")" "$expected" "system_type: exact text incl. unicode" || return 1
    assert_success "$(_hi_post '{"action": "send_key", "key": "tab"}' "$base")" "send_key tab" >/dev/null || return 1
    _hi_harvest "$base" "$HI_RESULTS_DIR/typing-human.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/typing-human.json" "$HI_RESULTS_DIR/typing-human.scores.json" || return 1
    _hi_judge typing "$HI_RESULTS_DIR/typing-human.json" "$HI_RESULTS_DIR/typing-human.scores.json" human || return 1

    # Typos on a plain text field: corrected with Backspace, final value exact.
    _hi_open_probe "$base" "$probe" || return 1
    _hi_click_target "$base" text-input || return 1
    resp=$(_hi_type "$base" "$HI_TYPO_TEXT" "{\"typos\": true, \"interval\": $HI_TYPO_INTERVAL}")
    assert_success "$resp" "system_type typos" >/dev/null || return 1
    assert_eq "$(echo "$resp" | json_get "['data']['typos']")" "True" "typos applied on text field" || return 1
    assert_eq "$(_hi_field_value "$base" text-input)" "$HI_TYPO_TEXT" "typos: final value exact" || return 1
    _hi_harvest "$base" "$HI_RESULTS_DIR/typing-typos.json"
    backspaces=$(_hi_backspaces "$HI_RESULTS_DIR/typing-typos.json")
    if [ "$backspaces" -lt 1 ]; then
        echo "  FAIL: typos: no Backspace corrections in ${#HI_TYPO_TEXT} characters"
        return 1
    fi
    echo "  OK: typos corrected with $backspaces Backspace presses"

    # Typos are refused on risky fields and nothing is erased there.
    for case in "password|field_type" "maxlen|maxlength"; do
        IFS='|' read -r id reason <<<"$case"
        _hi_open_probe "$base" "$probe" || return 1
        _hi_click_target "$base" "$id" || return 1
        resp=$(_hi_type "$base" "secret value" '{"typos": true}')
        assert_success "$resp" "system_type typos on $id" >/dev/null || return 1
        assert_eq "$(echo "$resp" | json_get "['data']['typos']")" "False" "typos off on $id" || return 1
        assert_eq "$(echo "$resp" | json_get "['data']['typos_disabled_reason']")" "$reason" "typos reason on $id" || return 1
        assert_eq "$(_hi_field_value "$base" "$id")" "secret value" "$id value exact" || return 1
        _hi_harvest "$base" "$HI_RESULTS_DIR/typing-$id.json"
        assert_eq "$(_hi_backspaces "$HI_RESULTS_DIR/typing-$id.json")" "0" "no Backspace on $id" || return 1
    done
    echo "  OK: typos refused on password and maxlength fields"

    # Robotic control: pyautogui.write, fixed interval and zero-hold keys.
    _hi_open_probe "$base" "$probe" || return 1
    _hi_click_target "$base" textarea || return 1
    _hi_robot "$browser_name" "pyautogui.write('Hello World, THIS is a robotic TEST of typing.', interval=0.05)" || {
        echo "  FAIL: robotic control session could not run"
        return 1
    }
    _hi_harvest "$base" "$HI_RESULTS_DIR/typing-robot.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/typing-robot.json" "$HI_RESULTS_DIR/typing-robot.scores.json" || return 1
    _hi_judge typing "$HI_RESULTS_DIR/typing-robot.json" "$HI_RESULTS_DIR/typing-robot.scores.json" robot || return 1

    stop_extra_container "$browser_name"
    stop_extra_container "$det_name"
    echo "OK: humanized typing (exact text, typo safety, robotic control caught)"
}

test_humanized_input_off_event_loop() {
    local browser_name="stealthy-auto-browse-test-hi-loop"
    local base typing_pid i elapsed slow=0
    base=$(_hi_start_browser "$browser_name") || return 1
    assert_success "$(_hi_post "$(_hi_json "{'action': 'goto', 'url': '$TEST_PAGE'}")" "$base")" "goto" >/dev/null || return 1
    assert_success "$(_hi_post '{"action": "click", "selector": "#sys-input"}' "$base")" "focus" >/dev/null || return 1

    _hi_type "$base" "$HI_LONG_TEXT" >/dev/null &
    typing_pid=$!
    sleep 1
    for ((i = 0; i < HI_HEALTH_POLLS; i++)); do
        if ! kill -0 "$typing_pid" 2>/dev/null; then
            echo "  FAIL: typing finished before health polling ended; text too short to prove anything"
            return 1
        fi
        elapsed=$(curl -sf -o /dev/null -w '%{time_total}' "$base/health")
        echo "  /health answered in ${elapsed}s while typing"
        if ! python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) <= float(sys.argv[2]) else 1)' "$elapsed" "$HI_HEALTH_MAX_SECONDS"; then
            slow=$((slow + 1))
        fi
        sleep 0.5
    done
    wait "$typing_pid"
    assert_eq "$slow" "0" "health stays responsive during system_type" || return 1
    assert_eq "$(_hi_eval_js "$base" "document.getElementById('sys-input').value")" "$HI_LONG_TEXT" "long text typed exactly" || return 1

    stop_extra_container "$browser_name"
    echo "OK: humanized input runs off the event loop"
}

test_humanized_scroll() {
    local det_name="stealthy-auto-browse-test-hi-scroll-det"
    local browser_name="stealthy-auto-browse-test-hi-scroll"
    local det base probe resp ox oy
    mkdir -p "$HI_RESULTS_DIR"

    det=$(_hi_start_detectors "$det_name") || return 1
    base=$(_hi_start_browser "$browser_name") || return 1
    probe="$det$HI_PROBE_PATH"

    _hi_open_probe "$base" "$probe" || return 1
    assert_success "$(_hi_post '{"action": "mouse_move", "x": 600, "y": 700, "w": 400, "h": 200}' "$base")" "pointer over page" >/dev/null || return 1
    resp=$(_hi_post "$(_hi_json "{'action': 'scroll', 'amount': -$HI_SCROLL_NOTCHES}")" "$base")
    assert_success "$resp" "scroll" >/dev/null || return 1
    sleep 0.5
    _hi_harvest "$base" "$HI_RESULTS_DIR/scroll-human.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/scroll-human.json" "$HI_RESULTS_DIR/scroll-human.scores.json" || return 1
    _hi_judge scroll "$HI_RESULTS_DIR/scroll-human.json" "$HI_RESULTS_DIR/scroll-human.scores.json" human --notches "$HI_SCROLL_NOTCHES" || return 1

    # Whole-page humanized scroll: reaches the bottom, comes back with Home.
    _hi_open_probe "$base" "$probe" || return 1
    resp=$(_hi_post '{"action": "scroll_to_bottom_humanized", "min_clicks": 3, "max_clicks": 6, "delay": 0.25}' "$base")
    assert_success "$resp" "scroll_to_bottom_humanized" >/dev/null || {
        echo "  FAIL: scroll_to_bottom_humanized: $resp"
        return 1
    }
    assert_eq "$(echo "$resp" | json_get "['data']['reached_bottom']")" "True" "reached bottom" || return 1
    assert_eq "$(echo "$resp" | json_get "['data']['returned_to_top']")" "True" "returned to top" || return 1
    assert_eq "$(_hi_eval_js "$base" "window.scrollY")" "0" "scrollY back to 0" || return 1
    _hi_harvest "$base" "$HI_RESULTS_DIR/scroll-page.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/scroll-page.json" "$HI_RESULTS_DIR/scroll-page.scores.json" || return 1
    _hi_judge scroll "$HI_RESULTS_DIR/scroll-page.json" "$HI_RESULTS_DIR/scroll-page.scores.json" human || return 1

    # Robotic control: every notch in one burst.
    _hi_open_probe "$base" "$probe" || return 1
    read -r ox oy <<<"$(_hi_window_offset "$base")"
    _hi_robot "$browser_name" "pyautogui.moveTo(600 + $ox, 700 + $oy); pyautogui.scroll(-$HI_SCROLL_NOTCHES)" || {
        echo "  FAIL: robotic control session could not run"
        return 1
    }
    sleep 0.5
    _hi_harvest "$base" "$HI_RESULTS_DIR/scroll-robot.json"
    _hi_score "$det_name" "$HI_RESULTS_DIR/scroll-robot.json" "$HI_RESULTS_DIR/scroll-robot.scores.json" || return 1
    _hi_judge scroll "$HI_RESULTS_DIR/scroll-robot.json" "$HI_RESULTS_DIR/scroll-robot.scores.json" robot --notches "$HI_SCROLL_NOTCHES" || return 1

    stop_extra_container "$browser_name"
    stop_extra_container "$det_name"
    echo "OK: humanized scroll (single spaced notches, page scroll, robotic burst caught)"
}

# _hi_median <harvest> <python expression over events e>: median of values.
_hi_median() {
    python3 -c '
import json, statistics, sys
events = json.load(open(sys.argv[1]))["events"]
values = eval(sys.argv[2], {"events": events})
print(round(statistics.median(values), 3))
' "$1" "$2"
}

# _hi_in_range <value> <lo> <hi> <name>
_hi_in_range() {
    if python3 -c 'import sys; v, lo, hi = map(float, sys.argv[1:4]); sys.exit(0 if lo <= v <= hi else 1)' "$1" "$2" "$3"; then
        echo "  OK: $4 = $1 (expected $2-$3)"
        return 0
    fi
    echo "  FAIL: $4 = $1 (expected $2-$3)"
    return 1
}

test_humanized_overrides() {
    local det_name="stealthy-auto-browse-test-hi-overrides-det"
    local browser_name="stealthy-auto-browse-test-hi-overrides"
    local det base probe id cx cy w h resp median
    mkdir -p "$HI_RESULTS_DIR"

    det=$(_hi_start_detectors "$det_name") || return 1
    base=$(_hi_start_browser "$browser_name") || return 1
    probe="$det$HI_PROBE_PATH"

    # click_hold: the page sees the requested hold.
    _hi_open_probe "$base" "$probe" || return 1
    # Bounds below are ~3 standard deviations of the sample median.
    for id in target-0 target-4 target-2 target-6 target-5 target-1 target-7 target-3 target-0 target-5 target-2 target-6; do
        read -r cx cy w h <<<"$(_hi_target_rect "$base" "$id")"
        resp=$(_hi_post "$(_hi_json "{'action': 'system_click', 'x': $cx, 'y': $cy, 'w': $w, 'h': $h, 'click_hold': 0.5, 'speed': 1.5}")" "$base")
        assert_success "$resp" "system_click with overrides" >/dev/null || return 1
    done
    _hi_harvest "$base" "$HI_RESULTS_DIR/overrides-click.json"
    median=$(_hi_median "$HI_RESULTS_DIR/overrides-click.json" "[u['t'] - d['t'] for d, u in zip([e for e in events if e['type'] == 'mousedown'], [e for e in events if e['type'] == 'mouseup'])]")
    _hi_in_range "$median" 330 760 "median click hold ms with click_hold=0.5" || return 1

    # key_hold: every key is held around the requested time.
    _hi_open_probe "$base" "$probe" || return 1
    _hi_click_target "$base" text-input || return 1
    # A wide interval keeps 0.25 s holds from colliding with the next key.
    resp=$(_hi_type "$base" "override check text" '{"key_hold": 0.25, "interval": 0.6}')
    assert_success "$resp" "system_type with overrides" >/dev/null || return 1
    assert_eq "$(_hi_field_value "$base" text-input)" "override check text" "typed exactly with overrides" || return 1
    _hi_harvest "$base" "$HI_RESULTS_DIR/overrides-typing.json"
    median=$(python3 -c '
import json, statistics, sys
events = json.load(open(sys.argv[1]))["events"]
downs, holds = {}, []
for e in events:
    if e["type"] == "keydown":
        downs[e["code"]] = e["t"]
    elif e["type"] == "keyup" and e["code"] in downs:
        holds.append(e["t"] - downs.pop(e["code"]))
print(round(statistics.median(holds), 1))
' "$HI_RESULTS_DIR/overrides-typing.json")
    _hi_in_range "$median" 180 340 "median key hold ms with key_hold=0.25" || return 1

    # instant: send_key falls back to a raw press with no hold.
    _hi_open_probe "$base" "$probe" || return 1
    _hi_click_target "$base" text-input || return 1
    assert_success "$(_hi_post '{"action": "send_key", "key": "x", "instant": true}' "$base")" "send_key instant" >/dev/null || return 1
    _hi_harvest "$base" "$HI_RESULTS_DIR/overrides-instant.json"
    median=$(_hi_median "$HI_RESULTS_DIR/overrides-instant.json" "(lambda d, u: [b['t'] - a['t'] for a, b in zip(d, u)])([e for e in events if e['type'] == 'keydown'], [e for e in events if e['type'] == 'keyup'])")
    _hi_in_range "$median" 0 15 "key hold ms with instant=true" || return 1

    # notch_gap: wheel notches arrive around the requested spacing.
    _hi_open_probe "$base" "$probe" || return 1
    assert_success "$(_hi_post '{"action": "mouse_move", "x": 600, "y": 700, "w": 400, "h": 200}' "$base")" "pointer over page" >/dev/null || return 1
    for _ in 1 2 3; do
        resp=$(_hi_post '{"action": "scroll", "amount": -4, "notch_gap": 0.3}' "$base")
        assert_success "$resp" "scroll with notch_gap" >/dev/null || return 1
        sleep 1.2
    done
    _hi_harvest "$base" "$HI_RESULTS_DIR/overrides-scroll.json"
    # Gaps over 1 s are the breaks between the three requests.
    median=$(_hi_median "$HI_RESULTS_DIR/overrides-scroll.json" "(lambda w: [b - a for a, b in zip(w, w[1:]) if b - a < 1000])([e['t'] for e in events if e['type'] == 'wheel'])")
    _hi_in_range "$median" 190 480 "median notch gap ms with notch_gap=0.3" || return 1

    # Out-of-range overrides are rejected before any input happens.
    resp=$(_hi_post '{"action": "system_click", "x": 10, "y": 10, "speed": 50}' "$base")
    assert_eq "$(echo "$resp" | json_get "['error']")" "speed must be between 0.1 and 10.0" "speed out of range rejected" || return 1

    stop_extra_container "$browser_name"
    stop_extra_container "$det_name"
    echo "OK: humanized input overrides reach the page"
}

ALL_TESTS+=(
    test_humanized_overrides
    test_humanized_mouse
    test_humanized_typing
    test_humanized_input_off_event_loop
    test_humanized_scroll
)
