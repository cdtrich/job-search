# IO job roundup

A weekly email digest of job openings at European and international public
institutions (EU, UN, OECD, OSCE, NATO, IMO, Council of Europe, ...), filtered
for Brussels + a 500 km commuting radius + fully-remote roles.

Runs on a free GitHub Actions weekly cron. No server, no database, no admin
rights needed.

## How filtering works

Each opening is scored and bucketed:

| Tier | Rule |
|---|---|
| **Brussels** | duty station within 30 km of the origin |
| **Near Brussels (\u2264120 km)** | within 120 km (Antwerp, Ghent, Lille, ...) |
| **Fully remote / home-based** | matches a remote term |
| **Within 500 km** | any known duty station inside `radius_km` |
| *(dropped)* | unknown location and not remote, or beyond the radius |

Distance is real great-circle distance (haversine) from `origin` to the
matched duty station in `config.yaml`. Keyword boosts nudge comms/data/policy
roles up within each tier.

## Sources

1. **ReliefWeb v2 JSON API** \u2014 free, no key, works immediately. Humanitarian-
   skewed but indexes a lot of UN. This is what runs out of the box.
2. **RSS feeds** \u2014 cover what ReliefWeb misses (NATO, OECD, OSCE, EU). They're
   left `enabled: false` until you paste confirmed feed URLs (see config). RSS
   is far more stable than scraping these portals' HTML.
3. **HTML scrapers** \u2014 not included by default. EU Careers and OECD (Taleo)
   have no feeds; either rely on the aggregators above (which index them) or
   add a small scraper as a new `fetch_*` function. As a zero-code backstop,
   EU Careers and most of these portals offer native saved-search email
   alerts.

## Setup

1. Push this folder to a GitHub repo.
2. Create a Gmail **app password** (Google Account -> Security -> 2-step
   verification -> App passwords). This is account-level, so no admin rights.
3. In the repo: **Settings -> Secrets and variables -> Actions** -> add:
   - `SMTP_USER` = your Gmail address
   - `SMTP_PASS` = the 16-char app password
   - `EMAIL_TO`  = where the digest should land (comma-separate for several)
   - *(optional)* `ANTHROPIC_API_KEY` if you turn on the LLM relevance pass
   - *(optional)* `SMTP_HOST`, `SMTP_PORT` if not using Gmail
4. Run it once manually: **Actions -> Weekly IO job roundup -> Run workflow**.
5. Tune `config.yaml` (radius, duty stations, keywords) and add RSS feeds.

## Local dry run

```bash
pip install -r requirements.txt
python roundup.py        # no SMTP creds -> writes roundup.html to preview
```

## Notes

- Stateless by design: it emails everything posted in the last `lookback_days`.
  If you later want strict cross-run dedup, persist seen URLs to a committed
  `state/seen.json` and add a git-commit step to the workflow.
- Not a substitute for the institutions' own alerts \u2014 think of it as a single
  pane of glass over several of them, with your geography rules applied.
# job-search
