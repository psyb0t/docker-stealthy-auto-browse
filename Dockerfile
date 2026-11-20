# The desktop, VNC, humanized input and the HTTP/MCP/script server come from
# auto-linux-desk. This image adds the browser and loads it as a plugin.
#
# The base is pinned by tag and digest; tags are mutable, digests are not.
# Bump both together, then re-resolve the digest with:
#   docker buildx imagetools inspect psyb0t/auto-linux-desk:vX.Y.Z --format '{{.Manifest.Digest}}'
# For a local base build, pass --build-arg BASE_IMAGE=psyb0t/auto-linux-desk:local.
ARG BASE_IMAGE=psyb0t/auto-linux-desk:v0.5.0@sha256:17ee3cec9127ea8d7b3effdb9bfcadf8a3f2f629842cd654584678e706f1bb24
FROM ${BASE_IMAGE}

# MCP Registry ownership label (replaces the base image's).
LABEL io.modelcontextprotocol.server.name="io.github.psyb0t/stealthy-auto-browse"

USER root

# Firefox/Camoufox dependencies - install firefox-esr to pull correct GTK deps for any arch
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        firefox-esr \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-urw-base35 \
    && apt-get remove -y --purge firefox-esr \
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
#
# camoufox depends on indexed-zstd, which has no arm64 wheel, so arm64 builds
# compile it. The compilers and zstd headers are added and purged in this one
# layer so no compiler ships in the image. Only the packages this step added
# are purged: autoremove would also take the GTK libraries the browser needs,
# which the firefox-esr step above leaves marked as automatically installed.
RUN dpkg-query -W -f='${Package}\n' | sort > /tmp/packages-before \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libzstd-dev \
    && dpkg-query -W -f='${Package}\n' | sort > /tmp/packages-after \
    && pip install --no-cache-dir \
        "camoufox[geoip]==0.5.8" \
        "playwright==1.62.0" \
        "redis[hiredis]" \
    && apt-get purge -y $(comm -13 /tmp/packages-before /tmp/packages-after) \
    && rm -rf /var/lib/apt/lists/* /tmp/packages-before /tmp/packages-after

# camoufox installs fpgen's fingerprint model inside site-packages, checked
# against the sha256 values camoufox pins. Install it as root here so the
# browser user only ever reads it.
RUN python -c 'from camoufox.fpgen_model import ensure_fpgen_model; ensure_fpgen_model()'

# The base user is desk (UID/GID 1000, /home/desk). Rename it to browser and
# move its home, keeping the IDs, so /home/browser and the browser user stay
# what they have always been. The Openbox config moves with the home.
RUN usermod -l browser -d /home/browser -m desk \
    && groupmod -n browser desk \
    && mkdir -p /app /loaders \
    && chown browser:browser /app /loaders

# Allow browser user to write GeoIP db into camoufox package dir
RUN chown -R browser:browser /usr/local/lib/python3.12/site-packages/camoufox/

# Under PUID/PGID, hand the GeoIP database files to that UID before the
# entrypoint drops privileges.
COPY --chmod=644 docker/entrypoint.d/10-camoufox-geoip.sh /etc/auto-linux-desk/entrypoint.d/10-camoufox-geoip.sh

# Switch to non-root user for the Camoufox archive and extensions
USER browser

# Install the browser build camoufox 0.5.8 pairs with, checked against the
# sha256 pinned here. The archive is downloaded by its release URL and laid out
# the way `camoufox fetch` would (browsers/official/<version>-<build>-<sha8>,
# version.json, the active version in config.json, the 0.5 layout flag).
# `camoufox fetch` itself is not used: it lists releases through the
# unauthenticated GitHub API, which rate-limits shared CI runners.
ARG TARGETARCH
ARG CAMOUFOX_VERSION=156.0.1
ARG CAMOUFOX_BUILD=beta.36
RUN set -eux; \
    case "$TARGETARCH" in \
        amd64) camoufox_arch='x86_64'; camoufox_sha='72637885ee67c93466ad377831c647192f8f08fb5c6fd9279de1320b49dd2b53' ;; \
        arm64) camoufox_arch='arm64'; camoufox_sha='8b5fd083721c807ee2a752265683482477105052fa0e21739388b527ae067c15' ;; \
        *) echo "unsupported Camoufox architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    release="${CAMOUFOX_VERSION}-${CAMOUFOX_BUILD}"; \
    cache_dir=/home/browser/.cache/camoufox; \
    install_rel="browsers/official/${release}-$(echo "$camoufox_sha" | cut -c1-8)"; \
    wget -q -O /tmp/camoufox.zip \
        "https://github.com/daijro/camoufox/releases/download/v${release}/camoufox-${release}-lin.${camoufox_arch}.zip"; \
    echo "${camoufox_sha}  /tmp/camoufox.zip" | sha256sum -c -; \
    mkdir -p "$cache_dir/$install_rel"; \
    python -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' /tmp/camoufox.zip "$cache_dir/$install_rel"; \
    rm /tmp/camoufox.zip; \
    printf '{"version":"%s","build":"%s","prerelease":false,"sha256":"%s","interface":1}' \
        "$CAMOUFOX_VERSION" "$CAMOUFOX_BUILD" "$camoufox_sha" > "$cache_dir/$install_rel/version.json"; \
    printf '{"active_version":"%s"}' "$install_rel" > "$cache_dir/config.json"; \
    touch "$cache_dir/.0.5_FLAG"; \
    chmod -R 0755 "$cache_dir/$install_rel"; \
    test "$(python -c 'from camoufox.pkgman import installed_verstr; print(installed_verstr())')" = "${CAMOUFOX_VERSION}-${CAMOUFOX_BUILD}"

# Copy scripts and install extensions (writes to ~/.cache/camoufox)
COPY --chown=browser:browser scripts/ /scripts/
RUN python /scripts/install_extensions.py

# Copy app
COPY --chown=browser:browser app/ /app/

WORKDIR /app

USER root

# The entrypoint is the base one. /entrypoint.sh stays as a path for anyone
# who calls it directly.
RUN ln -s /usr/local/bin/auto-linux-desk-entrypoint /entrypoint.sh

# The base entrypoint reads these. run_command and launch_app ship disabled:
# the browser API never had shell access, and deployments without AUTH_TOKEN
# must not gain it on upgrade. Set DESK_EXEC_ENABLED=true to turn them on.
ENV DESK_HOME=/home/browser \
    DESK_WRITABLE_DIRS="/userdata /loaders /recordings" \
    DESK_BANNER="STEALTHY AUTO-BROWSE" \
    DESK_EXEC_ENABLED=false \
    AUTO_LINUX_DESK_PLUGINS=stealthy_plugin \
    PYTHONPATH=/app

EXPOSE 5900 8080

ENTRYPOINT ["/entrypoint.sh"]
