#!/bin/bash
# tests/test_tabs.sh - Tab management tests

test_list_tabs() {
    local resp count
    resp=$(post '{"action": "list_tabs"}')
    assert_success "$resp" "list_tabs" || return 1
    count=$(echo "$resp" | json_get "['data']['count']")
    if [ "$count" -lt 1 ]; then
        echo "FAIL: list_tabs: expected >= 1 tab, got $count"
        return 1
    fi
    echo "OK: list_tabs (count=$count)"
}

test_new_tab() {
    local resp index count

    # Open new tab with URL
    resp=$(post "{\"action\": \"new_tab\", \"url\": \"$TEST_PAGE\"}")
    assert_success "$resp" "new_tab" || return 1
    index=$(echo "$resp" | json_get "['data']['index']")

    # Should now have 2+ tabs
    resp=$(post '{"action": "list_tabs"}')
    count=$(echo "$resp" | json_get "['data']['count']")
    if [ "$count" -lt 2 ]; then
        echo "FAIL: new_tab: expected >= 2 tabs, got $count"
        return 1
    fi
    echo "OK: new_tab (index=$index, total=$count)"
}

test_switch_tab() {
    local resp url

    # Switch to first tab (index 0)
    resp=$(post '{"action": "switch_tab", "index": 0}')
    assert_success "$resp" "switch_tab: to 0" || return 1

    # Verify it's active
    resp=$(post '{"action": "list_tabs"}')
    local active
    active=$(echo "$resp" | python3 -c "
import sys, json
tabs = json.load(sys.stdin)['data']['tabs']
for t in tabs:
    if t['active']:
        print(t['index'])
        break
")
    assert_eq "$active" "0" "switch_tab: active index" || return 1
    echo "OK: switch_tab (switched to tab 0)"
}

# Close every tab except index 0 so tests that assume a known tab layout
# aren't polluted by tabs left open by earlier tests in the shared container.
_reset_to_single_tab() {
    local count
    count=$(post '{"action": "list_tabs"}' | json_get "['data']['count']")
    while [ "$count" -gt 1 ]; do
        post "{\"action\": \"close_tab\", \"index\": $((count - 1))}" >/dev/null
        count=$(post '{"action": "list_tabs"}' | json_get "['data']['count']")
    done
}

# Sample the dominant color of the live desktop (Xvfb) via the container's PIL.
# Returns one of RED / BLUE / GREEN / <rgb-tuple>.
_desktop_dominant_color() {
    curl -sf "$BASE/screenshot/desktop?whLargest=300" -o "$TESTDATA_DIR/tabcolor.png"
    docker cp "$TESTDATA_DIR/tabcolor.png" "$CONTAINER_NAME:/tmp/tabcolor.png" >/dev/null
    docker exec "$CONTAINER_NAME" python3 -c "
from PIL import Image
from collections import Counter
im = Image.open('/tmp/tabcolor.png').convert('RGB')
w, h = im.size
c = Counter()
for x in range(0, w, 15):
    for y in range(0, h, 15):
        c[im.getpixel((x, y))] += 1
top = c.most_common(1)[0][0]
print({(255, 0, 0): 'RED', (0, 0, 255): 'BLUE', (0, 128, 0): 'GREEN'}.get(top, str(top)))
"
}

# Regression: switch_tab must FOREGROUND the tab's window (what Xvfb renders),
# not just redirect where Playwright verbs land. Firefox opens each page as a
# separate OS window and bring_to_front() is a no-op there, so without the
# xdotool windowactivate fix the desktop keeps showing the last-raised tab.
test_switch_tab_foreground() {
    _reset_to_single_tab
    post '{"action": "goto", "url": "data:text/html,<body style=background:red></body>", "wait_until": "domcontentloaded"}' >/dev/null
    sleep 0.5
    post '{"action": "new_tab", "url": "data:text/html,<body style=background:blue></body>"}' >/dev/null
    sleep 0.8

    local color
    post '{"action": "switch_tab", "index": 0}' >/dev/null
    sleep 0.8
    color=$(_desktop_dominant_color)
    assert_eq "$color" "RED" "switch_tab_foreground: tab 0 (red) is displayed" || return 1

    post '{"action": "switch_tab", "index": 1}' >/dev/null
    sleep 0.8
    color=$(_desktop_dominant_color)
    assert_eq "$color" "BLUE" "switch_tab_foreground: tab 1 (blue) is displayed" || return 1

    echo "OK: switch_tab_foreground (display follows active tab)"
}

# Regression: after switch_tab, OS-level keyboard input must reach the
# switched-to tab's content (windowactivate alone leaves Firefox content
# without X focus; the content-focus click fixes it).
test_switch_tab_keyboard() {
    local resp scrolled
    # Tall pages via data URL (single-quoted HTML attr keeps the JSON payload
    # valid — no embedded double quotes). No eval-injection needed.
    local tall="data:text/html,<body style='height:6000px'></body>"

    _reset_to_single_tab
    post "{\"action\": \"goto\", \"url\": \"$tall\", \"wait_until\": \"domcontentloaded\"}" >/dev/null
    post "{\"action\": \"new_tab\", \"url\": \"$tall\"}" >/dev/null
    sleep 0.5

    post '{"action": "switch_tab", "index": 0}' >/dev/null
    sleep 0.6
    post '{"action": "eval", "expression": "window.scrollTo(0,0)"}' >/dev/null
    post '{"action": "send_key", "key": "pagedown"}' >/dev/null
    sleep 0.5
    resp=$(post '{"action": "eval", "expression": "window.scrollY"}')
    scrolled=$(echo "$resp" | json_get "['data']['result']")
    if [ "$scrolled" -le 0 ]; then
        echo "FAIL: switch_tab_keyboard: tab 0 did not scroll (scrollY=$scrolled) — input not reaching content"
        return 1
    fi
    echo "OK: switch_tab_keyboard (send_key reached switched tab content, scrollY=$scrolled)"
}

# Regression: the focus click on switch_tab must NOT activate a link/button
# under the gesture point. A full-viewport <a> with an onclick marker covers
# screen (5,200); the transparent overlay injected before the focus click must
# absorb the click so the link never fires. Also confirms keyboard focus still
# reaches content.
test_switch_tab_no_link_activation() {
    local resp fired scrolled
    local linkpage="data:text/html,<body style='margin:0'><a onclick='window.__fired=1' style='display:block;width:100vw;height:300vh;background:khaki'>FULL VIEWPORT LINK</a></body>"

    _reset_to_single_tab
    post "{\"action\": \"goto\", \"url\": \"$linkpage\", \"wait_until\": \"domcontentloaded\"}" >/dev/null
    post '{"action": "new_tab", "url": "about:blank"}' >/dev/null
    sleep 0.5

    # switch to the link tab -> the focus click fires; the overlay must absorb it
    post '{"action": "switch_tab", "index": 0}' >/dev/null
    sleep 0.7
    resp=$(post '{"action": "eval", "expression": "window.__fired||0"}')
    fired=$(echo "$resp" | json_get "['data']['result']")
    if [ "$fired" != "0" ]; then
        echo "FAIL: switch_tab_no_link_activation: focus click activated the link (fired=$fired)"
        return 1
    fi
    echo "OK: switch_tab_no_link_activation: overlay absorbed the click, link not activated"

    # keyboard focus still reaches content
    post '{"action": "eval", "expression": "window.scrollTo(0,0)"}' >/dev/null
    post '{"action": "send_key", "key": "pagedown"}' >/dev/null
    sleep 0.5
    resp=$(post '{"action": "eval", "expression": "window.scrollY"}')
    scrolled=$(echo "$resp" | json_get "['data']['result']")
    if [ "$scrolled" -le 0 ]; then
        echo "FAIL: switch_tab_no_link_activation: keyboard focus did not reach content (scrollY=$scrolled)"
        return 1
    fi
    echo "OK: switch_tab_no_link_activation: keyboard reaches content (scrollY=$scrolled)"
}

test_close_tab() {
    local resp remaining

    # Get current tab count
    resp=$(post '{"action": "list_tabs"}')
    local before
    before=$(echo "$resp" | json_get "['data']['count']")

    # Open a tab then close it
    post '{"action": "new_tab"}' >/dev/null
    resp=$(post '{"action": "close_tab"}')
    assert_success "$resp" "close_tab" || return 1
    remaining=$(echo "$resp" | json_get "['data']['remaining']")
    assert_eq "$remaining" "$before" "close_tab: remaining count" || return 1
    echo "OK: close_tab (remaining=$remaining)"
}

ALL_TESTS+=(
    test_list_tabs
    test_new_tab
    test_switch_tab
    test_switch_tab_foreground
    test_switch_tab_keyboard
    test_switch_tab_no_link_activation
    test_close_tab
)
