#!/bin/bash
# tests/test_fingerprint.sh - Browser-visible fingerprint consistency tests

LIARJS_URL="https://liarjs.dev/"

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
(async () => {
    const sample = "mmmmmmmmmmlli WwWwWw 0123456789 ABCXYZ";
    const fontCanvas = document.createElement("canvas");
    const fontContext = fontCanvas.getContext("2d");
    const measure = family => {
        fontContext.font = `72px ${family}`;
        return fontContext.measureText(sample).width;
    };
    const fontNames = [
        "__definitely_missing_font__",
        "Arimo",
        "Cousine",
        "Tinos",
        "DejaVu Sans",
        "DejaVu Serif",
        "Liberation Sans",
        "Liberation Serif",
        "Ubuntu",
        "Cantarell",
        "Nimbus Sans",
        "sans-serif",
        "serif",
        "monospace",
    ];
    const webglCanvas = document.createElement("canvas");
    const gl = webglCanvas.getContext("webgl");
    const gl2 = webglCanvas.getContext("webgl2");
    const rendererInfo = gl && gl.getExtension("WEBGL_debug_renderer_info");
    const canvasText = "liarjs 😀 Cwm fjord bank glyphs vext quiz 中文 カナ";
    const draw = (context, width, height, text) => {
        context.textBaseline = "top";
        context.font = "14px 'Arial'";
        context.fillStyle = "#f60";
        context.fillRect(0, 0, width, height);
        context.fillStyle = "#069";
        context.fillText(text, 2, 2);
        context.fillStyle = "rgba(102, 204, 0, 0.7)";
        context.font = "18px 'Times New Roman'";
        context.fillText(text, 4, 24);
        context.strokeStyle = "rgba(0,120,200,0.8)";
        context.beginPath();
        context.arc(240, 30, 18, 0, Math.PI * 2);
        context.stroke();
        context.globalCompositeOperation = "multiply";
        const gradient = context.createLinearGradient(0, 0, width, height);
        gradient.addColorStop(0, "#f0f");
        gradient.addColorStop(1, "#0ff");
        context.fillStyle = gradient;
        context.fillRect(width * 0.6, 0, width * 0.4, height);
        context.globalCompositeOperation = "source-over";
    };
    const hashPixels = pixels => {
        let hash = 5381;
        for (const value of pixels) {
            hash = ((hash << 5) + hash + value) >>> 0;
        }
        return hash;
    };
    const htmlCanvas = document.createElement("canvas");
    htmlCanvas.width = 280;
    htmlCanvas.height = 60;
    const htmlContext = htmlCanvas.getContext("2d");
    draw(htmlContext, htmlCanvas.width, htmlCanvas.height, canvasText);
    const htmlHash = hashPixels(
        htmlContext.getImageData(0, 0, htmlCanvas.width, htmlCanvas.height).data,
    );
    const mainCanvas = new OffscreenCanvas(280, 60);
    const mainContext = mainCanvas.getContext("2d");
    draw(mainContext, mainCanvas.width, mainCanvas.height, canvasText);
    const mainHash = hashPixels(
        mainContext.getImageData(0, 0, mainCanvas.width, mainCanvas.height).data,
    );
    const workerSource = `
        "use strict";
        const draw = ${draw.toString()};
        const hashPixels = ${hashPixels.toString()};
        onmessage = event => {
            const canvas = new OffscreenCanvas(event.data.width, event.data.height);
            const context = canvas.getContext("2d");
            draw(context, event.data.width, event.data.height, event.data.text);
            postMessage(hashPixels(context.getImageData(
                0, 0, event.data.width, event.data.height,
            ).data));
        };
    `;
    const workerUrl = URL.createObjectURL(
        new Blob([workerSource], {type: "text/javascript"}),
    );
    const workerHash = await new Promise((resolve, reject) => {
        const worker = new Worker(workerUrl);
        const timeout = setTimeout(() => {
            worker.terminate();
            reject(new Error("worker canvas probe timed out"));
        }, 5000);
        worker.onmessage = event => {
            clearTimeout(timeout);
            worker.terminate();
            resolve(event.data);
        };
        worker.onerror = event => {
            clearTimeout(timeout);
            worker.terminate();
            reject(new Error(event.message));
        };
        worker.postMessage({width: 280, height: 60, text: canvasText});
    });
    URL.revokeObjectURL(workerUrl);
    return {
        fonts: Object.fromEntries(
            fontNames.map(name => [name, {
                available: document.fonts.check(`72px "${name}"`),
                width: measure(`"${name}"`),
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
        webgl2: gl2 ? {extensions: gl2.getSupportedExtensions()} : null,
        canvas: {htmlHash, mainHash, workerHash},
    };
})()
"""

fingerprint = post({"action": "eval", "expression": expression})["result"]
fonts = fingerprint["fonts"]
problems = []

for name in ("Arimo", "Cousine", "Tinos"):
    if fonts[name]["available"] is not True:
        problems.append(f"bundled font is unavailable: {name}")

linux_signature_fonts = (
    "DejaVu Sans",
    "DejaVu Serif",
    "Liberation Sans",
    "Liberation Serif",
    "Ubuntu",
    "Cantarell",
    "Nimbus Sans",
)
missing_width = round(fonts["__definitely_missing_font__"]["width"], 3)
resolved_linux_fonts = sum(
    round(fonts[name]["width"], 3) != missing_width
    for name in linux_signature_fonts
)
if resolved_linux_fonts < 4:
    problems.append(
        "fewer than half of the Linux signature fonts resolve: "
        f"{resolved_linux_fonts}/{len(linux_signature_fonts)}"
    )

generic_widths = {
    round(fonts[name]["width"], 3)
    for name in ("sans-serif", "serif", "monospace")
}
if len(generic_widths) != 3:
    problems.append("sans-serif, serif, and monospace collapse to one metric")

if all(
    round(fonts[name]["width"], 3) == missing_width
    for name in ("Cousine", "Tinos")
):
    problems.append("bundled fonts collapse to the missing-font fallback")

webgl = fingerprint["webgl"]
webgl2 = fingerprint["webgl2"]
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
    extensions = webgl["extensions"]
    if webgl2 is not None:
        extensions += webgl2["extensions"]
    exposed_mobile_extensions = mobile_texture_extensions.intersection(extensions)
    if exposed_mobile_extensions:
        problems.append(
            "desktop WebGL renderer exposes mobile texture extensions: "
            + ", ".join(sorted(exposed_mobile_extensions))
        )

canvas = fingerprint["canvas"]
if canvas["htmlHash"] != canvas["mainHash"]:
    problems.append("HTML canvas and main-thread OffscreenCanvas hashes disagree")
if canvas["mainHash"] != canvas["workerHash"]:
    problems.append("main-thread and worker canvas hashes disagree")

assert not problems, json.dumps(
    {"problems": problems, "fingerprint": fingerprint},
    indent=2,
)
print("OK: fingerprint_consistency (fonts, canvas, and WebGL agree)")
PYEOF
    ) || {
        echo "FAIL: fingerprint_consistency"
        return 1
    }
    echo "$result"
}

test_liarjs_live_fingerprint_consistency() {
    local attempts_remaining=30
    local response
    local text=""
    local expected
    local -a expected_sections=(
        $'OS by font set\nlinux (UA claims linux)'
        $'OS by font metrics\nlinux'
        $'Mobile-only GL extensions\nnone'
        $'OffscreenCanvas matches main\nyes'
        $'WORKER ↔ MAIN THREAD\nAgreement\nall probed fields agree'
    )

    response=$(post "{\"action\": \"goto\", \"url\": \"$LIARJS_URL\"}") || {
        echo "FAIL: liarjs navigation failed"
        return 1
    }
    assert_success "$response" "liarjs navigation" || return 1

    while ((attempts_remaining > 0)); do
        response=$(post '{"action": "get_text"}') || {
            echo "FAIL: liarjs text retrieval failed"
            return 1
        }
        text=$(json_get '["data"]["text"]' <<<"$response")
        if grep -Fq "WHAT I CAUGHT" <<<"$text"; then
            break
        fi
        attempts_remaining=$((attempts_remaining - 1))
        sleep 1
    done

    if ! grep -Fq "WHAT I CAUGHT" <<<"$text"; then
        echo "FAIL: liarjs scan did not finish"
        return 1
    fi
    for expected in "${expected_sections[@]}"; do
        if ! grep -Fq "$expected" <<<"$text"; then
            echo "FAIL: liarjs did not report: ${expected//$'\n'/ /}"
            return 1
        fi
    done

    echo "OK: liarjs live fingerprint consistency"
}

ALL_TESTS+=(test_fingerprint_consistency)
