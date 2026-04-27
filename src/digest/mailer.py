"""
Mailer — sends the rendered digest via Resend.

Usage in run_digest.py:
    from src.digest.mailer import send_digest
    send_digest(html=rendered_html, digest=digest)

Env vars required:
    RESEND_API_KEY   starts with re_
    DIGEST_FROM      verified sender address, e.g. pulse@alerts.nibbles.com
    DIGEST_TO        comma-separated recipient list
"""
from __future__ import annotations

import os

from src.models import Digest


def send_digest(html: str, digest: Digest) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set.")

    from_addr = os.environ.get("DIGEST_FROM", "pulse@alerts.nibbles.com")
    to_raw = os.environ.get("DIGEST_TO", "")
    recipients = [r.strip() for r in to_raw.split(",") if r.strip()]
    if not recipients:
        raise RuntimeError("DIGEST_TO is empty — no recipients configured.")

    delta_str = f"+{digest.sentiment_delta_vs_7d}" if digest.sentiment_delta_vs_7d >= 0 else str(digest.sentiment_delta_vs_7d)
    subject = (
        f"Nibbles Pulse · {digest.date.strftime('%a %b %-d')} "
        f"· Sentiment {digest.sentiment_score} ({delta_str})"
    )

    try:
        import resend  # type: ignore
        resend.api_key = api_key
        params: resend.Emails.SendParams = {
            "from": from_addr,
            "to": recipients,
            "subject": subject,
            "html": html,
        }
        resp = resend.Emails.send(params)
        print(f"[mailer] sent via Resend: id={resp.get('id')} to={recipients}")
    except ImportError:
        # Fallback: raw HTTP to the Resend API
        import json
        import requests
        payload = {
            "from": from_addr,
            "to": recipients,
            "subject": subject,
            "html": html,
        }
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        print(f"[mailer] sent via Resend (raw HTTP): id={data.get('id')} to={recipients}")
