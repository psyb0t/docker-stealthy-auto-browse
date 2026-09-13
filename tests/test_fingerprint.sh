#!/bin/bash
# tests/test_fingerprint.sh - Browser-visible fingerprint consistency tests

test_fingerprint_consistency() {
    local result
    result=$(
        python3 - "$BASE" <<'PYEOF'
import json
import sys
import urllib.request

base_url = sys.argv[1]


def post(payload):
    request = urllib.request.Request(
        base_url + "/",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode())
    assert body["success"] is True, body
    return body["data"]


expression = r"""
(() => {
    const sample = "mmmmmmmmmmlliWWWWWWWWWW0123456789";
    const fontCanvas = document.createElement("canvas");
    const fontContext = fontCanvas.getContext("2d");
    const measure = family => {
        fontContext.font = `32px ${family}`;
        return fontContext.measureText(sample).width;
    };
    const fontNames = [
        "__definitely_missing_font__",
        "Arimo",
        "Cousine",
        "Tinos",
        "sans-serif",
        "serif",
        "monospace",
    ];
    const webglCanvas = document.createElement("canvas");
    const gl = webglCanvas.getContext("webgl");
    const rendererInfo = gl && gl.getExtension("WEBGL_debug_renderer_info");
    return {
        fonts: Object.fromEntries(
            fontNames.map(name => [name, {
                available: document.fonts.check(`32px ${name}`),
                width: measure(name),
            }]),
        ),
        webgl: gl ? {
            vendor: rendererInfo
                ? gl.getParameter(rendererInfo.UNMASKED_VENDOR_WEBGL)
                : null,
            renderer: rendererInfo
                ? gl.getParameter(rendererInfo.UNMASKED_RENDERER_WEBGL)
                : null,
            extensions: gl.getSupportedExtensions(),
        } : null,
    };
})()
"""

fingerprint = post({"action": "eval", "expression": expression})["result"]
fonts = fingerprint["fonts"]
problems = []

for name in ("Arimo", "Cousine", "Tinos"):
    if fonts[name]["available"] is not True:
        problems.append(f"bundled font is unavailable: {name}")

generic_widths = {
    round(fonts[name]["width"], 3)
    for name in ("sans-serif", "serif", "monospace")
}
if len(generic_widths) != 3:
    problems.append("sans-serif, serif, and monospace collapse to one metric")

missing_width = round(fonts["__definitely_missing_font__"]["width"], 3)
if all(
    round(fonts[name]["width"], 3) == missing_width
    for name in ("Cousine", "Tinos")
):
    problems.append("bundled fonts collapse to the missing-font fallback")

webgl = fingerprint["webgl"]
if webgl is None:
    problems.append("WebGL is unavailable")
else:
    if not webgl["vendor"] or not webgl["renderer"]:
        problems.append("WebGL does not expose a vendor and renderer")
    mobile_texture_extensions = {
        "WEBGL_compressed_texture_astc",
        "WEBGL_compressed_texture_etc",
        "WEBGL_compressed_texture_etc1",
    }
    exposed_mobile_extensions = mobile_texture_extensions.intersection(
        webgl["extensions"]
    )
    if exposed_mobile_extensions:
        problems.append(
            "desktop WebGL renderer exposes mobile texture extensions: "
            + ", ".join(sorted(exposed_mobile_extensions))
        )

assert not problems, json.dumps(
    {"problems": problems, "fingerprint": fingerprint},
    indent=2,
)
print("OK: fingerprint_consistency (font metrics and WebGL extensions agree)")
PYEOF
    ) || {
        echo "FAIL: fingerprint_consistency"
        return 1
    }
    echo "$result"
}

ALL_TESTS+=(test_fingerprint_consistency)
