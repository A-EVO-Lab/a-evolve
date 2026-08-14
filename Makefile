.PHONY: install test lint fmt

install:
	pip install -e ".[all,dev]"

test:
	python3 scripts/check_release.py

lint:
	ruff check agent_evolve/ examples/

fmt:
	ruff format agent_evolve/ examples/
