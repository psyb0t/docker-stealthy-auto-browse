#!/bin/bash
# tests/test_extensions.sh - Default extensions are installed in the running browser

readonly DEFAULT_EXTENSION_IDS=(
    "uBlock0@raymondhill.net"
    "{b86e4813-687a-43e6-ab65-0bde4ab75758}"
    "{74145f27-f039-47ce-a470-a662b129930a}"
    "gdpr@cavi.au.dk"
)
readonly FIREFOX_EXTENSIONS_STATE="/userdata/extensions.json"
readonly EXTENSION_STATE_WAIT_SECONDS=30

# Prints the IDs of the active add-ons Firefox recorded in the profile.
_active_extension_ids() {
    docker exec "$CONTAINER_NAME" python3 -c "
import json, sys
try:
    state = json.load(open('$FIREFOX_EXTENSIONS_STATE'))
except (OSError, ValueError):
    sys.exit(0)
for addon in state.get('addons', []):
    if addon.get('active'):
        print(addon['id'])
"
}

_missing_extension_ids() {
    local active="$1" extension_id
    for extension_id in "${DEFAULT_EXTENSION_IDS[@]}"; do
        if ! grep -qxF "$extension_id" <<<"$active"; then
            echo "$extension_id"
        fi
    done
}

test_default_extensions_loaded() {
    local resp active missing=""

    resp=$(post "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
    assert_success "$resp" "default extensions: goto" || return 1

    # Firefox installs force_installed policy extensions asynchronously after
    # startup and records them in extensions.json once they are active.
    for _ in $(seq 1 "$EXTENSION_STATE_WAIT_SECONDS"); do
        active=$(_active_extension_ids)
        missing=$(_missing_extension_ids "$active")
        if [ -z "$missing" ]; then
            echo "OK: default extensions loaded (${#DEFAULT_EXTENSION_IDS[@]} active)"
            return 0
        fi
        sleep 1
    done

    echo "  FAIL: default extensions not active after ${EXTENSION_STATE_WAIT_SECONDS}s: $(echo "$missing" | tr '\n' ' ')"
    return 1
}

ALL_TESTS+=(test_default_extensions_loaded)
