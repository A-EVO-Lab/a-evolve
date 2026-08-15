.PHONY: install test lint fmt

install:
	pip install -e ".[all,dev]"

test:
	python3 tools/check_release.py

lint:
	ruff check agent_evolve/ evo_harness/ tools/

fmt:
	ruff format agent_evolve/ evo_harness/ tools/
