"""Lightweight embeddings with keyword-overlap boost for entity matching.

Two-component scoring:
  1. Hash embedding cosine similarity — captures shared token structure
  2. Keyword overlap bonus           — rewards candidates whose tokens appear
                                       verbatim in the question, so named
                                       entities like "NovaPay" rank above
                                       coincidental hash collisions

Final score = 0.6 × cosine_similarity + 0.4 × overlap_ratio

This design intentionally avoids heavyweight ML dependencies so the project
can run in a minimal environment while still offering useful retrieval.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

from app.config import get_settings

_DIMENSIONS = 128
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Weights for the combined score
_EMBEDDING_WEIGHT = 0.6
_KEYWORD_WEIGHT = 0.4


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def embed_text(text: str, dimensions: int = _DIMENSIONS) -> list[float]:
    """Create a deterministic, normalized embedding vector for text."""
    settings = get_settings()
    _ = settings.embedding_model

    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * dimensions

    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % dimensions
        vector[index] += 1.0

    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0.0:
        return [0.0] * dimensions

    return [value / magnitude for value in vector]


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    """Compute cosine similarity between two vectors."""
    left_values = list(left)
    right_values = list(right)

    if len(left_values) != len(right_values):
        raise ValueError("Vectors must have the same length")

    if not left_values or not right_values:
        return 0.0

    dot = sum(a * b for a, b in zip(left_values, right_values))
    left_norm = math.sqrt(sum(a * a for a in left_values))
    right_norm = math.sqrt(sum(b * b for b in right_values))

    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    return dot / (left_norm * right_norm)


def find_best_matches(query: str, candidates: Iterable[str], top_k: int = 5) -> list[tuple[float, str]]:
    """Rank candidate strings by combined embedding + keyword-overlap score.

    The keyword-overlap component gives a significant bonus to candidates
    whose tokens appear verbatim in the question (e.g. the user literally
    mentions "NovaPay"). This ensures named-entity matches surface higher
    than coincidental embedding collisions from unrelated words.

    Score = 0.6 × cosine_similarity + 0.4 × overlap_ratio
    where overlap_ratio = |candidate_tokens ∩ query_tokens| / |candidate_tokens|
    """
    query_tokens = set(_tokenize(query))
    query_embedding = embed_text(query)
    ranked: list[tuple[float, str]] = []

    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue

        candidate_embedding = embed_text(candidate)
        base_score = cosine_similarity(query_embedding, candidate_embedding)

        # Keyword overlap: fraction of candidate tokens found anywhere in the query
        candidate_tokens = set(_tokenize(candidate))
        overlap = (
            len(candidate_tokens & query_tokens) / len(candidate_tokens)
            if candidate_tokens
            else 0.0
        )

        score = _EMBEDDING_WEIGHT * base_score + _KEYWORD_WEIGHT * overlap
        ranked.append((score, candidate.strip()))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[: max(1, top_k)]
