#!/bin/bash
# tests/test_extensions.sh - Default extensions are installed in the running browser

readonly DEFAULT_EXTENSION_IDS=(
    "uBlock0@raymondhill.net"
    "{b86e4813-687a-43e6-ab65-0bde4ab75758}"
    "{74145f27-f039-47ce-a470-a662b129930a}"
    "gdpr@cavi.au.dk"
)
readonly FIREFOX_EXTENSIONS_STATE="/userdata/extensions.json"
readonly FIREFOX_EXTENSION_PREFERENCES="/userdata/extension-preferences.json"
readonly EXTENSION_STATE_WAIT_SECONDS=30

# Prints the IDs of the active add-ons Firefox recorded in the profile.
_active_extension_ids() {
    local target_container="$1"
    docker exec "$target_container" python3 -c "
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

test_live_captcha_solver_extensions_loaded() {
    local entry provider extension_id name container_ip base
    local providers=(
        "nopecha|nopecha@stealthy-auto-browse.local"
        "capsolver|support@capsolver.com"
        "capmonster|capmonser-cloud-ext-fire@zennolab.com"
        "nocaptchaai|addon@nocaptchaai.com"
        "yescaptcha|{defcf59e-d157-44f0-b224-4b1f78f85e46}"
        "2captcha|{f8b523da-039a-46c6-8782-cb2cb84f2697}"
    )

    for entry in "${providers[@]}"; do
        IFS='|' read -r provider extension_id <<<"$entry"
        name="${CONTAINER_NAME}-${provider}-live-$$"
        if [ "$provider" = "nopecha" ]; then
            container_ip=$(start_extra_container "$name" \
                -e "CAPTCHA_SOLVER_PROVIDER=$provider")
        else
            container_ip=$(start_extra_container "$name" \
                -e "CAPTCHA_SOLVER_PROVIDER=$provider" \
                -e "CAPTCHA_SOLVER_API_KEY=EXAMPLE-DO-NOT-USE")
        fi
        base="http://${container_ip}:${INTERNAL_PORT}"
        if ! wait_for_api "$base" 90; then
            echo "FAIL: $provider browser did not become ready"
            docker logs "$name" 2>&1 | tail -50
            return 1
        fi

        local loaded=false
        for _ in $(seq 1 "$EXTENSION_STATE_WAIT_SECONDS"); do
            if docker exec "$name" python3 -c "
import json, sys
try:
    state = json.load(open('$FIREFOX_EXTENSION_PREFERENCES'))
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if '$extension_id' in state else 1)
"; then
                loaded=true
                break
            fi
            sleep 1
        done
        if [ "$loaded" != true ]; then
            echo "FAIL: $provider extension did not become active"
            return 1
        fi
        echo "OK: pinned $provider extension loaded"
    done
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
        active=$(_active_extension_ids "$CONTAINER_NAME")
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
