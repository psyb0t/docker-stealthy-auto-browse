#!/bin/bash
# tests/test_recovery.sh — verify the API auto-recovers from Camoufox crashes.
#
# Regression for the v1.0.1+ "Connection closed while reading from the
# driver" issue reported by @shadowjig. Without auto-recovery, every
# subsequent request after a Camoufox crash returned the same error until
# the container was manually restarted.

readonly RECOVERY_BLANK_GOTO='{"action": "goto", "url": "about:blank", "wait_until": "domcontentloaded"}'
readonly RECOVERY_LIST_TABS='{"action": "list_tabs"}'
readonly RECOVERY_NEW_TAB='{"action": "new_tab"}'
readonly RECOVERY_HTTP_OK=200
readonly RECOVERY_EXTRA_TABS=2
readonly RECOVERY_CONCURRENT_SCREENSHOTS=4

test_recovery_camoufox_crash() {
    # Baseline: navigate works
    local resp
    resp=$(post '{"action": "goto", "url": "about:blank", "wait_until": "domcontentloaded"}')
    assert_success "$resp" "recovery: baseline navigation" || return 1

    # Force-kill Camoufox to simulate OOM / segfault / SIGKILL.
    docker exec "$CONTAINER_NAME" bash -c 'pkill -9 -f camoufox-bin' 2>/dev/null
    sleep 1

    # First post-crash call must detect the dead browser, relaunch, and
    # succeed. Allow up to 10s for the relaunch (Camoufox cold start is ~3-5s).
    resp=$(curl -sf --max-time 30 -X POST "$BASE" \
        -H 'Content-Type: application/json' \
        -d '{"action": "goto", "url": "about:blank", "wait_until": "domcontentloaded"}')
    if ! echo "$resp" | grep -qE '"success":\s*true'; then
        echo "  FAIL: recovery: post-crash navigation did not recover. resp: $resp"
        return 1
    fi
    echo "  OK: recovery: post-crash navigation auto-recovered"

    # And another one to prove it's not a one-shot fluke.
    resp=$(post '{"action": "goto", "url": "about:blank", "wait_until": "domcontentloaded"}')
    assert_success "$resp" "recovery: post-recovery navigation stays healthy"
}

# Reopens a tab when the pool is empty, so a failure here cannot leave the
# shared container without a page and cascade into every later test. Tab
# actions are handled before the API's "needs a page" guard, so this works even
# in the broken state it cleans up.
_recovery_restore_tab() {
    local count
    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    if [[ "$count" == "0" ]]; then
        post "$RECOVERY_NEW_TAB" >/dev/null
    fi
}

# Closes every open tab through the API and asserts the pool is empty, so the
# caller starts from the broken state. Restores a tab before failing.
_recovery_close_all_tabs() {
    local resp count index remaining

    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    for ((index = 1; index <= count; index++)); do
        resp=$(post '{"action": "close_tab"}')
        if ! assert_success "$resp" "recovery: close tab $index of $count"; then
            _recovery_restore_tab
            return 1
        fi
    done
    remaining=$(echo "$resp" | json_get "['data']['remaining']")
    if ! assert_eq "$remaining" "0" "recovery: every tab closed"; then
        _recovery_restore_tab
        return 1
    fi
}

# Closing the last tab leaves Camoufox running with an empty page pool. The
# health probe still passes because the context answers, so nothing relaunches
# the browser, and every action that needs a page fails with "No active page"
# until the container restarts. The API must open a replacement tab instead.
test_recovery_last_tab_closed() {
    local resp count

    resp=$(post "$RECOVERY_BLANK_GOTO")
    assert_success "$resp" "recovery: baseline navigation before closing every tab" || return 1

    _recovery_close_all_tabs || return 1

    resp=$(post "$RECOVERY_BLANK_GOTO")
    if ! assert_success "$resp" "recovery: navigation after the last tab closed"; then
        _recovery_restore_tab
        return 1
    fi

    resp=$(post "$RECOVERY_BLANK_GOTO")
    assert_success "$resp" "recovery: navigation stays healthy after the replacement tab" || return 1

    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    assert_eq "$count" "1" "recovery: exactly one replacement tab opened"
}

# list_tabs is read-only: at zero tabs it reports an empty pool instead of
# opening a replacement, so callers can observe the broken state.
test_recovery_list_tabs_does_not_open_tab() {
    local count count_again

    _recovery_close_all_tabs || return 1

    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    count_again=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    _recovery_restore_tab
    assert_eq "$count" "0" "recovery: list_tabs at zero tabs reports zero" || return 1
    assert_eq "$count_again" "0" "recovery: repeated list_tabs opens no tab" || return 1
    echo "  OK: recovery: list_tabs leaves an empty pool empty"
}

# Same recovery when several tabs were open and all of them were closed.
test_recovery_many_tabs_closed() {
    local resp count index

    for ((index = 1; index <= RECOVERY_EXTRA_TABS; index++)); do
        resp=$(post "$RECOVERY_NEW_TAB")
        assert_success "$resp" "recovery: open extra tab $index" || return 1
    done
    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    assert_eq "$count" "$((RECOVERY_EXTRA_TABS + 1))" "recovery: extra tabs open" || return 1

    _recovery_close_all_tabs || return 1

    resp=$(post "$RECOVERY_BLANK_GOTO")
    if ! assert_success "$resp" "recovery: navigation after closing every tab"; then
        _recovery_restore_tab
        return 1
    fi

    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    assert_eq "$count" "1" "recovery: one replacement tab after closing several"
}

# new_tab at zero tabs must still open a tab and navigate it, since Firefox
# refuses a plain new page once no window is left.
test_recovery_new_tab_at_zero_tabs() {
    local resp url count

    _recovery_close_all_tabs || return 1

    resp=$(post '{"action": "new_tab", "url": "about:blank#recovered"}')
    if ! assert_success "$resp" "recovery: new_tab at zero tabs"; then
        _recovery_restore_tab
        return 1
    fi
    url=$(echo "$resp" | json_get "['data']['url']")
    assert_eq "$url" "about:blank#recovered" "recovery: new_tab navigated after recovery" || return 1

    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    assert_eq "$count" "1" "recovery: new_tab at zero tabs left one tab"
}

# The screenshot route runs outside the request lock, so screenshots and an
# action arriving together at zero tabs race to relaunch the browser. Every
# request must get a page, and only one tab may be opened.
test_recovery_concurrent_requests_open_one_tab() {
    local workdir index code count
    local failed=0
    local -a pids=()

    _recovery_close_all_tabs || return 1

    workdir=$(mktemp -d)
    for ((index = 1; index <= RECOVERY_CONCURRENT_SCREENSHOTS; index++)); do
        curl -s --max-time 30 -o /dev/null -w '%{http_code}' \
            "$BASE/screenshot/browser" >"$workdir/screenshot_$index" &
        pids+=("$!")
    done
    post "$RECOVERY_BLANK_GOTO" >"$workdir/goto" &
    pids+=("$!")
    wait "${pids[@]}"

    for ((index = 1; index <= RECOVERY_CONCURRENT_SCREENSHOTS; index++)); do
        code=$(<"$workdir/screenshot_$index")
        assert_eq "$code" "$RECOVERY_HTTP_OK" "recovery: concurrent screenshot $index at zero tabs" || failed=1
    done
    assert_success "$(<"$workdir/goto")" "recovery: navigation concurrent with screenshots" || failed=1
    rm -r "$workdir"
    if ((failed)); then
        _recovery_restore_tab
        return 1
    fi
    echo "  OK: recovery: $RECOVERY_CONCURRENT_SCREENSHOTS concurrent screenshots got a page"

    count=$(post "$RECOVERY_LIST_TABS" | json_get "['data']['count']")
    assert_eq "$count" "1" "recovery: concurrent requests opened one tab"
}

ALL_TESTS+=(
    test_recovery_camoufox_crash
    test_recovery_last_tab_closed
    test_recovery_list_tabs_does_not_open_tab
    test_recovery_many_tabs_closed
    test_recovery_new_tab_at_zero_tabs
    test_recovery_concurrent_requests_open_one_tab
)
