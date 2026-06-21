#!/bin/bash
# tests/test_recovery.sh — verify the API auto-recovers from Camoufox crashes.
#
# Regression for the v1.0.1+ "Connection closed while reading from the
# driver" issue reported by @shadowjig. Without auto-recovery, every
# subsequent request after a Camoufox crash returned the same error until
# the container was manually restarted.

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

ALL_TESTS+=(
    test_recovery_camoufox_crash
)
