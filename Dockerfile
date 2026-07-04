FROM python:3.12-slim-bookworm

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Timezone - override with -e TZ=Your/Timezone
ENV TZ=UTC

# Install tzdata for proper timezone support
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Base utils
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# X11 and display
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb xauth dbus dbus-x11 x11-xserver-utils xcvt \
    && rm -rf /var/lib/apt/lists/*

# VNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

# Window manager (title bars + resize handles for popup windows)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openbox \
    && rm -rf /var/lib/apt/lists/*

# noVNC auto-connect redirect
RUN echo '<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=vnc.html?autoconnect=true&resize=scale"></head></html>' > /usr/share/novnc/index.html

# Firefox/Camoufox dependencies - install firefox-esr to pull correct GTK deps for any arch
RUN apt-get update \
    && apt-get install -y --no-install-recommends firefox-esr fonts-liberation \
    && apt-get remove -y --purge firefox-esr \
    && rm -rf /var/lib/apt/lists/*

# UI automation tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    xdotool scrot python3-tk python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Screen recording (ffmpeg x11grab + libx264)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages (system-level, needs root).
#
# Playwright is pinned to 1.53.0 ON PURPOSE. camoufox 0.4.11 declares an
# UNPINNED `playwright` dependency, so a fresh `pip install` pulls whatever is
# newest — and Playwright >= 1.60 crashes camoufox's custom Firefox 135 build
# on protocol events (uncaught page errors with no location, WebSocket asserts,
# etc.) because the driver's juggler expectations no longer match. See
# daijro/camoufox#617 and microsoft/playwright#39767. Pinning to the last
# pre-1.60 release the camoufox 0.4.11 / Firefox 135 juggler was built against
# fixes the whole class of crashes at the source (vs. patching each failing
# assert). Bump this in lockstep with any camoufox version bump.
RUN pip install --no-cache-dir \
    "camoufox[geoip]" \
    "playwright==1.53.0" \
    pyautogui fastapi uvicorn fastmcp Pillow pyyaml "redis[hiredis]"

# Create non-root user and directories
RUN groupadd -g 1000 browser && useradd -u 1000 -g 1000 -m browser
RUN mkdir -p /app /userdata /loaders /recordings && chown -R browser:browser /app /userdata /loaders /recordings

# Allow browser user to write GeoIP db into camoufox package dir
RUN chown -R browser:browser /usr/local/lib/python3.12/site-packages/camoufox/

# Switch to non-root user for camoufox fetch + extensions
USER browser

# Download Camoufox browser (writes to ~/.cache/camoufox + GeoIP db to site-packages)
RUN python -m camoufox fetch

# Copy scripts and install extensions (writes to ~/.cache/camoufox)
COPY --chown=browser:browser scripts/ /scripts/
RUN python /scripts/install_extensions.py

# Copy app
COPY --chown=browser:browser app/ /app/

# Set working directory
WORKDIR /app

# Install gosu for privilege dropping in entrypoint
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy entrypoint (runs as root, drops to target user)
COPY --chmod=755 entrypoint.sh /entrypoint.sh

# Environment variables
ENV XVFB_RESOLUTION=1920x1080
ENV XVFB_DEPTH=24

# Expose ports (VNC and session HTTP)
EXPOSE 5900 8080

ENTRYPOINT ["/entrypoint.sh"]
