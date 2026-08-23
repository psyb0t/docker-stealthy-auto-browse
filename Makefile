SHELL := /bin/bash

IMAGE_NAME := psyb0t/stealthy-auto-browse
TAG ?= latest
TEST_TAG ?= $(TAG)-test
DEV_BASE_IMAGE := $(IMAGE_NAME):dev-base
DEV_IMAGE := $(IMAGE_NAME):dev
PROJECT_ROOT := $(CURDIR)
TEST_ARGS ?=
PYTHON_SOURCES := app/*.py tests/*.py tests/apps/*.py scripts/*.py

UID := $(shell id -u)
GID := $(shell id -g)
DOCKER_SOCK := /var/run/docker.sock
DOCKER_GID := $(shell stat -c '%g' $(DOCKER_SOCK) 2>/dev/null || echo 0)

DEV_RUN := docker run --rm --init --read-only \
	--tmpfs /tmp:rw,noexec,nosuid,size=512m \
	--tmpfs /work-env:rw,exec,nosuid,size=512m \
	--user $(UID):$(GID) \
	-e HOME=/tmp \
	-e PYTHONDONTWRITEBYTECODE=1 \
	-e PYTHONPATH=/work/app \
	-e RUFF_CACHE_DIR=/work-env/ruff-cache \
	-v "$(PROJECT_ROOT):/work:ro" \
	-w /work \
	$(DEV_IMAGE)

DEV_RUN_WRITE := docker run --rm --init --read-only \
	--tmpfs /tmp:rw,noexec,nosuid,size=512m \
	--tmpfs /work-env:rw,exec,nosuid,size=512m \
	--user $(UID):$(GID) \
	-e HOME=/tmp \
	-e PYTHONDONTWRITEBYTECODE=1 \
	-e PYTHONPATH=/work/app \
	-e RUFF_CACHE_DIR=/work-env/ruff-cache \
	-v "$(PROJECT_ROOT):/work" \
	-w /work \
	$(DEV_IMAGE)

DEV_RUN_DIND := docker run --rm --init --read-only \
	--tmpfs /tmp:rw,noexec,nosuid,size=512m \
	--tmpfs /work-env:rw,exec,nosuid,size=512m \
	--user $(UID):$(GID) \
	--group-add $(DOCKER_GID) \
	-e HOME=/tmp \
	-e PYTHONDONTWRITEBYTECODE=1 \
	-e TEST_DOCKER_NETWORK_ATTACH=true \
	-v "$(PROJECT_ROOT):$(PROJECT_ROOT)" \
	-v "$(DOCKER_SOCK):$(DOCKER_SOCK)" \
	-w "$(PROJECT_ROOT)" \
	$(DEV_IMAGE)

.DEFAULT_GOAL := help
.PHONY: help dev-image shell build build-test lint lint-fix format test test-unit sec generate clean

help: ## Show supported development commands.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "%-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev-image: ## Build the sandboxed development image.
	docker build -t $(DEV_BASE_IMAGE) .
	docker build --build-arg BASE_IMAGE=$(DEV_BASE_IMAGE) -f Dockerfile.dev -t $(DEV_IMAGE) .

shell: dev-image ## Open a shell in the development image.
	$(DEV_RUN_WRITE) bash

build: ## Build the production image.
	docker build -t $(IMAGE_NAME):$(TAG) .

build-test: ## Build the test-tagged production image.
	docker build -t $(IMAGE_NAME):$(TEST_TAG) .

format: dev-image ## Format Python source files.
	$(DEV_RUN_WRITE) python -m ruff format $(PYTHON_SOURCES)

lint: dev-image ## Run Python and shell checks in Docker.
	$(DEV_RUN) python -m ruff check --ignore E402 $(PYTHON_SOURCES)
	$(DEV_RUN) shellcheck entrypoint.sh test.sh tests/*.sh scripts/*.sh

lint-fix: dev-image ## Apply safe Python lint fixes and formatting.
	$(DEV_RUN_WRITE) python -m ruff check --ignore E402 --fix $(PYTHON_SOURCES)
	$(DEV_RUN_WRITE) python -m ruff format $(PYTHON_SOURCES)

test: dev-image ## Run the complete Docker-backed test suite.
	$(DEV_RUN_DIND) bash test.sh $(TEST_ARGS)

test-unit: dev-image ## Run in-process Python unit tests.
	$(DEV_RUN) python tests/test_navigation_options.py
	$(DEV_RUN) python tests/test_install_extensions.py

sec: dev-image ## Write security findings to sec.sarif for GitHub Security.
	$(DEV_RUN_WRITE) bash scripts/sec.sh

generate: ## This repository has no generated source.
	@:

clean: ## Remove only this project's local images.
	docker image rm $(DEV_IMAGE) $(DEV_BASE_IMAGE) $(IMAGE_NAME):$(TAG) $(IMAGE_NAME):$(TEST_TAG)
