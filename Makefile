# Every target here is offline. Nothing in this file sends a request, spends
# traffic or touches the gateway - that is what makes it safe to run on every
# commit. The targets that do cost money are scripts, and they are invoked by
# hand with their parameters visible.

# Prefer the project virtualenv when one exists, on either platform. Without
# this, `make check` reaches whatever `python` means on PATH: on a machine where
# the venv is not active that is a system interpreter with no ruff installed,
# and the run fails with RuffNotFound. That reads as a broken repository rather
# than an inactive venv, which is the wrong lesson to teach at the exact moment
# someone is running the gate before spending traffic.
#
# `?=` is kept, so `make check PYTHON=python3.12` still overrides it.
VENV_PYTHON := $(firstword $(wildcard .venv/Scripts/python.exe) \
                          $(wildcard .venv/bin/python))
ifeq ($(VENV_PYTHON),)
PYTHON ?= python
else
PYTHON ?= $(VENV_PYTHON)
endif

.PHONY: help install install-dev test lint fmt check plan queries clean

help:
	@echo "install      runtime dependencies"
	@echo "install-dev  runtime plus pytest and ruff"
	@echo "test         the suite, offline"
	@echo "lint         ruff, no changes written"
	@echo "fmt          ruff with fixes applied"
	@echo "check        lint and test: run this before spending traffic"
	@echo "plan         print the default benchmark plan and its cost, send nothing"
	@echo "queries      regenerate and verify the committed query list"
	@echo "clean        remove caches, never data/"

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

fmt:
	$(PYTHON) -m ruff check --fix .

check: lint test

# An engine and the unmodified control, on the two targets that carry the
# findings. Deliberately a matrix somebody would actually run: `chromium` is
# what makes the other column a claim rather than an observation, and a plan
# printed without a control would teach the wrong shape on first contact.
plan:
	$(PYTHON) scripts/benchmark.py --dry-run --engines patchright,chromium \
		--targets google_serp,amazon_search --queries 1000

# The list is committed, so this only proves the file matches the generator.
queries:
	$(PYTHON) scripts/tools/build_queries.py --check

# data/runs/ is evidence and is never removed by a build target.
clean:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in list(pathlib.Path('.').rglob('__pycache__'))]"
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.ruff_cache']]"
