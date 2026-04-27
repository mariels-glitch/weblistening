"""
Aggregation: enriched Items + TrendPoints → raw material the summarizer uses.

Responsibilities:
  - compute the daily sentiment score (0-100)
  - group items by theme, ranked by weighted volume
  - pick 3 quote candidates (verbatim, high-engagement, no PII, no toxicity)
  - flag anomalies vs baseline (stub baseline for V1 dogfood)
  - collect leadership mentions for the quiet callout (Decision 10)
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from src.enrichers.themes import label as theme_label
from src.models import Alert, Item, LeadershipMention, QuoteCard, ThemeCard, TrendPoint


def sentiment_score(items: list[Item], weights: dict) -> int:
    """Convert sentiment distribution into a 0-100 score, weighted by config."""
    if not items:
        return 50
    vals = []
    for it in items:
        w = weights.get(it.sentiment or "neutral", 0.5)
        vals.append(w)
    avg = sum(vals) / len(vals)
    return int(round(avg * 100))


def group_by_theme(items: list[Item]) -> dict[str, list[Item]]:
    buckets: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        for t in it.themes or []:
            buckets[t].append(it)
    return buckets


def _one_line_for_theme(theme_id: str, theme_items: list[Item]) -> str:
    """Pick a short, human-readable, theme-relevant line from the theme items.

    Prefers items where the theme keywords actually appear in the *selected
    line*, not just the item overall. Falls back to the shortest line.
    """
    from src.enrichers.themes import THEMES
    _, keywords = THEMES.get(theme_id, (theme_id, []))

    def lines(it: Item) -> list[str]:
        # Split on sentences for finer granularity
        raw = it.text.replace("\n", " ")
        parts: list[str] = []
        for sep in [". ", "! ", "? "]:
            if not parts:
                parts = raw.split(sep)
            else:
                new_parts = []
                for p in parts:
                    new_parts.extend(p.split(sep))
                parts = new_parts
        return [p.strip() + "." if not p.endswith(".") else p.strip() for p in parts if p.strip()]

    candidates: list[tuple[int, int, str]] = []  # (relevance, length, text)
    for it in theme_items:
        for line in lines(it):
            if not (20 <= len(line) <= 160):
                continue
            ll = line.lower()
            rel = sum(1 for k in keywords if k in ll)
            eng = it.engagement.get("upvotes", 0) + it.engagement.get("stars", 0) * 4
            candidates.append((rel, -len(line) + eng, line))

    if candidates:
        # Rank by theme relevance desc, then (shorter, higher-engagement) desc
        candidates.sort(key=lambda t: (-t[0], -t[1]))
        # Keep only candidates that actually matched a theme keyword if any exist
        best_rel = candidates[0][0]
        if best_rel > 0:
            candidates = [c for c in candidates if c[0] == best_rel]
        return candidates[0][2]

    t = theme_items[0].text.split("\n", 1)[0]
    return (t[:140] + "…") if len(t) > 140 else t


def theme_cards(
    items: list[Item],
    sentiment: str,
    min_items: int,
    max_cards: int = 3,
    suppress: set[str] | None = None,
) -> list[ThemeCard]:
    """Build theme cards for one sentiment bucket.

    `suppress` is a set of theme_ids that should be dropped from this bucket,
    typically because they're more dominantly expressed in the opposite bucket
    (e.g. don't show "App UX" under Loved when 6 items dislike it).
    """
    suppress = suppress or set()
    buckets = group_by_theme([i for i in items if i.sentiment == sentiment])
    ranked = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    out: list[ThemeCard] = []
    for theme_id, ts in ranked:
        if theme_id in suppress:
            continue
        if len(ts) < min_items:
            continue
        out.append(
            ThemeCard(
                theme=theme_label(theme_id),
                one_line=_one_line_for_theme(theme_id, ts),
                supporting_item_ids=[t.item_id for t in ts[:6]],
                count=len(ts),
                sentiment=sentiment,
            )
        )
        if len(out) >= max_cards:
            break
    return out


def resolve_theme_overlap(items: list[Item], min_items: int) -> tuple[set[str], set[str]]:
    """
    Return (suppress_from_loved, suppress_from_disliked).

    Rule: if a theme appears in both positive and negative buckets, keep it in
    the bucket with the higher volume and suppress it from the other — unless
    the volumes are within 30%, in which case we keep it in BOTH (it's a
    genuinely mixed theme; this is the kind of nuance we want to surface).
    """
    pos = group_by_theme([i for i in items if i.sentiment == "positive"])
    neg = group_by_theme([i for i in items if i.sentiment == "negative"])
    suppress_loved: set[str] = set()
    suppress_disliked: set[str] = set()
    for theme_id in set(pos) & set(neg):
        p, n = len(pos[theme_id]), len(neg[theme_id])
        if max(p, n) < min_items:
            continue
        if p == 0 or n == 0:
            continue
        ratio = min(p, n) / max(p, n)
        if ratio >= 0.7:
            continue  # genuinely mixed — keep in both
        if p > n:
            suppress_disliked.add(theme_id)
        else:
            suppress_loved.add(theme_id)
    return suppress_loved, suppress_disliked


def pick_quotes(items: list[Item], limit: int = 3) -> list[QuoteCard]:
    """Pick verbatim quotes — short, high-signal, PII-scrubbed already."""
    # Keep strong-valence items only; skip neutral
    candidates = [it for it in items if it.sentiment in {"positive", "negative", "mixed"}]
    # Prefer short, high-engagement items
    def score(it: Item) -> float:
        length_penalty = max(0, len(it.text) - 200) * -0.02
        eng = it.engagement.get("upvotes", 0) + it.engagement.get("stars", 0) * 10
        conf = (it.sentiment_conf or 0.6)
        return eng + conf * 10 + length_penalty

    top = sorted(candidates, key=score, reverse=True)

    picked: list[QuoteCard] = []
    seen_text = set()
    for it in top:
        # Take the first sentence-ish chunk for the quote
        first_line = it.text.strip().split("\n", 1)[0]
        for splitter in [". ", "! ", "? "]:
            if splitter in first_line:
                first_line = first_line.split(splitter, 1)[0] + splitter.strip()
                break
        first_line = first_line.strip()
        if len(first_line) < 12 or len(first_line) > 220:
            continue
        key = first_line.lower()
        if key in seen_text:
            continue
        seen_text.add(key)

        # Verify verbatim substring of source text (PRD §7.2 guardrail)
        if first_line not in it.text:
            continue

        source_label = {
            "reddit": "Reddit",
            "appstore": "App Store · US",
            "trustpilot_manual": "Trustpilot",
            "trends": "Google Trends",
        }.get(it.source.value, it.source.value.title())
        if it.source.value == "reddit":
            # Try to extract subreddit from URL
            try:
                sub = it.url.split("/r/")[1].split("/")[0]
                source_label = f"Reddit · r/{sub}"
            except Exception:
                pass

        eng_label = ""
        if "stars" in it.engagement:
            eng_label = "★" * int(it.engagement["stars"]) + " · verified verbatim"
        elif "upvotes" in it.engagement:
            eng_label = f"{it.engagement['upvotes']} upvotes · verified verbatim"

        picked.append(
            QuoteCard(
                text=first_line,
                source_label=source_label,
                item_id=it.item_id,
                url=it.url,
                engagement_label=eng_label,
            )
        )
        if len(picked) >= limit:
            break
    return picked


def anomalies(items: list[Item], sigma_threshold: float = 2.0) -> list[Alert]:
    """
    V1 stub anomaly detector: compare today's theme×negative volume to a
    small rolling baseline. Until we have real history, we fire on any
    theme where negative volume >= 5 AND exceeds 2x the median theme size.
    Production: replace with a proper z-score against stored baselines.
    """
    neg = [i for i in items if i.sentiment == "negative"]
    if len(neg) < 5:
        return []

    buckets: dict[str, list[Item]] = defaultdict(list)
    for it in neg:
        for t in it.themes or []:
            buckets[t].append(it)

    sizes = [len(v) for v in buckets.values()]
    if not sizes:
        return []
    median = statistics.median(sizes)
    alerts: list[Alert] = []
    for theme_id, ts in buckets.items():
        if len(ts) >= 5 and len(ts) >= max(2.0, sigma_threshold) * median:
            alerts.append(
                Alert(
                    theme=theme_label(theme_id),
                    delta_sigma=round(len(ts) / max(median, 1.0), 1),
                    human_readable=(
                        f"{theme_label(theme_id)} negative mentions at {len(ts)}×, "
                        f"vs median theme size of {int(median)}. Investigate."
                    ),
                    supporting_item_ids=[t.item_id for t in ts[:6]],
                )
            )
    return alerts


def leadership_mentions(items: list[Item]) -> list[LeadershipMention]:
    out: list[LeadershipMention] = []
    for it in items:
        if not it.mentions_leadership:
            continue
        out.append(
            LeadershipMention(
                text=it.text[:240],
                source_label={
                    "reddit": "Reddit",
                    "appstore": "App Store",
                    "trustpilot_manual": "Trustpilot",
                }.get(it.source.value, it.source.value),
                item_id=it.item_id,
                url=it.url,
                subject="(name match in text)",
            )
        )
    return out


def summarize_trending(trend_points: list[TrendPoint], limit: int = 3) -> list[TrendPoint]:
    # Keep the strongest risers
    return sorted(trend_points, key=lambda p: p.wow_percent, reverse=True)[:limit]
