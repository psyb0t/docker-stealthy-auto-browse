# Base image pinned by digest — tags are mutable, digests aren't. Re-resolve
# with: docker buildx imagetools inspect python:3.12-slim-bookworm --format '{{.Manifest.Digest}}'
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

# MCP Registry ownership label
LABEL io.modelcontextprotocol.server.name="io.github.psyb0t/stealthy-auto-browse"

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
# camoufox is pinned to 0.4.11 ON PURPOSE. Leaving it unpinned pulled whatever
# was newest at build time — camoufox 0.5.x reshaped the browser's on-disk
# `distribution/` layout (no default `policies.json`), which broke
# install_extensions.py mid-build (FileNotFoundError). Pinning locks the browser
# build + layout the rest of this image is designed around (Firefox 135).
#
# Playwright is pinned to 1.53.0 to MATCH camoufox 0.4.11. camoufox 0.4.11
# declares an UNPINNED `playwright` dependency, so a fresh `pip install` pulls
# whatever is newest — and Playwright >= 1.60 crashes camoufox's custom Firefox
# 135 build on protocol events (uncaught page errors with no location, WebSocket
# asserts, etc.) because the driver's juggler expectations no longer match. See
# daijro/camoufox#617 and microsoft/playwright#39767. Pinning to the last
# pre-1.60 release the camoufox 0.4.11 / Firefox 135 juggler was built against
# fixes the whole class of crashes at the source (vs. patching each failing
# assert). Bump camoufox and playwright IN LOCKSTEP, and re-verify
# install_extensions.py against the new browser layout.
RUN pip install --no-cache-dir \
    "camoufox[geoip]==0.4.11" \
    "playwright==1.53.0" \
    pyautogui fastapi uvicorn fastmcp Pillow pyyaml "redis[hiredis]"

# Create non-root user and directories
RUN groupadd -g 1000 browser && useradd -u 1000 -g 1000 -m browser
RUN mkdir -p /app /userdata /loaders /recordings && chown -R browser:browser /app /userdata /loaders /recordings

# Allow browser user to write GeoIP db into camoufox package dir
RUN chown -R browser:browser /usr/local/lib/python3.12/site-packages/camoufox/

# Switch to non-root user for the Camoufox archive and extensions
USER browser

# Camoufox 0.4.11 resolves its browser binary from the latest compatible GitHub
# release. Pin the archive too: beta.29 stopped executing the fixture's second
# inline script, while beta.28 is compatible with the pinned Playwright driver.
ARG TARGETARCH
RUN set -eux; \
    case "$TARGETARCH" in \
        amd64) camoufox_arch='x86_64'; camoufox_sha='924f3109ccd6d47cd6a0384d67a345fadf975d48b6319f8dbbd5954c588982bd' ;; \
        arm64) camoufox_arch='arm64'; camoufox_sha='3a105a2fc929e80a79b4b7fce2c93ed62c4fb2c877f3c1ed2a5d66a1c4fe968f' ;; \
        *) echo "unsupported Camoufox architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    camoufox_url="https://github.com/daijro/camoufox/releases/download/v152.0.4-beta.28/camoufox-152.0.4-beta.28-lin.${camoufox_arch}.zip"; \
    wget -q -O /tmp/camoufox.zip "$camoufox_url"; \
    echo "${camoufox_sha}  /tmp/camoufox.zip" | sha256sum -c -; \
    mkdir -p /home/browser/.cache/camoufox; \
    python -c 'import zipfile; zipfile.ZipFile("/tmp/camoufox.zip").extractall("/home/browser/.cache/camoufox")'; \
    printf '{"version":"152.0.4","release":"beta.28"}' > /home/browser/.cache/camoufox/version.json; \
    chmod -R 0755 /home/browser/.cache/camoufox; \
    rm /tmp/camoufox.zip

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
