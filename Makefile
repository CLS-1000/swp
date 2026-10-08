PYTHON ?= python
PIP_INSTALL = $(PYTHON) -m pip install -q -e .[dev,ingest]

.PHONY: setup parse curated validate test build all

setup: .deps-installed

.deps-installed: pyproject.toml
	$(PIP_INSTALL)
	@touch $@

parse: setup
	spw parse assets/EDC-1057.pdf

curated: setup
	$(PYTHON) -c "from spw.parse import apply_curated_sql; apply_curated_sql()"

validate: setup
	spw validate

test: setup
	$(PYTHON) -m pytest -q

build: setup
	spw build
	@test $$(wc -c < dist/spw.html) -lt 400000

all: parse curated validate test build
