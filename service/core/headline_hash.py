"""Headline normalization and hashing for cross-source deduplication."""

import hashlib
import re
import unicodedata


def normalize_headline(headline: str) -> str:
    text = headline.lower()
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compute_headline_hash(headline: str) -> str:
    normalized = normalize_headline(headline)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
