.PHONY: all parse validate test build clean

DB = clone_clusters.db
PDF = assets/EDC-1057.pdf

all: parse validate test build

parse:
	python -m spw.cli parse $(PDF)

validate:
	python -m spw.cli validate

test:
	python -m pytest tests/ -v --tb=short

build:
	python -m spw.cli build

clean:
	rm -f $(DB) dist/spw.html dist/vehicles_flat.csv
