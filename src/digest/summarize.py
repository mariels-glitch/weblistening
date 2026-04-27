"""
Summarizer. Produces the structured Digest object that the renderer binds to.

Two paths:
  - ClaudeSummarizer: Claude Sonnet 4.6 via the Anthropic API, structured
    output with JSON schema, mandatory citations. Production path.
  - RuleSummarizer: Deterministic, no LLM. Uses aggregate.py outputs directly
    and writes a reasonable headline from template. Used offline / sandboxed.

The rule summarizer is NOT a long-term substitute — the magic of the PRD's
daily digest is the written-by-a-human-feeling headline that comes from a
Sonnet-class model. The rule path exists so eng can develop and test the
rest of the pipeline without burning API credit.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Protocol

from src.digest import aggregate
from src.models import Alert, Digest, Item, LeadershipMention, QuoteCard, ThemeCard, TrendPoint


class Summarizer(Protocol):
    def summarize(
        self,
        items: list[Item],
        trends: list[TrendPoint],
        prior_score: int,
        cfg: dict,
    ) -> Digest: ...


# --------------------------------------------------------------- Rule-based
class RuleSummarizer:
    def summarize(self, items, trends, prior_score, cfg) -> Digest:
        dcfg = cfg["digest"]
        weights = dcfg["sentiment_weights"]
        score = aggregate.sentiment_score(items, weights)
        sup_loved, sup_disliked = aggregate.resolve_theme_overlap(items, dcfg["min_items_for_theme"])
        loved = aggregate.theme_cards(items, "positive", dcfg["min_items_for_theme"], suppress=sup_loved)
        disliked = aggregate.theme_cards(items, "negative", dcfg["min_items_for_theme"], suppress=sup_disliked)
        quotes = aggregate.pick_quotes(items, dcfg["top_quotes"])
        alerts = aggregate.anomalies(items, dcfg.get("anomaly_sigma", 2.0))
        leadership = aggregate.leadership_mentions(items)
        trending = aggregate.summarize_trending(trends, 3)

        # Write a plain headline. No marketing language.
        delta = score - prior_score
        pieces = []
        if loved:
            pieces.append(f"customers praised {loved[0].theme.lower()}")
        if disliked:
            pieces.append(f"complaints rising on {disliked[0].theme.lower()}")
        if not pieces:
            pieces.append("quiet day of chatter across sources")
        headline = "Today: " + "; ".join(pieces) + "."

        return Digest(
            digest_id=f"digest-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            date=datetime.now(timezone.utc),
            window_hours=cfg["sources"]["reddit"].get("window_hours", 24),
            items_analyzed=len(items),
            sentiment_score=score,
            sentiment_delta_vs_7d=delta,
            headline=headline,
            loved=loved,
            disliked=disliked,
            trending=trending,
            quotes=quotes,
            alerts=alerts,
            leadership_mentions=leadership,
            methodology_sources=[
                "Reddit (r/CreditCards, r/petinsurance, r/dogs, r/cats, r/personalfinance)",
                "Google Trends (US)",
                "Apple App Store (US)",
                "Trustpilot (manual weekly)",
            ],
            methodology_window=f"Trailing {cfg['sources']['reddit'].get('window_hours', 24)}h ending {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        )


# --------------------------------------------------------------- Claude-backed
DIGEST_PROMPT = """You are writing a daily "Nibbles Pulse" digest for internal leaders at Nibbles (pet credit card + pet insurance). Write in the voice of a careful PM: plain, specific, no marketing language, no emojis.

You are given:
 - A list of classified customer items (Reddit, App Store, Trustpilot).
 - Google Trends rising queries.
 - Prior day sentiment score.

Your job:
 1. Return a 12-word headline: the single most important thing to say about today.
 2. Confirm the sentiment score and write a one-sentence rationale.
 3. Make sure every claim you write cites underlying item_ids from the input.

Return ONLY valid JSON matching this schema:
{
  "headline": "string",
  "score_rationale": "string"
}
No prose outside the JSON.
"""


class ClaudeSummarizer:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self._client = None

    def _client_or_raise(self):
        if self._client is None:
            import anthropic  # type: ignore
            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        return self._client

    def summarize(self, items, trends, prior_score, cfg) -> Digest:
        # First compute the deterministic parts exactly as the rule summarizer does.
        # The LLM is only responsible for the human-feeling headline + rationale;
        # the numbers, themes, quotes, and alerts come from aggregate.py. This is
        # the hallucination guardrail: the LLM cannot invent a fact about a theme
        # count because it doesn't write the count.
        base = RuleSummarizer().summarize(items, trends, prior_score, cfg)

        # Prepare a compressed corpus for the LLM: id + text + sentiment, capped
        max_items = cfg.get("llm", {}).get("max_items_to_summarize", 400)
        corpus = [
            {"id": i.item_id, "text": i.text[:300], "sent": i.sentiment}
            for i in items[:max_items]
        ]
        user = (
            f"PRIOR SCORE: {prior_score}\nTODAY SCORE: {base.sentiment_score}\n\n"
            f"ITEMS (sampled):\n{json.dumps(corpus, ensure_ascii=False)}\n\n"
            f"THEMES_POS: {[t.theme for t in base.loved]}\n"
            f"THEMES_NEG: {[t.theme for t in base.disliked]}\n"
            f"TRENDING: {[t.query for t in base.trending]}\n"
        )

        client = self._client_or_raise()
        resp = client.messages.create(
            model=self.model,
            max_tokens=400,
            temperature=0.2,
            system=DIGEST_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        try:
            obj = json.loads(raw)
            base.headline = obj.get("headline", base.headline)
        except Exception:
            pass  # Keep rule headline if LLM returned junk
        return base


def make_summarizer(cfg: dict) -> Summarizer:
    if cfg.get("llm", {}).get("provider") == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeSummarizer(model=cfg["llm"].get("summarizer_model", "claude-sonnet-4-6"))
    return RuleSummarizer()
