import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.abspath(os.path.dirname(__file__))
LOG_PATH = os.path.join(HERE, "logs", "view_events.jsonl")


def parse_args():
    p = argparse.ArgumentParser(description="Analyze view events to find top titles.")
    p.add_argument("--days", type=int, default=7, help="look back this many days (default: 7)")
    p.add_argument("--top", type=int, default=20, help="how many titles to show")
    p.add_argument("--source", type=str, help="filter by source (e.g., search, card, api)")
    p.add_argument("--file", type=str, default=LOG_PATH, help="path to JSONL log file")
    return p.parse_args()


def load_events(path, min_ts):
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = ev.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts < min_ts:
                continue
            events.append(ev)
    return events


def main():
    args = parse_args()
    now = datetime.now(timezone.utc)
    min_ts = now - timedelta(days=args.days)

    events = load_events(args.file, min_ts)
    if args.source:
        events = [e for e in events if e.get("source") == args.source]

    counts = Counter()
    by_source = defaultdict(Counter)
    for ev in events:
        title = (ev.get("title") or "").strip()
        if not title:
            continue
        src = ev.get("source") or "unknown"
        counts[title] += 1
        by_source[src][title] += 1

    print(f"[info] events loaded: {len(events)}, window: last {args.days} day(s)")
    if not counts:
        print("No data.")
        return 0

    print("\nTop titles:")
    for title, n in counts.most_common(args.top):
        print(f"{n:5d}  {title}")

    if not args.source:
        print("\nBy source (top 5 per source):")
        for src, counter in by_source.items():
            top_items = counter.most_common(5)
            top_str = "; ".join(f"{t} ({c})" for t, c in top_items)
            print(f"- {src}: {top_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
