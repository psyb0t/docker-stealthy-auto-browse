# Test-only image: serves the input probe page with the motion-attestation
# collector and scores captured event logs with open-source bot detectors.
# The detectors are fetched at pinned commits at build time and never
# vendored into this repository (Gaitcha is GPL; motion-attestation is MIT).
FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS fetch

ARG MOTION_ATTESTATION_REPO=https://github.com/libcaptcha/motion-attestation.git
ARG MOTION_ATTESTATION_COMMIT=bea077b307e58d7a034a75fdaf84979f438cfdd7
ARG GAITCHA_REPO=https://github.com/willybahuaud/gaitcha.git
ARG GAITCHA_COMMIT=40a3e241d320064e101a5fbca89f53cfd363fb82

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN git init -q /opt/motion-attestation \
    && git -C /opt/motion-attestation fetch -q --depth 1 "$MOTION_ATTESTATION_REPO" "$MOTION_ATTESTATION_COMMIT" \
    && git -C /opt/motion-attestation checkout -q FETCH_HEAD \
    && git init -q /opt/gaitcha \
    && git -C /opt/gaitcha fetch -q --depth 1 "$GAITCHA_REPO" "$GAITCHA_COMMIT" \
    && git -C /opt/gaitcha checkout -q FETCH_HEAD \
    && rm -rf /opt/motion-attestation/.git /opt/gaitcha/.git

FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392

RUN apt-get update \
    && apt-get install -y --no-install-recommends php-cli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=fetch --chown=0:0 /opt/motion-attestation/src /opt/motion-attestation/src
COPY --from=fetch --chown=0:0 /opt/motion-attestation/LICENSE /opt/motion-attestation/LICENSE
COPY --from=fetch --chown=0:0 /opt/gaitcha/src/php /opt/gaitcha/src/php
COPY --from=fetch --chown=0:0 /opt/gaitcha/LICENSE /opt/gaitcha/LICENSE
COPY --chown=0:0 server.mjs score.mjs score_gaitcha.php /app/

USER 1000:1000
EXPOSE 8099
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=10s \
    CMD ["node", "-e", "fetch('http://127.0.0.1:8099/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["node", "/app/server.mjs"]
