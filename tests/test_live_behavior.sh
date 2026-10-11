#!/bin/bash
# tests/test_live_behavior.sh - Opt-in live checks against public bot-detection
# demos that score input behavior. They run through `make test-real` only and
# are not part of the default suite, because the sites can change or go down.
#
# Each test does the same task three ways on the shared browser: humanized
# OS-level input, Playwright DOM input, and raw pyautogui (instant straight
# moves, zero-hold clicks and keys). The humanized run must pass and both
# robotic runs must fail or score worse on the same IP and fingerprint, which
# isolates the input behavior from network and fingerprint reputation.

readonly LIVE_DBI_URL="https://deviceandbrowserinfo.com/are_you_a_bot_interactions"
readonly LIVE_FCAPTCHA_URL="https://www.webdecoy.com/product/fcaptcha-demo"
readonly LIVE_EMAIL="test.user@example.com"
# The login demo only submits a password of 8+ characters with one of > ! ? @ # $ &.
readonly LIVE_PASSWORD="not-a-real-password!"
readonly LIVE_MESSAGE="Hi, I would like to know more about pricing for a small team."
readonly LIVE_RESULT_WAIT_SECONDS=25
readonly LIVE_PAGE_SETTLE_SECONDS=3
readonly LIVE_SCROLL_ATTEMPTS=12
readonly LIVE_SCROLL_NOTCHES=-3
readonly LIVE_SCROLL_PAUSE_SECONDS=0.8
readonly LIVE_SCROLL_INTO_VIEW_SECONDS=0.3
readonly LIVE_VIEW_MARGIN_PX=150
# Where the pointer rests on the page before wheel scrolling: x y w h.
readonly LIVE_POINTER_REST="700 450 400 200"
# FCaptcha risk score: 0 is human, 1 is bot. Humanized runs scored 0.06-0.07,
# raw pyautogui 0.29 and Playwright 0.60 (blocked) when these tests were written.
readonly LIVE_FCAPTCHA_HUMAN_MAX_RISK=0.2
readonly LIVE_FCAPTCHA_MIN_MARGIN=0.1
readonly LIVE_X_ENV=(-u browser -e DISPLAY=:99 -e XAUTHORITY=/home/browser/.Xauthority)

readonly LIVE_DBI_EMAIL='document.getElementById("email")'
readonly LIVE_DBI_PASSWORD='document.getElementById("password")'
readonly LIVE_DBI_SUBMIT='document.querySelector("main form button")'
readonly LIVE_FC_CHECKBOX='document.querySelector("[role=checkbox]")'
readonly LIVE_FC_EMAIL='document.getElementById("demo-email")'
readonly LIVE_FC_MESSAGE='document.getElementById("demo-message")'
readonly LIVE_FC_SUBMIT_XPATH='//textarea[@id="demo-message"]/following::button[1]'
readonly LIVE_FC_SUBMIT="document.evaluate('$LIVE_FC_SUBMIT_XPATH', document, null, 9, null).singleNodeValue"

# --- Helpers ---

# _live_action <python dict literal>: POST an action, print the JSON response.
_live_action() {
    python3 -c '
import ast, json, sys, urllib.request
body = json.dumps(ast.literal_eval(sys.argv[1])).encode()
req = urllib.request.Request(sys.argv[2], data=body, headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=120).read().decode())
' "$1" "$BASE"
}

# _live_eval <expression>: evaluate in the page and print the result.
_live_eval() {
    python3 -c '
import json, sys, urllib.request
body = json.dumps({"action": "eval", "expression": sys.argv[1]}).encode()
req = urllib.request.Request(sys.argv[2], data=body, headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=120)).get("data", {}).get("result", ""))
' "$1" "$BASE"
}

# _live_box <element expression>: "cx cy w h viewport_height" in viewport px.
_live_box() {
    _live_eval "(() => { const r = ($1).getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2), Math.round(r.width), Math.round(r.height), innerHeight].join(' '); })()"
}

_live_open() {
    assert_success "$(_live_action "{'action': 'goto', 'url': '$1', 'wait_until': 'load'}")" "open $1" >/dev/null || return 1
    sleep "$LIVE_PAGE_SETTLE_SECONDS"
}

# _live_wheel_to <element expression>: rest the pointer on the page, then
# scroll with the mouse wheel until the element is comfortably in view.
_live_wheel_to() {
    local cx cy w h vh i rx ry rw rh
    read -r rx ry rw rh <<<"$LIVE_POINTER_REST"
    _live_action "{'action': 'mouse_move', 'x': $rx, 'y': $ry, 'w': $rw, 'h': $rh}" >/dev/null
    for ((i = 0; i < LIVE_SCROLL_ATTEMPTS; i++)); do
        read -r cx cy w h vh <<<"$(_live_box "$1")"
        if [ "$cy" -gt "$LIVE_VIEW_MARGIN_PX" ] && [ "$cy" -lt $((vh - LIVE_VIEW_MARGIN_PX)) ]; then
            return 0
        fi
        _live_action "{'action': 'scroll', 'amount': $LIVE_SCROLL_NOTCHES}" >/dev/null
        sleep "$LIVE_SCROLL_PAUSE_SECONDS"
    done
    echo "  FAIL: could not scroll the target into view"
    return 1
}

# _live_human_click <element expression>: humanized system_click on it.
_live_human_click() {
    local cx cy w h vh
    read -r cx cy w h vh <<<"$(_live_box "$1")"
    assert_success "$(_live_action "{'action': 'system_click', 'x': $cx, 'y': $cy, 'w': $w, 'h': $h}")" "system_click" >/dev/null
}

_live_human_type() {
    local body
    body=$(python3 -c 'import sys; print(repr({"action": "system_type", "text": sys.argv[1]}))' "$1")
    assert_success "$(_live_action "$body")" "system_type" >/dev/null
}

# _live_robot_fill <element expression> <text or empty> ...: for each element
# in turn, scroll it into view with JS, then click it and type with raw
# pyautogui: straight instant moves, zero-hold clicks and zero-hold keys.
# Each element is measured right before its click, because scrolling to the
# next one moves the ones before it.
_live_robot_fill() {
    local offset ox oy script cx cy w h vh output
    offset=$(_live_action "{'action': 'calibrate'}" |
        python3 -c 'import json, sys; o = json.load(sys.stdin)["data"]["window_offset"]; print(o["x"], o["y"])')
    read -r ox oy <<<"$offset"
    while [ $# -gt 0 ]; do
        _live_eval "(($1).scrollIntoView({block: 'center'}), 1)" >/dev/null
        sleep "$LIVE_SCROLL_INTO_VIEW_SECONDS"
        read -r cx cy w h vh <<<"$(_live_box "$1")"
        script="g.moveTo($cx + $ox, $cy + $oy); g.click()"
        if [ -n "$2" ]; then
            script+="; g.write($(python3 -c 'import sys; print(repr(sys.argv[1]))' "$2"))"
        fi
        # Output is shown only on failure; on success it is just pyautogui's
        # harmless Xlib xauth warning.
        if ! output=$(docker exec "${LIVE_X_ENV[@]}" "$CONTAINER_NAME" \
            python3 -c "import pyautogui as g; g.FAILSAFE = False; $script" 2>&1); then
            echo "  FAIL: raw pyautogui step failed: $output"
            return 1
        fi
        shift 2
    done
}

# _live_wait_eval <expression>: poll until it returns a non-empty value.
_live_wait_eval() {
    local value="" i
    for ((i = 0; i < LIVE_RESULT_WAIT_SECONDS; i++)); do
        value=$(_live_eval "$1")
        [ -n "$value" ] && break
        sleep 1
    done
    echo "$value"
}

# --- deviceandbrowserinfo.com login form ---

# "human|bot suspiciousClientSideBehavior superHumanSpeed", empty until shown.
readonly LIVE_DBI_VERDICT='(() => { const t = document.body.innerText; const v = t.match(/You are (human|a bot)!/); if (!v) return ""; const f = k => (t.match(new RegExp("\"" + k + "\": (true|false)")) || [])[1]; return [v[1] === "human" ? "human" : "bot", f("suspiciousClientSideBehavior"), f("superHumanSpeed")].join(" "); })()'
# The verdict and every signal the site reported, shown when a run is misjudged.
readonly LIVE_DBI_DETAILS='(() => { const t = document.body.innerText; const i = Math.max(t.indexOf("You are"), 0); const e = document.getElementById("email"), p = document.getElementById("password"); return JSON.stringify({url: location.href, email: e && e.value, password_length: p && p.value.length, focused: document.activeElement && document.activeElement.id}) + " " + t.slice(i, i + 3000); })()'

test_live_deviceandbrowserinfo_behavior() {
    local verdict

    _live_open "$LIVE_DBI_URL" || return 1
    _live_human_click "$LIVE_DBI_EMAIL" || return 1
    _live_human_type "$LIVE_EMAIL" || return 1
    _live_human_click "$LIVE_DBI_PASSWORD" || return 1
    _live_human_type "$LIVE_PASSWORD" || return 1
    _live_action "{'action': 'send_key', 'key': 'enter'}" >/dev/null
    verdict=$(_live_wait_eval "$LIVE_DBI_VERDICT")
    echo "  humanized: $verdict"
    if [ "$verdict" != "human false false" ]; then
        echo "  site details: $(_live_eval "$LIVE_DBI_DETAILS")"
    fi
    assert_eq "$verdict" "human false false" "deviceandbrowserinfo: humanized login is human" || return 1

    _live_open "$LIVE_DBI_URL" || return 1
    _live_action "{'action': 'fill', 'selector': '#email', 'value': '$LIVE_EMAIL'}" >/dev/null
    _live_action "{'action': 'fill', 'selector': '#password', 'value': '$LIVE_PASSWORD'}" >/dev/null
    _live_action "{'action': 'click', 'selector': 'main form button'}" >/dev/null
    verdict=$(_live_wait_eval "$LIVE_DBI_VERDICT")
    echo "  playwright: $verdict"
    assert_eq "${verdict%% *}" "bot" "deviceandbrowserinfo: Playwright login is a bot" || return 1

    _live_open "$LIVE_DBI_URL" || return 1
    _live_robot_fill "$LIVE_DBI_EMAIL" "$LIVE_EMAIL" "$LIVE_DBI_PASSWORD" "$LIVE_PASSWORD" "$LIVE_DBI_SUBMIT" ""
    verdict=$(_live_wait_eval "$LIVE_DBI_VERDICT")
    echo "  raw pyautogui: $verdict"
    assert_eq "${verdict%% *}" "bot" "deviceandbrowserinfo: raw pyautogui login is a bot" || return 1

    echo "OK: deviceandbrowserinfo scores humanized input as human and both robots as bots"
}

# --- FCaptcha demo (webdecoy.com) ---

# "verified" or "failed", empty while the widget is still checking.
readonly LIVE_FC_CHECKBOX_STATE='(() => { let s = document.querySelector("[role=checkbox]"); for (let i = 0; i < 4; i++) s = s.parentElement; const t = s.innerText; return /Verified!/.test(t) ? "verified" : /Verification failed/.test(t) ? "failed" : ""; })()'

test_live_fcaptcha_checkbox_behavior() {
    local state

    _live_open "$LIVE_FCAPTCHA_URL" || return 1
    _live_wheel_to "$LIVE_FC_CHECKBOX" || return 1
    _live_human_click "$LIVE_FC_CHECKBOX" || return 1
    state=$(_live_wait_eval "$LIVE_FC_CHECKBOX_STATE")
    echo "  humanized: $state"
    assert_eq "$state" "verified" "fcaptcha checkbox: humanized click verifies" || return 1

    _live_open "$LIVE_FCAPTCHA_URL" || return 1
    _live_action "{'action': 'click', 'selector': '[role=checkbox]'}" >/dev/null
    state=$(_live_wait_eval "$LIVE_FC_CHECKBOX_STATE")
    echo "  playwright: $state"
    assert_eq "$state" "failed" "fcaptcha checkbox: Playwright click fails" || return 1

    _live_open "$LIVE_FCAPTCHA_URL" || return 1
    _live_robot_fill "$LIVE_FC_CHECKBOX" ""
    state=$(_live_wait_eval "$LIVE_FC_CHECKBOX_STATE")
    echo "  raw pyautogui: $state"
    assert_eq "$state" "failed" "fcaptcha checkbox: raw pyautogui click fails" || return 1

    echo "OK: FCaptcha checkbox verifies humanized clicks and rejects both robots"
}

# "<risk> allowed|blocked", empty until the invisible check reports.
readonly LIVE_FC_RISK='(() => { const m = document.body.innerText.match(/([0-9.]+)\s*Risk Score[^\n]*\n?[^\n]*(allowed|blocked)/); return m ? m[1] + " " + m[2] : ""; })()'

# _live_more_risky <robot_score> <human_score>: robot exceeds human by the margin.
_live_more_risky() {
    python3 -c 'import sys; r, h, m = map(float, sys.argv[1:]); sys.exit(0 if r >= h + m else 1)' \
        "$1" "$2" "$LIVE_FCAPTCHA_MIN_MARGIN"
}

# _live_human_risk_ok <"score verdict">: allowed and at or under the ceiling.
_live_human_risk_ok() {
    local score="${1%% *}" verdict="${1##* }"
    [ -n "$score" ] && [ "$verdict" = "allowed" ] &&
        python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) <= float(sys.argv[2]) else 1)' \
            "$score" "$LIVE_FCAPTCHA_HUMAN_MAX_RISK"
}

test_live_fcaptcha_invisible_behavior() {
    local human robot

    _live_open "$LIVE_FCAPTCHA_URL" || return 1
    _live_wheel_to "$LIVE_FC_EMAIL" || return 1
    _live_human_click "$LIVE_FC_EMAIL" || return 1
    _live_human_type "$LIVE_EMAIL" || return 1
    _live_human_click "$LIVE_FC_MESSAGE" || return 1
    _live_human_type "$LIVE_MESSAGE" || return 1
    _live_human_click "$LIVE_FC_SUBMIT" || return 1
    human=$(_live_wait_eval "$LIVE_FC_RISK")
    echo "  humanized: $human"
    if ! _live_human_risk_ok "$human"; then
        echo "  FAIL: fcaptcha invisible: humanized risk '$human' is not an allowed score <= $LIVE_FCAPTCHA_HUMAN_MAX_RISK"
        return 1
    fi

    _live_open "$LIVE_FCAPTCHA_URL" || return 1
    _live_action "{'action': 'fill', 'selector': '#demo-email', 'value': '$LIVE_EMAIL'}" >/dev/null
    _live_action "{'action': 'fill', 'selector': '#demo-message', 'value': '$LIVE_MESSAGE'}" >/dev/null
    _live_action "{'action': 'click', 'selector': 'xpath=$LIVE_FC_SUBMIT_XPATH'}" >/dev/null
    robot=$(_live_wait_eval "$LIVE_FC_RISK")
    echo "  playwright: $robot"
    if [ -z "$robot" ] || ! _live_more_risky "${robot%% *}" "${human%% *}"; then
        echo "  FAIL: fcaptcha invisible: Playwright risk '$robot' is not above humanized ${human%% *} by $LIVE_FCAPTCHA_MIN_MARGIN"
        return 1
    fi

    _live_open "$LIVE_FCAPTCHA_URL" || return 1
    _live_robot_fill "$LIVE_FC_EMAIL" "$LIVE_EMAIL" "$LIVE_FC_MESSAGE" "$LIVE_MESSAGE" "$LIVE_FC_SUBMIT" ""
    robot=$(_live_wait_eval "$LIVE_FC_RISK")
    echo "  raw pyautogui: $robot"
    if [ -z "$robot" ] || ! _live_more_risky "${robot%% *}" "${human%% *}"; then
        echo "  FAIL: fcaptcha invisible: raw pyautogui risk '$robot' is not above humanized ${human%% *} by $LIVE_FCAPTCHA_MIN_MARGIN"
        return 1
    fi

    echo "OK: FCaptcha invisible mode scores humanized input lowest risk"
}
