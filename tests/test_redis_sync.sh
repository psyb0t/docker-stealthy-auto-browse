#!/bin/bash
# tests/test_redis_sync.sh - Redis cross-instance cookie sync tests
#
# Tests that two browser instances with REDIS_URL set share cookies via
# Redis pubsub: set on one, appears on other; delete on one, clears other.

_RS_REDIS="stealthy-auto-browse-test-redis-server"
_RS_BROWSER1="stealthy-auto-browse-test-redis-b1"
_RS_BROWSER2="stealthy-auto-browse-test-redis-b2"

test_redis_cookie_sync() {
    local redis_ip b1_ip b2_ip resp found count

    # --- Start Redis ---
    docker rm -f "$_RS_REDIS" >/dev/null 2>&1 || true
    docker run -d --name "$_RS_REDIS" redis:7-alpine >/dev/null
    EXTRA_CONTAINERS+=("$_RS_REDIS")
    redis_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$_RS_REDIS")

    for _ in $(seq 1 20); do
        docker exec "$_RS_REDIS" redis-cli ping 2>/dev/null | grep -q "PONG" && break
        sleep 1
    done

    # --- Start two browser instances with REDIS_URL ---
    b1_ip=$(start_extra_container "$_RS_BROWSER1" -e "REDIS_URL=redis://$redis_ip:6379")
    b2_ip=$(start_extra_container "$_RS_BROWSER2" -e "REDIS_URL=redis://$redis_ip:6379")
    echo "  redis=$redis_ip b1=$b1_ip b2=$b2_ip"

    if ! wait_for_api "http://$b1_ip:8080" 180; then
        echo "FAIL: redis sync: browser1 not ready"
        docker logs "$_RS_BROWSER1" 2>&1 | tail -10
        return 1
    fi
    if ! wait_for_api "http://$b2_ip:8080" 180; then
        echo "FAIL: redis sync: browser2 not ready"
        docker logs "$_RS_BROWSER2" 2>&1 | tail -10
        return 1
    fi

    # Navigate both to example.com so cookies have a valid domain
    resp=$(post_to "http://$b1_ip:8080" '{"action":"goto","url":"https://example.com","wait_until":"load"}')
    assert_success "$resp" "redis sync: b1 goto" || return 1
    resp=$(post_to "http://$b2_ip:8080" '{"action":"goto","url":"https://example.com","wait_until":"load"}')
    assert_success "$resp" "redis sync: b2 goto" || return 1

    # --- Test 1: set cookie on b1, verify it appears on b2 ---
    resp=$(post_to "http://$b1_ip:8080" \
        '{"action":"set_cookie","name":"sync_test","value":"alpha","domain":".example.com","path":"/"}')
    assert_success "$resp" "redis sync: set cookie on b1" || return 1
    sleep 4

    resp=$(post_to "http://$b2_ip:8080" '{"action":"get_cookies"}')
    found=$(echo "$resp" | python3 -c "
import sys, json
cookies = json.load(sys.stdin)['data']['cookies']
c = next((c for c in cookies if c['name'] == 'sync_test'), None)
print(c['value'] if c else 'NOT_FOUND')
")
    assert_eq "$found" "alpha" "redis sync: b2 sees cookie set on b1" || return 1
    echo "  OK: b2 sees cookie set on b1"

    # --- Test 2: update same cookie on b1, verify new value propagates ---
    resp=$(post_to "http://$b1_ip:8080" \
        '{"action":"set_cookie","name":"sync_test","value":"beta","domain":".example.com","path":"/"}')
    assert_success "$resp" "redis sync: update cookie on b1" || return 1
    sleep 4

    resp=$(post_to "http://$b2_ip:8080" '{"action":"get_cookies"}')
    found=$(echo "$resp" | python3 -c "
import sys, json
cookies = json.load(sys.stdin)['data']['cookies']
c = next((c for c in cookies if c['name'] == 'sync_test'), None)
print(c['value'] if c else 'NOT_FOUND')
")
    assert_eq "$found" "beta" "redis sync: b2 sees updated cookie value" || return 1
    echo "  OK: updated cookie value propagated"

    # --- Test 3: multiple cookies all sync ---
    post_to "http://$b1_ip:8080" \
        '{"action":"set_cookie","name":"cookie_a","value":"aaa","domain":".example.com","path":"/"}' >/dev/null
    post_to "http://$b1_ip:8080" \
        '{"action":"set_cookie","name":"cookie_b","value":"bbb","domain":".example.com","path":"/"}' >/dev/null
    sleep 4

    resp=$(post_to "http://$b2_ip:8080" '{"action":"get_cookies"}')
    found=$(echo "$resp" | python3 -c "
import sys, json
cookies = json.load(sys.stdin)['data']['cookies']
names = {c['name']: c['value'] for c in cookies}
a = names.get('cookie_a', 'MISSING')
b = names.get('cookie_b', 'MISSING')
print(f'{a},{b}')
")
    assert_eq "$found" "aaa,bbb" "redis sync: multiple cookies synced" || return 1
    echo "  OK: multiple cookies synced"

    # --- Test 4: delete on b1 clears b2 ---
    resp=$(post_to "http://$b1_ip:8080" '{"action":"delete_cookies"}')
    assert_success "$resp" "redis sync: delete cookies on b1" || return 1
    sleep 4

    resp=$(post_to "http://$b2_ip:8080" '{"action":"get_cookies"}')
    count=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['count'])")
    assert_eq "$count" "0" "redis sync: b2 cookies cleared after b1 delete" || return 1
    echo "  OK: b2 cookies cleared after delete on b1"

    # --- Test 5: Redis key empty after delete ---
    local redis_keys
    redis_keys=$(docker exec "$_RS_REDIS" redis-cli HGETALL SABROWSE:COOKIES 2>/dev/null)
    if [ -n "$redis_keys" ]; then
        echo "FAIL: redis sync: Redis SABROWSE:COOKIES not empty after delete: $redis_keys"
        return 1
    fi
    echo "  OK: Redis keys cleaned up after delete"

    echo "OK: redis_cookie_sync (set, update, multi, delete all sync)"

    stop_extra_container "$_RS_BROWSER1"
    stop_extra_container "$_RS_BROWSER2"
    stop_extra_container "$_RS_REDIS"
}

ALL_TESTS+=(test_redis_cookie_sync)
