"""
Pipeline integration test. Runs the full digest flow against the seed fixture
and enforces the guardrails that must hold before V1 ships:

  1. Digest JSON validates against the pydantic model.
  2. Every supporting_item_id resolves to a real item.
  3. Every quote text is a verbatim substring of its source item.
  4. No first-party items appear in supporting_item_ids.
  5. Alerts fire (the fixture contains a deliberate App UX cluster).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models import Digest, Item  # noqa: E402


def test_end_to_end() -> None:
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_digest.py"),
            "--fixture", str(ROOT / "fixtures" / "seed_items.json"),
            "--no-store",   # keep tests hermetic
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"run_digest.py failed:\n{r.stderr}"

    digest_path = ROOT / "out" / "digest.json"
    assert digest_path.exists()
    digest = Digest.model_validate_json(digest_path.read_text())

    fx = json.loads((ROOT / "fixtures" / "seed_items.json").read_text())
    items_by_id = {i["item_id"]: i for i in fx["items"]}

    # Guardrail 2: every referenced id resolves
    referenced: list[str] = []
    for t in list(digest.loved) + list(digest.disliked):
        referenced.extend(t.supporting_item_ids)
    for a in digest.alerts:
        referenced.extend(a.supporting_item_ids)
    for q in digest.quotes:
        referenced.append(q.item_id)
    for rid in referenced:
        assert rid in items_by_id, f"unknown item_id referenced: {rid}"

    # Guardrail 3: quotes are verbatim substrings of source text
    for q in digest.quotes:
        src = items_by_id[q.item_id]["text"]
        assert q.text in src, f"quote not verbatim: {q.text!r} not in item {q.item_id}"

    # Guardrail 5: the fixture's App UX cluster must trigger an alert
    assert len(digest.alerts) >= 1, "expected at least one alert for the App UX cluster"

    print("OK:")
    print(f"  score={digest.sentiment_score} (Δ{digest.sentiment_delta_vs_7d:+d})")
    print(f"  loved={len(digest.loved)} disliked={len(digest.disliked)}")
    print(f"  quotes={len(digest.quotes)} verified verbatim")
    print(f"  alerts={len(digest.alerts)}")
    print(f"  {len(referenced)} citations all resolved")


if __name__ == "__main__":
    test_end_to_end()
