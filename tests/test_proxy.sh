#!/bin/bash
# tests/test_proxy.sh - Proxy support tests

_PROXY_CONTAINER="stealthy-auto-browse-test-proxy"
_PROXY_WEBSERVER="stealthy-auto-browse-test-proxy-web"
_PROXY_BROWSER="stealthy-auto-browse-test-proxy-browser"

test_proxy() {
    local proxy_ip web_ip browser_ip resp

    # --- Start HTTP proxy container ---
    docker rm -f "$_PROXY_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$_PROXY_CONTAINER" \
        -v "$WORKDIR/tests/apps/http_proxy.py:/proxy.py:ro" \
        python:3.12-slim python /proxy.py 8888 >/dev/null
    EXTRA_CONTAINERS+=("$_PROXY_CONTAINER")

    proxy_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$_PROXY_CONTAINER")
    for _ in $(seq 1 20); do
        docker logs "$_PROXY_CONTAINER" 2>&1 | grep -q "PROXY_READY" && break
        sleep 1
    done

    # --- Start web server container (target for proxied request) ---
    docker rm -f "$_PROXY_WEBSERVER" >/dev/null 2>&1 || true
    docker run -d --name "$_PROXY_WEBSERVER" \
        -v "$WORKDIR/tests/apps/http_server.py:/server.py:ro" \
        -v "$FIXTURES_DIR:/srv:ro" \
        python:3.12-slim python /server.py 80 /srv >/dev/null
    EXTRA_CONTAINERS+=("$_PROXY_WEBSERVER")

    web_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$_PROXY_WEBSERVER")
    for _ in $(seq 1 20); do
        docker logs "$_PROXY_WEBSERVER" 2>&1 | grep -q "SERVER_READY" && break
        sleep 1
    done

    # --- Start browser container with proxy ---
    browser_ip=$(start_extra_container "$_PROXY_BROWSER" \
        -e "PROXY_URL=http://$proxy_ip:8888")
    echo "  proxy=$proxy_ip, web=$web_ip, browser=$browser_ip"

    if ! wait_for_api "http://$browser_ip:8080" 180; then
        echo "FAIL: proxy: API not ready"
        echo "  browser logs:"
        docker logs "$_PROXY_BROWSER" 2>&1 | tail -15
        echo "  proxy logs:"
        docker logs "$_PROXY_CONTAINER" 2>&1 | tail -15
        return 1
    fi

    # Navigate to the web server via proxy (plain HTTP IP, no HTTPS upgrade)
    resp=$(post_to "http://$browser_ip:8080" \
        "{\"action\": \"goto\", \"url\": \"http://$web_ip/index.html\"}")
    assert_success "$resp" "proxy: goto through proxy" || {
        echo "  proxy logs:"
        docker logs "$_PROXY_CONTAINER" 2>&1 | tail -5
        echo "  browser logs:"
        docker logs "$_PROXY_BROWSER" 2>&1 | tail -5
        return 1
    }

    # Verify the page loaded correctly
    local title
    title=$(echo "$resp" | json_get "['data']['title']")
    assert_eq "$title" "Test Page" "proxy: page title" || return 1

    # Verify proxy saw the request
    sleep 1
    if ! docker logs "$_PROXY_CONTAINER" 2>&1 | grep -q "PROXIED.*$web_ip"; then
        echo "FAIL: proxy: request not logged by proxy"
        docker logs "$_PROXY_CONTAINER" 2>&1 | tail -10
        return 1
    fi

    echo "OK: proxy (request routed through proxy, page loaded)"

    stop_extra_container "$_PROXY_BROWSER"
    stop_extra_container "$_PROXY_WEBSERVER"
    stop_extra_container "$_PROXY_CONTAINER"
}

ALL_TESTS+=(test_proxy)
