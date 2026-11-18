#!/bin/bash
# tests/test_puid.sh - PUID/PGID user override tests

# Get the UID of the server process (python -m autolinuxdesk) inside a container
_app_uid() {
    docker exec "$1" \
        ps -o uid= -p "$(docker exec "$1" pgrep -f 'python -m autolinuxdesk')" \
        | tr -d ' '
}

_app_gid() {
    docker exec "$1" \
        stat -c '%g' "/proc/$(docker exec "$1" pgrep -f 'python -m autolinuxdesk')" \
        2>/dev/null | tr -d ' '
}

test_puid_default() {
    local name="${CONTAINER_NAME}-puid-default"

    local ip base
    ip=$(start_extra_container "$name")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: puid_default: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        return 1
    fi

    # App should run as browser (1000)
    local uid
    uid=$(_app_uid "$name")
    assert_eq "$uid" "1000" \
        "puid_default: app runs as uid 1000" || {
        stop_extra_container "$name"
        return 1
    }

    # /userdata writable by app
    local resp
    resp=$(post_to "$base" '{"action":"ping"}')
    assert_success "$resp" \
        "puid_default: API responds" || {
        stop_extra_container "$name"
        return 1
    }

    stop_extra_container "$name"
    echo "OK: puid_default"
}

test_puid_custom() {
    local name="${CONTAINER_NAME}-puid-custom"

    local ip base
    ip=$(start_extra_container "$name" \
        -e "PUID=1500" -e "PGID=1500")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: puid_custom: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        return 1
    fi

    # App should run as 1500:1500
    local uid gid
    uid=$(_app_uid "$name")
    gid=$(_app_gid "$name")
    assert_eq "$uid" "1500" \
        "puid_custom: app runs as uid 1500" || {
        stop_extra_container "$name"
        return 1
    }
    assert_eq "$gid" "1500" \
        "puid_custom: app runs as gid 1500" || {
        stop_extra_container "$name"
        return 1
    }

    # /userdata owned by 1500:1500
    local owner
    owner=$(docker exec "$name" stat -c '%u:%g' /userdata)
    assert_eq "$owner" "1500:1500" \
        "puid_custom: /userdata owned by 1500:1500" || {
        stop_extra_container "$name"
        return 1
    }

    # API works (proves /userdata writable — app writes props.json on boot)
    local resp
    resp=$(post_to "$base" '{"action":"ping"}')
    assert_success "$resp" \
        "puid_custom: API responds" || {
        stop_extra_container "$name"
        return 1
    }

    stop_extra_container "$name"
    echo "OK: puid_custom"
}

test_puid_only() {
    local name="${CONTAINER_NAME}-puid-only"

    local ip base
    ip=$(start_extra_container "$name" -e "PUID=2000")
    base="http://${ip}:${INTERNAL_PORT}"

    if ! wait_for_api "$base" 90; then
        echo "FAIL: puid_only: API not ready"
        docker logs "$name" 2>&1 | tail -20
        stop_extra_container "$name"
        return 1
    fi

    # GID should default to PUID when PGID not set
    local uid gid
    uid=$(_app_uid "$name")
    gid=$(_app_gid "$name")
    assert_eq "$uid" "2000" \
        "puid_only: app runs as uid 2000" || {
        stop_extra_container "$name"
        return 1
    }
    assert_eq "$gid" "2000" \
        "puid_only: gid defaults to puid 2000" || {
        stop_extra_container "$name"
        return 1
    }

    # API works
    local resp
    resp=$(post_to "$base" '{"action":"ping"}')
    assert_success "$resp" \
        "puid_only: API responds" || {
        stop_extra_container "$name"
        return 1
    }

    stop_extra_container "$name"
    echo "OK: puid_only"
}

ALL_TESTS+=(test_puid_default test_puid_custom test_puid_only)
