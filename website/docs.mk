# The documentation site. The root Makefile includes this file with -include.

DOCS_NODE_IMAGE := node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
DOCS_RUN := docker run --rm --init --cap-drop ALL --security-opt no-new-privileges:true \
	--user $(UID):$(GID) -e HOME=/tmp -e COREPACK_HOME=/work/website/.corepack \
	-v "$(CURDIR)/website:/work/website" -w /work/website
DOCS_SITE := .testing/docs-site
DOCS_PREVIEW_PORT ?= 61328

.PHONY: docs docs-lock docs-audit docs-preview

docs-lock: ## Refresh the documentation lockfile from website/package.json
	@$(DOCS_RUN) $(DOCS_NODE_IMAGE) corepack pnpm install --lockfile-only --ignore-scripts

docs-audit: ## Check the locked documentation dependencies against known advisories
	@$(DOCS_RUN) $(DOCS_NODE_IMAGE) corepack pnpm audit

# The install step downloads the locked packages; the build itself runs with
# no network. The finished site is copied under its base path for preview.
docs: ## Build the documentation site into website/build and .testing/docs-site
	@python3 -I website/prepare.py
	@$(DOCS_RUN) $(DOCS_NODE_IMAGE) corepack pnpm install --frozen-lockfile --ignore-scripts --store-dir .pnpm-store
	@$(DOCS_RUN) --network none $(DOCS_NODE_IMAGE) corepack pnpm run build
	@rm -rf "$(DOCS_SITE)" && mkdir -p "$(DOCS_SITE)" && cp -r website/build "$(DOCS_SITE)/docker-stealthy-auto-browse"

# Serves http://127.0.0.1:61328/docker-stealthy-auto-browse/ on loopback only.
docs-preview: docs ## Build, then serve the site on loopback port 61328 (Ctrl+C stops it)
	@cd "$(DOCS_SITE)" && python3 -I -m http.server "$(DOCS_PREVIEW_PORT)" --bind 127.0.0.1
