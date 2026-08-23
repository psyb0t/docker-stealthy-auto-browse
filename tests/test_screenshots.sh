#!/bin/bash
# tests/test_screenshots.sh - Screenshot endpoint tests

test_screenshot_browser() {
    local tmpdir="$TESTDATA_DIR/screenshots"
    mkdir -p "$tmpdir"
    curl -sf "$BASE/screenshot/browser" -o "$tmpdir/browser.png"
    # Verify it's a valid PNG with width=1920 (height varies due to browser chrome)
    local dims w
    dims=$(png_dimensions "$tmpdir/browser.png")
    w="${dims%%x*}"
    assert_eq "$w" "1920" "screenshot/browser: width"
}

test_screenshot_desktop() {
    local tmpdir="$TESTDATA_DIR/screenshots"
    mkdir -p "$tmpdir"
    curl -sf "$BASE/screenshot/desktop" -o "$tmpdir/desktop.png"
    # Verify it's a valid PNG with 1920x1080 dimensions (default resolution)
    local dims
    dims=$(png_dimensions "$tmpdir/desktop.png")
    assert_eq "$dims" "1920x1080" "screenshot/desktop: dimensions"
}

test_screenshot_resize() {
    local tmpdir="$TESTDATA_DIR/screenshots"
    mkdir -p "$tmpdir"

    # Get original browser screenshot dimensions for ratio calculations
    curl -sf "$BASE/screenshot/browser" -o "$tmpdir/orig.png"
    local orig_dims orig_w orig_h
    orig_dims=$(png_dimensions "$tmpdir/orig.png")
    orig_w="${orig_dims%%x*}"
    orig_h="${orig_dims##*x}"

    # Cases: "label|query_params|expected_width|expected_height"
    local cases=(
        "width_only|width=800|800|$(( orig_h * 800 / orig_w ))"
        "height_only|height=300|$(( orig_w * 300 / orig_h ))|300"
        "width_and_height|width=400&height=400|400|400"
        "whLargest|whLargest=512|512|$(( orig_h * 512 / orig_w ))"
    )

    local entry label params exp_w exp_h
    for entry in "${cases[@]}"; do
        IFS='|' read -r label params exp_w exp_h <<< "$entry"
        curl -sf "$BASE/screenshot/browser?${params}" -o "$tmpdir/resize_${label}.png"
        local dims w h
        dims=$(png_dimensions "$tmpdir/resize_${label}.png")
        w="${dims%%x*}"
        h="${dims##*x}"
        assert_eq "$w" "$exp_w" "screenshot_resize[$label]: width" || return 1
        assert_eq "$h" "$exp_h" "screenshot_resize[$label]: height" || return 1
    done

    # Also verify desktop resize works
    curl -sf "$BASE/screenshot/desktop?whLargest=256" -o "$tmpdir/resize_desktop.png"
    local desk_dims desk_w
    desk_dims=$(png_dimensions "$tmpdir/resize_desktop.png")
    desk_w="${desk_dims%%x*}"
    # Default is 1920x1080, width is largest so should be 256
    assert_eq "$desk_w" "256" "screenshot_resize[desktop_whLargest]: width" || return 1

    echo "OK: screenshot_resize (${#cases[@]} browser cases + desktop)"
}

ALL_TESTS+=(
    test_screenshot_browser
    test_screenshot_desktop
    test_screenshot_resize
)
