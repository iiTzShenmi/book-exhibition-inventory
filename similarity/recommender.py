"""Lightweight title+topics recommender shared by the web app and CLI tools."""
import json
import re
from dataclasses import dataclass, field
from rapidfuzz import fuzz  
from typing import Iterable, List, Sequence, Tuple


def _normalize(text: str) -> str:
    """Normalize text for comparison."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _tokenize(text: str) -> List[str]:
    """Split a string into lowercase keywords."""
    return [tok for tok in re.split(r"[\s,，、/;；]+", _normalize(text)) if tok]


@dataclass
class BookProfile:
    title: str
    topics: List[str] = field(default_factory=list)
    id: int | None = None
    author: str = ""
    cabinet: str = ""
    cabinet_type: str = ""
    in_stock: bool | None = None


def parse_topics_field(raw) -> List[str]:
    """Convert mixed topic formats (JSON list, comma string, list) into a clean list."""
    if raw is None:
        return []

    candidates: Sequence[str]
    if isinstance(raw, (list, tuple, set)):
        candidates = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        loaded = None
        try:
            loaded = json.loads(text)
        except Exception:
            loaded = None
        if isinstance(loaded, list):
            candidates = loaded
        else:
            candidates = re.split(r"[;,，、/；]+", text)

    cleaned: List[str] = []
    seen = set()
    for cand in candidates:
        topic = str(cand).strip()
        if not topic:
            continue
        norm = _normalize(topic)
        if norm in seen:
            continue
        seen.add(norm)
        cleaned.append(topic)
    return cleaned


DEFAULT_WEIGHTS = {
    "title": 0.8,
    "topics": 0.5,
    "title_substring_bonus": 0.2,
    "topic_hit_bonus": 0.15,
}


def score_profile(query: str, profile: BookProfile, weights: dict | None = None) -> float:
    weights = weights or DEFAULT_WEIGHTS
    
    # RapidFuzz handles normalization internally, but keeping yours ensures consistency
    # with your topic logic.
    q_norm = _normalize(query)
    title_norm = _normalize(profile.title)
    
    if not q_norm or not title_norm:
        return 0.0

    # OPTIMIZATION: rapidfuzz.fuzz.ratio returns 0-100, so divide by 100
    # This replaces SequenceMatcher(None, a, b).ratio()
    title_sim = fuzz.ratio(q_norm, title_norm) / 100.0
    
    title_score = title_sim * weights.get("title", 1.0)

    if q_norm in title_norm:
        title_score += weights.get("title_substring_bonus", 0.0)

    # Keep your topic logic (sets are fast enough for now)
    query_tokens = set(_tokenize(query))
    topic_tokens = set()
    for t in profile.topics or []:
        topic_tokens.update(_tokenize(t))

    topic_score = 0.0
    if topic_tokens and query_tokens:
        overlap = len(topic_tokens & query_tokens) / len(topic_tokens)
        topic_score = overlap * weights.get("topics", 0.0)
        if overlap > 0:
            topic_score += weights.get("topic_hit_bonus", 0.0)

    return title_score + topic_score

def suggest_for_missing_title(
    profiles: Iterable[BookProfile], query: str, top: int = 5
) -> List[Tuple[float, BookProfile]]:
    """Return top matching BookProfile items for a missing query."""
    scored: List[Tuple[float, BookProfile]] = []
    for prof in profiles:
        s = score_profile(query, prof)
        if s <= 0:
            continue
        scored.append((s, prof))

    if not scored:
        return []

    best_by_title = {}
    for score, prof in scored:
        key = _normalize(prof.title)
        existing = best_by_title.get(key)
        if (
            existing is None
            or score > existing[0]
            or (
                score == existing[0]
                and bool(prof.in_stock)
                and not bool(existing[1].in_stock)
            )
        ):
            best_by_title[key] = (score, prof)

    return sorted(best_by_title.values(), key=lambda pair: pair[0], reverse=True)[:top]


