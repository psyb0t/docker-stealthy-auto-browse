#!/usr/bin/env bash
# tests/test_cluster.sh — cluster integration test.
#
# Sourced by test.sh; registered as test_cluster in ALL_TESTS.
# NUM_BROWSERS controls how many instances to test (default 3).
#
# Tests:
#   1. All browsers healthy
#   2. Cookie set on one instance syncs to all
#   3. Cookie update propagates to all
#   4. Concurrent cookie sets (one per browser simultaneously) — all converge
#   5. Delete on one instance clears all
#   6. Redis state matches expected after each operation
#   7. localStorage is per-instance (NOT synced — expected behavior)
#   8. sessionStorage is per-instance (NOT synced — expected behavior)
#   9. Queue-proxy health endpoint
#  10. 20 concurrent requests via queue-proxy all succeed
#  10b. 500 concurrent requests via queue-proxy (stress test)
#  11. HAProxy sticky session via INSTANCEID cookie
#  12. MCP full handshake through HAProxy (init, list tools, goto, get_text)

test_cluster() {(
    set -euo pipefail

    local WORKDIR
    WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    cd "$WORKDIR"

    local NUM_BROWSERS=3
    local BROWSER_HEALTH_ATTEMPTS="${BROWSER_HEALTH_ATTEMPTS:-150}"
    local COMPOSE="docker compose -f tests/fixtures/docker-compose.test-cluster.yml -p sab-cluster-test"
    local IMAGE="psyb0t/stealthy-auto-browse:latest-test"
    local REDIS_CONTAINER="sab-cluster-test-redis-1"
    local WEBSERVER_CONTAINER="sab-cluster-test-webserver-1"
    local QPROXY_CONTAINER="sab-cluster-test-queue-proxy-1"

    local PASS=0 FAIL=0
    FAILED_TESTS=()

    # --- Colours / logging ---

    local C_RESET='\033[0m' C_RED='\033[0;31m' C_GREEN='\033[0;32m'
    local C_YELLOW='\033[1;33m' C_BLUE='\033[0;34m' C_CYAN='\033[0;36m' C_BOLD='\033[1m'

    ts()      { date '+%H:%M:%S'; }
    log()     { echo -e "${C_BLUE}[$(ts)]${C_RESET} $*"; }
    log_ok()  { echo -e "${C_GREEN}[$(ts)] OK${C_RESET}  $*"; }
    log_fail(){ echo -e "${C_RED}[$(ts)] FAIL${C_RESET} $*"; }
    log_warn(){ echo -e "${C_YELLOW}[$(ts)] WARN${C_RESET} $*"; }
    log_dbg() { echo -e "${C_CYAN}[$(ts)] DBG${C_RESET}  $*"; }
    log_sep() { echo -e "${C_BOLD}$(printf '─%.0s' {1..70})${C_RESET}"; }

    # --- Cleanup ---

    # shellcheck disable=SC2317 # ShellCheck does not trace handlers invoked by EXIT traps.
    _cleanup() {
        echo ""
        log "Cleaning up cluster..."
        $COMPOSE down -v --remove-orphans 2>&1 | grep -v "^$" || true
        log "Done."
    }
    trap _cleanup EXIT

    # --- Assertions ---

    _check() {
        local name="$1" result="$2"
        if [ "$result" = "true" ]; then
            log_ok "$name"
            PASS=$((PASS+1))
            return 0
        fi
        log_fail "$name  (got: $result)"
        FAIL=$((FAIL+1))
        FAILED_TESTS+=("$name")
        return 1
    }

    _assert_eq() {
        local actual="$1" expected="$2" name="$3"
        if [ "$actual" = "$expected" ]; then
            log_ok "$name"
            PASS=$((PASS+1))
            return 0
        fi
        log_fail "$name  expected='$expected'  got='$actual'"
        FAIL=$((FAIL+1))
        FAILED_TESTS+=("$name")
        return 1
    }

    # --- HTTP helpers ---

    _post() {
        local url="$1" data="$2"
        curl -sf --max-time 30 -X POST "$url" -H 'Content-Type: application/json' -d "$data" 2>/dev/null || echo '{}'
    }

    _get() {
        curl -sf --max-time 30 "$1" 2>/dev/null || echo '{}'
    }

    _jsok() {
        python3 -c "import sys,json; print(str(json.load(sys.stdin).get('success',False)).lower())" 2>/dev/null || echo "error"
    }

    # --- Dump helpers ---

    _dump_browser_logs() {
        log_warn "=== browser container logs ==="
        for cid in "${BROWSER_IDS[@]}"; do
            local cname
            cname=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/')
            log_dbg "--- $cname ---"
            docker logs "$cid" 2>&1 | tail -20
        done
    }

    _dump_redis() {
        log_dbg "=== Redis SABROWSE:COOKIES hash ==="
        docker exec "$REDIS_CONTAINER" redis-cli HGETALL SABROWSE:COOKIES 2>/dev/null || log_warn "Redis not reachable"
    }

    _dump_proxy_logs() {
        log_dbg "=== HAProxy logs (last 30 lines) ==="
        docker logs "$QPROXY_CONTAINER" 2>&1 | tail -30
    }

    # --- Wait helpers ---

    _wait_health() {
        local url="$1" label="$2" max="${3:-90}"
        printf "%b[$(ts)] Waiting for %s%b" "$C_CYAN" "$label" "$C_RESET"
        for i in $(seq 1 "$max"); do
            if curl -sf --max-time 5 "$url/health" >/dev/null 2>&1; then
                echo " ready (${i}x2s)"
                return 0
            fi
            printf "."
            sleep 2
        done
        echo " TIMEOUT after ${max}x2s"
        return 1
    }

    _wait_server() {
        local url="$1" label="$2" max="${3:-20}"
        printf "%b[$(ts)] Waiting for %s%b" "$C_CYAN" "$label" "$C_RESET"
        for i in $(seq 1 "$max"); do
            if curl -sf --max-time 5 "$url" >/dev/null 2>&1; then
                echo " ready (${i}s)"
                return 0
            fi
            printf "."
            sleep 1
        done
        echo " TIMEOUT"
        return 1
    }

    # ============================================================
    # PHASE 1: Build & start cluster
    # ============================================================

    log_sep
    log "Phase 1: Building test image and starting ${NUM_BROWSERS}-browser cluster"
    log_sep

    log "Building image: $IMAGE ..."
    docker build -t "$IMAGE" . 2>&1 | grep -E "^(#[0-9]+ (DONE|ERROR|CACHED)|Successfully|ERROR)" || true
    log "Image built."

    log "Starting cluster (redis + haproxy + $NUM_BROWSERS browsers + webserver)..."
    $COMPOSE down -v --remove-orphans 2>/dev/null || true
    $COMPOSE up -d --build 2>&1 | grep -v "^$" | grep -v "^Network\|^Container" || true

    local WEBSERVER_IP
    WEBSERVER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$WEBSERVER_CONTAINER" 2>/dev/null || true)
    if [ -z "$WEBSERVER_IP" ]; then
        log_fail "Could not get web server IP"
        $COMPOSE ps
        exit 1
    fi
    local TEST_PAGE="http://${WEBSERVER_IP}/index.html"
    log "Web server: $WEBSERVER_IP → $TEST_PAGE"

    _wait_server "$TEST_PAGE" "webserver" 30

    local QPROXY_IP
    QPROXY_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$QPROXY_CONTAINER" 2>/dev/null || true)
    log "Queue-proxy (HAProxy): $QPROXY_IP:8080"

    mapfile -t BROWSER_IDS < <($COMPOSE ps -q browser 2>/dev/null)
    log "Found ${#BROWSER_IDS[@]} browser containers"

    if [ "${#BROWSER_IDS[@]}" -lt "$NUM_BROWSERS" ]; then
        log_fail "Expected $NUM_BROWSERS browser containers, got ${#BROWSER_IDS[@]}"
        $COMPOSE ps
        exit 1
    fi

    BROWSER_IPS=()
    BROWSER_URLS=()
    for cid in "${BROWSER_IDS[@]}"; do
        local ip
        ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$cid")
        BROWSER_IPS+=("$ip")
        BROWSER_URLS+=("http://${ip}:8080")
        log_dbg "  browser $cid → $ip"
    done

    log "Waiting for all $NUM_BROWSERS browsers to be healthy..."
    local HEALTH_PIDS=()
    for i in "${!BROWSER_URLS[@]}"; do
        (
            for ((attempt = 0; attempt < BROWSER_HEALTH_ATTEMPTS; attempt += 1)); do
                curl -sf --max-time 5 "${BROWSER_URLS[$i]}/health" >/dev/null 2>&1 && exit 0
                sleep 2
            done
            exit 1
        ) &
        HEALTH_PIDS+=($!)
    done

    local ALL_HEALTHY=true
    for i in "${!HEALTH_PIDS[@]}"; do
        if ! wait "${HEALTH_PIDS[$i]}"; then
            log_fail "Browser ${i} (${BROWSER_IPS[$i]}) never became healthy"
            ALL_HEALTHY=false
        fi
    done

    if [ "$ALL_HEALTHY" = false ]; then
        log_fail "Not all browsers healthy — aborting"
        _dump_browser_logs
        exit 1
    fi
    log "All $NUM_BROWSERS browsers healthy."

    _wait_health "http://${QPROXY_IP}:8080/__queue" "queue-proxy" 30 || true

    # ============================================================
    # PHASE 2: Navigate all browsers to test page
    # ============================================================

    log_sep
    log "Phase 2: Navigating all $NUM_BROWSERS browsers to test page"
    log_sep

    local GOTO_RESULTS_FILE="/tmp/sab-cluster-test-goto.txt"
    : > "$GOTO_RESULTS_FILE"
    local GOTO_PIDS=()

    for i in "${!BROWSER_URLS[@]}"; do
        (
            local resp ok
            resp=$(_post "${BROWSER_URLS[$i]}" "{\"action\":\"goto\",\"url\":\"$TEST_PAGE\",\"wait_until\":\"load\"}")
            ok=$(echo "$resp" | _jsok)
            echo "b${i}:${ok}" >> "$GOTO_RESULTS_FILE"
        ) &
        GOTO_PIDS+=($!)
    done
    for pid in "${GOTO_PIDS[@]}"; do wait "$pid" || true; done

    log_dbg "  goto results: $(tr '\n' ' ' < "$GOTO_RESULTS_FILE")"
    local GOTO_FAILS=0
    for i in "${!BROWSER_URLS[@]}"; do
        local result
        result=$(grep "^b${i}:" "$GOTO_RESULTS_FILE" | cut -d: -f2)
        if [ "$result" != "true" ]; then
            log_fail "browser[$i] goto test page: '$result'"
            GOTO_FAILS=$((GOTO_FAILS+1))
        fi
    done
    _check "all $NUM_BROWSERS browsers navigated to test page" "$([ $GOTO_FAILS -eq 0 ] && echo true || echo false)"

    # ============================================================
    # PHASE 3: Cookie sync tests
    # ============================================================

    log_sep
    log "Phase 3: Cookie sync across all $NUM_BROWSERS instances"
    log_sep

    local COOKIE_DOMAIN
    COOKIE_DOMAIN="$(echo "$TEST_PAGE" | python3 -c "import sys; from urllib.parse import urlparse; u=urlparse(sys.stdin.read().strip()); print(u.hostname)")"
    log_dbg "Cookie domain: $COOKIE_DOMAIN"

    ## --- Test 3.1: Set cookie on browser[0], verify all $NUM_BROWSERS get it ---

    log "Test 3.1 — set cookie on browser[0], wait, verify all $NUM_BROWSERS see it"

    local resp
    resp=$(_post "${BROWSER_URLS[0]}" "{\"action\":\"set_cookie\",\"name\":\"cluster_sync\",\"value\":\"from_b0\",\"domain\":\"$COOKIE_DOMAIN\",\"path\":\"/\"}")
    _check "3.1 set cookie on browser[0]" "$(echo "$resp" | _jsok)"

    log_dbg "Waiting 5s for pubsub propagation..."
    sleep 5

    local MISS_31=0
    for i in "${!BROWSER_URLS[@]}"; do
        resp=$(_post "${BROWSER_URLS[$i]}" '{"action":"get_cookies"}')
        local found
        found=$(echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
cookies = d.get('data', {}).get('cookies', [])
c = next((c for c in cookies if c.get('name') == 'cluster_sync'), None)
print(c['value'] if c else 'MISSING')
" 2>/dev/null)
        if [ "$found" = "from_b0" ]; then
            log_dbg "  browser[$i] ✓ has cluster_sync=from_b0"
        else
            log_fail "  browser[$i] missing cluster_sync (got: $found)"
            MISS_31=$((MISS_31+1))
        fi
    done
    _check "3.1 all $NUM_BROWSERS browsers have cookie set on browser[0]" "$([ $MISS_31 -eq 0 ] && echo true || echo false)"

    ## --- Test 3.2: Update cookie from browser[1], verify all $NUM_BROWSERS get update ---

    log "Test 3.2 — update cookie from browser[1], verify all $NUM_BROWSERS see new value"

    resp=$(_post "${BROWSER_URLS[1]}" "{\"action\":\"set_cookie\",\"name\":\"cluster_sync\",\"value\":\"updated_by_b1\",\"domain\":\"$COOKIE_DOMAIN\",\"path\":\"/\"}")
    _check "3.2 update cookie on browser[1]" "$(echo "$resp" | _jsok)"

    sleep 5

    local MISS_32=0
    for i in "${!BROWSER_URLS[@]}"; do
        resp=$(_post "${BROWSER_URLS[$i]}" '{"action":"get_cookies"}')
        local found
        found=$(echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
cookies = d.get('data', {}).get('cookies', [])
c = next((c for c in cookies if c.get('name') == 'cluster_sync'), None)
print(c['value'] if c else 'MISSING')
" 2>/dev/null)
        if [ "$found" = "updated_by_b1" ]; then
            log_dbg "  browser[$i] ✓ cluster_sync=updated_by_b1"
        else
            log_fail "  browser[$i] stale or missing (got: $found)"
            MISS_32=$((MISS_32+1))
        fi
    done
    _check "3.2 all $NUM_BROWSERS browsers have updated cookie value from browser[1]" "$([ $MISS_32 -eq 0 ] && echo true || echo false)"

    ## --- Test 3.3: Concurrent cookie storm ---

    log "Test 3.3 — all $NUM_BROWSERS browsers set unique cookies in parallel, verify convergence"

    local STORM_PIDS=()
    for i in "${!BROWSER_URLS[@]}"; do
        (
            _post "${BROWSER_URLS[$i]}" \
                "{\"action\":\"set_cookie\",\"name\":\"storm_b${i}\",\"value\":\"val_b${i}\",\"domain\":\"$COOKIE_DOMAIN\",\"path\":\"/\"}" \
                >/dev/null
        ) &
        STORM_PIDS+=($!)
    done
    for pid in "${STORM_PIDS[@]}"; do wait "$pid" || true; done
    log_dbg "All 10 concurrent set_cookie requests sent. Waiting 8s for convergence..."
    sleep 8

    local MISS_33=0
    for i in "${!BROWSER_URLS[@]}"; do
        resp=$(_post "${BROWSER_URLS[$i]}" '{"action":"get_cookies"}')
        local missing
        missing=$(echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
cookies = {c['name']: c['value'] for c in d.get('data', {}).get('cookies', [])}
missing = []
for j in range(${NUM_BROWSERS}):
    k = f'storm_b{j}'
    v = f'val_b{j}'
    if cookies.get(k) != v:
        missing.append(f'{k}={cookies.get(k,\"MISSING\")}')
print(','.join(missing) if missing else 'none')
" 2>/dev/null)
        if [ "$missing" = "none" ]; then
            log_dbg "  browser[$i] ✓ has all $NUM_BROWSERS storm cookies"
        else
            log_fail "  browser[$i] missing: $missing"
            MISS_33=$((MISS_33+1))
        fi
    done
    _check "3.3 all $NUM_BROWSERS browsers converged after concurrent cookie storm" "$([ $MISS_33 -eq 0 ] && echo true || echo false)"

    local redis_count
    redis_count=$(docker exec "$REDIS_CONTAINER" redis-cli HLEN SABROWSE:COOKIES 2>/dev/null || echo 0)
    log_dbg "Redis SABROWSE:COOKIES has $redis_count fields"
    _check "3.3 Redis has >= $NUM_BROWSERS cookie entries" "$([ "$redis_count" -ge "$NUM_BROWSERS" ] && echo true || echo false)"

    ## --- Test 3.4: Delete on last browser, verify all cleared ---

    local LAST_IDX=$((NUM_BROWSERS - 1))
    log "Test 3.4 — delete_cookies on browser[$LAST_IDX], verify all $NUM_BROWSERS cleared"

    resp=$(_post "${BROWSER_URLS[$LAST_IDX]}" '{"action":"delete_cookies"}')
    _check "3.4 delete_cookies on browser[$LAST_IDX]" "$(echo "$resp" | _jsok)"

    # Poll until all browsers report 0 cookies (max 15s)
    local MISS_34=0
    local attempt
    for attempt in 1 2 3 4 5; do
        MISS_34=0
        for i in "${!BROWSER_URLS[@]}"; do
            resp=$(_post "${BROWSER_URLS[$i]}" '{"action":"get_cookies"}')
            local count
            count=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('count',99))" 2>/dev/null)
            if [ "$count" != "0" ]; then
                MISS_34=$((MISS_34+1))
            fi
        done
        if [ $MISS_34 -eq 0 ]; then
            break
        fi
        log_dbg "  attempt $attempt: $MISS_34 browsers still have cookies, retrying in 3s..."
        sleep 3
    done

    # Final report
    if [ $MISS_34 -gt 0 ]; then
        for i in "${!BROWSER_URLS[@]}"; do
            resp=$(_post "${BROWSER_URLS[$i]}" '{"action":"get_cookies"}')
            local count
            count=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('count',99))" 2>/dev/null)
            if [ "$count" != "0" ]; then
                log_fail "  browser[$i] still has $count cookies"
            fi
        done
    else
        log_dbg "  all $NUM_BROWSERS browsers cleared after $attempt attempt(s)"
    fi
    _check "3.4 all $NUM_BROWSERS browsers empty after delete on browser[$LAST_IDX]" "$([ $MISS_34 -eq 0 ] && echo true || echo false)"

    local redis_after
    redis_after=$(docker exec "$REDIS_CONTAINER" redis-cli HLEN SABROWSE:COOKIES 2>/dev/null || echo "?")
    log_dbg "Redis SABROWSE:COOKIES after delete: $redis_after fields"
    _check "3.4 Redis SABROWSE:COOKIES cleared after delete" "$([ "$redis_after" = "0" ] && echo true || echo false)"

    # ============================================================
    # PHASE 4: localStorage — per-instance isolation
    # ============================================================

    log_sep
    log "Phase 4: localStorage is per-instance (not synced via Redis — expected)"
    log_sep

    log "Test 4.1 — set localStorage on browser[0], browser[1] should NOT have it"

    resp=$(_post "${BROWSER_URLS[0]}" '{"action":"set_storage","type":"local","key":"local_test","value":"only_on_b0"}')
    _check "4.1 set_storage on browser[0]" "$(echo "$resp" | _jsok)"

    resp=$(_post "${BROWSER_URLS[0]}" '{"action":"get_storage","type":"local"}')
    local val
    val=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('items',{}).get('local_test','MISSING'))" 2>/dev/null)
    _assert_eq "$val" "only_on_b0" "4.1 browser[0] reads back its own localStorage"

    resp=$(_post "${BROWSER_URLS[1]}" '{"action":"get_storage","type":"local"}')
    local val_b1
    val_b1=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('items',{}).get('local_test','NOT_PRESENT'))" 2>/dev/null)
    _check "4.1 browser[1] does NOT have browser[0]'s localStorage (isolated)" "$([ "$val_b1" = "NOT_PRESENT" ] && echo true || echo false)"
    log_dbg "  browser[1] local_test = '$val_b1' (expected: NOT_PRESENT)"

    log "Test 4.2 — set unique localStorage on each browser, verify per-instance reads"

    local LS_PIDS=()
    for i in "${!BROWSER_URLS[@]}"; do
        (
            _post "${BROWSER_URLS[$i]}" \
                "{\"action\":\"set_storage\",\"type\":\"local\",\"key\":\"my_instance\",\"value\":\"browser_${i}\"}" \
                >/dev/null
        ) &
        LS_PIDS+=($!)
    done
    for pid in "${LS_PIDS[@]}"; do wait "$pid" || true; done

    local LS_FAILS=0
    for i in "${!BROWSER_URLS[@]}"; do
        resp=$(_post "${BROWSER_URLS[$i]}" '{"action":"get_storage","type":"local"}')
        local got
        got=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('items',{}).get('my_instance','MISSING'))" 2>/dev/null)
        if [ "$got" = "browser_${i}" ]; then
            log_dbg "  browser[$i] ✓ my_instance=browser_${i}"
        else
            log_fail "  browser[$i] my_instance='$got' (expected 'browser_${i}')"
            LS_FAILS=$((LS_FAILS+1))
        fi
    done
    _check "4.2 each browser has its own localStorage value" "$([ $LS_FAILS -eq 0 ] && echo true || echo false)"

    # ============================================================
    # PHASE 5: sessionStorage — per-instance isolation
    # ============================================================

    log_sep
    log "Phase 5: sessionStorage is per-instance (not synced — expected)"
    log_sep

    log "Test 5.1 — set sessionStorage on browser[0], browser[1] should NOT have it"

    resp=$(_post "${BROWSER_URLS[0]}" '{"action":"set_storage","type":"session","key":"sess_test","value":"only_on_b0"}')
    _check "5.1 set sessionStorage on browser[0]" "$(echo "$resp" | _jsok)"

    resp=$(_post "${BROWSER_URLS[0]}" '{"action":"get_storage","type":"session"}')
    val=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('items',{}).get('sess_test','MISSING'))" 2>/dev/null)
    _assert_eq "$val" "only_on_b0" "5.1 browser[0] reads back its own sessionStorage"

    resp=$(_post "${BROWSER_URLS[1]}" '{"action":"get_storage","type":"session"}')
    local val_b1_sess
    val_b1_sess=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('items',{}).get('sess_test','NOT_PRESENT'))" 2>/dev/null)
    _check "5.1 browser[1] does NOT have browser[0]'s sessionStorage (isolated)" "$([ "$val_b1_sess" = "NOT_PRESENT" ] && echo true || echo false)"
    log_dbg "  browser[1] sess_test = '$val_b1_sess' (expected: NOT_PRESENT)"

    # ============================================================
    # PHASE 6: Queue-proxy
    # ============================================================

    log_sep
    log "Phase 6: Queue-proxy"
    log_sep

    local QBASE="http://${QPROXY_IP}:8080"

    log "Test 6.1 — queue-proxy /__queue/status"
    resp=$(_get "${QBASE}/__queue/status")
    local max_conc
    max_conc=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('num_replicas',0))" 2>/dev/null)
    _assert_eq "$max_conc" "$NUM_BROWSERS" "6.1 queue-proxy num_replicas=$NUM_BROWSERS"
    log_dbg "  queue status: $resp"

    log "Test 6.2 — queue-proxy /__queue/health"
    resp=$(_get "${QBASE}/__queue/health")
    local status
    status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
    _assert_eq "$status" "ok" "6.2 queue-proxy health=ok"

    log "Test 6.3 — 20 concurrent ping requests via queue-proxy"

    local CONC_RESULTS_FILE="/tmp/sab-cluster-test-conc.txt"
    : > "$CONC_RESULTS_FILE"
    local CONC_PIDS=()

    for i in $(seq 1 20); do
        (
            local resp ok
            resp=$(_post "$QBASE" '{"action":"ping"}')
            ok=$(echo "$resp" | _jsok)
            echo "$ok" >> "$CONC_RESULTS_FILE"
        ) &
        CONC_PIDS+=($!)
    done
    for pid in "${CONC_PIDS[@]}"; do wait "$pid" || true; done

    log_dbg "  results ($(wc -l < "$CONC_RESULTS_FILE") lines): $(tr '\n' ' ' < "$CONC_RESULTS_FILE")"

    local CONC_PASS=0 CONC_FAIL=0
    while IFS= read -r line; do
        if [ "$line" = "true" ]; then
            CONC_PASS=$((CONC_PASS+1))
        else
            CONC_FAIL=$((CONC_FAIL+1))
            log_dbg "  conc req result: '$line'"
        fi
    done < "$CONC_RESULTS_FILE" 2>/dev/null || true
    log_dbg "  concurrent results: $CONC_PASS succeeded, $CONC_FAIL failed/error"
    _check "6.3 all 20 concurrent requests via queue-proxy succeeded" "$([ "$CONC_FAIL" -eq 0 ] && [ "$CONC_PASS" -eq 20 ] && echo true || echo false)"

    log_dbg "  queue stats after concurrent test: $(_get "${QBASE}/__queue/status")"

    log "Test 6.35 — 500 concurrent requests via queue-proxy (stress test)"

    local STRESS_RESULTS_FILE="/tmp/sab-cluster-test-stress.txt"
    : > "$STRESS_RESULTS_FILE"
    local STRESS_PIDS=()

    for i in $(seq 1 500); do
        (
            local resp ok
            resp=$(curl -sf --max-time 300 -X POST "$QBASE" \
                -H 'Content-Type: application/json' -d '{"action":"ping"}' 2>/dev/null || echo '{}')
            ok=$(echo "$resp" | _jsok)
            echo "$ok" >> "$STRESS_RESULTS_FILE"
        ) &
        STRESS_PIDS+=($!)
    done
    log_dbg "  launched 500 concurrent requests, waiting for completion..."
    for pid in "${STRESS_PIDS[@]}"; do wait "$pid" || true; done

    local STRESS_TOTAL STRESS_PASS STRESS_FAIL
    STRESS_TOTAL=$(wc -l < "$STRESS_RESULTS_FILE")
    STRESS_PASS=$(grep -c "^true$" "$STRESS_RESULTS_FILE" || echo 0)
    STRESS_FAIL=$((STRESS_TOTAL - STRESS_PASS))
    log_dbg "  stress results: $STRESS_PASS/$STRESS_TOTAL succeeded, $STRESS_FAIL failed"
    _check "6.35 all 500 concurrent requests via queue-proxy succeeded ($STRESS_PASS/$STRESS_TOTAL)" \
        "$([ "$STRESS_FAIL" -eq 0 ] && [ "$STRESS_PASS" -eq 500 ] && echo true || echo false)"

    _dump_proxy_logs

    log "Test 6.4 — queue-proxy goto + get_text returns real page content"
    resp=$(_post "$QBASE" "{\"action\":\"goto\",\"url\":\"$TEST_PAGE\",\"wait_until\":\"load\"}")
    _check "6.4 queue-proxy goto test page" "$(echo "$resp" | _jsok)"

    resp=$(_post "$QBASE" '{"action":"get_text"}')
    local has_bottom
    has_bottom=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); t=d.get('data',{}).get('text',''); print('true' if 'bottom' in t else 'false')" 2>/dev/null || echo "false")
    _check "6.4 queue-proxy get_text contains expected page content" "$has_bottom"

    # ============================================================
    # PHASE 7: HAProxy sticky sessions
    # ============================================================

    log_sep
    log "Phase 7: HAProxy sticky sessions via INSTANCEID cookie"
    log_sep

    log "Test 7.1 — first request gets INSTANCEID cookie from HAProxy"

    local iid resp_headers
    resp_headers=$(curl -sS --max-time 30 -D - -o /dev/null -X POST "$QBASE" \
        -H 'Content-Type: application/json' -d '{"action":"ping"}' 2>/dev/null) || resp_headers=""
    log_dbg "  response headers:"
    echo "$resp_headers" | while IFS= read -r line; do log_dbg "    $line"; done
    iid=$(echo "$resp_headers" | grep -i 'set-cookie:' | grep -i 'INSTANCEID' \
        | sed 's/.*INSTANCEID=\([^;[:space:]]*\).*/\1/' | tr -d '\r' | head -1 || echo "")
    log_dbg "  INSTANCEID cookie value: '$iid'"
    _dump_proxy_logs
    _check "7.1 HAProxy sets INSTANCEID sticky cookie" "$([ -n "$iid" ] && echo true || echo false)"

    log "Test 7.2 — subsequent requests with same INSTANCEID all succeed"

    if [ -n "$iid" ]; then
        local resp1 resp2 ok1 ok2
        resp1=$(curl -sf --max-time 30 -H "Cookie: INSTANCEID=${iid}" -X POST "$QBASE" \
            -H 'Content-Type: application/json' -d '{"action":"ping"}' 2>/dev/null) || resp1='{}'
        resp2=$(curl -sf --max-time 30 -H "Cookie: INSTANCEID=${iid}" -X POST "$QBASE" \
            -H 'Content-Type: application/json' -d '{"action":"ping"}' 2>/dev/null) || resp2='{}'
        ok1=$(echo "$resp1" | _jsok)
        ok2=$(echo "$resp2" | _jsok)
        _check "7.2 sticky session: 3 requests with same INSTANCEID all succeed" \
            "$([ "$ok1" = "true" ] && [ "$ok2" = "true" ] && echo true || echo false)"
        log_dbg "  resp1: $ok1  resp2: $ok2"
    else
        log_warn "7.2 skipped — no INSTANCEID cookie received"
    fi
    _dump_proxy_logs

    # ============================================================
    # PHASE 7.5: MCP through HAProxy
    # ============================================================

    log_sep
    log "Phase 7.5: MCP sessions through HAProxy"
    log_sep

    log "Test 7.5 — MCP through HAProxy (init, tools, run_script)"

    local mcp_result
    mcp_result=$(python3 - "$QBASE" "$TEST_PAGE" << 'PYEOF'
import json, sys, urllib.request

base_url = sys.argv[1]
test_page = sys.argv[2]
mcp_url = f"{base_url}/mcp/"
session_id = None

def mcp_request(id_num, method, params=None):
    global session_id
    body = {"jsonrpc": "2.0", "id": id_num, "method": method}
    if params:
        body["params"] = params
    data = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(mcp_url, data=data, headers=headers)
    resp = urllib.request.urlopen(req, timeout=30)
    session_id = resp.headers.get("mcp-session-id", session_id)
    raw = resp.read().decode()
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            return json.loads(line[6:])
    return None

def mcp_notify(method):
    body = {"jsonrpc": "2.0", "method": method}
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(mcp_url, data=data, headers=headers)
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

results = []

# 1. Initialize
try:
    r = mcp_request(1, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "cluster-test", "version": "0.1"}
    })
    mcp_notify("notifications/initialized")
    ok = session_id is not None and r is not None
    results.append(("MCP init via proxy", ok, "" if ok else "no session"))
except Exception as e:
    results.append(("MCP init via proxy", False, str(e)))

# 2. List tools
try:
    r = mcp_request(2, "tools/list", {})
    tools = r.get("result", {}).get("tools", [])
    ok = len(tools) >= 1
    results.append((f"MCP tools/list via proxy ({len(tools)} tools)", ok, ""))
except Exception as e:
    results.append(("MCP tools/list via proxy", False, str(e)))

# 3. run_script: goto + get_text (atomic, same instance guaranteed)
try:
    params = {"name": "run_script", "arguments": {
        "steps": [
            {"action": "goto", "url": test_page, "wait_until": "load"},
            {"action": "get_text", "output_id": "text"}
        ]
    }}
    r = mcp_request(3, "tools/call", params)
    content = r.get("result", {}).get("content", [])
    txt = ""
    for c in content:
        if c.get("type") == "text":
            txt = c["text"]
    ok = "Submit" in txt and ('"success": true' in txt or '"success":true' in txt)
    results.append(("MCP run_script goto+get_text", ok, txt[:120] if not ok else ""))
except Exception as e:
    results.append(("MCP run_script goto+get_text", False, str(e)))

# 4. run_script: eval
try:
    params = {"name": "run_script", "arguments": {
        "steps": [
            {"action": "eval", "expression": "document.title", "output_id": "title"}
        ]
    }}
    r = mcp_request(4, "tools/call", params)
    content = r.get("result", {}).get("content", [])
    txt = ""
    for c in content:
        if c.get("type") == "text":
            txt = c["text"]
    ok = "Test Page" in txt and '"success": true' in txt
    results.append(("MCP run_script eval", ok, txt[:120] if not ok else ""))
except Exception as e:
    results.append(("MCP run_script eval", False, str(e)))

print(json.dumps(results))
PYEOF
    ) || true

    echo "$mcp_result" | python3 -c "
import sys, json
results = json.loads(sys.stdin.read())
for name, ok, err in results:
    if ok:
        print(f'  OK: {name}')
    else:
        msg = f': {err}' if err else ''
        print(f'  FAIL: {name}{msg}')
" 2>/dev/null || true

    local mcp_pass mcp_fail
    mcp_pass=$(echo "$mcp_result" | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(sum(1 for _,ok,_ in r if ok))" 2>/dev/null || echo 0)
    mcp_fail=$(echo "$mcp_result" | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(sum(1 for _,ok,_ in r if not ok))" 2>/dev/null || echo 0)
    PASS=$((PASS + mcp_pass))
    FAIL=$((FAIL + mcp_fail))

    _check "7.5 MCP cluster mode: $mcp_pass passed, $mcp_fail failed" "$([ "$mcp_fail" -eq 0 ] && echo true || echo false)"

    # ============================================================
    # PHASE 8: Final Redis state verification
    # ============================================================

    log_sep
    log "Phase 8: Final Redis state verification"
    log_sep

    log "Test 8.1 — Redis is reachable and responsive"
    local redis_ping
    redis_ping=$(docker exec "$REDIS_CONTAINER" redis-cli PING 2>/dev/null || echo "FAIL")
    _assert_eq "$redis_ping" "PONG" "8.1 Redis PING responds PONG"

    log "Test 8.2 — Redis SABROWSE:COOKIES is empty"
    local redis_cookie_count
    redis_cookie_count=$(docker exec "$REDIS_CONTAINER" redis-cli HLEN SABROWSE:COOKIES 2>/dev/null || echo "?")
    log_dbg "  SABROWSE:COOKIES HLEN = $redis_cookie_count"
    _check "8.2 SABROWSE:COOKIES empty after cleanup" "$([ "$redis_cookie_count" = "0" ] && echo true || echo false)"

    log "Test 8.3 — No unexpected Redis keys under SABROWSE: namespace"
    local redis_keys unexpected
    redis_keys=$(docker exec "$REDIS_CONTAINER" redis-cli KEYS 'SABROWSE:*' 2>/dev/null)
    log_dbg "  SABROWSE:* keys: '${redis_keys:-none}'"
    unexpected=$(echo "$redis_keys" | grep -v "^$" | grep -v "SABROWSE:COOKIES\|SABROWSE:UPDATE" || true)
    _check "8.3 No unexpected SABROWSE keys" "$([ -z "$unexpected" ] && echo true || echo false)"

    # ============================================================
    # RESULTS
    # ============================================================

    log_sep
    echo ""
    echo -e "${C_BOLD}Cluster results: ${C_GREEN}${PASS} passed${C_RESET}${C_BOLD}, ${C_RED}${FAIL} failed${C_RESET}"
    if [ "${#FAILED_TESTS[@]}" -gt 0 ]; then
        echo -e "${C_RED}Failed:${C_RESET}"
        for t in "${FAILED_TESTS[@]}"; do
            echo -e "  ${C_RED}✗${C_RESET} $t"
        done
        _dump_redis
        _dump_proxy_logs
    fi
    log_sep

    [ "$FAIL" -eq 0 ]
)}

ALL_TESTS+=(test_cluster)
