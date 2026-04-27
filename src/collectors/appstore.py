"""
Apple App Store collector — iTunes RSS customer reviews.

Uses the public RSS feed (JSON variant), which works without auth for any
US app ID. Nibbles Card iOS = 1588893484 (Decision 8).

Production note: the RSS feed only returns the most recent ~500 reviews
across 10 pages. For historical backfill, eng should add the App Store
Connect API with Nibbles' team credentials.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable

import requests

from src.models import Item, ProductLine, Source


RSS = "https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/sortBy=mostRecent/page={page}/json"


def _get(url: str) -> dict:
    r = requests.get(url, timeout=15, headers={"User-Agent": "nibbles-listening/0.1"})
    r.raise_for_status()
    return r.json()


def fetch(cfg: dict) -> list[Item]:
    src = cfg["sources"]["appstore"]
    if not src.get("enabled"):
        return []

    country = src.get("country", "us")
    app_id = src["app_id"]
    max_pages = src.get("max_pages", 5)

    items: list[Item] = []
    for page in range(1, max_pages + 1):
        try:
            data = _get(RSS.format(country=country, app_id=app_id, page=page))
        except Exception as e:
            print(f"[appstore] page {page} failed: {e}")
            break

        entries = data.get("feed", {}).get("entry", [])
        # Page 1 has the app metadata as entry[0]; reviews start at [1]
        for e in entries:
            if "im:rating" not in e:
                continue
            try:
                rating = int(e["im:rating"]["label"])
            except Exception:
                rating = 0
            title = e.get("title", {}).get("label", "")
            body = e.get("content", {}).get("label", "")
            text = f"{title}\n{body}".strip()
            if not text:
                continue
            rid = e.get("id", {}).get("label", f"unknown-{page}-{hash(text)}")
            author = e.get("author", {}).get("name", {}).get("label", "anonymous")
            updated = e.get("updated", {}).get("label")
            try:
                created_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                created_at = datetime.now(timezone.utc)

            items.append(
                Item(
                    item_id=f"appstore:{rid.split('/')[-1]}",
                    source=Source.APPSTORE,
                    product_line=ProductLine.CARD,  # The iOS app IS the card app
                    author_hash=Item.hash_author(author),
                    created_at=created_at,
                    text=text[:10000],
                    url=e.get("link", {}).get("attributes", {}).get("href", "https://apps.apple.com"),
                    engagement={"stars": rating},
                )
            )
        time.sleep(1.0)

    return items
