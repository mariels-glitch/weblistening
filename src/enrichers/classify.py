"""
Sentiment classifier.

Two implementations behind a shared interface:
  - ClaudeClassifier: Uses the Anthropic API (claude-haiku-4-5-20251001).
    Production path. Requires ANTHROPIC_API_KEY in the environment.
  - LexiconClassifier: Deterministic fallback. Used when LLM is unavailable
    (no API key, rate-limited, or sandboxed). Also used as a secondary check
    on low-confidence LLM calls in production.

The interface is `classify(text) -> (label, confidence)` so the rest of the
pipeline doesn't care which implementation is active.

Eng handoff note: per PRD §7.2, Haiku is used for classification (cheap,
high throughput) and Sonnet for the daily summarization (richer reasoning).
"""
from __future__ import annotations

import json
import os
from typing import Literal, Protocol

Sentiment = Literal["positive", "negative", "neutral", "mixed"]


# -------------------------------------------------------------- Protocol
class SentimentClassifier(Protocol):
    def classify(self, text: str) -> tuple[Sentiment, float]: ...


# -------------------------------------------------------------- Lexicon
# Curated for finance-product reviews, not general sentiment. Weighted
# because "denied" hits harder than "slow"; "approved in 2 minutes" harder
# than "nice."
_POS = {
    "love": 2, "loved": 2, "great": 1, "best": 2, "amazing": 2, "excellent": 2,
    "fantastic": 2, "awesome": 2, "thrilled": 2, "impressed": 1, "smooth": 1,
    "easy": 1, "quick": 1, "fast": 1, "helpful": 1, "reimbursed": 2,
    "approved": 1, "recommend": 2, "worth it": 2, "saved me": 3, "highly recommend": 3,
    "10/10": 3, "5 stars": 3, "five stars": 3, "no issues": 1, "seamless": 2,
}
_NEG = {
    "hate": 2, "terrible": 3, "awful": 3, "worst": 3, "scam": 3, "denied": 3,
    "denial": 2, "delay": 2, "delayed": 2, "slow": 1, "bug": 1, "crash": 2,
    "crashes": 2, "crashed": 2, "useless": 2, "never": 1, "ridiculous": 2,
    "rude": 2, "unresponsive": 2, "no one answered": 3, "still waiting": 3,
    "months": 1, "weeks": 1, "waste": 2, "disappointed": 2, "frustrated": 2,
    "broken": 2, "glitch": 1, "refused": 2, "rejected": 2, "1 star": 3, "one star": 3,
}
_NEGATIONS = {"not", "no", "never", "without", "didn't", "don't", "can't", "cannot", "wouldn't"}


class LexiconClassifier:
    """Deterministic sentiment scorer for fallback + offline dev."""

    def classify(self, text: str) -> tuple[Sentiment, float]:
        tl = text.lower()
        words = tl.split()
        pos = 0
        neg = 0

        # Simple phrase scan
        for phrase, w in _POS.items():
            if phrase in tl:
                pos += w
        for phrase, w in _NEG.items():
            if phrase in tl:
                neg += w

        # Very lightweight negation: flip a single following positive/negative
        for i, tok in enumerate(words):
            if tok in _NEGATIONS and i + 1 < len(words):
                nxt = words[i + 1].strip(".,!?")
                if nxt in _POS:
                    pos -= _POS[nxt]
                    neg += _POS[nxt]
                elif nxt in _NEG:
                    neg -= _NEG[nxt]
                    pos += _NEG[nxt]

        total = pos + neg
        if total == 0:
            return "neutral", 0.55
        if pos > 0 and neg > 0 and min(pos, neg) / max(pos, neg) > 0.4:
            # Both sides fired strongly — "mixed"
            return "mixed", 0.65
        if pos > neg:
            conf = min(0.60 + 0.06 * (pos - neg), 0.98)
            return "positive", round(conf, 2)
        conf = min(0.60 + 0.06 * (neg - pos), 0.98)
        return "negative", round(conf, 2)


# -------------------------------------------------------------- Claude
class ClaudeClassifier:
    """Production classifier. Uses Claude Haiku via the Anthropic API."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self.model = model
        self._client = None

    def _client_or_raise(self):
        if self._client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "Install `anthropic` to use ClaudeClassifier: pip install anthropic"
                ) from e
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set.")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def classify(self, text: str) -> tuple[Sentiment, float]:
        client = self._client_or_raise()
        prompt = (
            "You are classifying a short piece of customer-voice text about a pet "
            "credit card / pet insurance product. Reply with ONLY a JSON object "
            "of the form {\"sentiment\": \"positive|negative|neutral|mixed\", "
            "\"confidence\": 0.0-1.0}. No prose.\n\nTEXT:\n" + text[:4000]
        )
        resp = client.messages.create(
            model=self.model,
            max_tokens=60,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            obj = json.loads(raw.strip().split("\n")[-1])
            return obj["sentiment"], float(obj.get("confidence", 0.7))
        except Exception:
            # If the model wandered off-format, fall back to lexicon rather than
            # dropping the item.
            return LexiconClassifier().classify(text)


def make_classifier(cfg: dict) -> SentimentClassifier:
    provider = cfg.get("llm", {}).get("provider", "lexicon")
    if provider == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeClassifier(model=cfg["llm"].get("classifier_model", "claude-haiku-4-5-20251001"))
    return LexiconClassifier()
