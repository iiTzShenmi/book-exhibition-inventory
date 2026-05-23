release: python -m database.tools.db_tools init-db --no-sync-csv
web: EXIS_AUTO_INIT=0 gunicorn app:app
