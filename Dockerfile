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
    && apt-get install -y --no-install-recommends \
        firefox-esr \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-urw-base35 \
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
# camoufox, Playwright, and the Camoufox browser build below move IN LOCKSTEP.
# Each camoufox release pairs with one browser build and caps Playwright,
# because Playwright drives the browser over its Juggler protocol and every
# Playwright minor may change it (daijro/camoufox#617,
# microsoft/playwright#39767). camoufox 0.5.8 pairs with browser
# 156.0.1-beta.36 and requires Playwright < 1.63; Playwright 1.61+ needs
# browser beta.30 or newer. After a bump, re-verify install_extensions.py
# against the browser's distribution/ layout and run the full test suite.
RUN pip install --no-cache-dir \
    "camoufox[geoip]==0.5.8" \
    "playwright==1.62.0" \
    pyautogui fastapi uvicorn fastmcp Pillow pyyaml "redis[hiredis]"

# camoufox installs fpgen's fingerprint model inside site-packages, checked
# against the sha256 values camoufox pins. Install it as root here so the
# browser user only ever reads it.
RUN python -c 'from camoufox.fpgen_model import ensure_fpgen_model; ensure_fpgen_model()'

# Create non-root user and directories
RUN groupadd -g 1000 browser && useradd -u 1000 -g 1000 -m browser
RUN mkdir -p /app /userdata /loaders /recordings && chown -R browser:browser /app /userdata /loaders /recordings

# Allow browser user to write GeoIP db into camoufox package dir
RUN chown -R browser:browser /usr/local/lib/python3.12/site-packages/camoufox/

# Switch to non-root user for the Camoufox archive and extensions
USER browser

# Install the browser build camoufox 0.5.8 pairs with. `camoufox fetch` checks
# the download against the release's GitHub digest and records it in the
# install's version.json; the build fails unless that digest is the one pinned
# here, so a replaced release asset cannot slip into the image.
ARG TARGETARCH
RUN set -eux; \
    case "$TARGETARCH" in \
        amd64) camoufox_sha='72637885ee67c93466ad377831c647192f8f08fb5c6fd9279de1320b49dd2b53' ;; \
        arm64) camoufox_sha='8b5fd083721c807ee2a752265683482477105052fa0e21739388b527ae067c15' ;; \
        *) echo "unsupported Camoufox architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    python -m camoufox fetch official/156.0.1-beta.36; \
    installed_sha=$(python -c 'import json, pathlib, camoufox.pkgman as p; print(json.loads((pathlib.Path(p.camoufox_path(download_if_missing=False)) / "version.json").read_text()).get("sha256") or "")'); \
    test "$installed_sha" = "$camoufox_sha"

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
