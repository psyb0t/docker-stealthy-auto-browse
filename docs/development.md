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

| Source | Responsibility |
| --- | --- |
| [entrypoint.sh](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/entrypoint.sh) | UID/GID switch, display, window manager, VNC services, and application lifecycle. |
| [app/main.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/main.py) | HTTP routes, action dispatch, serialization, events, and handoff. |
| [app/mcp_server.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/mcp_server.py) | MCP schemas and the shared dispatcher bridge. |
| [app/browser.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/browser.py) | Camoufox startup, persistent identity, recovery, windows, and virtual media. |
| [app/input_actions.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/input_actions.py) | Input parameter validation and worker-thread dispatch. |
| [app/human_mouse.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/human_mouse.py), [human_keyboard.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/human_keyboard.py), [human_scroll.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/human_scroll.py) | Seedable timing and path planners. |
| [app/system.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/system.py) | X11 input replay. |
| [app/redis_sync.py](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/app/redis_sync.py) | Shared cookie state and reconciliation. |
| [tests](https://github.com/psyb0t/docker-stealthy-auto-browse/tree/main/tests) | Local fixtures, unit tests, browser integration tests, and opt-in external checks. |

## Test evidence and browser upgrades

Normal tests use local fixtures. Behavioral tests compare humanized sessions with robotic controls that the same detector must reject. Public sites change independently, so a historical passing result is not proof of current detection resistance. See [input behavior and test limits](stealth.md).

The [Dockerfile](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/main/Dockerfile) pins Camoufox, its browser build, and Playwright together. Upgrade those as a compatible set, verify archive hashes and installed extension compatibility, then run unit, complete integration, and opt-in live checks. Both amd64 and arm64 builds need validation. Do not patch an installed dependency tree to hide a mismatch.

## Documentation changes

Keep settings, action parameters, defaults, examples, and limitations aligned with the implementation. Every page needs unique front matter, a useful description, and one H1. Link between pages with relative `.md` paths; link repository files outside this site with full GitHub URLs. README documentation links use the published site URL. Protect Liquid-looking code examples with raw blocks. Run `make docs-build`, check rendered links and anchors, and review the dark layout and search in a browser before publication.
