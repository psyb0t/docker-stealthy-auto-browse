#!/bin/bash

# Determine target user: PUID/PGID env vars, or default browser (1000)
TARGET_UID="${PUID:-1000}"
TARGET_GID="${PGID:-$TARGET_UID}"

# Fix ownership of writable dirs (runs as root)
_fix_perms() {
    for dir in /userdata /loaders /recordings; do
        [ -d "$dir" ] || continue

        # Skip if already correct
        if [ "$(stat -c '%u:%g' "$dir")" = "$TARGET_UID:$TARGET_GID" ]; then
            continue
        fi

        chown -R "$TARGET_UID:$TARGET_GID" "$dir"
    done

    # Camoufox — only GeoIP db files, not the whole tree
    local cfox="/usr/local/lib/python3.12/site-packages/camoufox"
    if [ -d "$cfox" ]; then
        find "$cfox" -name "*.mmdb" \
            -exec chown "$TARGET_UID:$TARGET_GID" {} + 2>/dev/null || true
    fi

    # Browser home dir — camoufox cache lives here regardless
    # of which UID runs the app
    if [ -d /home/browser ]; then
        chown -R "$TARGET_UID:$TARGET_GID" /home/browser
    fi
}

_fix_perms

# Drop privileges — re-exec this script as target user
# If already non-root (docker --user), skip — perms were
# best-effort above (chown may have failed silently)
if [ "$(id -u)" = "0" ]; then
    exec gosu "$TARGET_UID:$TARGET_GID" \
        env HOME=/home/browser "$0" "$@"
fi

# Ensure HOME is set for non-root users without passwd entry
export HOME="${HOME:-/home/browser}"

PIDS=()

cleanup() {
    echo "" >&2
    echo "[*] Shutting down..." >&2
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait
    echo "[*] Done." >&2
    exit "${EXIT_CODE:-0}"
}

trap cleanup SIGINT SIGTERM SIGHUP EXIT

# Detect script mode early - before anything prints to stdout
SCRIPT_MODE=false
for arg in "$@"; do
    if [ "$arg" = "--script" ]; then
        SCRIPT_MODE=true
        break
    fi
done

# --script reads YAML from stdin, save to temp file before
# background processes consume stdin
if [ "$SCRIPT_MODE" = "true" ]; then
    _STDIN_FILE=$(mktemp /tmp/script-stdin-XXXXXX.yaml)
    cat > "$_STDIN_FILE"
    set -- --script "$_STDIN_FILE"
fi

# In script mode, save real stdout for main.py, redirect shell stdout to stderr
# so VNC/websockify noise doesn't pollute the JSON output
if [ "$SCRIPT_MODE" = "true" ]; then
    exec 3>&1 1>&2
fi

# Start Xvfb at the requested resolution.
#
# The framebuffer MUST be allocated at XVFB_RESOLUTION up front. Xvfb's root
# framebuffer is fixed at the size given to `-screen 0 WxHxD` and cannot grow
# later — `xrandr` can only switch to modes that FIT inside that allocation.
# The old approach (start at 1920x1080, then xrandr to a larger mode) silently
# failed for anything taller/wider than 1080p: the reported mode changed but
# the real framebuffer stayed 1920x1080, so ffmpeg x11grab (viewport recording)
# tried to capture outside the screen and died with
# "Capture area ... outside the screen size 1920x1080". Allocating the
# framebuffer at the requested size makes recording work at any resolution.
if [ -z "$DISPLAY" ] || [ "$DISPLAY" = ":99" ]; then
    DEPTH="${XVFB_DEPTH:-24}"
    RES="${XVFB_RESOLUTION:-1920x1080}"
    Xvfb :99 -screen 0 "${RES}x${DEPTH}" -ac +extension GLX +render -noreset &
    PIDS+=($!)
    export DISPLAY=:99
    sleep 0.5

    # Start window manager (title bars + resize handles for popup windows)
    openbox &
    PIDS+=($!)
    sleep 0.3
fi

VNC_LISTEN_HOST="${VNC_LISTEN_HOST:-0.0.0.0}"
VNC_LISTEN_PORT="${VNC_LISTEN_PORT:-5900}"
HTTP_LISTEN_HOST="${HTTP_LISTEN_HOST:-0.0.0.0}"
HTTP_LISTEN_PORT="${HTTP_LISTEN_PORT:-8080}"

# Start x11vnc
x11vnc -display :99 -rfbport 5901 -nopw -forever -shared -listen "$VNC_LISTEN_HOST" &
PIDS+=($!)
sleep 0.3

# Start noVNC (websockify)
websockify --web /usr/share/novnc "$VNC_LISTEN_HOST:$VNC_LISTEN_PORT" localhost:5901 &
PIDS+=($!)
sleep 0.3

# Start session (restore real stdout for main.py in script mode)
if [ "$SCRIPT_MODE" = "true" ]; then
    python main.py "$@" 1>&3 &
else
    python main.py "$@" &
fi
SESSION_PID=$!
PIDS+=($SESSION_PID)

if [ "$SCRIPT_MODE" = "false" ]; then
    # Wait for API to be ready
    for i in {1..30}; do
        if curl -s "http://localhost:${HTTP_LISTEN_PORT}/health" > /dev/null 2>&1; then
            break
        fi
        sleep 0.2
    done

    # Banner
    echo ""
    echo "=============================================="
    echo "  STEALTHY AUTO-BROWSE"
    echo "=============================================="
    echo ""
    echo "  VNC:  http://${VNC_LISTEN_HOST}:${VNC_LISTEN_PORT}/"
    echo "  API:  http://${HTTP_LISTEN_HOST}:${HTTP_LISTEN_PORT}"
    echo ""
    echo "  Ctrl+C to exit"
    echo "=============================================="
    echo ""
fi

# Wait for main.py to exit and preserve its exit code
wait $SESSION_PID
EXIT_CODE=$?
