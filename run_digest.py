#!/usr/bin/env python3
"""
Nibbles Listening Engine — digest entry point.

Usage:
  python run_digest.py                      # live fetch → renders to ./out/
  python run_digest.py --fixture fixtures/seed_items.json
  python run_digest.py --send               # render + email via Resend
  python run_digest.py --no-store           # skip DuckDB persistence

ENV:
  ANTHROPIC_API_KEY    enables Claude classifier + summarizer
  REDDIT_CLIENT_ID     } Reddit OAuth via PRAW
  REDDIT_CLIENT_SECRET }
  REDDIT_USERNAME      }
  REDDIT_PASSWORD      }
  RESEND_API_KEY       required for --send
  DIGEST_FROM          sender address (verified in Resend)
  DIGEST_TO            comma-separated recipient list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env if present so local runs don't need manual export
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from src.collectors import appstore, reddit, trends, trustpilot
from src.digest import aggregate, render
from src.digest.summarize import make_summarizer
from src.enrichers.enrich import enrich
from src.models import Item, TrendPoint


def load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def collect_live(cfg: dict) -> tuple[list[Item], list[TrendPoint]]:
    items: list[Item] = []
    items += reddit.fetch(cfg)
    items += appstore.fetch(cfg)
    items += trustpilot.fetch(cfg)
    trend_points = trends.fetch(cfg)
    return items, trend_points


def load_fixture(path: Path) -> tuple[list[Item], list[TrendPoint]]:
    data = json.loads(path.read_text())
    items = [Item.model_validate(i) for i in data.get("items", [])]
    trend_points = [TrendPoint.model_validate(t) for t in data.get("trends", [])]
    return items, trend_points


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--fixture", default=None)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--prior-score", type=int, default=74)
    ap.add_argument("--send", action="store_true", help="Send digest via Resend after rendering.")
    ap.add_argument("--no-store", action="store_true", help="Skip DuckDB persistence.")
    args = ap.parse_args()

    cfg = load_cfg(Path(args.config))
    print(f"[run] started {datetime.now(timezone.utc).isoformat()}")

    if args.fixture:
        print(f"[run] loading fixture {args.fixture}")
        items, trend_points = load_fixture(Path(args.fixture))
    else:
        print("[run] live collection")
        items, trend_points = collect_live(cfg)

    print(f"[run] collected {len(items)} items, {len(trend_points)} trend points")

    items = enrich(items, cfg)
    print(f"[run] enriched: {len(items)} items after dedupe + first-party filter")

    # DuckDB persistence — dedupe across runs, build anomaly baseline
    if not args.no_store and not args.fixture:
        try:
            from src.storage.store import ItemStore
            store = ItemStore()
            before = len(items)
            items = store.upsert(items)
            store.close()
            print(f"[run] store: {len(items)} new (of {before} enriched), {before - len(items)} already seen")
        except Exception as e:
            print(f"[run] store unavailable ({e}), continuing without persistence")

    summarizer = make_summarizer(cfg)
    digest = summarizer.summarize(items, trend_points, args.prior_score, cfg)
    print(
        f"[run] digest: score={digest.sentiment_score} (Δ{digest.sentiment_delta_vs_7d:+d}) "
        f"loved={len(digest.loved)} disliked={len(digest.disliked)} "
        f"alerts={len(digest.alerts)} leadership={len(digest.leadership_mentions)}"
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    html = render.render(digest, items)
    html_path = out_dir / "digest.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"[run] wrote {html_path}")

    json_path = out_dir / "digest.json"
    json_path.write_text(digest.model_dump_json(indent=2), encoding="utf-8")
    print(f"[run] wrote {json_path}")

    if args.send:
        from src.digest.mailer import send_digest
        send_digest(html=html, digest=digest)

    return 0


if __name__ == "__main__":
    sys.exit(main())
