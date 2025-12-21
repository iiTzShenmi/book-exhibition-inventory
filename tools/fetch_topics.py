"""
Tool: fetch topics/tags for books by scraping bookzone.cwgv.com.tw.

Notes:
- Uses a local JSON cache in this folder; does not change DB schema.
- Reuses search logic from the cover/author fetchers.
"""

import argparse
import json
import os
import sys
import time
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

MISSING_LOG = os.path.join(SCRIPT_DIR, "missing_topics.txt")
CACHE_PATH = os.path.join(SCRIPT_DIR, "topics_cache.json")
DEFAULT_TIMEOUT = 12

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def log_missing(title, reason=None):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    msg = f"{ts}\t{title}"
    if reason:
        msg += f"\t{reason}"
    with open(MISSING_LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def parse_topics_from_detail(html: str):
    soup = BeautifulSoup(html, "html.parser")
    tags = []
    container = soup.select_one(".s-tag.l2 ul")
    if container:
        for li in container.select("li"):
            txt = li.get_text(strip=True)
            if txt:
                tags.append(txt)
    if not tags:
        for el in soup.select(".s-tag-item, .tag, .chip"):
            txt = el.get_text(strip=True)
            if txt:
                tags.append(txt)
    seen = set()
    unique = []
    for t in tags:
        if t in seen:
            continue
        seen.add(t)
        unique.append(t)
    return unique


def fetch_topics_for_title(title, *, verbose=False):
    if verbose:
        print("[info] searching for topics:", title)

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
        parts = title.split()
        variants = []
        no_space = "".join(parts)
        if no_space and no_space != title:
            variants.append(no_space)
        if len(parts) >= 2:
            variants.append(" ".join(parts[:2]))
        if len(parts) >= 1:
            variants.append(parts[0])
        for alt in variants:
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
    topics = []
    if detail_url:
        try:
            resp = SESSION.get(detail_url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            topics = parse_topics_from_detail(resp.text)
            if verbose:
                print(f"[detail] topics: {topics}")
        except Exception as exc:
            if verbose:
                print(f"[warn] detail fetch failed: {exc}")

    return topics or None, detail_url


def iter_titles(limit=None, force=False, single_title=None):
    query = BookTitle.query
    if single_title:
        query = query.filter(BookTitle.title == single_title)
    elif not force:
        query = query.filter(
            (BookTitle.topics == None)  # noqa: E711
            | (db.func.length(db.func.trim(BookTitle.topics)) == 0)
        )
    if limit:
        query = query.limit(limit)
    return query.all()


def main():
    parser = argparse.ArgumentParser(description="Prototype: fetch topics for books from bookzone.cwgv.com.tw")
    parser.add_argument("--limit", type=int, help="max titles to process")
    parser.add_argument("--title", type=str, help="specific title to process")
    parser.add_argument("--force", action="store_true", help="overwrite existing topics (DB)")
    parser.add_argument("--verbose", action="store_true", help="verbose logging")
    parser.add_argument("--dry-run", action="store_true", help="do not write DB/cache")
    parser.add_argument("--set-title", type=str, help="manually set topics for a specific title")
    parser.add_argument("--set-topics", type=str, help="comma-separated topics for --set-title")
    args = parser.parse_args()

    cache = load_cache()

    if args.set_title:
        title = args.set_title.strip()
        if not args.set_topics:
            print("[error] --set-topics is required when using --set-title (comma-separated)")
            return 1
        topics = [t.strip() for t in args.set_topics.split(",") if t.strip()]
        if not topics:
            print("[error] no topics parsed from --set-topics")
            return 1
        cache[title] = topics
        if args.dry_run:
            print(f"[dry-run] would set topics for '{title}' -> {topics}")
            return 0
        bt = BookTitle.query.filter_by(title=title).first()
        if bt:
            bt.topics = json.dumps(topics, ensure_ascii=False)
            db.session.add(bt)
            db.session.commit()
        save_cache(cache)
        print(f"[done] set topics for '{title}' -> {topics}")
        return 0

    if args.title:
        args.verbose = True

    with app.app_context():
        titles = iter_titles(limit=args.limit, force=args.force, single_title=args.title)
        if not titles:
            print("[done] nothing to process")
            return 0

        total = len(titles)
        updated = 0
        skipped = 0
        print(f"[start] processing {total} title(s); force={args.force}; dry_run={args.dry_run}")
        for bt in titles:
            existing = (bt.topics or "").strip()
            if not args.force and existing:
                skipped += 1
                if args.verbose:
                    print(f"[skip] has topics -> {existing}")
                continue
            if args.verbose:
                print(f"[title] {bt.title}")
            topics, source = fetch_topics_for_title(bt.title, verbose=args.verbose)
            if not topics:
                log_missing(bt.title, "no topics found")
                if args.verbose:
                    print("[skip] no topics found")
                continue
            if args.verbose:
                print(f"[update] {bt.title} -> topics={topics} (source: {source})")
            if not args.dry_run:
                bt.topics = json.dumps(topics, ensure_ascii=False)
                db.session.add(bt)
                cache[bt.title] = topics
                # commit per record to ensure persistence even if later items fail
                db.session.commit()
                updated += 1

        if not args.dry_run and updated:
            save_cache(cache)
            print(f"[done] updated topics for {updated}/{total} titles (skipped {skipped} existing)")
        elif args.dry_run:
            print(f"[dry-run] would update {updated}/{total} titles (skipped {skipped} existing)")
        else:
            print(f"[done] no topics updated out of {total} (skipped {skipped} existing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
