"""
Similarity and recommendation module for book search.

This module provides similarity-based search suggestions when no direct
search results are found.
"""

from .recommender import (
    BookProfile,
    suggest_for_missing_title,
    parse_topics_field,
    score_profile,
    DEFAULT_WEIGHTS,
)

__all__ = [
    "BookProfile",
    "suggest_for_missing_title",
    "parse_topics_field",
    "score_profile",
    "DEFAULT_WEIGHTS",
]


