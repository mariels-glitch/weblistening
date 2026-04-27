"""
Render the Digest model to Apple-style HTML email.
"""
from __future__ import annotations

from pathlib import Path
from statistics import mean

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import Digest, Item


TEMPLATE_DIR = Path(__file__).parent / "templates"


def _sparkline_for_delta(pct: float) -> str:
    """Crude monochrome sparkline path based on the wow_percent."""
    # A positive pct draws a rising line; negative draws falling.
    if pct >= 0:
        return "0,22 15,20 30,18 45,16 60,13 75,10 90,7 105,5 120,3"
    return "0,3 15,5 30,7 45,10 60,13 75,16 90,18 105,20 120,22"


def render(digest: Digest, items: list[Item], flag_base_url: str = "https://example.com/flag") -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )

    # Fast lookup so template helpers don't pay O(n) per cell
    by_id = {it.item_id: it for it in items}

    def avg_conf(ids: list[str]) -> float:
        confs = [by_id[i].sentiment_conf for i in ids if i in by_id and by_id[i].sentiment_conf is not None]
        return mean(confs) if confs else 0.7

    def flag_url(item_id: str, where: str) -> str:
        return f"{flag_base_url}?item={item_id}&section={where}&digest={digest.digest_id}"

    def source_url(item_id: str) -> str:
        """Return the original post URL for a given item_id, or empty string."""
        it = by_id.get(item_id)
        return it.url if it and it.url else ""

    def source_label(item_id: str) -> str:
        """Return a short human label for the source of an item."""
        it = by_id.get(item_id)
        if not it:
            return ""
        if it.source.value == "reddit":
            try:
                sub = it.url.split("/r/")[1].split("/")[0]
                return f"r/{sub}"
            except Exception:
                return "Reddit"
        if it.source.value == "appstore":
            return "App Store"
        return it.source.value.title()

    # Pull out App Store items separately so the template can render them
    appstore_items = [it for it in items if it.source.value == "appstore"]

    template = env.get_template("digest.html.j2")
    return template.render(
        digest=digest,
        avg_conf=avg_conf,
        flag_url=flag_url,
        source_url=source_url,
        source_label=source_label,
        appstore_items=appstore_items,
        sparkline=_sparkline_for_delta,
    )
