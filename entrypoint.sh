#!/bin/bash

PIDS=()
LISTEN_HOST="${LISTEN_HOST:-localhost}"

cleanup() {
    echo ""
    echo "[*] Shutting down..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait
    echo "[*] Done."
    exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP EXIT

# Start Xvfb (Full HD max, can resize down via API)
if [ -z "$DISPLAY" ] || [ "$DISPLAY" = ":99" ]; then
    DEPTH="${XVFB_DEPTH:-24}"
    Xvfb :99 -screen 0 1920x1080x${DEPTH} -ac +extension GLX +render -noreset &
    PIDS+=($!)
    export DISPLAY=:99
    sleep 0.5

    # Resize to XVFB_RESOLUTION if set to something other than 1920x1080
    TARGET_RES="${XVFB_RESOLUTION:-1920x1080}"
    if [[ "$TARGET_RES" != "1920x1080" ]]; then
        TARGET_W="${TARGET_RES%%x*}"
        TARGET_H="${TARGET_RES#*x}"
        MODELINE=$(cvt "$TARGET_W" "$TARGET_H" 60 2>/dev/null | grep Modeline)
        if [ -n "$MODELINE" ]; then
            MODE_NAME=$(echo "$MODELINE" | sed 's/.*"\([^"]*\)".*/\1/')
            MODE_PARAMS=$(echo "$MODELINE" | sed 's/.*"[^"]*"  *//')
            xrandr --newmode "$MODE_NAME" $MODE_PARAMS 2>/dev/null || true
            xrandr --addmode screen "$MODE_NAME" 2>/dev/null || true
            xrandr -s "$MODE_NAME" 2>/dev/null || true
        fi
    fi
fi

# Start x11vnc
if [ "$LISTEN_HOST" != "localhost" ]; then
    x11vnc -display :99 -rfbport 5901 -nopw -forever -shared &
else
    x11vnc -display :99 -rfbport 5901 -nopw -forever -shared -localhost &
fi
PIDS+=($!)
sleep 0.3

# Start noVNC
websockify --web /usr/share/novnc 5900 localhost:5901 &
PIDS+=($!)
sleep 0.3

# Start session API
python main.py "$@" &
SESSION_PID=$!
PIDS+=($SESSION_PID)

# Wait for API to be ready
for i in {1..30}; do
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
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
echo "  VNC:  http://${LISTEN_HOST}:5900/"
echo "  API:  http://${LISTEN_HOST}:8080  # always listens on 0.0.0.0"
echo ""
echo "  Ctrl+C to exit"
echo "=============================================="
echo ""

# Wait for main.py to exit
wait $SESSION_PID
