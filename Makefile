PYTHON ?= python
PIP ?= $(PYTHON) -m pip
DOCKER_IMAGE ?= patchguard-python-sandbox:latest
DEMO_REPORT ?= .patchguard/quickstart/demo_security_bug.json
VENV_PATCHGUARD := $(wildcard .venv/bin/patchguard)
PATCHGUARD ?= $(if $(VENV_PATCHGUARD),.venv/bin/patchguard,patchguard)

.PHONY: install install-dev sandbox demo demo-no-docker test lint frontend-build api app-worker app-worker-no-docker clean-quickstart

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

sandbox:
	docker build -t $(DOCKER_IMAGE) -f sandbox/python/Dockerfile sandbox/python

demo:
	mkdir -p .patchguard/quickstart
	env -u OPENAI_API_KEY $(PATCHGUARD) analyze-demo examples/demo_security_bug --out $(DEMO_REPORT) --skip-llm

demo-no-docker:
	mkdir -p .patchguard/quickstart
	env -u OPENAI_API_KEY $(PATCHGUARD) analyze-demo examples/demo_security_bug --out $(DEMO_REPORT) --skip-llm --skip-docker

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

frontend-build:
	cd frontend && npm run build

api:
	$(PYTHON) -m uvicorn patchguard.api_app:app --reload --host 127.0.0.1 --port 8000

app-worker:
	$(PATCHGUARD) app-worker --publish-checks

app-worker-no-docker:
	$(PATCHGUARD) app-worker --publish-checks --skip-docker

clean-quickstart:
	rm -rf .patchguard/quickstart
