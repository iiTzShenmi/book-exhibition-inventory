PYTHON ?= python

.PHONY: push diag fetch-meta sync-csv

push:
	$(PYTHON) database/tools/local_db_sync.py push

diag:
	$(PYTHON) database/tools/local_db_sync.py diagnose

fetch-meta:
	$(PYTHON) database/tools/local_db_sync.py push --fill-metadata --fetch-limit 50 --skip-cloud-diagnose

sync-csv:
	$(PYTHON) -m database.tools.db_tools sync-csv
