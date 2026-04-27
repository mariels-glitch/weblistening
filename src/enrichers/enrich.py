"""
Enrichment pipeline.

Takes raw normalized Items out of the collectors, runs PII scrub, language
detect, roster checks, theme tagging, and sentiment classification. Returns
the same Item shape with enrichment fields populated.

Dedupe happens here too, since cross-source near-dupes are common (a Reddit
post quoting an App Store review, for example).
"""
from __future__ import annotations

import hashlib

from src.enrichers import pii, themes
from src.enrichers.classify import make_classifier
from src.models import Item


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for it in items:
        key = hashlib.sha256(it.text.strip().lower().encode("utf-8")).hexdigest()[:16]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _check_roster(it: Item, roster: dict) -> Item:
    """Apply Decision 10 (brand safety)."""
    first_party_handles = {h.lower() for h in roster.get("first_party_usernames", [])}
    leadership = [n.lower() for n in roster.get("leadership_names", [])]
    # author_hash is hashed; we need a convention: if the source data included
    # the raw username, we'd pre-mark before hashing. For V1 we scan the TEXT
    # for leadership names — the common case is "I saw Sarah from Nibbles…".
    tl = it.text.lower()
    # NB: in production, is_first_party should be set in the collector, not
    # here, because by the time we have author_hash we've already dropped the
    # raw author. V1 flags conservatively based on in-text self-reference.
    if any(f"i work at {brand}" in tl or f"/u/{h}" in tl for brand in ["nibbles"] for h in first_party_handles):
        it.is_first_party = True
    if any(name in tl for name in leadership):
        it.mentions_leadership = True
    return it


def enrich(items: list[Item], cfg: dict) -> list[Item]:
    items = _dedupe(items)
    classifier = make_classifier(cfg)
    roster = cfg.get("roster", {})

    out: list[Item] = []
    for it in items:
        # PII scrub first so the classifier never sees an email or phone
        scrubbed, was_modified = pii.scrub(it.text)
        it.text = scrubbed
        it.pii_scrubbed = was_modified

        # Roster checks
        it = _check_roster(it, roster)

        # Drop first-party items (Decision 10)
        if it.is_first_party:
            continue

        # Theme tagging
        it.themes = themes.tag(it.text)

        # Sentiment
        sentiment, conf = classifier.classify(it.text)
        it.sentiment = sentiment
        it.sentiment_conf = conf

        out.append(it)
    return out
