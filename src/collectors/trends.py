"""
Google Trends collector — unofficial pytrends.

Caveat per PRD §10: pytrends is unofficial and rate-limited. The digest must
degrade gracefully without Trends; this collector returns [] on any failure
rather than raising.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.models import TrendPoint


def fetch(cfg: dict) -> list[TrendPoint]:
    src = cfg["sources"]["google_trends"]
    if not src.get("enabled"):
        return []
    try:
        from pytrends.request import TrendReq
    except Exception as e:
        print(f"[trends] pytrends unavailable: {e}")
        return []

    keywords = cfg.get("trends_keywords", [])
    if not keywords:
        return []

    try:
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 15))
        pt.build_payload(keywords, timeframe=src.get("timeframe", "now 7-d"), geo=src.get("geo", "US"))
        iot = pt.interest_over_time()
        if iot is None or iot.empty:
            return []

        points: list[TrendPoint] = []
        for kw in keywords:
            if kw not in iot.columns:
                continue
            series = iot[kw].dropna()
            if len(series) < 4:
                continue
            current = float(series.iloc[-1])
            prior = float(series.iloc[: max(1, len(series) // 2)].mean() or 0.0)
            wow = ((current - prior) / prior * 100.0) if prior > 0 else 0.0
            points.append(
                TrendPoint(
                    query=kw,
                    wow_percent=round(wow, 1),
                    current_index=current,
                    as_of=datetime.now(timezone.utc),
                )
            )

        # Rising related queries for the first keyword
        try:
            related = pt.related_queries()
            for kw in keywords:
                rising = (related.get(kw) or {}).get("rising")
                if rising is None or rising.empty:
                    continue
                for _, row in rising.head(src.get("rising_count", 5)).iterrows():
                    points.append(
                        TrendPoint(
                            query=str(row["query"]),
                            wow_percent=float(row["value"]),
                            current_index=0.0,
                            as_of=datetime.now(timezone.utc),
                        )
                    )
        except Exception as e:
            print(f"[trends] related_queries failed (non-fatal): {e}")

        return sorted(points, key=lambda p: p.wow_percent, reverse=True)
    except Exception as e:
        print(f"[trends] failed (non-fatal): {e}")
        return []
