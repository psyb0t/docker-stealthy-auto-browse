#!/bin/bash
# tests/test_identity_upgrade.sh - A /userdata identity saved by a 3.0.x image
# (Camoufox 0.4, Firefox 152) must keep working after the Camoufox upgrade.
#
# The fixture is the props file a 3.0.x container wrote on first start. The
# browser must launch with it, keep the identity (platform, fonts, GPU), move
# the User-Agent to the installed Firefox, drop the values stock Firefox
# decides for itself, and stop forcing a window size the real window no
# longer has.

readonly IDENTITY_UPGRADE_FIXTURE="$SCRIPT_DIR/tests/fixtures/userdata-3.0/stealthy-auto-browse-props.json"
readonly IDENTITY_PROPS_PATH="/userdata/stealthy-auto-browse-props.json"
readonly IDENTITY_UPGRADE_READY_ATTEMPTS=90
# A profile copied in by another user must still be writable by the browser.
readonly IDENTITY_UPGRADE_FOREIGN_ID=1001

# _identity_props <container> <python expression over the dict c>
_identity_props() {
    docker exec "$1" cat "$IDENTITY_PROPS_PATH" |
        python3 -c "import json, sys; c = json.load(sys.stdin); print($2)"
}

test_persisted_identity_upgrade() {
    local name="${CONTAINER_NAME}-identity-upgrade"
    local ip base firefox_major resp

    docker rm -f "$name" >/dev/null 2>&1 || true
    docker create --name "$name" "$IMAGE_NAME:$TEST_TAG" >/dev/null
    EXTRA_CONTAINERS+=("$name")
    tar -C "$(dirname "$IDENTITY_UPGRADE_FIXTURE")" \
        --owner="$IDENTITY_UPGRADE_FOREIGN_ID" --group="$IDENTITY_UPGRADE_FOREIGN_ID" \
        -cf - "$(basename "$IDENTITY_UPGRADE_FIXTURE")" |
        docker cp - "$name:$(dirname "$IDENTITY_PROPS_PATH")"
    docker start "$name" >/dev/null
    ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$name")
    base="http://${ip}:${INTERNAL_PORT}"

    # A container that exits frees its IP for another test container, so a
    # ready API alone does not prove this one is up.
    if ! wait_for_api "$base" "$IDENTITY_UPGRADE_READY_ATTEMPTS" ||
        [ "$(docker inspect -f '{{.State.Running}}' "$name")" != "true" ]; then
        echo "FAIL: identity upgrade: browser did not stay up with a 3.0.x identity owned by another user"
        docker logs "$name" 2>&1 | tail -30
        stop_extra_container "$name"
        return 1
    fi

    assert_success "$(post_to "$base" '{"action": "goto", "url": "about:blank"}')" "identity upgrade: browser works" >/dev/null || {
        stop_extra_container "$name"
        return 1
    }

    # The browser is installed in the browser user's cache, not root's.
    firefox_major=$(docker exec -u browser "$name" python3 -c \
        'from camoufox.pkgman import installed_verstr; print(installed_verstr().split(".", 1)[0])')
    resp=$(post_to "$base" '{"action": "eval", "expression": "JSON.stringify([navigator.userAgent, navigator.platform, navigator.doNotTrack, navigator.globalPrivacyControl, mozInnerScreenY + innerHeight <= screen.height && outerHeight <= screen.height])"}' |
        json_get "['data']['result']")
    echo "$resp" | FIREFOX_MAJOR="$firefox_major" python3 -c '
import json, os, sys
user_agent, platform, dnt, gpc, fits = json.loads(sys.stdin.read())
major = os.environ["FIREFOX_MAJOR"]
assert user_agent == f"Mozilla/5.0 (X11; Linux x86_64; rv:{major}.0) Gecko/20100101 Firefox/{major}.0", user_agent
assert platform == "Linux x86_64", platform
assert dnt == "unspecified", dnt
assert gpc is False, gpc
assert fits is True, "page or window does not fit the screen"
' || {
        echo "  FAIL: identity upgrade: page sees $resp (installed Firefox $firefox_major)"
        stop_extra_container "$name"
        return 1
    }

    resp=$(_identity_props "$name" \
        '[c["webGl:vendor"], c["webGl:renderer"], "Firefox/'"$firefox_major"'.0" in c["navigator.userAgent"], any(k.startswith("window.") and k != "window.devicePixelRatio" for k in c), "navigator.doNotTrack" in c, "Arimo" in c["fonts"]]')
    assert_eq "$resp" "['Intel', 'Intel(R) HD Graphics, or similar', True, False, False, True]" \
        "identity upgrade: saved identity keeps the GPU and fonts, updates the UA, drops forced values" || {
        stop_extra_container "$name"
        return 1
    }

    stop_extra_container "$name"
    echo "OK: a 3.0.x identity launches and is upgraded in place"
}

ALL_TESTS+=(test_persisted_identity_upgrade)
