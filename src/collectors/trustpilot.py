"""
Trustpilot collector — reads from fixtures/trustpilot_paste.txt.

Workflow (5 min/week):
  1. Go to https://www.trustpilot.com/review/nibbles.com
  2. Copy any new reviews and append them to fixtures/trustpilot_paste.txt
     using the format already in that file (--- blocks with AUTHOR/DATE/STARS/TITLE/BODY)
  3. Run the digest as normal — new reviews are picked up automatically

The file is append-only. The collector dedupes by author+date so running
the digest multiple times won't double-count anything.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from src.models import Item, ProductLine, Source


PASTE_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "trustpilot_paste.txt"
TRUSTPILOT_URL = "https://www.trustpilot.com/review/nibbles.com"


def _parse_paste(text: str) -> list[dict]:
    """Parse the ---block format into raw review dicts."""
    reviews: list[dict] = []
    for block in text.split("---"):
        block = block.strip()
        if not block:
            continue
        rv: dict = {}
        for line in block.splitlines():
            if line.startswith("AUTHOR:"):
                rv["author"] = line[7:].strip()
            elif line.startswith("DATE:"):
                rv["date"] = line[5:].strip()
            elif line.startswith("STARS:"):
                try:
                    rv["stars"] = int(line[6:].strip())
                except ValueError:
                    rv["stars"] = 3
            elif line.startswith("TITLE:"):
                rv["title"] = line[6:].strip()
            elif line.startswith("BODY:"):
                rv["body"] = line[5:].strip()
        if rv.get("body") or rv.get("title"):
            reviews.append(rv)
    return reviews


def _to_item(rv: dict) -> Item | None:
    try:
        title = rv.get("title", "").strip()
        body  = rv.get("body", "").strip()
        text  = f"{title}\n{body}".strip() if title else body
        if not text:
            return None

        stars  = rv.get("stars", 3)
        author = rv.get("author", "anonymous")

        date_str = rv.get("date", "")
        try:
            created_at = datetime.fromisoformat(date_str + "T12:00:00+00:00") if date_str else datetime.now(timezone.utc)
        except ValueError:
            created_at = datetime.now(timezone.utc)

        # Stable ID: hash of author + date so reruns don't duplicate
        uid = hashlib.sha256(f"{author}{date_str}".encode()).hexdigest()[:12]

        return Item(
            item_id=f"trustpilot:{uid}",
            source=Source.TRUSTPILOT_MANUAL,
            product_line=ProductLine.BRAND,
            author_hash=Item.hash_author(author),
            created_at=created_at,
            text=text[:10000],
            url=TRUSTPILOT_URL,
            engagement={"stars": stars},
        )
    except Exception as e:
        print(f"[trustpilot] skipping review: {e}")
        return None


def fetch(cfg: dict) -> list[Item]:
    if not PASTE_FILE.exists():
        print(f"[trustpilot] no paste file found at {PASTE_FILE} — skipping")
        return []

    raw = PASTE_FILE.read_text(encoding="utf-8")
    reviews = _parse_paste(raw)
    items = [item for rv in reviews if (item := _to_item(rv)) is not None]
    print(f"[trustpilot] loaded {len(items)} reviews from paste file")
    return items
