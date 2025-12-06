# Tools

Helper scripts for maintaining book metadata.

## File map
- `fetch_cover_url.py` + `missing_covers.txt`
- `fetch_author.py` + `missing_authors.txt`
- `fetch_topics.py` + `missing_topics.txt`, `topics_cache.json`
- `generate_security_code.py` (produce admin security codes from username/email)
- `create_admin_code.py` (create invite codes without needing user details)

## Fetch cover links
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

> The script retries with spacing variants (no spaces, first two tokens, first token) if the initial query returns no matches.

## Fetch authors
- Purpose: Scrape bookzone.cwgv.com.tw search results to populate missing `author` fields in `book_title`.
- Run from repo root:
  - `python tools/fetch_author.py` (fill missing authors)
  - `python tools/fetch_author.py --title "書名"` (single title)
  - `python tools/fetch_author.py --limit 20 --verbose` (debug a few)
  - `python tools/fetch_author.py --force` (overwrite existing authors)
- Optional: `--dry-run` to preview updates without writing to the database.
- Missing results are logged to `tools/missing_authors.txt`.
- Manual set: `python tools/fetch_author.py --set-title "書名" --set-author "作者名"` (supports `--dry-run`)

## Fetch topics (tags)
- Purpose: Scrape tag chips (topics) from book detail pages and cache them in `tools/topics_cache.json` (no DB column required).
- Run from repo root:
  - `python tools/fetch_topics.py --title "書名"` (single title)
  - `python tools/fetch_topics.py --limit 20 --verbose`
  - `python tools/fetch_topics.py --force` (overwrite cached topics)
  - Optional: `--dry-run` to preview updates
  - Manual set: `python tools/fetch_topics.py --set-title "書名" --set-topics "財經企管,職場應對"`
- Missing results are logged to `tools/missing_topics.txt`.

## Generate admin security code
- Purpose: Produce a deterministic security code for admin registration (requires username/email).
- Run: `python tools/generate_security_code.py --username alice --email alice@gmail.com`

## Create admin invite code (no user details)
- Purpose: Create a random invite code stored in DB, no username/email needed.
- Run: `python tools/create_admin_code.py --memo "for Alice"` (optional memo)
- Share the printed code; the first successful registration consumes it.
