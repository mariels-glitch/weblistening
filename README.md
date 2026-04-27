# Nibbles Listening Engine — V1 MVP

A daily (M/W/F) AI-summarized digest of Reddit, Google Trends, and iOS App Store signal for the Nibbles Pet Rewards Credit Card and its built-in Pet Insurance. Ships as an Apple-style HTML email.

See `docs/PRD.docx` (or the parent PRD doc) for full product context and the 11 resolved decisions this code implements.

---

## Quick start

```bash
pip install -r requirements.txt
python run_digest.py --fixture fixtures/seed_items.json   # demo run
open out/digest.html                                       # preview
```

For a live run against the real public APIs (requires outbound network):

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # optional; enables Claude path
python run_digest.py                     # no --fixture → live fetch
```

## What this code does

```
  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
  │ Reddit (JSON)│   │ App Store    │   │ Google Trends│
  │ 7 subreddits │   │ RSS, id=     │   │ pytrends US  │
  │ 72h window   │   │ 1588893484   │   │ (unofficial) │
  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
         │                  │                  │
         └──────────────────┴──────────────────┘
                            ▼
                   ┌──────────────────┐
                   │ Enrichers        │
                   │  • PII scrub     │
                   │  • Roster check  │   ← Decision 10 (brand safety)
                   │  • Theme tag     │   ← PRD §6.2
                   │  • Sentiment     │   ← Claude Haiku OR lexicon
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │ Aggregator       │
                   │  • Sentiment 0-100│
                   │  • Theme cards   │
                   │  • Quote picker  │  (verbatim verified)
                   │  • Anomaly flags │
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │ Summarizer       │
                   │  Claude Sonnet   │   ← Claude path writes headline only
                   │  OR rule-based   │   ← deterministic numbers + themes
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │ Renderer         │
                   │  Apple-style HTML│   ← Jinja2 template
                   └────────┬─────────┘
                            ▼
                     out/digest.html
                     out/digest.json  (audit trail, 90-day retention)
```

## Files

| Path                               | What it is                                                    |
|------------------------------------|---------------------------------------------------------------|
| `config.yaml`                      | Keywords, subreddits, competitors, roster, app IDs. PM-owned. |
| `fixtures/seed_items.json`         | Realistic Nibbles items for offline dev / demo runs.          |
| `run_digest.py`                    | Main entry point.                                             |
| `src/models.py`                    | Pydantic models: Item, TrendPoint, Digest.                    |
| `src/collectors/{reddit,appstore,trends}.py` | Source-specific fetchers.                          |
| `src/enrichers/{pii,themes,classify,enrich}.py` | Enrichment pipeline.                            |
| `src/digest/{aggregate,summarize,render}.py` | Aggregation, summarization, HTML render.          |
| `src/digest/templates/digest.html.j2` | Apple-style email template.                                |
| `out/digest.html`                  | Rendered digest for the most recent run.                      |
| `out/digest.json`                  | Structured digest (audit trail).                              |

## Configuration

All product-facing config (keywords, subreddits, competitors, brand-safety roster, digest thresholds) lives in `config.yaml`. Changes to this file should be reviewed by PM + Marketing before merging (Decision 7).

Sensitive config (Anthropic API key, SMTP credentials) lives in environment variables, never in this repo.

## Production runbook (for eng)

1. **Credentials.** Provision `ANTHROPIC_API_KEY` with a $5/day spend cap. Provision `POSTMARK_API_KEY` (or SendGrid) once mailer is wired.
2. **Scheduler.** Deploy as a GitHub Actions cron or Cloud Scheduler hitting a Lambda. Cron: `0 14 * * 1,3,5 UTC` for 07:00 PT Mon/Wed/Fri; add a per-recipient TZ offset when mailing.
3. **Storage.** Point `out/` at an S3/GCS bucket with 90-day lifecycle retention for digest JSON + HTML. DuckDB or Postgres for the Item table.
4. **LLM switch.** Set `llm.provider: claude` in `config.yaml`. The fallback lexicon kicks in automatically when `ANTHROPIC_API_KEY` is not set — don't rely on it for production quality.
5. **Mailer.** `run_digest.py --send` is the hook; wire it to Postmark/SendGrid in `src/digest/mailer.py` (not yet created, 2-hour task).
6. **Flag-this-claim.** Add a form or Linear webhook at the URL referenced by `flag_url()` in `render.py`.
7. **Brand-safety roster.** Keep the roster in `config.yaml` current — quarterly refresh is owned by PM (Decision 10).

## V1 scope boundaries (what this repo deliberately does NOT do)

- No X/Twitter — deferred to V2 pending paid API access.
- No Google Play — Nibbles is iOS-only today (Decision 8).
- No Trustpilot automated scrape — ToS-restricted. A weekly manual copy-paste goes through an intake form into the same Item schema with `source=trustpilot_manual`.
- No hosted dashboard — the email IS the product.
- No translation — US English only.

## Extending

- **New source.** Add a collector in `src/collectors/`, return `list[Item]`, wire into `collect_live()` in `run_digest.py`.
- **New theme.** Add to `THEMES` in `src/enrichers/themes.py`.
- **New summarizer model.** Subclass the `Summarizer` Protocol in `src/digest/summarize.py`.

## Quality guardrails built in

- Every quote in the digest is verified as a verbatim substring of source text before render.
- Every claim in the digest is traceable to an Item via `supporting_item_ids` / `item_id`.
- First-party posts (anyone on the roster) are excluded at enrichment time.
- Leadership mentions route to a quiet callout, not the main sentiment sections.
- PII (email, phone, card numbers, SSN) is scrubbed at ingest.

## License

Internal. Contact PM (Mariel).
