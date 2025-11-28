# Tools

Helper scripts for maintaining book metadata.

## Fetch cover links

- Script: `tools/fetch_cover_url.py`
- Purpose: Search bookzone.cwgv.com.tw for each `BookTitle` and store the derived cover URL in `cover_link`.
- Install deps: `pip install -r requirements.txt`
- Run from repo root:
  - `python tools/fetch_cover_url.py` (fill missing covers)
  - `python tools/fetch_cover_url.py --title "書名"` (single title)
  - `python tools/fetch_cover_url.py --limit 20 --verbose` (debug a few)
  - `python tools/fetch_cover_url.py --force` (overwrite existing cover links)
- Optional: `--dry-run` to preview results without writing to the database (misses are still logged).
- Manual override: `--set-title "書名" --set-cover "https://..."` (respects `--dry-run`)
- Missing results are logged to `tools/missing_covers.txt` for follow-up.

> Note: The script retries with spacing variants (no spaces, first two tokens, first token) if the initial query returns no matches.
