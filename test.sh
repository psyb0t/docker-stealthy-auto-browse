#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source shared helpers and variables
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$SCRIPT_DIR/tests/common.sh"

# Source all test files (each appends to ALL_TESTS)
for f in "$SCRIPT_DIR"/tests/test_*.sh; do
    # shellcheck disable=SC1090 # The glob selects every repository-owned test module.
    source "$f"
done

# --- Tests that use the shared main container (must run sequentially) ---
# These use $BASE (the main browser container started by setup)
MAIN_TESTS=(
    # test_api.sh
    test_health
    test_ping
    test_state
    test_goto
    test_page_content
    test_get_interactive_elements
    test_get_resolution
    test_calibrate
    # test_cookies.sh
    test_set_get_cookies
    test_delete_cookies
    test_storage
    # test_console.sh (3 of 4 use main container)
    test_console_log
    test_console_log_disabled
    test_console_log_getclear
    # test_dialogs.sh
    test_dialogs
    test_dialog_confirm_changes_page
    # test_downloads.sh
    test_download
    # test_input.sh
    test_mouse_move
    test_mouse_click
    test_system_click
    test_scroll
    test_system_type
    test_send_key
    test_send_key_pagedown
    test_fullscreen
    test_selector_input
    test_click
    # test_mcp.sh
    test_mcp
    # test_navigation.sh
    test_refresh
    test_refresh_wait_until
    test_goto_referer
    # test_network.sh
    test_network_log
    # test_run_script.sh
    test_run_script_json_steps
    test_run_script_yaml
    test_run_script_on_error_stop
    test_run_script_on_error_continue
    test_run_script_no_steps
    test_run_script_control_flow
    test_request_lock
    # test_screenshots.sh
    test_screenshot_browser
    test_screenshot_desktop
    test_screenshot_resize
    # test_tabs.sh
    test_list_tabs
    test_new_tab
    test_switch_tab
    test_switch_tab_foreground
    test_switch_tab_keyboard
    test_switch_tab_no_link_activation
    test_close_tab
    # test_uploads.sh
    test_upload_file
    # test_waits.sh
    test_waits
    # test_recording.sh
    test_recording_basic
    test_recording_stop_without_start
    test_recording_double_start
    test_recording_bad_slug
    test_recording_viewport_uses_calibration
    test_recording_hide_cursor
    test_recording_slug_collision
    # test_recovery.sh
    test_recovery_camoufox_crash
)

# --- CLI ---

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
fi

# Max parallel extra tests (don't kill the machine)
MAX_PARALLEL="${MAX_PARALLEL:-3}"

TESTS_TO_RUN=("${@}")
if [ ${#TESTS_TO_RUN[@]} -eq 0 ]; then
    TESTS_TO_RUN=("${ALL_TESTS[@]}")
fi

# Validate test names
for t in "${TESTS_TO_RUN[@]}"; do
    if ! declare -f "$t" >/dev/null 2>&1; then
        echo "Unknown test: $t"
        echo ""
        usage
        exit 1
    fi
done

# Prepare results dir
mkdir -p "$RESULTS_DIR"

_ts() {
    date "+%Y-%m-%d %H:%M:%S"
}

# Heavy tests that must run alone (not parallel with others)
HEAVY_TESTS=(test_mcp_cluster_mode test_cluster)

# Classify requested tests into main vs extra vs heavy
MAIN_TO_RUN=()
EXTRA_TO_RUN=()
HEAVY_TO_RUN=()

_is_main() {
    local t="$1"
    for m in "${MAIN_TESTS[@]}"; do
        [ "$t" = "$m" ] && return 0
    done
    return 1
}

_is_heavy() {
    local t="$1"
    for h in "${HEAVY_TESTS[@]}"; do
        [ "$t" = "$h" ] && return 0
    done
    return 1
}

for t in "${TESTS_TO_RUN[@]}"; do
    if _is_main "$t"; then
        MAIN_TO_RUN+=("$t")
    elif _is_heavy "$t"; then
        HEAVY_TO_RUN+=("$t")
    else
        EXTRA_TO_RUN+=("$t")
    fi
done

trap cleanup EXIT
setup

echo ""
echo "=== Running ${#TESTS_TO_RUN[@]} test(s): ${#MAIN_TO_RUN[@]} sequential + ${#EXTRA_TO_RUN[@]} parallel (max $MAX_PARALLEL) + ${#HEAVY_TO_RUN[@]} heavy ==="
echo ""

FAILED=0
PASSED=0

# --- Run main container tests sequentially ---

if [ ${#MAIN_TO_RUN[@]} -gt 0 ]; then
    echo "--- Sequential tests (shared container) ---"
    for t in "${MAIN_TO_RUN[@]}"; do
        local_log="$RESULTS_DIR/${t}.log"
        echo "=== $t === [$(_ts)]" > "$local_log"

        echo -n "[$(_ts)] $t ... "
        test_setup
        if $t >> "$local_log" 2>&1; then
            PASSED=$((PASSED + 1))
            echo "OK"
        else
            FAILED=$((FAILED + 1))
            echo "FAIL (see tests/results/${t}.log)"
        fi
        test_teardown
    done
    echo ""
fi

# --- Run extra container tests in parallel ---

if [ ${#EXTRA_TO_RUN[@]} -gt 0 ]; then
    echo "--- Parallel tests (own containers, max $MAX_PARALLEL at a time) ---"

    declare -A BG_PIDS=()
    active=0

    for t in "${EXTRA_TO_RUN[@]}"; do
        # Wait for a slot if at capacity
        while [ $active -ge "$MAX_PARALLEL" ]; do
            for _t in "${!BG_PIDS[@]}"; do
                if ! kill -0 "${BG_PIDS[$_t]}" 2>/dev/null; then
                    wait "${BG_PIDS[$_t]}" && _rc=0 || _rc=$?
                    if [ "$_rc" -eq 0 ]; then
                        PASSED=$((PASSED + 1))
                        echo "[$(_ts)] $_t ... OK"
                    else
                        FAILED=$((FAILED + 1))
                        echo "[$(_ts)] $_t ... FAIL (see tests/results/${_t}.log)"
                    fi
                    unset "BG_PIDS[$_t]"
                    active=$((active - 1))
                fi
            done
            [ $active -ge "$MAX_PARALLEL" ] && sleep 1
        done

        # Launch test in background
        local_log="$RESULTS_DIR/${t}.log"
        echo "=== $t === [$(_ts)]" > "$local_log"
        (
            set +e
            $t >> "$local_log" 2>&1
            exit $?
        ) &
        BG_PIDS["$t"]=$!
        active=$((active + 1))
        echo "[$(_ts)] $t ... started"
    done

    # Wait for remaining
    for _t in "${!BG_PIDS[@]}"; do
        wait "${BG_PIDS[$_t]}" && _rc=0 || _rc=$?
        if [ "$_rc" -eq 0 ]; then
            PASSED=$((PASSED + 1))
            echo "[$(_ts)] $_t ... OK"
        else
            FAILED=$((FAILED + 1))
            echo "[$(_ts)] $_t ... FAIL (see tests/results/${_t}.log)"
        fi
    done
    echo ""
fi

# --- Run heavy tests sequentially (resource-intensive, run alone) ---

if [ ${#HEAVY_TO_RUN[@]} -gt 0 ]; then
    echo "--- Heavy tests (run alone) ---"
    for t in "${HEAVY_TO_RUN[@]}"; do
        local_log="$RESULTS_DIR/${t}.log"
        echo "=== $t === [$(_ts)]" > "$local_log"

        echo -n "[$(_ts)] $t ... "
        if $t >> "$local_log" 2>&1; then
            PASSED=$((PASSED + 1))
            echo "OK"
        else
            FAILED=$((FAILED + 1))
            echo "FAIL (see tests/results/${t}.log)"
        fi
    done
    echo ""
fi

echo "=== [$(_ts)] Results: $PASSED passed, $FAILED failed ==="

if [ "$FAILED" -gt 0 ]; then
    echo ""
    echo "Failed test logs:"
    for t in "${TESTS_TO_RUN[@]}"; do
        local_log="$RESULTS_DIR/${t}.log"
        if [ -f "$local_log" ] && grep -qiE "FAIL" "$local_log" 2>/dev/null; then
            echo "  tests/results/${t}.log"
        fi
    done
    exit 1
fi
