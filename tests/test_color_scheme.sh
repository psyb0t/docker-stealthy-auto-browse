#!/bin/bash
# tests/test_color_scheme.sh - Page color scheme emulation tests

readonly COLOR_SCHEME_DARK="dark"
readonly COLOR_SCHEME_LIGHT="light"
readonly COLOR_SCHEME_NO_PREFERENCE="no-preference"
readonly COLOR_SCHEME_DEFAULT="default"
readonly COLOR_SCHEME_INVALID_INPUTS=("purple" "" "DARK" " dark")

_set_color_scheme() {
    post "{\"action\": \"set_color_scheme\", \"scheme\": \"$1\"}"
}

_media_matches() {
    post "{\"action\": \"eval\", \"expression\": \"matchMedia('(prefers-color-scheme: $1)').matches\"}" |
        json_get "['data']['result']"
}

_assert_scheme() {
    local resp="$1"
    local expected="$2"
    assert_success "$resp" "set_color_scheme: $expected" || return 1
    assert_eq "$(echo "$resp" | json_get "['data']['scheme']")" "$expected" "set_color_scheme: $expected response" || return 1
}

test_color_scheme() {
    local resp invalid

    resp=$(post "{\"action\": \"goto\", \"url\": \"$TEST_PAGE\"}")
    assert_success "$resp" "set_color_scheme: goto" || return 1

    _assert_scheme "$(_set_color_scheme "$COLOR_SCHEME_DARK")" "$COLOR_SCHEME_DARK" || return 1
    assert_eq "$(_media_matches dark)" "True" "set_color_scheme: dark on open tab" || return 1

    # Setting the same scheme again is accepted and keeps it.
    _assert_scheme "$(_set_color_scheme "$COLOR_SCHEME_DARK")" "$COLOR_SCHEME_DARK" || return 1

    resp=$(post "{\"action\": \"new_tab\", \"url\": \"$TEST_PAGE\"}")
    assert_success "$resp" "set_color_scheme: new_tab" || return 1
    assert_eq "$(_media_matches dark)" "True" "set_color_scheme: dark on new tab" || return 1

    _assert_scheme "$(_set_color_scheme "$COLOR_SCHEME_LIGHT")" "$COLOR_SCHEME_LIGHT" || return 1
    assert_eq "$(_media_matches light)" "True" "set_color_scheme: light" || return 1
    assert_eq "$(_media_matches dark)" "False" "set_color_scheme: light is not dark" || return 1

    _assert_scheme "$(_set_color_scheme "$COLOR_SCHEME_NO_PREFERENCE")" "$COLOR_SCHEME_NO_PREFERENCE" || return 1
    # Firefox has no no-preference media value, so pages see light.
    assert_eq "$(_media_matches dark)" "False" "set_color_scheme: no-preference is not dark" || return 1

    for invalid in "${COLOR_SCHEME_INVALID_INPUTS[@]}"; do
        resp=$(_set_color_scheme "$invalid")
        if echo "$resp" | grep -qE '"success":\s*true'; then
            echo "  FAIL: set_color_scheme: invalid scheme '$invalid' accepted: $resp"
            return 1
        fi
    done

    resp=$(post '{"action": "set_color_scheme"}')
    if echo "$resp" | grep -qE '"success":\s*true'; then
        echo "  FAIL: set_color_scheme: missing scheme accepted: $resp"
        return 1
    fi

    _assert_scheme "$(_set_color_scheme "$COLOR_SCHEME_DARK")" "$COLOR_SCHEME_DARK" || return 1
    resp=$(_set_color_scheme "${COLOR_SCHEME_INVALID_INPUTS[0]}")
    # A rejected scheme leaves the previous one in place.
    assert_eq "$(_media_matches dark)" "True" "set_color_scheme: invalid keeps dark" || return 1

    _assert_scheme "$(_set_color_scheme "$COLOR_SCHEME_DEFAULT")" "$COLOR_SCHEME_DEFAULT" || return 1

    _reset_to_single_tab
    echo "OK: set_color_scheme (dark, repeat, new tab, light, no-preference, invalid, missing, default)"
}

ALL_TESTS+=(test_color_scheme)
