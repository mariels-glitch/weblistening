"""
DuckDB-backed item store.

Persists every Item across runs so the enrichment pipeline has a true
deduplication window (not just within-run) and anomaly detection can
accumulate a real baseline.

Schema is intentionally minimal — just enough to dedupe and compute
rolling sentiment volumes per theme×day.

Usage:
    store = ItemStore()           # opens/creates nibbles.duckdb
    new_items = store.upsert(items)  # returns only the truly new ones
    store.close()
"""
from __future__ import annotations

import json
from pathlib import Path

from src.models import Item


DB_PATH = Path(__file__).resolve().parents[2] / "nibbles.duckdb"


class ItemStore:
    def __init__(self, db_path: Path = DB_PATH):
        import duckdb  # type: ignore
        self._con = duckdb.connect(str(db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS items (
                item_id       TEXT PRIMARY KEY,
                source        TEXT,
                product_line  TEXT,
                created_at    TIMESTAMPTZ,
                sentiment     TEXT,
                themes        TEXT,       -- JSON array
                text_hash     TEXT,       -- sha256[:16] for soft-dedupe
                ingested_at   TIMESTAMPTZ DEFAULT current_timestamp
            )
        """)

    def upsert(self, items: list[Item]) -> list[Item]:
        """Insert items not already in the DB. Returns only the new ones."""
        import hashlib
        new: list[Item] = []
        for it in items:
            text_hash = hashlib.sha256(it.text.strip().lower().encode()).hexdigest()[:16]
            existing = self._con.execute(
                "SELECT item_id FROM items WHERE item_id = ? OR text_hash = ?",
                [it.item_id, text_hash],
            ).fetchone()
            if existing:
                continue
            self._con.execute(
                """INSERT INTO items
                   (item_id, source, product_line, created_at, sentiment, themes, text_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    it.item_id,
                    it.source.value,
                    it.product_line.value,
                    it.created_at,
                    it.sentiment,
                    json.dumps(it.themes),
                    text_hash,
                ],
            )
            new.append(it)
        return new

    def theme_negative_volumes(self, days: int = 14) -> dict[str, list[int]]:
        """Returns {theme_id: [daily_neg_count]} for the rolling window.

        Used by the anomaly detector to compute z-scores against a real baseline
        rather than the V1 stub heuristic.
        """
        rows = self._con.execute(f"""
            SELECT
                unnest(json_extract_string(themes, '$[*]')) AS theme_id,
                date_trunc('day', created_at)::DATE          AS day,
                count(*)                                     AS cnt
            FROM items
            WHERE sentiment = 'negative'
              AND created_at >= current_timestamp - INTERVAL '{days} days'
              AND themes != '[]'
            GROUP BY 1, 2
            ORDER BY 1, 2
        """).fetchall()

        volumes: dict[str, list[int]] = {}
        for theme_id, _day, cnt in rows:
            volumes.setdefault(theme_id, []).append(cnt)
        return volumes

    def close(self) -> None:
        self._con.close()
