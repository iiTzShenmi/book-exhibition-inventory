"""
Utility script to backfill missing authors by scraping bookzone.cwgv.com.tw.

Usage examples (from repo root):
  python tools/fetch_author.py                 # fetch authors for titles missing author
  python tools/fetch_author.py --limit 20      # cap how many titles to process
  python tools/fetch_author.py --title "原子習慣"  # process a single title
  python tools/fetch_author.py --force         # overwrite existing authors
"""

import argparse
import os
import sys
import time
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if ROOT_DIR not in sys.path:
  sys.path.insert(0, ROOT_DIR)

from app import app, db  # noqa: E402
from database.models import BookTitle  # noqa: E402
from tools.fetch_cover_url import (  # noqa: E402
  BASE_URL,
  HEADERS,
  SEARCH_PATH,
  parse_search_results,
  pick_best_result,
)

MISSING_LOG = os.path.join(SCRIPT_DIR, "missing_authors.txt")
DEFAULT_TIMEOUT = 12

# Reuse a session to avoid repeated TCP handshakes
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def log_missing(title, reason=None):
  ts = time.strftime("%Y-%m-%d %H:%M:%S")
  msg = f"{ts}\t{title}"
  if reason:
    msg += f"\t{reason}"
  with open(MISSING_LOG, "a", encoding="utf-8") as f:
    f.write(msg + "\n")


def parse_author_from_detail(html: str) -> str | None:
  """Attempt to extract author name(s) from a book detail page."""
  soup = BeautifulSoup(html, "html.parser")

  # Structured author block seen on bookzone
  bi_item = soup.select_one(".bi-author-item")
  if bi_item:
    names = []
    primary = bi_item.select_one("h5")
    secondary = bi_item.select_one("p")
    if primary and primary.get_text(strip=True):
      names.append(primary.get_text(strip=True))
    if secondary and secondary.get_text(strip=True):
      names.append(secondary.get_text(strip=True))
    if names:
      return " / ".join(names)

  # Common selectors seen on bookzone pages
  selectors = [
    ".author",
    ".book-author",
    ".bookAuthor",
    "span.author",
    "p.author",
    "div.author",
  ]
  for sel in selectors:
    el = soup.select_one(sel)
    if el:
      txt = el.get_text(strip=True)
      if txt:
        return txt

  # Fallback: look for a label containing "作者"
  label_el = soup.find(string=re.compile("作者"))
  if label_el:
    # If it's like "作者：XXX"
    txt = str(label_el)
    m = re.search(r"作者[：:]\s*(.+)", txt)
    if m:
      return m.group(1).strip()
    # Otherwise grab the next sibling text
    if label_el.parent:
      sibling_text = label_el.parent.get_text(" ", strip=True)
      m = re.search(r"作者[：:]\s*(.+)", sibling_text)
      if m:
        return m.group(1).strip()
  return None


def fetch_author_for_title(title, *, verbose=False):
  """Search bookzone and return (author, source_url)."""
  if verbose:
    print("[info] searching for author:", title)

  def do_search(param_key, query):
    search_url = urljoin(BASE_URL, SEARCH_PATH)
    resp = SESSION.get(search_url, params={param_key: query}, timeout=DEFAULT_TIMEOUT)
    if verbose:
      print(f"[http] {resp.url} -> {resp.status_code}")
    resp.raise_for_status()
    return resp

  candidates = []
  try:
    resp = do_search("keyword", title)
    candidates = parse_search_results(resp.text)
  except Exception as exc:
    if verbose:
      print(f"[warn] primary search failed: {exc}")

  if not candidates:
    try:
      resp = do_search("q", title)
      candidates = parse_search_results(resp.text)
    except Exception as exc:
      if verbose:
        print(f"[warn] fallback search failed: {exc}")

  if not candidates:
    # Retry with spacing variants similar to cover fetcher
    def alt_queries(raw: str):
      base = raw or ""
      parts = base.split()
      tokens = [p for p in parts if p]
      queries = []
      no_space = "".join(parts)
      if no_space and no_space != base:
        queries.append(no_space)
      if len(tokens) >= 2:
        queries.append(" ".join(tokens[:2]))
      if len(tokens) >= 1:
        queries.append(tokens[0])
      return [q for q in queries if q]

    for alt in alt_queries(title):
      try:
        resp = SESSION.get(
          urljoin(BASE_URL, SEARCH_PATH),
          params={"keyword": alt},
          timeout=DEFAULT_TIMEOUT,
        )
        if verbose:
          print(f"[http] alt search({alt}) -> {resp.status_code}")
        resp.raise_for_status()
        candidates = parse_search_results(resp.text)
        if candidates:
          break
      except Exception as exc:
        if verbose:
          print(f"[warn] alt search failed for '{alt}': {exc}")

  if not candidates:
    return None, None

  best, score = pick_best_result(title, candidates)
  if verbose:
    print(f"[match] best score={score:.2f} for '{best.get('title') if best else ''}'")

  if not best:
    return None, None

  detail_url = best.get("url")

  # Prefer author from detail page to avoid ad snippets
  author = None
  if detail_url:
    try:
      resp = SESSION.get(detail_url, timeout=DEFAULT_TIMEOUT)
      resp.raise_for_status()
      author = parse_author_from_detail(resp.text) or None
      if verbose:
        print(f"[detail] fetched author from detail page: '{author or ''}'")
    except Exception as exc:
      if verbose:
        print(f"[warn] detail fetch failed: {exc}")

  # Fallback to search snippet if detail page failed
  if not author:
    author = (best.get("author") or "").strip() or None

  return (author or None), detail_url


def iter_titles(limit=None, force=False, single_title=None):
  query = BookTitle.query
  if single_title:
    query = query.filter(BookTitle.title == single_title)
  elif not force:
    query = query.filter(
      (BookTitle.author == None)  # noqa: E711
      | (db.func.length(db.func.trim(BookTitle.author)) == 0)
    )

  if limit:
    query = query.limit(limit)
  return query.all()


def main():
  parser = argparse.ArgumentParser(description="Backfill authors from bookzone.cwgv.com.tw")
  parser.add_argument("--limit", type=int, help="max titles to process")
  parser.add_argument("--title", type=str, help="specific title to process")
  parser.add_argument("--force", action="store_true", help="overwrite existing authors")
  parser.add_argument("--verbose", action="store_true", help="verbose logging")
  parser.add_argument("--dry-run", action="store_true", help="do not write to DB")
  parser.add_argument("--set-title", type=str, help="manually set author for a specific title")
  parser.add_argument("--set-author", type=str, help="author to set when using --set-title")
  args = parser.parse_args()

  # Auto-verbose when targeting a single title so you see progress
  if args.title:
    args.verbose = True

  with app.app_context():
    # Manual set mode
    if args.set_title:
      title = args.set_title.strip()
      author = (args.set_author or "").strip()
      if not author:
        print("[error] --set-author is required when using --set-title")
        return 1
      bt = BookTitle.query.filter_by(title=title).first()
      if not bt:
        print(f"[error] title not found: {title}")
        return 1
      if args.dry_run:
        print(f"[dry-run] would set author for '{title}' -> '{author}'")
        return 0
      bt.author = author
      db.session.add(bt)
      db.session.commit()
      print(f"[done] set author for '{title}' -> '{author}'")
      return 0

    titles = iter_titles(limit=args.limit, force=args.force, single_title=args.title)
    if not titles:
      print("[done] nothing to process")
      return 0

    total = len(titles)
    updated = 0
    skipped = 0
    print(f"[start] processing {total} title(s); force={args.force}; dry_run={args.dry_run}")
    for bt in titles:
      existing = (bt.author or "").strip()
      if not args.force and existing:
        skipped += 1
        if args.verbose:
          print(f"[skip] already has author -> '{existing}'")
        continue
      if args.verbose:
        print(f"[title] {bt.title}")
      author, source = fetch_author_for_title(bt.title, verbose=args.verbose)
      if not author:
        log_missing(bt.title, "no author found")
        if args.verbose:
          print("[skip] no author found")
        continue
      if args.verbose:
        print(f"[update] {bt.title} -> author='{author}' (source: {source})")
      if not args.dry_run:
        bt.author = author
        db.session.add(bt)
        # commit per record to avoid losing progress if interrupted
        db.session.commit()
        updated += 1

    if args.dry_run:
      print(f"[dry-run] would update {updated}/{total} titles (skipped {skipped} already had author)")
    else:
      print(f"[done] updated authors for {updated}/{total} titles (skipped {skipped} already had author)")


if __name__ == "__main__":
  raise SystemExit(main())
