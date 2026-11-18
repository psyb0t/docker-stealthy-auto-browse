---
title: Development
description: Build and lint the browser image in Docker, run unit and integration tests, inspect live detector checks, and preview the documentation site.
nav_order: 10
---

# Development

Docker, Make, Git, and a shell are the host tools. The [Makefile](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/Makefile) runs Python and shell tooling in the development image. Run `make help` for the command list.

## Commands

| Command | Purpose |
| --- | --- |
| `make dev-image` | Build the runtime base and development image. |
| `make shell` | Open the dev container with a writable source mount. |
| `make build` | Build the production image; `TAG` defaults to `latest`. |
| `make build-test` | Build a test-tagged runtime image. |
| `make lint` | Check Python with Ruff and shell scripts with ShellCheck. |
| `make lint-fix`, `make format` | Apply safe Ruff fixes or format Python. Markdown is not reflowed. |
| `make test-unit` | Run in-process Python regression tests. |
| `make test` | Run the complete Docker-backed suite. |
| `make test TEST_ARGS="test_redis_cookie_sync"` | Run a named integration test, including real browser cookie propagation. |
| `make test-real` | Opt in to public detector and CAPTCHA-provider compatibility checks. |
| `make sec` | Write security findings to `sec.sarif`. Findings and execution failures are separate concerns; inspect the report. |
| `make generate` | No-op: this project has no generated application source. |
| `make docs-build` | Build the Jekyll documentation into ignored local output. |
| `make clean` | Remove the local project image tags named in the target. Inspect them before using it. |

The complete and live test targets mount the Docker socket to launch sibling containers. Lint, unit tests, formatting, and the documentation build do not need that socket. The [test harness](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/test.sh) owns its test containers and cleans them up. Do not run two suites using the same fixed container names concurrently.

## Source map

The image is built on [auto-linux-desk](https://psyb0t.github.io/docker-auto-linux-desk/), which provides the entrypoint, the display and VNC services, the HTTP and MCP server, script mode, events, handoff, recording, the clipboard and the humanized input planners and executor. This repository adds the browser as a plugin of that server.

| Source | Responsibility |
| --- | --- |
| [Dockerfile](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/Dockerfile) | The pinned base, the `browser` user, Camoufox, Playwright and the extensions. |
| [docker/entrypoint.d](https://github.com/psyb0t/docker-stealthy-auto-browse/tree/main/docker/entrypoint.d) | Root hooks the base entrypoint runs before dropping privileges. |
| [app/stealthy_plugin.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/stealthy_plugin.py) | Registers the browser actions, MCP tools, state, browser screenshots, startup and shutdown with the base. |
| [app/browser_actions.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/browser_actions.py) | Browser session, page event handlers and every browser action. |
| [app/browser_input.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/browser_input.py) | Viewport coordinates for humanized input, and the typo guard. |
| [app/mcp_server.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/mcp_server.py) | MCP tools and instructions. |
| [app/browser.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/browser.py) | Camoufox startup, persistent identity, recovery, windows, and virtual media. |
| [app/redis_sync.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/redis_sync.py) | Shared cookie state and reconciliation. |
| [tests](https://github.com/psyb0t/docker-stealthy-auto-browse/tree/main/tests) | Local fixtures, unit tests, browser integration tests, opt-in external checks, and the contract snapshot. |

Humanized input timing lives in the base; change it there and bump the pinned base. Pass `BASE_IMAGE=psyb0t/auto-linux-desk:local` to `make build`, `make dev-image` or `make build-test` to build on a local base image.

## Test evidence and browser upgrades

Normal tests use local fixtures. Behavioral tests compare humanized sessions with robotic controls that the same detector must reject. Public sites change independently, so a historical passing result is not proof of current detection resistance. See [input behavior and test limits](stealth.md).

The [Dockerfile](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/Dockerfile) pins Camoufox, its browser build, and Playwright together. Upgrade those as a compatible set, verify archive hashes and installed extension compatibility, then run unit, complete integration, and opt-in live checks. Both amd64 and arm64 builds need validation. Do not patch an installed dependency tree to hide a mismatch.

## Documentation changes

Keep settings, action parameters, defaults, examples, and limitations aligned with the implementation. Every page needs unique front matter, a useful description, and one H1. Link between pages with relative `.md` paths; link repository files outside this site with full GitHub URLs. README documentation links use the published site URL. Protect Liquid-looking code examples with raw blocks. Run `make docs-build`, check rendered links and anchors, and review the dark layout and search in a browser before publication.
