"""
Utility script to backfill cover URLs by scraping bookzone.cwgv.com.tw.

Usage examples (from repo root):
  python tools/fetch_cover_url.py                   # fetch missing covers for all titles
  python tools/fetch_cover_url.py --limit 20        # cap how many titles to process
  python tools/fetch_cover_url.py --title "原子習慣"    # process a single title
  python tools/fetch_cover_url.py --force           # overwrite existing cover links
"""

import argparse
import os
import sys
import time
from difflib import SequenceMatcher
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app, db
from database.models import BookTitle
from database.tools.drop_book_csv_missing import drop_titles as drop_missing_titles
from database.tools.drop_book_csv_missing import load_titles as load_missing_titles

MISSING_LOG = os.path.join(SCRIPT_DIR, "missing_covers.txt")
MISSING_TITLES_FILE = os.path.join(ROOT_DIR, "database", "logs", "titles_not_in_csv.txt")

BASE_URL = "https://bookzone.cwgv.com.tw"
SEARCH_PATH = "/search"
HEADERS = {"User-Agent": "book-expo-cover-fetch/1.0 (+https://bookzone.cwgv.com.tw)"}


def log_missing(title, reason=None):
    """Append a missing title to the log file for follow-up."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    msg = f"{ts}\t{title}"
    if reason:
        msg += f"\t{reason}"
    with open(MISSING_LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def build_cover_from_book_url(book_url: str) -> str | None:
    """
    Build the cover URL from a book detail URL.
    Example: https://bookzone.cwgv.com.tw/book/BBP490 ->
             https://imgs.cwgv.com.tw/books/BBP/BBP490/cover/thumb/BBP490.png
    """
    if not book_url:
        return None
    parts = book_url.rstrip("/").split("/")
    if not parts:
        return None
    code = parts[-1]
    if not code:
        return None
    folder = code[:3] if len(code) >= 3 else code
    return f"https://imgs.cwgv.com.tw/books/{folder}/{code}/cover/thumb/{code}.png"


def parse_search_results(html):
    """Extract search result entries from the search page."""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen = set()
    anchors = soup.select("a[href*='/book/']")
    #print("\n==========================\n", anchors, "\n==========================\n") #debug
    for anchor in anchors:
        # Skip sponsored/ads blocks
        if anchor.find_parent(class_="search-ads"):
            continue
        parent_classes = anchor.parent.get("class", []) if anchor.parent else []
        lower_classes = {cls.lower() for cls in parent_classes}
        if lower_classes & {"ad", "ads", "advertisement", "sponsored"}:
            continue
        
        src = anchor.get("href") or ""
        #print("\n==========================\n", src, "\n==========================\n") #debug
        if not src or src in seen:
            continue
        seen.add(src)
        #print("\n==========================\n", seen, "\n==========================\n") #debug
        full_url = urljoin(BASE_URL, src)
        title_el = (
            anchor.select_one(".title")
            or anchor.select_one(".book-title")
            or anchor.select_one("p")
            or anchor.select_one("h3")
        )
        title = title_el.get_text(strip=True) if title_el else anchor.get_text(strip=True)
        if not title:
            img = anchor.find("img")
            if img and img.get("alt"):
                title = img.get("alt", "").strip()
        author_el = anchor.select_one(".author")
        author = author_el.get_text(strip=True) if author_el else ""
        if not title:
            continue
        entries.append({"url": full_url, "title": title, "author": author})
    return entries


def pick_best_result(query_title, candidates):
    """Pick the best matching search result based on fuzzy ratio."""
    if not candidates:
        return None, 0.0
    def normalize(text: str) -> str:
        txt = (text or "").strip().lower()
        # remove spaces and common separators to handle variants
        return "".join(ch for ch in txt if ch.isalnum())

    def tokens(text: str):
        import re
        return set(re.findall(r"[\\w']+", (text or "").lower()))

    q_norm = normalize(query_title)
    q_tokens = tokens(query_title)

    scored = []
    for item in candidates:
        title_raw = (item.get("title") or "").strip()
        title_norm = normalize(title_raw)
        ratio_norm = SequenceMatcher(None, q_norm, title_norm).ratio() if title_norm else 0
        overlap = 0
        if q_tokens:
            overlap = len(q_tokens & tokens(title_raw)) / len(q_tokens)
        score = max(ratio_norm, overlap)
        # Small boost if one string contains the other after normalization
        if title_norm and (title_norm.startswith(q_norm) or q_norm.startswith(title_norm)):
            score += 0.2
        scored.append((score, item, ratio_norm, overlap, title_raw))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best = scored[0]
    best_score, best_item, ratio_norm, overlap, title_raw = best
    return best_item, best_score


def fetch_url_for_title(title, *, verbose=False):
    """Search bookzone and return (cover_link, source_url) or (None, None)."""
    if verbose:
        print("        [info] searching for", title)

    def do_search(param_key):
        search_url = urljoin(BASE_URL, SEARCH_PATH)
        resp = requests.get(
            search_url,
            params={param_key: title},
            headers=HEADERS,
            timeout=15,
        )
        if verbose:
            print(f"        [http] search({param_key}) {resp.url} -> {resp.status_code}")
        resp.raise_for_status()
        return resp

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

    try:
        resp = do_search("keyword")
    except Exception as exc:
        print(f"[warn] search failed for '{title}' with keyword: {exc}")
        return None, None

    candidates = parse_search_results(resp.text)
    if not candidates:
        try:
            resp = do_search("q")
            candidates = parse_search_results(resp.text)
        except Exception as exc:
            print(f"[warn] fallback search failed for '{title}' with q: {exc}")

    if (not candidates) and verbose:
        print("        [info] retrying with alternate queries (spacing variants)")

    if not candidates:
        for alt in alt_queries(title):
            try:
                resp = requests.get(
                    urljoin(BASE_URL, SEARCH_PATH),
                    params={"keyword": alt},
                    headers=HEADERS,
                    timeout=15,
                )
                if verbose:
                    print(f"        [http] search(alt:{alt}) {resp.url} -> {resp.status_code}")
                resp.raise_for_status()
                candidates = parse_search_results(resp.text)
                if candidates:
                    if verbose:
                        print(f"        [parse] found {len(candidates)} candidates using alt='{alt}'")
                    break
            except Exception as exc:
                if verbose:
                    print(f"        [warn] alt search failed for '{alt}': {exc}")

    if verbose:
        print(f"        [parse] found {len(candidates)} candidates")
        def _norm(txt: str) -> str:
            return "".join(ch for ch in (txt or "").lower() if ch.isalnum())
        def _tok(txt: str):
            import re
            return set(re.findall(r"[\\w']+", (txt or "").lower()))

        qn = _norm(title)
        qt = _tok(title)

        for cand in candidates[:5]:
            ct = cand.get('title') or ''
            cn = _norm(ct)
            ratio = SequenceMatcher(None, qn, cn).ratio() if cn else 0
            overlap = len(qt & _tok(ct)) / len(qt) if qt else 0
            score = max(ratio, overlap)
            if cn and (cn.startswith(qn) or qn.startswith(cn)):
                score += 0.2
            print(f"          - title='{ct}' author='{cand.get('author')}' score={score:.2f} (norm={ratio:.2f}, overlap={overlap:.2f}) url={cand.get('url')}")

    pick, best_score = pick_best_result(title, candidates)
    if not pick:
        if verbose:
            print("        [match] no suitable candidate (no results)")
        return None, None

    detail_url = pick["url"]
    if verbose:
        print(f"        [match] picked: {pick.get('title')} ({detail_url}) score={best_score:.2f}")
    q_low = title.strip().lower()
    cand_low = (pick.get("title") or "").strip().lower()
    strong_substring = q_low in cand_low or cand_low in q_low
    if best_score < 0.25 and not strong_substring:
        if verbose:
            print("        [match] similarity too low; skipping this candidate")
        return None, None
    try:
        detail_resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        if verbose:
            print(f"        [http] detail {detail_url} -> {detail_resp.status_code}")
        detail_resp.raise_for_status()
    except Exception as exc:
        print(f"[warn] detail fetch failed for '{title}' ({detail_url}): {exc}")
        return None, None

    cover_link = build_cover_from_book_url(detail_url)
    if verbose:
        print(f"        [parse] cover_link={cover_link}")
    return cover_link, detail_url


def main():
    parser = argparse.ArgumentParser(description="Fetch book cover links from bookzone.cwgv.com.tw")
    parser.add_argument("--title", help="Only process a specific title")
    parser.add_argument("--limit", type=int, default=None, help="Max titles to process")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cover links")
    parser.add_argument("--sleep", type=float, default=1.0, help="Delay between requests (sec)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes")
    parser.add_argument("--verbose", action="store_true", help="Show debug info (match URLs/ratios)")
    parser.add_argument("--skip-missing-check", action="store_true", help="Skip checking titles_not_in_csv before running")
    parser.add_argument("--drop-missing", action="store_true", help="Auto-drop titles listed in titles_not_in_csv.txt without prompt")
    parser.add_argument("--force-drop-missing", action="store_true", help="Allow dropping even if inventory > 0")
    parser.add_argument("--missing-file", default=MISSING_TITLES_FILE, help="Path to titles_not_in_csv list")
    manual = parser.add_argument_group("manual override")
    manual.add_argument("--set-title", help="Specific title to set manually")
    manual.add_argument("--set-cover", help="Cover URL to assign (requires --set-title)")
    args = parser.parse_args()

    if args.set_title and args.set_cover:
        with app.app_context():
            title_obj = BookTitle.query.filter_by(title=args.set_title.strip()).first()
            if not title_obj:
                print(f"[error] title not found: {args.set_title}")
                return 1
            print(f"[manual] setting cover for '{title_obj.title}' -> {args.set_cover}")
            if not args.dry_run:
                title_obj.cover_link = args.set_cover.strip()
                db.session.commit()
                print("[manual] saved")
            else:
                print("[manual] dry-run; not saved")
        return 0
    elif args.set_title or args.set_cover:
        print("[error] use both --set-title and --set-cover for manual mode")
        return 1

    # Fresh log for each run
    open(MISSING_LOG, "w", encoding="utf-8").write("")

    if not args.skip_missing_check and os.path.exists(args.missing_file):
        missing_titles = load_missing_titles(args.missing_file)
        missing_titles = [t for t in missing_titles if t]
        if missing_titles:
            print(f"[info] titles_not_in_csv entries: {len(missing_titles)} (from {args.missing_file})")
            proceed = args.drop_missing
            if not proceed:
                reply = input("\n[warning] Drop these titles before fetching covers? [y/N]: ").strip().lower()
                proceed = reply.startswith("y")
            if proceed:
                with app.app_context():
                    removed, skipped, not_found = drop_missing_titles(missing_titles, force=args.force_drop_missing)
                try:
                    open(args.missing_file, "w", encoding="utf-8").write("")
                except Exception:
                    pass
                print(f"[missing] removed={removed}, skipped={skipped}, not_found={not_found}")
            else:
                print("[info] skipping drop of titles_not_in_csv entries")

    with app.app_context():
        q = BookTitle.query
        if args.title:
            q = q.filter(BookTitle.title == args.title)
        elif not args.force:
            q = q.filter((BookTitle.cover_link == None) | (BookTitle.cover_link == ""))  # noqa: E711

        titles = q.order_by(BookTitle.updated_at.desc()).all()
        if args.limit:
            titles = titles[: args.limit]

        if not titles:
            print("No titles to process.")
            return

        total = len(titles)
        processed = 0
        found = 0
        skipped = 0
        failed = 0

        print(f"[start] processing {total} title(s) (force={args.force}, dry_run={args.dry_run})")

        for idx, title_obj in enumerate(titles, start=1):
            if title_obj.cover_link and not args.force:
                skipped += 1
                print(f"[skip {idx}/{total}] {title_obj.title} (already has cover link)")
                continue

            print(f"[{idx}/{total}] lookup: {title_obj.title}")
            cover_link, source_url = fetch_url_for_title(title_obj.title, verbose=args.verbose)

            if not cover_link:
                failed += 1
                print("        -> not found")
                log_missing(title_obj.title, reason="not_found")
            else:
                found += 1
                print(f"        -> cover {cover_link} ({source_url})")
                if not args.dry_run:
                    title_obj.cover_link = cover_link
                    db.session.commit()

            processed += 1
            if args.limit and processed >= args.limit:
                break
            if args.sleep:
                time.sleep(args.sleep)

        print(f"[done] processed={processed}, found={found}, skipped={skipped}, not_found={failed}")


if __name__ == "__main__":
    sys.exit(main())
