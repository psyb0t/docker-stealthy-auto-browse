# shellcheck shell=bash
# Sourced as root by the auto-linux-desk entrypoint before it drops
# privileges. Camoufox writes its GeoIP database inside its package
# directory, so hand only those files to the UID the browser runs as.
_cfox="/usr/local/lib/python3.12/site-packages/camoufox"
if [ -d "$_cfox" ]; then
    find "$_cfox" -name "*.mmdb" \
        -exec chown "$TARGET_UID:$TARGET_GID" {} + 2>/dev/null || true
fi
unset _cfox
