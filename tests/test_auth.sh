#!/bin/bash
# tests/test_auth.sh - AUTH_TOKEN Bearer authentication tests (HTTP API + MCP)

test_auth_token() {
    local name="${CONTAINER_NAME}-auth"
    local key="test-token-only-not-a-secret"

    local ip base
    ip=$(start_extra_container "$name" -e "AUTH_TOKEN=${key}")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: auth_token: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        return 1
    fi

    # /health must work without auth
    local health
    health=$(curl -sf "$base/health" 2>/dev/null || echo "FAIL")
    assert_eq "$health" "ok" "auth: /health works without auth" || { stop_extra_container "$name"; return 1; }

    # POST / without auth must return 401
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base" \
        -H "Content-Type: application/json" -d '{"action":"ping"}')
    assert_eq "$code" "401" "auth: POST / no token returns 401" || { stop_extra_container "$name"; return 1; }

    # POST / with wrong key must return 401
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer wrong-token-not-a-secret" \
        -d '{"action":"ping"}')
    assert_eq "$code" "401" "auth: POST / wrong token returns 401" || { stop_extra_container "$name"; return 1; }

    # POST / with correct key must succeed
    local resp
    resp=$(curl -sf -X POST "$base" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${key}" \
        -d '{"action":"ping"}')
    assert_success "$resp" "auth: POST / correct token succeeds" || { stop_extra_container "$name"; return 1; }

    # Query-string tokens are never accepted.
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base?auth_token=${key}" \
        -H "Content-Type: application/json" \
        -d '{"action":"ping"}')
    assert_eq "$code" "401" "auth: query token returns 401" || { stop_extra_container "$name"; return 1; }

    # Reject query parameters even with a valid header: they are not auth.
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base?auth_token=ignored" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${key}" \
        -d '{"action":"ping"}')
    assert_eq "$code" "401" "auth: query parameter with valid header returns 401" || { stop_extra_container "$name"; return 1; }

    # MCP /mcp/ without auth must return 401
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base/mcp/" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}')
    assert_eq "$code" "401" "auth: MCP no token returns 401" || { stop_extra_container "$name"; return 1; }

    # MCP must reject query-string tokens too.
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base/mcp/?auth_token=${key}" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}')
    assert_eq "$code" "401" "auth: MCP query token returns 401" || { stop_extra_container "$name"; return 1; }

    # MCP /mcp/ with correct key must succeed (not 401)
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$base/mcp/" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Authorization: Bearer ${key}" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}')
    if [ "$code" = "401" ]; then
        echo "FAIL: auth: MCP correct token still returns 401"
        stop_extra_container "$name"
        return 1
    fi
    echo "  OK: auth: MCP correct token accepted (HTTP $code)"

    stop_extra_container "$name"
    echo "OK: auth_token"
}

ALL_TESTS+=(test_auth_token)
