"""
Reddit collector — RSS feed approach, zero credentials required.

Uses Reddit's public Atom/RSS endpoints which:
  - Require no OAuth app, no client ID, no account login
  - Work for all public subreddits including quarantined ones
    (quarantined subs ARE accessible via RSS with the right headers)
  - Return the same post data as the JSON API

Rate limit: Reddit allows ~1 req/2sec on RSS without auth. We sleep 2s
between subreddits to stay comfortably under that.

If REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET are ever provided, the collector
automatically upgrades to PRAW OAuth (~100 req/min) for higher throughput.
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from src.models import Item, ProductLine, Source


# Atom namespace used in Reddit's RSS feed
_ATOM = "http://www.w3.org/2005/Atom"
_RSS_URL = "https://www.reddit.com/r/{sub}/new.rss?limit={limit}"

# Rotate through realistic User-Agents to avoid Reddit's bot filter
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


def _text_matches(text: str, must_any: list[str], away_from: list[str]) -> bool:
    tl = text.lower()
    if any(w.lower() in tl for w in away_from):
        return False
    return any(w.lower() in tl for w in must_any)


def _classify_stream(text: str, streams_cfg: dict) -> ProductLine | None:
    for key, cfg in streams_cfg.items():
        must = cfg.get("must_match_any", [])
        away = cfg.get("disambiguate_away_from", [])
        if _text_matches(text, must, away):
            co_req = cfg.get("require_co_occurrence_of_any")
            if co_req:
                if not any(w.lower() in text.lower() for w in co_req):
                    continue
            return {
                "card": ProductLine.CARD,
                "insurance": ProductLine.INSURANCE,
                "brand_umbrella": ProductLine.BRAND,
            }.get(key, ProductLine.BRAND)
    return None


_NIBBLES_ANCHORS = ["nibbles card", "nibbles credit", "nibbles insurance",
                    "nibbles pet", "nibbles.com", "nibbles app", "nibbles claim",
                    "nibbles reimburs", "nibbles reward", "odie nibbles", "nibbles odie"]

def _mentions_nibbles(text: str) -> bool:
    """True only if the text has a clear Nibbles product reference — not just the word 'nibbles'."""
    tl = text.lower()
    return any(anchor in tl for anchor in _NIBBLES_ANCHORS)

def _match_competitor(text: str, competitors: list[dict]) -> str | None:
    """Only flag as a competitor post if Nibbles is also mentioned — pure competitor
    posts with no Nibbles context are noise for this digest."""
    if not _mentions_nibbles(text):
        return None
    tl = text.lower()
    for c in competitors:
        for kw in c.get("keywords", []):
            if kw.lower() in tl:
                return c["name"]
    return None


def _parse_rss(xml_text: str, sub: str, streams: dict, competitors: list[dict], window_cutoff: float, ua_index: int, is_owned: bool = False) -> tuple[list[Item], str | None]:
    """Parse Reddit Atom feed XML into Items. Returns (items, next_after_cursor)."""
    items: list[Item] = []
    after: str | None = None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[reddit/rss] r/{sub} XML parse error: {e}")
        return [], None

    # Extract pagination cursor from the "next" link
    for link in root.findall(f"{{{_ATOM}}}link"):
        if link.attrib.get("rel") == "next":
            href = link.attrib.get("href", "")
            if "after=" in href:
                after = href.split("after=")[-1].split("&")[0]

    for entry in root.findall(f"{{{_ATOM}}}entry"):
        # Title
        title_el = entry.find(f"{{{_ATOM}}}title")
        title = title_el.text or "" if title_el is not None else ""

        # Body — Reddit puts HTML in <content>, plain summary in <summary>
        content_el = entry.find(f"{{{_ATOM}}}content")
        summary_el = entry.find(f"{{{_ATOM}}}summary")
        body_raw = ""
        if content_el is not None and content_el.text:
            body_raw = content_el.text
        elif summary_el is not None and summary_el.text:
            body_raw = summary_el.text
        # Strip HTML tags simply
        import re
        body = re.sub(r"<[^>]+>", " ", body_raw).strip()

        text = f"{title}\n{body}".strip()
        if not text:
            continue

        # Timestamp
        published_el = entry.find(f"{{{_ATOM}}}published")
        updated_el   = entry.find(f"{{{_ATOM}}}updated")
        ts_str = None
        if published_el is not None and published_el.text:
            ts_str = published_el.text
        elif updated_el is not None and updated_el.text:
            ts_str = updated_el.text
        try:
            created_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else datetime.now(timezone.utc)
        except Exception:
            created_at = datetime.now(timezone.utc)

        if created_at.timestamp() < window_cutoff:
            continue

        # URL + ID
        link_el = entry.find(f"{{{_ATOM}}}link")
        url = link_el.attrib.get("href", "") if link_el is not None else ""
        post_id = url.rstrip("/").split("/")[-1] if url else str(hash(text))[:8]

        # Author
        author_el = entry.find(f"{{{_ATOM}}}author")
        author_name_el = author_el.find(f"{{{_ATOM}}}name") if author_el is not None else None
        author = author_name_el.text if author_name_el is not None and author_name_el.text else "unknown"

        # Owned subreddits: pull everything, tag as brand
        if is_owned:
            items.append(Item(
                item_id=f"reddit:{post_id}",
                source=Source.REDDIT,
                product_line=ProductLine.BRAND,
                author_hash=Item.hash_author(author),
                created_at=created_at,
                text=text[:10000],
                url=url,
                engagement={"upvotes": 0, "num_comments": 0},
            ))
            continue

        # Stream / competitor classification
        stream = _classify_stream(text, streams)
        competitor = _match_competitor(text, competitors) if stream is None else None
        if stream is None and competitor is None:
            continue

        items.append(Item(
            item_id=f"reddit:{post_id}",
            source=Source.REDDIT,
            product_line=stream or ProductLine.COMPETITOR,
            author_hash=Item.hash_author(author),
            created_at=created_at,
            text=text[:10000],
            url=url,
            engagement={"upvotes": 0, "num_comments": 0},
            competitor_match=competitor,
        ))

    return items, after


_SEARCH_TERMS = ["nibbles card", "nibbles credit", "nibbles insurance", "nibbles pet"]

def _search_subreddit(sub: str, headers: dict) -> list[dict]:
    """Search a subreddit for Nibbles-specific posts using Reddit's search endpoint.
    Returns raw post dicts. Used alongside the RSS feed to catch older posts."""
    posts: list[dict] = []
    seen: set[str] = set()
    for term in _SEARCH_TERMS:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/search.json",
                headers=headers,
                params={"q": term, "sort": "new", "restrict_sr": "1", "limit": 25},
                timeout=15,
            )
            r.raise_for_status()
            for child in r.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                pid = d.get("id", "")
                if pid and pid not in seen:
                    seen.add(pid)
                    posts.append(d)
        except Exception as e:
            print(f"[reddit/search] r/{sub} q='{term}': {e}")
        time.sleep(1)
    return posts


def _fetch_rss(cfg: dict) -> list[Item]:
    src = cfg["sources"]["reddit"]
    streams = cfg["streams"]
    competitors = cfg.get("competitors", [])
    window_cutoff = datetime.now(timezone.utc).timestamp() - src["window_hours"] * 3600
    owned = {s.lower() for s in src.get("owned_subreddits", [])}
    limit = min(src["max_posts_per_sub"], 100)

    items: list[Item] = []
    for i, sub in enumerate(src["subreddits"]):
        ua = _USER_AGENTS[i % len(_USER_AGENTS)]
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.reddit.com/",
        }
        is_owned = sub.lower() in owned
        sub_items: list[Item] = []

        # Paginate RSS — Reddit supports `after` param for next-page cursor
        after = None
        pages_fetched = 0
        max_pages = 3  # up to 300 posts per owned sub, 100 for others

        while pages_fetched < (max_pages if is_owned else 1):
            url = _RSS_URL.format(sub=sub, limit=limit)
            if after:
                url += f"&after={after}"
            try:
                r = requests.get(url, headers=headers, timeout=15)
                r.raise_for_status()
                page_items, after = _parse_rss(
                    r.text, sub, streams, competitors,
                    window_cutoff, i, is_owned=is_owned
                )
                sub_items.extend(page_items)
                pages_fetched += 1
                if not after:
                    break
            except Exception as e:
                print(f"[reddit/rss] r/{sub} page {pages_fetched+1} failed: {e}")
                break
            time.sleep(2)

        # For non-owned subs, also run keyword search to catch older Nibbles posts
        if not is_owned:
            search_posts = _search_subreddit(sub, headers)
            seen_ids = {it.item_id for it in sub_items}
            for d in search_posts:
                text = (d.get("title", "") + "\n" + d.get("selftext", "")).strip()
                created = int(d.get("created_utc", 0))
                author = str(d.get("author", "unknown"))
                url = f"https://www.reddit.com{d.get('permalink', '')}"
                post_id = d.get("id", hash(text))
                item_id = f"reddit:{post_id}"
                if item_id in seen_ids:
                    continue
                stream = _classify_stream(text, streams)
                competitor = _match_competitor(text, competitors) if stream is None else None
                if stream is None and competitor is None:
                    continue
                seen_ids.add(item_id)
                sub_items.append(Item(
                    item_id=item_id,
                    source=Source.REDDIT,
                    product_line=stream or ProductLine.COMPETITOR,
                    author_hash=Item.hash_author(author),
                    created_at=datetime.fromtimestamp(created, tz=timezone.utc) if created else datetime.now(timezone.utc),
                    text=text[:10000],
                    url=url,
                    engagement={"upvotes": d.get("ups", 0), "num_comments": d.get("num_comments", 0)},
                    competitor_match=competitor,
                ))

        label = " [owned — all posts]" if is_owned else ""
        print(f"[reddit/rss] r/{sub}{label}: {len(sub_items)} items")
        items.extend(sub_items)
        if pages_fetched == 0:
            time.sleep(2)

    return items


def _fetch_praw(cfg: dict) -> list[Item]:
    import praw  # type: ignore
    src = cfg["sources"]["reddit"]
    streams = cfg["streams"]
    competitors = cfg.get("competitors", [])
    window_cutoff = datetime.now(timezone.utc).timestamp() - src["window_hours"] * 3600

    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        username=os.environ.get("REDDIT_USERNAME", ""),
        password=os.environ.get("REDDIT_PASSWORD", ""),
        user_agent=os.environ.get("REDDIT_USER_AGENT", src.get("user_agent", "nibbles-listening/0.1")),
    )
    items: list[Item] = []
    for sub_name in src["subreddits"]:
        try:
            for post in reddit.subreddit(sub_name).new(limit=src["max_posts_per_sub"]):
                d = {
                    "id": post.id, "title": post.title, "selftext": post.selftext,
                    "created_utc": int(post.created_utc), "ups": post.ups,
                    "num_comments": post.num_comments, "permalink": post.permalink,
                    "author": str(post.author) if post.author else "unknown",
                }
                text = f"{d['title']}\n{d['selftext']}".strip()
                if not text or d["created_utc"] < window_cutoff:
                    continue
                stream = _classify_stream(text, streams)
                competitor = _match_competitor(text, competitors) if stream is None else None
                if stream is None and competitor is None:
                    continue
                items.append(Item(
                    item_id=f"reddit:{d['id']}",
                    source=Source.REDDIT,
                    product_line=stream or ProductLine.COMPETITOR,
                    author_hash=Item.hash_author(d["author"]),
                    created_at=datetime.fromtimestamp(d["created_utc"], tz=timezone.utc),
                    text=text[:10000],
                    url=f"https://www.reddit.com{d['permalink']}",
                    engagement={"upvotes": d["ups"], "num_comments": d["num_comments"]},
                    competitor_match=competitor,
                ))
        except Exception as e:
            print(f"[reddit/praw] r/{sub_name}: {e}")
        time.sleep(0.5)
    return items


def fetch(cfg: dict) -> list[Item]:
    if not cfg["sources"]["reddit"].get("enabled"):
        return []

    # Prefer PRAW if credentials are fully configured
    if os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"):
        try:
            print("[reddit] OAuth credentials found — using PRAW")
            return _fetch_praw(cfg)
        except ImportError:
            print("[reddit] praw not installed, falling back to RSS")
        except Exception as e:
            print(f"[reddit] PRAW failed ({e}), falling back to RSS")

    print("[reddit] using RSS feed (no credentials needed)")
    return _fetch_rss(cfg)
